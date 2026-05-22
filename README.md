# Apple Wallet Dashboard

A tiny self-hosted webhook receiver and live dashboard for Apple Wallet / card transaction data exported from iPhone Shortcuts.

It was built for a Tailscale tailnet: your iPhone Shortcut posts JSON to a private HTTPS URL, the server stores normalized transactions in SQLite, and the dashboard updates live in the browser.

## Features

- **Webhook endpoint**: `POST /webhook`
- **Batch import support**: parses newline-delimited JSON inside a Shortcut wrapper such as `{ "data": "{...}\n{...}" }`
- **Apple Wallet-friendly parsing**:
  - amounts like `CHF 12.60`, `€ 3.95`, `$4.99`
  - timestamps like `22 May 2026 at 18:37:24 CEST`
  - odd Shortcut keys such as `card ` with trailing whitespace
- **SQLite persistence** with duplicate prevention by transaction fingerprint
- **Live dashboard** using Server-Sent Events
- **Per-currency totals** so CHF/EUR/etc. are not incorrectly merged
- **CHF display total**: the top-line total converts EUR and other currencies to CHF using [Frankfurter](https://frankfurter.dev/)
- **No framework dependencies**: Python standard library only
- **Tailscale Serve ready** for private HTTPS and MagicDNS access

## Screens

The dashboard shows:

- total spend converted to CHF
- transaction count
- current-month spend converted to CHF
- monthly trend converted to CHF
- top merchants
- latest transactions

## Data shape

The endpoint accepts either a single transaction object:

```json
{
  "merchant": "Example Grocery",
  "amount": "CHF 12.40",
  "currency": "CHF",
  "timestamp": "22 May 2026 at 18:37:24 CEST",
  "card ": "Apple Wallet"
}
```

or a Shortcut-style batch wrapper:

```json
{
  "data": "{\"amount\":\"CHF 12.60\",\"card \":\"Visa Debit\",\"timestamp\":\"22 May 2026 at 18:37:24 CEST\",\"merchant\":\"Coop\"}\n{\"amount\":\"€ 3.95\",\"card \":\"Visa Debit\",\"timestamp\":\"16 May 2026 at 19:34:17 CEST\",\"merchant\":\"Cafe\"}"
}
```

Normalized fields include:

- `merchant`
- `amount` / `amount_cents`
- `currency`
- `transaction_date`
- `card`
- `category`
- raw JSON for audit/debugging

## Quick start

```bash
git clone https://github.com/YOUR_USER/apple-wallet-dashboard.git
cd apple-wallet-dashboard
python3 server.py
```

Then open:

```text
http://127.0.0.1:8787/
```

Send a test transaction:

```bash
curl -X POST http://127.0.0.1:8787/webhook \
  -H 'content-type: application/json' \
  -d '{"merchant":"Test Coffee","amount":"CHF 4.50","timestamp":"22 May 2026 at 18:37:24 CEST","card ":"Apple Wallet"}'
```

## Configuration

Environment variables:

- `WALLET_DASHBOARD_HOST`: bind host, default `127.0.0.1`
- `WALLET_DASHBOARD_PORT`: HTTP port, default `8787`
- `WALLET_DASHBOARD_DIR`: data directory, default `/var/lib/wallet-dashboard`
- `WALLET_DASHBOARD_TOKEN`: optional webhook token. If set, `POST /webhook` requires either:
  - `?token=YOUR_TOKEN`, or
  - `Authorization: Bearer YOUR_TOKEN`

Example:

```bash
WALLET_DASHBOARD_DIR=./data \
WALLET_DASHBOARD_TOKEN=secret \
python3 server.py
```

## Deploy with systemd

Install files:

```bash
sudo mkdir -p /opt/wallet-dashboard /var/lib/wallet-dashboard
sudo cp server.py /opt/wallet-dashboard/server.py
sudo cp -r static /opt/wallet-dashboard/static
sudo cp deploy/wallet-dashboard.service /etc/systemd/system/wallet-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now wallet-dashboard
```

Check it:

```bash
curl http://127.0.0.1:8787/healthz
systemctl status wallet-dashboard
```

## Expose privately with Tailscale Serve

Install and authenticate Tailscale first, then run:

```bash
tailscale serve --bg --https=443 127.0.0.1:8787
```

Tailscale will print a private HTTPS MagicDNS URL, for example:

```text
https://wallet-dashboard.example-tailnet.ts.net/
```

Your webhook URL becomes:

```text
https://wallet-dashboard.example-tailnet.ts.net/webhook
```

This stays private to devices/users allowed on your tailnet.

### Containers or VMs without `/dev/net/tun`

Some containerized environments do not provide `/dev/net/tun`. In that case, run `tailscaled` in userspace networking mode:

```bash
sudo tailscaled --tun=userspace-networking
```

For systemd, override the Tailscale service to include:

```ini
[Service]
ExecStart=
ExecStart=/usr/sbin/tailscaled --tun=userspace-networking --state=/var/lib/tailscale/tailscaled.state --socket=/run/tailscale/tailscaled.sock --port=${PORT}
```

## iPhone Shortcut outline

One workable Shortcut flow:

1. Collect Apple Wallet / card transaction text into dictionaries with keys like:
   - `amount`
   - `merchant`
   - `timestamp`
   - `card `
2. Convert each dictionary to JSON.
3. Join JSON lines with newlines.
4. Make a `POST` request to `/webhook` with JSON body:

```json
{
  "data": "<newline-delimited-json>"
}
```

Use content type `application/json`.

## API

### `GET /`

Dashboard UI.

### `GET /healthz`

Health check.

### `GET /api/summary`

Summary totals, monthly data, top merchants, and exchange-rate metadata. The `total_chf` and `by_month_chf` fields are computed by converting all currency totals to CHF via Frankfurter and caching rates in memory.

### `GET /api/transactions?limit=500`

Latest normalized transactions.

### `GET /events`

Server-Sent Events stream used by the live dashboard.

### `POST /webhook`

Receives one transaction or a batch wrapper. Returns inserted rows. Duplicate transactions are ignored.

## Storage

SQLite database location:

```text
$WALLET_DASHBOARD_DIR/transactions.sqlite3
```

The app stores raw JSON for each transaction in the database. Keep the database private and do not commit it to git.

## Security notes

- Prefer exposing the app only through Tailscale Serve or another private network.
- Set `WALLET_DASHBOARD_TOKEN` if the endpoint is reachable by anything other than trusted tailnet devices.
- Do not commit `transactions.sqlite3`, raw payload files, logs, or Shortcut exports.

## Development

Run the tests and syntax check:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile server.py
```

## License

MIT
