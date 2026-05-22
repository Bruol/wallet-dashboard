#!/usr/bin/env python3
import json
import os
import re
import sqlite3
import threading
import time
import uuid
import hashlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

APP_DIR = os.environ.get("WALLET_DASHBOARD_DIR", "/var/lib/wallet-dashboard")
DB_PATH = os.path.join(APP_DIR, "transactions.sqlite3")
HOST = os.environ.get("WALLET_DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("WALLET_DASHBOARD_PORT", "8787"))
WEBHOOK_TOKEN = os.environ.get("WALLET_DASHBOARD_TOKEN", "")

os.makedirs(APP_DIR, exist_ok=True)
subscribers = []
subscribers_lock = threading.Lock()

AMOUNT_KEYS = ("amount", "transactionAmount", "value", "total", "cost", "price", "sum")
MERCHANT_KEYS = ("merchant", "merchantName", "merchant_name", "payee", "store", "vendor", "name", "description", "title")
DATE_KEYS = ("date", "transactionDate", "transaction_date", "timestamp", "time", "createdAt", "created_at")
CURRENCY_KEYS = ("currency", "currencyCode", "currency_code", "isoCurrencyCode")
CARD_KEYS = ("card", "cardName", "account", "source", "wallet", "paymentMethod")
CATEGORY_KEYS = ("category", "type", "group")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                received_at TEXT NOT NULL,
                transaction_date TEXT,
                merchant TEXT,
                amount_cents INTEGER,
                amount REAL,
                currency TEXT,
                category TEXT,
                card TEXT,
                note TEXT,
                fingerprint TEXT,
                raw_json TEXT NOT NULL
            )
            """
        )
        cols = {row[1] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()}
        if "fingerprint" not in cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN fingerprint TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_received ON transactions(received_at)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_fingerprint ON transactions(fingerprint)")


def flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out[key] = v
            out.update(flatten(v, key))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = f"{prefix}.{i}" if prefix else str(i)
            out[key] = v
            out.update(flatten(v, key))
    return out


def pick(flat, keys):
    lowered = {k.lower().split(".")[-1].strip(): v for k, v in flat.items() if v not in (None, "")}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    for full_key, value in flat.items():
        leaf = full_key.lower().split(".")[-1].strip()
        if any(k.lower() in leaf for k in keys) and value not in (None, ""):
            return value
    return None


def parse_amount(value):
    if value is None:
        return None, None
    if isinstance(value, dict):
        value = pick(flatten(value), AMOUNT_KEYS) or value.get("formatted") or value.get("text")
    if isinstance(value, (int, float, Decimal)):
        amt = Decimal(str(value))
    else:
        s = str(value).strip()
        # Handle common currency strings like "CHF 12.30", "-12,30", "$4.99"
        neg = "-" in s or s.strip().startswith("(")
        s = s.replace("'", "").replace(",", ".")
        match = re.search(r"\d+(?:\.\d+)?", s)
        if not match:
            return None, None
        try:
            amt = Decimal(match.group(0))
            if neg:
                amt = -amt
        except InvalidOperation:
            return None, None
    cents = int((amt * 100).quantize(Decimal("1")))
    return float(amt), cents


def parse_date(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        # milliseconds vs seconds
        ts = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    s = str(value).strip()
    # Shortcuts often produces ISO-ish text. Keep parse permissive.
    for candidate in (s, s.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate).isoformat()
        except ValueError:
            pass
    # Apple Wallet / Shortcuts text: "22 May 2026 at 18:37:24 CEST".
    tz_offsets = {"CEST": "+0200", "CET": "+0100", "UTC": "+0000"}
    m = re.match(r"^(\d{1,2} \w{3,9} \d{4}) at (\d{2}:\d{2}:\d{2}) (\w+)$", s)
    if m:
        tz = tz_offsets.get(m.group(3).upper())
        if tz:
            for fmt in ("%d %b %Y %H:%M:%S %z", "%d %B %Y %H:%M:%S %z"):
                try:
                    return datetime.strptime(f"{m.group(1)} {m.group(2)} {tz}", fmt).isoformat()
                except ValueError:
                    pass
    return s


def parse_payloads(payload):
    """Return one or more transaction dictionaries from common Shortcut shapes."""
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("data"), str):
        records = []
        for line in payload["data"].splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)
        if records:
            return records
    return [payload]


def currency_from_payload(payload):
    raw = json.dumps(payload, ensure_ascii=False)
    m = re.search(r"\b(CHF|USD|EUR|GBP|JPY|CAD|AUD)\b", raw, re.I)
    if m:
        return m.group(1).upper()
    symbols = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY"}
    for symbol, code in symbols.items():
        if symbol in raw:
            return code
    return "CHF"


def normalize(payload):
    flat = flatten(payload)
    amount, cents = parse_amount(pick(flat, AMOUNT_KEYS))
    currency = pick(flat, CURRENCY_KEYS)
    if not currency:
        currency = currency_from_payload(payload)
    merchant = pick(flat, MERCHANT_KEYS) or "Unknown merchant"
    tx_date = parse_date(pick(flat, DATE_KEYS))
    category = pick(flat, CATEGORY_KEYS)
    card = pick(flat, CARD_KEYS)
    note = pick(flat, ("note", "memo", "subtitle"))
    tx = {
        "id": str(uuid.uuid4()),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "transaction_date": tx_date,
        "merchant": str(merchant),
        "amount": amount,
        "amount_cents": cents,
        "currency": str(currency).upper() if currency else "CHF",
        "category": str(category) if category else None,
        "card": str(card) if card else None,
        "note": str(note) if note else None,
        "raw_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
    }
    fp_basis = "|".join(str(tx.get(k) or "") for k in ("transaction_date", "merchant", "amount_cents", "currency", "card"))
    tx["fingerprint"] = hashlib.sha256(fp_basis.encode("utf-8")).hexdigest()
    return tx


def insert_transaction(tx):
    print("received wallet webhook:", json.dumps(public_tx(tx), ensure_ascii=False), flush=True)
    inserted = False
    with db() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO transactions
            (id, received_at, transaction_date, merchant, amount_cents, amount, currency, category, card, note, fingerprint, raw_json)
            VALUES (:id, :received_at, :transaction_date, :merchant, :amount_cents, :amount, :currency, :category, :card, :note, :fingerprint, :raw_json)
            """,
            tx,
        )
        inserted = cur.rowcount > 0
    if inserted:
        broadcast({"type": "transaction", "transaction": public_tx(tx)})
    return inserted


