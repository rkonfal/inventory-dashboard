import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "fetch_ga4_overview.py"
SPEC = importlib.util.spec_from_file_location("fetch_ga4_overview", MODULE_PATH)
ga4 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(ga4)


class FetchGa4OverviewAiLayerTests(unittest.TestCase):
    def test_classify_source_bucket_distinguishes_explicit_and_assistant_like(self):
        self.assertEqual(ga4.classify_source_bucket("chatgpt.com / referral"), "explicit_ai")
        self.assertEqual(ga4.classify_source_bucket("perplexity.ai / referral"), "explicit_ai")
        self.assertEqual(ga4.classify_source_bucket("bing / organic"), "assistant_like")
        self.assertEqual(ga4.classify_source_bucket("ntp.msn.com / referral"), "assistant_like")
        self.assertIsNone(ga4.classify_source_bucket("google / organic"))

    def test_is_proxy_organic_direct_ignores_checkout_and_homepage(self):
        self.assertTrue(ga4.is_proxy_organic_direct("google / organic", "/kosmeticka-fytonaplast-jian-kang_z3158/"))
        self.assertTrue(ga4.is_proxy_organic_direct("(direct) / (none)", "/vyhledavani/?search=slaviton"))
        self.assertFalse(ga4.is_proxy_organic_direct("google / organic", "/"))
        self.assertFalse(ga4.is_proxy_organic_direct("(direct) / (none)", "/kosik/doprava-platba/"))

    def test_build_ai_traffic_layer_aggregates_buckets(self):
        source_rows = [
            {
                "sourceMedium": "chatgpt.com / referral",
                "sessions": 12,
                "activeUsers": 10,
                "ecommercePurchases": 2,
                "purchaseRevenue": 3200,
            },
            {
                "sourceMedium": "bing / organic",
                "sessions": 40,
                "activeUsers": 30,
                "ecommercePurchases": 4,
                "purchaseRevenue": 5800,
            },
        ]
        purchase_rows = [
            {
                "sessionSourceMedium": "chatgpt.com / referral",
                "firstUserSourceMedium": "(direct) / (none)",
                "landingPage": "/produkt-a_z123/",
                "purchaseRevenue": 1500,
            },
            {
                "sessionSourceMedium": "(direct) / (none)",
                "firstUserSourceMedium": "bing / organic",
                "landingPage": "/produkt-b_z456/",
                "purchaseRevenue": 2500,
            },
            {
                "sessionSourceMedium": "google / organic",
                "firstUserSourceMedium": "(direct) / (none)",
                "landingPage": "/vyhledavani/?search=slaviton",
                "purchaseRevenue": 900,
            },
        ]

        payload = ga4.build_ai_traffic_layer(source_rows, purchase_rows)
        buckets = {row["key"]: row for row in payload["buckets"]}

        self.assertEqual(payload["summary"]["explicitAiSessions7d"], 12)
        self.assertEqual(payload["summary"]["assistantLikeSessions7d"], 40)
        self.assertEqual(payload["summary"]["proxyTransactions30d"], 2)

        explicit = buckets["explicit_ai"]
        self.assertEqual(explicit["transactions30dLastClick"], 1)
        self.assertEqual(explicit["transactions30dInfluenced"], 0)

        assistant = buckets["assistant_like"]
        self.assertEqual(assistant["transactions30dInfluenced"], 1)
        self.assertEqual(assistant["revenue30dInfluenced"], 2500)

        proxy = buckets["organic_direct_proxy"]
        self.assertEqual(proxy["transactions30dLastClick"], 2)
        self.assertEqual(proxy["revenue30dLastClick"], 3400)


if __name__ == "__main__":
    unittest.main()
