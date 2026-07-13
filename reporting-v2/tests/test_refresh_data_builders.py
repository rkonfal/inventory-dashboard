import importlib.util
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
MODULE_PATH = SCRIPT_DIR / "refresh_data.py"
ORDERING_ACTIONS_MODULE_PATH = SCRIPT_DIR / "build_ordering_actions_from_xls.py"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

SPEC = importlib.util.spec_from_file_location("refresh_data", MODULE_PATH)
refresh_data = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["refresh_data"] = refresh_data
SPEC.loader.exec_module(refresh_data)

ORDERING_ACTIONS_SPEC = importlib.util.spec_from_file_location("build_ordering_actions_from_xls", ORDERING_ACTIONS_MODULE_PATH)
ordering_actions = importlib.util.module_from_spec(ORDERING_ACTIONS_SPEC)
assert ORDERING_ACTIONS_SPEC.loader is not None
sys.modules["build_ordering_actions_from_xls"] = ordering_actions
ORDERING_ACTIONS_SPEC.loader.exec_module(ordering_actions)

PRAGUE_TZ = ZoneInfo("Europe/Prague")


class RefreshDataBuilderTests(unittest.TestCase):
    def make_refresh_output_test_context(self):
        ctx = SimpleNamespace(
            generated_at="2026-06-26T10:00:00+02:00",
        )
        fetch_result = SimpleNamespace(
            cz_inventory={"items": []},
            sk_inventory={"items": []},
            cz_inventory_detail={"items": []},
            sk_inventory_detail={"items": []},
            cz_outbound={"items": []},
            sk_outbound={"items": []},
            finance_snapshot={"source": {"status": "ok"}},
            marketing_snapshot={"summary": {}},
            affiliate_overview={"summary": {}},
            abra_vykaz_hospodareni_reports={"exports": []},
        )
        build_result = SimpleNamespace(
            expiry_overview_payload={"summary": {}},
            store_expiry_watchdog_payload={"summary": {}, "items": []},
            combined_index_payload={"items": []},
            combined_overview_payload={"counts": {}},
            inventory_analytics_payload={"items": []},
            inventory_analytics_730_payload={"items": []},
            inventory_analytics_730_cz_payload={"items": []},
            inventory_analytics_730_sk_payload={"items": []},
            ordering_core_payload={"summary": {}},
            ordering_core_cz_payload={"summary": {}},
            ordering_core_sk_payload={"summary": {}},
            ordering_reference_payload={"items": []},
            ordering_reference_cz_payload={"items": []},
            ordering_reference_sk_payload={"items": []},
            ordering_sales_history_payload={"codes": {}},
            wpj_orders_payload={"items": []},
            wpj_products_payload={"items": []},
            wpj_history_payload={"days": []},
            eshop_ytd_payload={"months": []},
            customer_fact_payload={"customers": []},
            order_fact_payload={"orders": []},
            report_json={"reportDate": "2026-06-25"},
            report_text="report text",
            report_telegram_text="telegram text",
            report_manifest={"months": []},
            heavy_payloads={"ordering_sales_history.json", "order_fact_ytd_window.json"},
            skip_snapshot_for_heavy=True,
        )
        return ctx, fetch_result, build_result

    def test_build_combined_product_views_merges_aliases_and_flags_mismatch(self):
        ctx = refresh_data.CombinedProductsBuildContext(
            wpj_products=[
                {
                    "code": "SKU1",
                    "title": "Alpha Serum",
                    "ean": "111",
                    "url": "/alpha-serum",
                    "visible": True,
                    "price": {"withVat": 499},
                    "stores": [
                        {"store": {"id": 1, "name": "4PX Praha"}, "inStore": 20},
                        {"store": {"id": 2, "name": "Praha"}, "inStore": 5},
                    ],
                }
            ],
            yesterday_orders=[
                {
                    "dateCreated": "2026-06-25T10:00:00+02:00",
                    "deliveryAddress": {"country": {"code": "CZ"}},
                    "source": {"name": "eshop"},
                    "items": [
                        {
                            "type": "product",
                            "code": "SKU1",
                            "name": "Alpha Serum",
                            "pieces": 3,
                            "totalPrice": {"withVat": 1497},
                        }
                    ],
                }
            ],
            cz_inventory={
                "items": [
                    {
                        "sku_code": "SKU1/RED",
                        "sku_id": "A1",
                        "batch_no": "B1",
                        "available_stock": 7,
                        "pending_stock": 1,
                        "freeze_stock": 0,
                        "onway_stock": 2,
                    }
                ]
            },
            sk_inventory={
                "items": [
                    {
                        "sku_code": "SKU1",
                        "sku_id": "A2",
                        "batch_no": "B2",
                        "available_stock": 2,
                        "pending_stock": 0,
                        "freeze_stock": 1,
                        "onway_stock": 0,
                    }
                ]
            },
            cz_outbound={
                "items": [
                    {
                        "consignment_no": "CZ-1",
                        "logistics_product_code": "L1",
                        "carrier_brand_name": "DHL",
                        "create_time": "2026-06-25T12:00:00+02:00",
                        "outboundlist_sku": [
                            {"sku_code": "SKU1/RED", "sku_name": "Alpha Serum", "qty": 4}
                        ],
                    }
                ]
            },
            sk_outbound={"items": []},
            start_dt=datetime(2026, 6, 25, 0, 0, 1, tzinfo=PRAGUE_TZ),
            end_dt=datetime(2026, 6, 25, 23, 59, 59, tzinfo=PRAGUE_TZ),
            generated_at="2026-06-26T10:00:00+02:00",
            manual_overrides={"aliases": {}, "ignore": set()},
            pos_admin_views={},
        )

        combined_index, combined_overview = refresh_data.build_combined_product_views(ctx)

        self.assertEqual(combined_index["counts"]["allCodes"], 1)
        self.assertEqual(combined_index["counts"]["stockMismatch"], 1)
        self.assertEqual(combined_index["counts"]["lowAfterSales"], 1)
        self.assertEqual(combined_index["counts"]["autoMapped4pxAliases"], 1)

        item = combined_index["items"][0]
        self.assertEqual(item["fourpx"]["availableTotal"], 9.0)
        self.assertEqual(item["yesterdaySales"]["units"], 3.0)
        self.assertEqual(item["yesterdayOutbound"]["czUnits"], 4.0)
        self.assertEqual(item["yesterdayOutbound"]["shipments"], 1)
        self.assertIn("stock_mismatch", item["flags"])
        self.assertIn("low_after_sales", item["flags"])
        self.assertIn("auto_mapped_4px_alias", item["flags"])
        self.assertEqual(combined_overview["priorityShortlist"][0]["code"], "SKU1")

    def test_build_inventory_analytics_730d_computes_cover_and_reorder_metrics(self):
        combined_index = {
            "items": [
                {
                    "code": "SKU1",
                    "title": "Alpha Serum",
                    "wpj": {
                        "fourpxStoreTotal": 4,
                        "totalStore": 6,
                        "priceWithVat": 499,
                    },
                    "fourpx": {"availableTotal": 1},
                }
            ]
        }
        ctx = refresh_data.InventoryAnalyticsBuildContext(
            combined_index=combined_index,
            orders=[
                {
                    "dateCreated": "2026-06-20T10:00:00+02:00",
                    "deliveryAddress": {"country": {"code": "CZ"}},
                    "items": [{"type": "product", "code": "SKU1", "name": "Alpha Serum", "pieces": 9}],
                },
                {
                    "dateCreated": "2025-12-31T10:00:00+02:00",
                    "deliveryAddress": {"country": {"code": "SK"}},
                    "items": [{"type": "product", "code": "SKU1", "name": "Alpha Serum", "pieces": 30}],
                },
                {
                    "dateCreated": "2025-01-10T10:00:00+02:00",
                    "deliveryAddress": {"country": {"code": "CZ"}},
                    "items": [{"type": "product", "code": "SKU1", "name": "Alpha Serum", "pieces": 100}],
                },
            ],
            start_dt=datetime(2025, 1, 1, 0, 0, 0, tzinfo=PRAGUE_TZ),
            end_dt=datetime(2026, 6, 26, 23, 59, 59, tzinfo=PRAGUE_TZ),
            generated_at="2026-06-26T10:00:00+02:00",
            wpj_by_code={"SKU1": {"title": "Alpha Serum"}},
            manual_overrides={"aliases": {}, "ignore": set()},
            pos_admin_views={},
            ordering_reference_overrides={},
            ordering_packaging_map={},
        )

        payload = refresh_data.build_inventory_analytics_730d(ctx)

        self.assertEqual(payload["summary"]["trackedItems"], 1)
        self.assertEqual(payload["summary"]["criticalReorderItems"], 1)
        self.assertEqual(payload["summary"]["greenTurnoverItems"], 1)
        self.assertEqual(payload["summary"]["fastLowCoverItems"], 1)

        item = payload["items"][0]
        self.assertEqual(item["units730d"], 139.0)
        self.assertEqual(item["units365d"], 39.0)
        self.assertEqual(item["units90d"], 9.0)
        self.assertEqual(item["turnoverZone"], "green")
        self.assertEqual(item["reorderRisk"], "critical")
        self.assertEqual(item["recommendedOrderUnits"], 6)
        self.assertIn("reorder_candidate", item["tags"])
        self.assertIn("fast_mover_low_cover", item["tags"])
        self.assertEqual(item["byView"]["sk"]["units365d"], 30.0)

    def test_build_inventory_analytics_market_view_excludes_riga_only_sku_from_cz_ordering(self):
        base_payload = {
            "generatedAt": "2026-07-13T18:00:00+02:00",
            "market": "complete",
            "window": {"days": 730},
            "items": [
                {
                    "code": "135415-1",
                    "title": "Zlatý amarantový olej",
                    "effectiveStock": 42.0,
                    "unitSellingPrice": 299,
                    "unitCostAbraAvg": 120,
                    "fourpxAvailable": 42.0,
                    "orderable": True,
                    "sourceChannel": "riga",
                    "strategicPriority": "standard",
                    "excludeFromOrderingReason": None,
                    "referenceSource": "override:sku",
                    "referenceFlags": [],
                    "itemType": "product",
                    "giftCandidate": False,
                    "byView": {
                        "cz": {"units730d": 300.0, "units365d": 150.0, "units180d": 70.0, "units90d": 40.0, "units30d": 10.0, "units14d": 4.0},
                        "sk": {"units730d": 120.0, "units365d": 60.0, "units180d": 30.0, "units90d": 15.0, "units30d": 5.0, "units14d": 2.0},
                    },
                }
            ],
        }
        combined_index = {
            "items": [
                {
                    "code": "135415-1",
                    "fourpx": {
                        "availableTotal": 42.0,
                        "cz": {"availableStock": 0.0},
                        "sk": {"availableStock": 42.0},
                    },
                }
            ]
        }

        payload = refresh_data.build_inventory_analytics_market_view(
            base_payload,
            combined_index,
            "2026-07-13T18:00:00+02:00",
            market_key="cz",
        )

        item = payload["items"][0]
        self.assertFalse(item["orderable"])
        self.assertEqual(item["sourceChannel"], "riga")
        self.assertEqual(item["orderingRole"], "excluded")
        self.assertIn("Riga-only", item["excludeFromOrderingReason"])
        self.assertEqual(item["effectiveStock"], 0.0)
        self.assertGreater(item["recommendedOrderUnits"], 0)

    def test_load_json_if_fresh_uses_source_generated_at_when_requested(self):
        with TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "inventory_analytics_730d.json"
            cache_path.write_text(
                '{"generatedAt":"2026-06-30T10:44:59+02:00","sourceGeneratedAt":"2026-06-17T07:20:36+02:00","items":[1]}',
                encoding="utf-8",
            )

            with patch.object(
                refresh_data,
                "current_local_time",
                return_value=datetime(2026, 6, 30, 11, 0, 0, tzinfo=PRAGUE_TZ),
            ):
                fresh = refresh_data.load_json_if_fresh(cache_path, max_age_hours=24)
                stale = refresh_data.load_json_if_fresh(
                    cache_path,
                    max_age_hours=24,
                    freshness_key="sourceGeneratedAt",
                )

        self.assertIsNotNone(fresh)
        self.assertIsNone(stale)

    def test_build_expiry_overview_tracks_next_half_year_counts(self):
        payload = refresh_data.build_expiry_overview(
            "2026-06-26T10:00:00+02:00",
            {
                "items": [
                    {"code": "SKU1", "title": "Alpha Serum", "fourpx": {"sourceCodes": ["SKU1/RED"]}},
                    {"code": "SKU2", "title": "Beta Mask", "fourpx": {"sourceCodes": []}},
                ]
            },
            [
                {
                    "sku": "SKU1/RED",
                    "account": "CZ",
                    "dateExpiry": "2026-07-10",
                    "daysToExpiry": 14,
                    "datedStock": 10,
                    "batchCount": 1,
                    "riskScore": 120,
                },
                {
                    "sku": "SKU2",
                    "account": "CZ",
                    "dateExpiry": "2027-02-01",
                    "daysToExpiry": 220,
                    "datedStock": 4,
                    "batchCount": 1,
                    "riskScore": 15,
                },
            ],
            [
                {
                    "sku": "SKU3",
                    "account": "SK",
                    "dateExpiry": "2026-12-20",
                    "daysToExpiry": 177,
                    "datedStock": 3,
                    "batchCount": 1,
                    "riskScore": 40,
                },
                {
                    "sku": "SKU4",
                    "account": "SK",
                    "dateExpiry": "2026-06-20",
                    "daysToExpiry": -6,
                    "datedStock": 1,
                    "batchCount": 1,
                    "riskScore": 200,
                },
            ],
        )

        self.assertEqual(payload["summary"]["datedSkuCount"], 4)
        self.assertEqual(payload["summary"]["halfYearDays"], 183)
        self.assertEqual(payload["summary"]["halfYearSkuCount"], 2)
        self.assertEqual(payload["summary"]["halfYearRowCount"], 2)
        self.assertEqual(payload["summary"]["halfYearCzRowCount"], 1)
        self.assertEqual(payload["summary"]["halfYearSkRowCount"], 1)
        self.assertEqual(payload["topExpiring"][0]["title"], "SKU4")
        self.assertEqual(payload["topExpiring"][1]["title"], "Alpha Serum")

    def test_fetch_expiry_exact_sales_orders_uses_year_metrics_with_items(self):
        end_dt = datetime(2026, 7, 12, 23, 59, 59, tzinfo=PRAGUE_TZ)
        base_orders = [{"id": 1, "items": [{"type": "product", "code": "52911", "pieces": 2}]}]
        enriched_orders = base_orders + [{"id": 2, "__classifiedView": "ltm"}]

        with patch.object(
            refresh_data,
            "fetch_wpj_year_order_metrics",
            return_value=base_orders,
        ) as fetch_metrics_mock, patch.object(
            refresh_data,
            "apply_pos_view_overrides_to_orders",
            return_value=enriched_orders,
        ) as apply_overrides_mock:
            result = refresh_data.fetch_expiry_exact_sales_orders(
                "https://wpj.test/graphql",
                "secret-token",
                end_dt,
                pos_view_ids={"ltm": [11]},
                window_days=90,
                limit=500,
            )

        self.assertEqual(result, enriched_orders)
        fetch_metrics_mock.assert_called_once_with(
            "https://wpj.test/graphql",
            "secret-token",
            datetime(2026, 4, 14, 0, 0, 0, tzinfo=PRAGUE_TZ),
            end_dt,
            limit=500,
        )
        apply_overrides_mock.assert_called_once_with(
            base_orders,
            "https://wpj.test/graphql",
            "secret-token",
            datetime(2026, 4, 14, 0, 0, 0, tzinfo=PRAGUE_TZ),
            end_dt,
            detailed=False,
            pos_view_ids={"ltm": [11]},
            limit=500,
        )

    def test_build_ordering_core_prioritizes_critical_and_overstocked_items(self):
        analytics_payload = {
            "window": {"days": 548},
            "items": [
                {
                    "code": "TOP-1",
                    "title": "Critical Serum",
                    "orderable": True,
                    "orderingRole": "top_sku",
                    "reorderRisk": "critical",
                    "turnoverZone": "green",
                    "daysOfCover90d": 5,
                    "daysOfCover365d": 9.1,
                    "effectiveStock": 2,
                    "recommendedOrderUnits": 20,
                    "recommendedMinUnits": 8,
                    "units90d": 40,
                    "units365d": 180,
                    "units30d": 15,
                    "dailyRunRate90d": 0.444,
                    "dailyRunRate365d": 0.493,
                    "dailyRunRate730d": 0.4,
                    "trend90v365Pct": 30,
                    "stockValueSelling": 1000,
                    "unitSellingPrice": 50,
                    "orderPackOptions": [],
                    "sourceChannel": "unknown",
                },
                {
                    "code": "WATCH-1",
                    "title": "Watch Serum",
                    "orderable": True,
                    "orderingRole": "top_sku",
                    "reorderRisk": "watch",
                    "turnoverZone": "orange",
                    "daysOfCover90d": 40,
                    "daysOfCover365d": 55,
                    "effectiveStock": 12,
                    "recommendedOrderUnits": 5,
                    "recommendedMinUnits": 2,
                    "units90d": 20,
                    "units365d": 90,
                    "units30d": 8,
                    "dailyRunRate90d": 0.222,
                    "dailyRunRate365d": 0.247,
                    "dailyRunRate730d": 0.2,
                    "trend90v365Pct": 5,
                    "stockValueSelling": 600,
                    "unitSellingPrice": 40,
                    "orderPackOptions": [],
                    "sourceChannel": "unknown",
                },
                {
                    "code": "RED-1",
                    "title": "Overstock Cream",
                    "orderable": True,
                    "orderingRole": "fill_up",
                    "reorderRisk": "none",
                    "turnoverZone": "red",
                    "daysOfCover90d": 400,
                    "daysOfCover365d": 420,
                    "effectiveStock": 300,
                    "recommendedOrderUnits": 0,
                    "recommendedMinUnits": 0,
                    "units90d": 10,
                    "units365d": 50,
                    "units30d": 3,
                    "dailyRunRate90d": 0.111,
                    "dailyRunRate365d": 0.137,
                    "dailyRunRate730d": 0.1,
                    "trend90v365Pct": -40,
                    "stockValueSelling": 5000,
                    "unitSellingPrice": 20,
                    "orderPackOptions": [],
                    "sourceChannel": "unknown",
                },
                {
                    "code": "FILL-1",
                    "title": "Fill Up Oil",
                    "orderable": True,
                    "orderingRole": "fill_up",
                    "reorderRisk": "watch",
                    "turnoverZone": "green",
                    "daysOfCover90d": 45,
                    "daysOfCover365d": 70,
                    "effectiveStock": 18,
                    "recommendedOrderUnits": 8,
                    "recommendedMinUnits": 4,
                    "units90d": 25,
                    "units365d": 120,
                    "units30d": 10,
                    "dailyRunRate90d": 0.278,
                    "dailyRunRate365d": 0.329,
                    "dailyRunRate730d": 0.25,
                    "trend90v365Pct": 8,
                    "stockValueSelling": 900,
                    "unitSellingPrice": 45,
                    "orderPackOptions": [],
                    "sourceChannel": "unknown",
                },
            ],
        }

        payload = refresh_data.build_ordering_core(refresh_data.OrderingCoreBuildContext(
            analytics_payload=analytics_payload,
            generated_at="2026-06-26T10:00:00+02:00",
        ))

        self.assertEqual(payload["summary"]["trackedItems"], 4)
        self.assertEqual(payload["summary"]["criticalReorderItems"], 1)
        self.assertEqual(payload["summary"]["watchReorderItems"], 2)
        self.assertEqual(payload["summary"]["redTurnoverItems"], 1)
        self.assertEqual(payload["criticalReorder"][0]["code"], "TOP-1")
        self.assertEqual(payload["overstockRisks"][0]["code"], "RED-1")
        self.assertEqual(payload["suggestedFillers"][0]["code"], "FILL-1")
        self.assertTrue(any("kritické pokrytí" in alert for alert in payload["alerts"]))
        self.assertTrue(any("červené obrátkovosti" in alert for alert in payload["alerts"]))

    def test_round_to_allowed_pack_sizes_prefers_recommended_whole_pack_over_units(self):
        item = {
            "code": "54105",
            "orderingRole": "top_sku",
            "orderPackOptions": [1, 360],
            "recommendedOrderStep": 360,
        }

        rounded = refresh_data.round_to_allowed_pack_sizes(item, 396, scenario_type="balanced")

        self.assertEqual(rounded["orderStepUnits"], 360)
        self.assertEqual(rounded["orderStepKind"], "carton")
        self.assertEqual(rounded["roundedUnits"], 720)
        self.assertEqual(rounded["roundingMode"], "zaokrouhleno nahoru na karton")

    def test_ordering_actions_resolve_supplier_sku_alias_to_market_item(self):
        market_items = {
            "80112": {
                "code": "80112",
                "effectiveStock": 744,
                "packagingRaw": "1 / 24 / 720",
                "supplierSkus": ["80112/02", "80112/08", "80112/09"],
            }
        }

        resolved = ordering_actions.resolve_market_item("80112/09", market_items)
        enriched = ordering_actions.enrich_item(
            {
                "code": "80112/09",
                "name": "Ovocný balzám na rty",
                "unitsPerAction": 1,
                "packaging": "",
                "price": 68,
                "priceEur": 2.7,
            },
            market_items,
        )

        self.assertEqual(resolved["code"], "80112")
        self.assertEqual(enriched["stock"], 744.0)
        self.assertEqual(enriched["packaging"], "1 / 24 / 720")

    def test_refresh_action_item_snapshot_falls_back_to_base_code_for_variant(self):
        refreshed = refresh_data.refresh_action_item_snapshot(
            {"code": "80112/09", "unitsPerAction": 1, "packaging": ""},
            {"80112": {"code": "80112", "effectiveStock": 72, "packagingRaw": "1 / 24 / 720", "reorderRisk": "watch"}},
            {"80112": {"code": "80112", "fourpx": {"availableTotal": 72, "cz": {"availableStock": 72}, "sk": {"availableStock": 0}}}},
            {"80112": {"lastSaleDate": "2026-07-12", "daily": []}},
            "complete",
            "2026-07-01",
        )

        self.assertEqual(refreshed["stock"], 72.0)
        self.assertEqual(refreshed["packaging"], "1 / 24 / 720")
        self.assertEqual(refreshed["reorderRisk"], "watch")
        self.assertEqual(refreshed["stockBreakdown"]["fourpxTotal"], 72.0)

    def test_reapply_ordering_reference_to_analytics_excludes_discontinued_sku_override(self):
        payload = {
            "items": [
                {
                    "code": "12710",
                    "title": "Křišťálová kolagenová maska na oční víčka, 1 ks",
                    "unitSellingPrice": 87,
                    "orderable": True,
                    "itemType": "product",
                    "sourceChannel": "both",
                    "strategicPriority": "standard",
                    "giftCandidate": False,
                    "excludeFromOrderingReason": None,
                    "referenceSource": "default",
                    "referenceFlags": [],
                    "reorderRisk": "critical",
                    "turnoverZone": "green",
                    "units365d": 8415,
                    "recommendedOrderUnits": 1106,
                }
            ]
        }
        overrides = {
            "skus": {
                "12710": {
                    "orderable": False,
                    "sourceChannel": "both",
                    "strategicPriority": "risky",
                    "excludeFromOrderingReason": "trvale vyřazené zboží: Dlouhodobě nízké prodeje v rámci EU",
                }
            }
        }

        next_payload, changed = refresh_data.reapply_ordering_reference_to_analytics(payload, overrides)

        self.assertTrue(changed)
        self.assertFalse(next_payload["items"][0]["orderable"])
        self.assertEqual(next_payload["items"][0]["orderingRole"], "excluded")
        self.assertIn("trvale vyřazené zboží", next_payload["items"][0]["excludeFromOrderingReason"])

    def test_reapply_ordering_reference_to_analytics_excludes_discontinued_exact_title_match(self):
        payload = {
            "items": [
                {
                    "code": "35412-01",
                    "title": "Povzbuzující krém pro dokonalá stehna, 120 g (tube)",
                    "unitSellingPrice": 329,
                    "orderable": True,
                    "itemType": "product",
                    "sourceChannel": "both",
                    "strategicPriority": "standard",
                    "giftCandidate": False,
                    "excludeFromOrderingReason": None,
                    "referenceSource": "default",
                    "referenceFlags": [],
                    "reorderRisk": "watch",
                    "turnoverZone": "orange",
                    "units365d": 42,
                    "recommendedOrderUnits": 0,
                }
            ]
        }
        overrides = {
            "titles": {
                refresh_data.normalize_lookup_key("Povzbuzující krém pro dokonalá stehna, 120 g (tube)"): {
                    "orderable": False,
                    "sourceChannel": "both",
                    "strategicPriority": "risky",
                    "excludeFromOrderingReason": "trvale vyřazené zboží",
                }
            }
        }

        next_payload, changed = refresh_data.reapply_ordering_reference_to_analytics(payload, overrides)

        self.assertTrue(changed)
        self.assertFalse(next_payload["items"][0]["orderable"])
        self.assertEqual(next_payload["items"][0]["referenceSource"], "override:title_exact")
        self.assertIn("trvale vyřazené zboží", next_payload["items"][0]["excludeFromOrderingReason"])

    def test_reapply_ordering_reference_to_analytics_excludes_discontinued_exact_sku_with_slash(self):
        payload = {
            "items": [
                {
                    "code": "90159/01",
                    "title": "Stylingový hřeben - stříbrný",
                    "unitSellingPrice": 99,
                    "orderable": True,
                    "itemType": "product",
                    "sourceChannel": "both",
                    "strategicPriority": "standard",
                    "giftCandidate": False,
                    "excludeFromOrderingReason": None,
                    "referenceSource": "default",
                    "referenceFlags": [],
                    "reorderRisk": "none",
                    "turnoverZone": "green",
                    "units365d": 10,
                    "recommendedOrderUnits": 0,
                }
            ]
        }
        overrides = {
            "skus": {
                "90159/01": {
                    "orderable": False,
                    "sourceChannel": "both",
                    "strategicPriority": "risky",
                    "excludeFromOrderingReason": "trvale vyřazené zboží",
                }
            }
        }

        next_payload, changed = refresh_data.reapply_ordering_reference_to_analytics(payload, overrides)

        self.assertTrue(changed)
        self.assertFalse(next_payload["items"][0]["orderable"])
        self.assertEqual(next_payload["items"][0]["referenceSource"], "override:sku")
        self.assertIn("trvale vyřazené zboží", next_payload["items"][0]["excludeFromOrderingReason"])

    def test_reapply_ordering_reference_to_analytics_excludes_000_prefix_items(self):
        payload = {
            "items": [
                {
                    "code": "000082",
                    "title": "Klíčenka Království tianDe",
                    "unitSellingPrice": 39,
                    "orderable": True,
                    "itemType": "product",
                    "sourceChannel": "both",
                    "strategicPriority": "standard",
                    "giftCandidate": False,
                    "excludeFromOrderingReason": None,
                    "referenceSource": "default",
                    "referenceFlags": [],
                    "reorderRisk": "none",
                    "turnoverZone": "green",
                    "units365d": 12,
                    "recommendedOrderUnits": 0,
                }
            ]
        }
        overrides = {
            "prefixes": [
                {
                    "prefix": "000",
                    "meta": {
                        "orderable": False,
                        "itemType": "promo",
                        "sourceChannel": "praha",
                        "strategicPriority": "supplement",
                        "excludeFromOrderingReason": "technická / promo / tisková položka se SKU prefixem 000",
                    },
                }
            ]
        }

        next_payload, changed = refresh_data.reapply_ordering_reference_to_analytics(payload, overrides)

        self.assertTrue(changed)
        self.assertFalse(next_payload["items"][0]["orderable"])
        self.assertEqual(next_payload["items"][0]["referenceSource"], "override:prefix")
        self.assertIn("SKU prefixem 000", next_payload["items"][0]["excludeFromOrderingReason"])

    def test_reapply_ordering_reference_to_analytics_excludes_vyzset_prefix_items(self):
        payload = {
            "items": [
                {
                    "code": "VYZSET1",
                    "title": "Set královských vzorků",
                    "unitSellingPrice": 1,
                    "orderable": True,
                    "itemType": "product",
                    "sourceChannel": "both",
                    "strategicPriority": "standard",
                    "giftCandidate": False,
                    "excludeFromOrderingReason": None,
                    "referenceSource": "default",
                    "referenceFlags": [],
                    "reorderRisk": "watch",
                    "turnoverZone": "green",
                    "units365d": 10,
                    "recommendedOrderUnits": 3,
                }
            ]
        }
        overrides = {
            "prefixes": [
                {
                    "prefix": "VYZSET",
                    "meta": {
                        "orderable": False,
                        "itemType": "promo",
                        "sourceChannel": "praha",
                        "strategicPriority": "supplement",
                        "excludeFromOrderingReason": "vzorkový nebo promo set, ne standardní SKU pro doskladnění",
                    },
                }
            ]
        }

        next_payload, changed = refresh_data.reapply_ordering_reference_to_analytics(payload, overrides)

        self.assertTrue(changed)
        self.assertFalse(next_payload["items"][0]["orderable"])
        self.assertEqual(next_payload["items"][0]["referenceSource"], "override:prefix")
        self.assertIn("promo set", next_payload["items"][0]["excludeFromOrderingReason"])

    def test_reapply_ordering_reference_to_analytics_excludes_discontinued_title_stem_match(self):
        payload = {
            "items": [
                {
                    "code": "30256",
                    "title": "Lipolytický gel na celulitidu",
                    "unitSellingPrice": 379,
                    "orderable": True,
                    "itemType": "product",
                    "sourceChannel": "both",
                    "strategicPriority": "standard",
                    "giftCandidate": False,
                    "excludeFromOrderingReason": None,
                    "referenceSource": "default",
                    "referenceFlags": [],
                    "reorderRisk": "none",
                    "turnoverZone": "no_sales",
                    "units365d": 0,
                    "recommendedOrderUnits": 0,
                }
            ]
        }
        overrides = {
            "titles": {
                refresh_data.normalize_lookup_key("Lipolytický gel na celulitidu, 120 g"): {
                    "orderable": False,
                    "sourceChannel": "both",
                    "strategicPriority": "risky",
                    "excludeFromOrderingReason": "trvale vyřazené zboží",
                }
            },
            "titleStems": {
                refresh_data.normalize_title_stem_key("Lipolytický gel na celulitidu, 120 g"): {
                    "orderable": False,
                    "sourceChannel": "both",
                    "strategicPriority": "risky",
                    "excludeFromOrderingReason": "trvale vyřazené zboží",
                }
            },
        }

        next_payload, changed = refresh_data.reapply_ordering_reference_to_analytics(payload, overrides)

        self.assertTrue(changed)
        self.assertFalse(next_payload["items"][0]["orderable"])
        self.assertEqual(next_payload["items"][0]["referenceSource"], "override:title_stem")
        self.assertIn("trvale vyřazené zboží", next_payload["items"][0]["excludeFromOrderingReason"])

    def test_ordering_sales_history_needs_rebuild_when_window_end_is_stale(self):
        payload = {
            "window": {"to": "2026-05-15T12:00:00+02:00"},
            "codes": {"SKU1": {"code": "SKU1"}},
        }

        self.assertTrue(
            refresh_data.ordering_sales_history_needs_rebuild(
                payload,
                datetime(2026, 7, 11, 21, 0, 0, tzinfo=PRAGUE_TZ),
            )
        )

    def test_refresh_ordering_actions_payload_rehydrates_live_stock_and_sales(self):
        payload = {
            "generatedAt": "2026-06-30T13:39:33+02:00",
            "markets": {
                "complete": {
                    "summary": {},
                    "actions": [
                        {
                            "key": "1",
                            "kind": "bundle",
                            "label": "Akce 1",
                            "title": "Akce SKU1",
                            "requiredGroups": [
                                {
                                    "label": "Spouštěcí produkt",
                                    "mode": "min",
                                    "availableActions": 5,
                                    "items": [{"code": "SKU1", "name": "Alpha Serum", "unitsPerAction": 1, "stock": 5.0, "capacity": 5}],
                                }
                            ],
                            "giftGroups": [],
                            "items": [{"code": "SKU1", "name": "Alpha Serum", "unitsPerAction": 1, "stock": 5.0, "capacity": 5}],
                            "availableActions": 5,
                            "bottleneckLabel": "Spouštěcí produkt",
                            "bottleneckActions": 5,
                            "totalStock": 5.0,
                        }
                    ],
                },
                "cz": {
                    "summary": {},
                    "actions": [
                        {
                            "key": "1",
                            "kind": "bundle",
                            "label": "Akce 1",
                            "title": "Akce SKU1",
                            "requiredGroups": [
                                {
                                    "label": "Spouštěcí produkt",
                                    "mode": "min",
                                    "availableActions": 3,
                                    "items": [{"code": "SKU1", "name": "Alpha Serum", "unitsPerAction": 1, "stock": 3.0, "capacity": 3}],
                                }
                            ],
                            "giftGroups": [],
                            "items": [{"code": "SKU1", "name": "Alpha Serum", "unitsPerAction": 1, "stock": 3.0, "capacity": 3}],
                            "availableActions": 3,
                            "bottleneckLabel": "Spouštěcí produkt",
                            "bottleneckActions": 3,
                            "totalStock": 3.0,
                        }
                    ],
                },
                "sk": {
                    "summary": {},
                    "actions": [
                        {
                            "key": "1",
                            "kind": "bundle",
                            "label": "Akce 1",
                            "title": "Akce SKU1",
                            "requiredGroups": [
                                {
                                    "label": "Spouštěcí produkt",
                                    "mode": "min",
                                    "availableActions": 2,
                                    "items": [{"code": "SKU1", "name": "Alpha Serum", "unitsPerAction": 1, "stock": 2.0, "capacity": 2}],
                                }
                            ],
                            "giftGroups": [],
                            "items": [{"code": "SKU1", "name": "Alpha Serum", "unitsPerAction": 1, "stock": 2.0, "capacity": 2}],
                            "availableActions": 2,
                            "bottleneckLabel": "Spouštěcí produkt",
                            "bottleneckActions": 2,
                            "totalStock": 2.0,
                        }
                    ],
                },
            },
        }
        market_payloads = {
            "complete": {
                "items": [{"code": "SKU1", "effectiveStock": 12.0, "daysOfCover90d": 24.0, "reorderRisk": "watch", "unitSellingPrice": 499}],
            },
            "cz": {
                "items": [{"code": "SKU1", "effectiveStock": 7.0, "daysOfCover90d": 14.0, "reorderRisk": "critical", "unitSellingPrice": 499}],
            },
            "sk": {
                "items": [{"code": "SKU1", "effectiveStock": 5.0, "daysOfCover90d": 45.0, "reorderRisk": "none", "unitSellingPrice": 499}],
            },
        }
        combined_index_payload = {
            "items": [
                {
                    "code": "SKU1",
                    "wpj": {
                        "fourpxStoreTotal": 12.0,
                        "totalStore": 14.0,
                        "stores": [
                            {"storeName": "4PX CZ", "inStore": 7.0},
                            {"storeName": "4PX SK", "inStore": 5.0},
                            {"storeName": "[FlexiBee] Sklad Měčín", "inStore": 2.0},
                        ],
                    },
                    "fourpx": {
                        "availableTotal": 12.0,
                        "cz": {"availableStock": 7.0},
                        "sk": {"availableStock": 5.0},
                    },
                }
            ]
        }
        sales_history_payload = {
            "window": {"to": "2026-07-11T21:00:00+02:00"},
            "codes": {
                "SKU1": {
                    "code": "SKU1",
                    "lastSaleDate": "2026-07-10T10:00:00+02:00",
                    "dailyByView": {
                        "complete": [["2026-06-30", 2.0], ["2026-07-01", 3.0], ["2026-07-05", 4.0]],
                        "cz": [["2026-07-01", 2.0], ["2026-07-05", 2.0]],
                        "sk": [["2026-07-05", 2.0]],
                        "ltm": [],
                        "mecin": [],
                    },
                }
            },
        }
        overrides = {"defaultStartDate": "2026-07-01", "actions": {}}

        refreshed = refresh_data.refresh_ordering_actions_payload(
            payload,
            market_payloads,
            combined_index_payload,
            sales_history_payload,
            "2026-07-11T21:15:00+02:00",
            overrides,
        )

        complete_item = refreshed["markets"]["complete"]["actions"][0]["items"][0]
        cz_item = refreshed["markets"]["cz"]["actions"][0]["items"][0]
        self.assertEqual(refreshed["sourceGeneratedAt"], "2026-06-30T13:39:33+02:00")
        self.assertEqual(complete_item["stock"], 12.0)
        self.assertEqual(complete_item["salesSinceStart"], 7.0)
        self.assertEqual(complete_item["stockBreakdown"]["fourpxCz"], 7.0)
        self.assertNotIn("otherStoresTotal", complete_item["stockBreakdown"])
        self.assertEqual(cz_item["stock"], 7.0)
        self.assertEqual(cz_item["salesSinceStart"], 4.0)
        self.assertEqual(refreshed["markets"]["complete"]["actions"][0]["availableActions"], 12)
        self.assertEqual(refreshed["markets"]["complete"]["summary"]["salesWindowStart"], "2026-07-01")

    def test_build_inventory_health_summary_scores_by_top_sku_share(self):
        analytics_payload = {
            "items": [
                {
                    "code": f"TOP-{idx}",
                    "title": f"Top SKU {idx}",
                    "orderable": True,
                    "orderingRole": "top_sku",
                    "effectiveStock": 20,
                    "stockValueSelling": 1000,
                    "units90d": 90,
                    "unitSellingPrice": 10,
                    "daysOfCover90d": 20,
                    "daysOfCover365d": 20,
                    "daysOfCover730d": 20,
                    "reorderRisk": "none",
                    "turnoverZone": "green",
                    "tags": [],
                }
                for idx in range(10)
            ]
        }
        for idx in range(3):
            analytics_payload["items"][idx]["reorderRisk"] = "critical"
            analytics_payload["items"][idx]["daysOfCover90d"] = 5
        for idx in range(3, 7):
            analytics_payload["items"][idx]["reorderRisk"] = "watch"
            analytics_payload["items"][idx]["daysOfCover90d"] = 25

        payload = refresh_data.build_inventory_health_summary(
            analytics_payload,
            {"summary": {"excludedItems": 2}},
        )

        self.assertEqual(payload["healthScore"], 64)
        self.assertEqual(payload["aCriticalCount"], 3)
        self.assertEqual(payload["aCriticalShare"], 30.0)
        self.assertEqual(payload["aWarningCount"], 4)
        self.assertEqual(payload["aWarningShare"], 40.0)
        self.assertEqual(payload["topSkuCount"], 10)
        self.assertEqual(payload["blockedItems"], 2)

    def test_inventory_health_headline_includes_top_sku_context(self):
        headline = refresh_data.inventory_health_headline({
            "healthScore": 72,
            "aCriticalCount": 3,
            "topSkuCount": 10,
            "slowDeadShare": 12.4,
        })

        self.assertEqual(headline, "score skladu 72/100 · A riziko 3 z 10 SKU · slow/dead 12,4 %")

    def test_classify_abc_buckets_uses_revenue_and_units_mix(self):
        buckets = refresh_data.classify_abc_buckets([
            {"code": "SKU_A1", "orderable": True, "units365d": 120, "unitSellingPrice": 500},
            {"code": "SKU_A2", "orderable": True, "units365d": 80, "unitSellingPrice": 450},
            {"code": "SKU_B1", "orderable": True, "units365d": 20, "unitSellingPrice": 250},
            {"code": "SKU_C1", "orderable": True, "units365d": 5, "unitSellingPrice": 100},
            {"code": "SKU_IGNORED", "orderable": False, "units365d": 999, "unitSellingPrice": 999},
        ])

        self.assertEqual(buckets["SKU_A1"]["abcClass"], "A")
        self.assertEqual(buckets["SKU_A2"]["abcClass"], "A")
        self.assertEqual(buckets["SKU_B1"]["abcClass"], "B")
        self.assertEqual(buckets["SKU_C1"]["abcClass"], "C")
        self.assertNotIn("SKU_IGNORED", buckets)

    def test_inventory_health_uses_abc_a_instead_of_top_sku_fallback_when_available(self):
        analytics_payload = {
            "items": [
                {
                    "code": "A_OK",
                    "orderable": True,
                    "abcClass": "A",
                    "abcRank": 1,
                    "abcRevenue365d": 50000,
                    "effectiveStock": 100,
                    "unitSellingPrice": 500,
                    "units90d": 40,
                    "reorderRisk": "watch",
                    "daysOfCover90d": 25,
                    "daysOfCover365d": 25,
                    "daysOfCover730d": 25,
                    "turnoverZone": "green",
                    "tags": [],
                },
                {
                    "code": "C_BAD",
                    "orderable": True,
                    "abcClass": "C",
                    "abcRank": 200,
                    "abcRevenue365d": 50,
                    "effectiveStock": -4,
                    "unitSellingPrice": 10,
                    "units90d": 1,
                    "reorderRisk": "critical",
                    "daysOfCover90d": -4,
                    "daysOfCover365d": -4,
                    "daysOfCover730d": -4,
                    "turnoverZone": "green",
                    "tags": [],
                    "orderingRole": "top_sku",
                },
            ]
        }

        payload = refresh_data.build_inventory_health_summary(
            analytics_payload,
            {"summary": {"excludedItems": 0}},
        )

        self.assertEqual(payload["aBaseCount"], 1)
        self.assertEqual(payload["aCriticalCount"], 0)
        self.assertEqual(payload["topRiskCodes"], [])

    def test_build_morning_report_keeps_context_and_calculates_deltas(self):
        ctx = refresh_data.MorningReportBuildContext(
            report_date=date(2026, 6, 25),
            wpj_summary={"orders": 12, "revenueWithVat": 2400},
            baseline_orders=10,
            baseline_revenue=2000,
            stock_summary={"lowStockSoldYesterday": [], "negativeStoreStock": []},
            inventory_summary={"health": "ok"},
            logistics_summary={"shipmentsTotal": 8, "coverageWarnings": [], "expiringProducts": []},
            alerts=["alert"],
            priorities=["priority"],
            warnings=["warning"],
            mtd_summary={"orders": 100},
            inventory_health={"aCriticalCount": 2, "topRiskCodes": ["SKU1"], "slowDeadShare": 10},
        )
        fixed_now = datetime(2026, 6, 26, 10, 30, 0, tzinfo=PRAGUE_TZ)

        with patch.object(refresh_data, "current_local_time", return_value=fixed_now):
            payload = refresh_data.build_morning_report(ctx)

        self.assertEqual(payload["generatedAt"], fixed_now.isoformat())
        self.assertEqual(payload["reportDate"], "2026-06-25")
        self.assertEqual(payload["quickSummary"]["orders"]["deltaPct"], 20.0)
        self.assertEqual(payload["quickSummary"]["revenueWithVat"]["deltaPct"], 20.0)
        self.assertEqual(payload["quickSummary"]["shipmentsTotal"], 8)
        self.assertEqual(payload["priorities"], ["priority"])
        self.assertEqual(payload["warnings"], ["warning"])
        self.assertEqual(payload["window"]["from"], "2026-06-25T00:00:01+02:00")
        self.assertEqual(payload["window"]["to"], "2026-06-25T23:59:59+02:00")

    def test_build_refresh_output_registry_includes_expected_outputs(self):
        ctx, fetch_result, build_result = self.make_refresh_output_test_context()

        outputs = refresh_data.build_refresh_output_registry(
            ctx,
            fetch_result,
            build_result,
            remote_sync_result={"status": "ok"},
        )
        by_name = {output.name: output for output in outputs}

        self.assertEqual(by_name["finance_overview.json"].writer, "finance")
        self.assertEqual(by_name["morning_report_previous_day.txt"].writer, "text")
        self.assertEqual(by_name["ordering_sales_history.json"].snapshot_policy, "skip_heavy")
        self.assertEqual(by_name["order_fact_ytd_window.json"].snapshot_policy, "skip_heavy")
        self.assertIn("store_expiry_watchdog.json", by_name)
        self.assertEqual(by_name["reporting_remote_storage_status.json"].payload["status"], "ok")

    def test_normalize_google_sheet_csv_url_converts_edit_link(self):
        url = refresh_data.normalize_google_sheet_csv_url(
            "https://docs.google.com/spreadsheets/d/abc123/edit#gid=456"
        )
        self.assertEqual(
            url,
            "https://docs.google.com/spreadsheets/d/abc123/export?format=csv&gid=456",
        )

    def test_build_store_expiry_watchdog_allocates_sales_fifo(self):
        payload = refresh_data.build_store_expiry_watchdog(
            "2026-07-03T15:00:00+02:00",
            datetime(2026, 7, 3, 15, 0, 0, tzinfo=PRAGUE_TZ),
            {
                "source": {"status": "ok", "mode": "local_json"},
                "warnings": [],
                "rows": [
                    {
                        "storeView": "ltm",
                        "storeLabel": "Litomerice",
                        "sku": "SKU1",
                        "title": "Alpha Serum",
                        "batch": "B1",
                        "expiryDate": "2026-07-20",
                        "receivedDate": "2026-06-01",
                        "receivedUnits": 10,
                        "discardedUnits": 1,
                        "transferredUnits": 0,
                        "note": "",
                        "active": True,
                    },
                    {
                        "storeView": "ltm",
                        "storeLabel": "Litomerice",
                        "sku": "SKU1",
                        "title": "Alpha Serum",
                        "batch": "B2",
                        "expiryDate": "2026-08-10",
                        "receivedDate": "2026-06-15",
                        "receivedUnits": 8,
                        "discardedUnits": 0,
                        "transferredUnits": 0,
                        "note": "",
                        "active": True,
                    },
                ],
            },
            wpj_products=[{"code": "SKU1", "title": "Alpha Serum"}],
            sales_orders=[
                {
                    "__classifiedView": "ltm",
                    "dateCreated": "2026-06-20T10:00:00+02:00",
                    "deliveryAddress": {"country": {"code": "CZ"}},
                    "source": {"name": "Pokladna"},
                    "items": [
                        {"type": "product", "code": "SKU1", "name": "Alpha Serum", "pieces": 12}
                    ],
                }
            ],
            manual_overrides={"aliases": {}, "ignore": set()},
            pos_admin_views={1: "ltm"},
        )

        self.assertEqual(payload["summary"]["visibleRows"], 1)
        self.assertEqual(payload["items"][0]["batch"], "B2")
        self.assertEqual(payload["items"][0]["remainingUnits"], 5.0)
        self.assertEqual(payload["groups"][0]["unmatchedSalesUnits"], 0.0)

    def test_should_write_refresh_snapshot_skips_only_heavy_outputs(self):
        _, _, build_result = self.make_refresh_output_test_context()

        self.assertFalse(refresh_data.should_write_refresh_snapshot(
            refresh_data.RefreshOutputSpec(
                "ordering_sales_history.json",
                {"codes": {}},
                snapshot_policy="skip_heavy",
            ),
            build_result,
        ))
        self.assertTrue(refresh_data.should_write_refresh_snapshot(
            refresh_data.RefreshOutputSpec(
                "marketing_overview.json",
                {"summary": {}},
                snapshot_policy="always",
            ),
            build_result,
        ))


if __name__ == "__main__":
    unittest.main()