def public_tx(row):
    d = dict(row) if not isinstance(row, dict) else dict(row)
    try:
        d["raw"] = json.loads(d.pop("raw_json"))
    except Exception:
        d.pop("raw_json", None)
    return d


def query_transactions(limit=500):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM transactions
            ORDER BY COALESCE(transaction_date, received_at) DESC, received_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [public_tx(r) for r in rows]


def summary():
    txs = query_transactions(5000)
    totals_by_currency = {}
    by_month = {}
    by_month_currency = {}
    by_merchant = {}
    by_category = {}
    for t in txs:
        dt = t.get("transaction_date") or t.get("received_at") or "unknown"
        month = str(dt)[:7] if len(str(dt)) >= 7 else "unknown"
        cents = t.get("amount_cents") or 0
        currency = t.get("currency") or "CHF"
        totals_by_currency[currency] = totals_by_currency.get(currency, 0) + cents
        by_month[month] = by_month.get(month, 0) + cents
        by_month_currency.setdefault(month, {})[currency] = by_month_currency.setdefault(month, {}).get(currency, 0) + cents
        merchant = (t.get("merchant") or "Unknown merchant") + " · " + currency
        by_merchant[merchant] = by_merchant.get(merchant, 0) + cents
        category = t.get("category") or "Uncategorized"
        by_category[category] = by_category.get(category, 0) + cents
    primary_currency = max(totals_by_currency.items(), key=lambda kv: abs(kv[1]))[0] if totals_by_currency else "CHF"
    return {
        "count": len(txs),
        "total": totals_by_currency.get(primary_currency, 0) / 100,
        "currency": primary_currency,
        "totals_by_currency": {k: v / 100 for k, v in sorted(totals_by_currency.items())},
        "by_month": dict(sorted(by_month.items(), reverse=True)),
        "by_month_currency": dict(sorted(by_month_currency.items(), reverse=True)),
        "by_merchant": dict(sorted(by_merchant.items(), key=lambda kv: abs(kv[1]), reverse=True)[:15]),
        "by_category": dict(sorted(by_category.items(), key=lambda kv: abs(kv[1]), reverse=True)),
    }


def broadcast(obj):
    data = f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()
    with subscribers_lock:
        dead = []
        for w in subscribers:
            try:
                w.write(data)
                w.flush()
            except Exception:
                dead.append(w)
        for w in dead:
            try:
                subscribers.remove(w)
            except ValueError:
                pass


class Handler(BaseHTTPRequestHandler):
    server_version = "WalletDashboard/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type, authorization")
        self.end_headers()

    def authorized(self):
        if not WEBHOOK_TOKEN:
            return True
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        token = qs.get("token", [None])[0]
        auth = self.headers.get("Authorization", "")
        return token == WEBHOOK_TOKEN or auth == f"Bearer {WEBHOOK_TOKEN}"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/dashboard"):
            with open("/opt/wallet-dashboard/static/index.html", "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif parsed.path == "/api/transactions":
            limit = int(parse_qs(parsed.query).get("limit", [500])[0])
            self.send_json({"transactions": query_transactions(min(limit, 5000))})
        elif parsed.path == "/api/summary":
            self.send_json(summary())
        elif parsed.path == "/healthz":
            self.send_json({"ok": True, "time": datetime.now(timezone.utc).isoformat()})
        elif parsed.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            with subscribers_lock:
                subscribers.append(self.wfile)
            try:
                while True:
                    time.sleep(15)
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except Exception:
                with subscribers_lock:
                    try:
                        subscribers.remove(self.wfile)
                    except ValueError:
                        pass
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path not in ("/webhook", "/api/transactions"):
            return self.send_json({"error": "not found"}, 404)
        if not self.authorized():
            return self.send_json({"error": "unauthorized"}, 401)
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except Exception as e:
            return self.send_json({"error": "invalid json", "detail": str(e)}, 400)
        payloads = parse_payloads(payload)
        transactions = [normalize(p) for p in payloads]
        transactions = [tx for tx in transactions if tx.get("amount_cents") is not None]
        inserted = []
        for tx in transactions:
            if insert_transaction(tx):
                inserted.append(tx)
        self.send_json({"ok": True, "received_count": len(transactions), "inserted_count": len(inserted), "transactions": [public_tx(tx) for tx in inserted]}, 201)


if __name__ == "__main__":
    init_db()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"wallet dashboard listening on http://{HOST}:{PORT}")
    httpd.serve_forever()

