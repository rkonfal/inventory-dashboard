import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
MODULE_PATH = SCRIPT_DIR / "refresh_data.py"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

SPEC = importlib.util.spec_from_file_location("refresh_data", MODULE_PATH)
refresh_data = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["refresh_data"] = refresh_data
SPEC.loader.exec_module(refresh_data)

PRAGUE_TZ = ZoneInfo("Europe/Prague")


class ExpiryVariantExactMetricsTests(unittest.TestCase):
    def test_build_expiry_overview_keeps_variant_exact_sales_separate(self):
        payload = refresh_data.build_expiry_overview(
            "2026-07-10T07:09:35+02:00",
            {
                "items": [
                    {"code": "41328", "title": "Levandulová náplast na nohy", "fourpx": {"sourceCodes": ["41328/01", "41328/05", "41328/07"]}},
                ]
            },
            [
                {
                    "sku": "41328/01",
                    "account": "CZ",
                    "dateExpiry": "2026-08-07",
                    "daysToExpiry": 28,
                    "datedStock": 100,
                    "batchCount": 1,
                    "riskScore": 12,
                },
                {
                    "sku": "41328/05",
                    "account": "CZ",
                    "dateExpiry": "2026-08-07",
                    "daysToExpiry": 28,
                    "datedStock": 40,
                    "batchCount": 1,
                    "riskScore": 8,
                },
            ],
            [
                {
                    "sku": "41328/07",
                    "account": "SK",
                    "dateExpiry": "2026-08-07",
                    "daysToExpiry": 28,
                    "datedStock": 20,
                    "batchCount": 1,
                    "riskScore": 4,
                },
            ],
            cz_inventory_items=[
                {"sku_code": "41328/01", "available_stock": 150, "pending_stock": 0, "freeze_stock": 0, "onway_stock": 0},
                {"sku_code": "41328/05", "available_stock": 45, "pending_stock": 0, "freeze_stock": 0, "onway_stock": 0},
            ],
            sk_inventory_items=[
                {"sku_code": "41328/07", "available_stock": 21, "pending_stock": 0, "freeze_stock": 0, "onway_stock": 0},
            ],
            sales_orders=[
                {
                    "dateCreated": "2026-07-08T10:00:00+02:00",
                    "deliveryAddress": {"country": {"code": "CZ"}},
                    "items": [{"type": "product", "code": "41328/01", "name": "Lavender Patch", "pieces": 12}],
                },
                {
                    "dateCreated": "2026-07-09T10:00:00+02:00",
                    "deliveryAddress": {"country": {"code": "CZ"}},
                    "items": [{"type": "product", "code": "41328/05", "name": "Lavender Patch 5", "pieces": 3}],
                },
                {
                    "dateCreated": "2026-07-07T10:00:00+02:00",
                    "deliveryAddress": {"country": {"code": "SK"}},
                    "items": [{"type": "product", "code": "41328/07", "name": "Lavender Patch 7", "pieces": 2}],
                },
            ],
            end_dt=datetime(2026, 7, 10, 12, 0, 0, tzinfo=PRAGUE_TZ),
            pos_admin_views={},
        )

        by_sku_market = {
            (row["sku"], row["account"]): row["exactAnalytics"]
            for row in payload["topExpiring"]
        }

        self.assertEqual(by_sku_market[("41328/01", "CZ")]["units30d"], 12.0)
        self.assertEqual(by_sku_market[("41328/05", "CZ")]["units30d"], 3.0)
        self.assertEqual(by_sku_market[("41328/07", "SK")]["units30d"], 2.0)
        self.assertNotEqual(
            by_sku_market[("41328/01", "CZ")]["units30d"],
            by_sku_market[("41328/05", "CZ")]["units30d"],
        )
        self.assertNotEqual(
            by_sku_market[("41328/05", "CZ")]["daysOfCover30d"],
            by_sku_market[("41328/07", "SK")]["daysOfCover30d"],
        )


if __name__ == "__main__":
    unittest.main()
