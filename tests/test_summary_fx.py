import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "server.py"


def load_server(temp_dir):
    os.environ["WALLET_DASHBOARD_DIR"] = temp_dir
    spec = importlib.util.spec_from_file_location("wallet_dashboard_server", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SummaryFxTests(unittest.TestCase):
    def test_summary_reports_total_spend_converted_to_chf(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = load_server(tmp)
            server.init_db()
            server.get_exchange_rate = lambda base, target="CHF": 0.9 if (base, target) == ("EUR", "CHF") else 1.0

            for tx in (
                server.normalize({"merchant": "Swiss Coffee", "amount": "CHF 10.00", "date": "2026-05-22T10:00:00+00:00"}),
                server.normalize({"merchant": "Euro Cafe", "amount": "€ 20.00", "date": "2026-05-22T11:00:00+00:00"}),
            ):
                self.assertTrue(server.insert_transaction(tx))

            result = server.summary()

            self.assertEqual(result["display_currency"], "CHF")
            self.assertEqual(result["total_chf"], 28.0)
            self.assertEqual(result["totals_in_chf"], {"CHF": 10.0, "EUR": 18.0})
            self.assertEqual(result["exchange_rates"]["EURCHF"], 0.9)
            self.assertEqual(result["exchange_rate_errors"], {})


if __name__ == "__main__":
    unittest.main()
