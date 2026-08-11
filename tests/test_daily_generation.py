import io
import json
import sys
import types
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

# CI installs requirements. The local smoke test can still exercise scheduling without the SDK.
try:
    import openai  # noqa: F401
except ModuleNotFoundError:
    fake_openai = types.ModuleType("openai")
    fake_openai.APIConnectionError = type("APIConnectionError", (Exception,), {})
    fake_openai.APITimeoutError = type("APITimeoutError", (Exception,), {})
    fake_openai.RateLimitError = type("RateLimitError", (Exception,), {})
    fake_openai.OpenAI = object
    sys.modules["openai"] = fake_openai

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    fake_requests = types.ModuleType("requests")
    fake_requests.RequestException = type("RequestException", (Exception,), {})
    fake_requests.get = lambda *args, **kwargs: None
    sys.modules["requests"] = fake_requests

import main
import rakuten
import selector


def _items():
    return [
        {
            "itemCode": f"shop:item{i}",
            "itemName": f"商品{i}",
            "itemCaption": "具体的な商品説明",
            "itemPrice": 2000 + i,
            "reviewAverage": 4.5,
            "reviewCount": 200,
            "shopName": "shop",
            "affiliateUrl": f"https://example.com/{i}",
            "imageUrls": [f"https://example.com/{i}.jpg"],
        }
        for i in range(1, 4)
    ]


def _posts():
    return [
        {"post_id": "E1", "type": "empathy", "parent_text": "朝の支度は毎日少しずつ違う。", "theme_group": "朝夜/休日", "tone": "neutral"},
        {"post_id": "P1", "type": "product", "selected_item_code": "shop:item1", "parent_text": "十分に具体的な商品投稿です。", "child_text_base": "十分に具体的な補足文です。"},
        {"post_id": "E2", "type": "empathy", "parent_text": "買い物の順番で一日の流れが変わる。", "theme_group": "買い物", "tone": "positive"},
        {"post_id": "P2", "type": "product", "selected_item_code": "shop:item2", "parent_text": "別の商品について具体的に説明します。", "child_text_base": "別の商品について具体的に補足します。"},
        {"post_id": "E3", "type": "empathy", "parent_text": "暑い日は洗濯物が早く乾く。", "theme_group": "洗濯/衣類", "tone": "positive"},
    ]


class DailyGenerationTest(unittest.TestCase):
    def _run(self, now, save):
        saved = {}

        def replace(posts):
            saved["posts"] = posts
            return len(posts), 0

        with patch.object(main, "load_queue", return_value={"posts": []}), \
             patch.object(main, "get_shortlist", return_value=([], _items(), _items())), \
             patch.object(main, "get_active_rakuten_events", return_value=[]), \
             patch.object(main, "recent_entries", return_value=[]), \
             patch.object(main, "generate_mixed_stock", return_value=_posts()), \
             patch.object(main, "replace_slots", side_effect=replace), \
             patch.object(main, "stock_count", return_value=0), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            main.build_today_remaining(save=save, now=now)
        return json.loads(stdout.getvalue()), saved.get("posts", [])

    def test_22_jst_generates_and_saves_exactly_next_day_five_slots(self):
        result, saved = self._run(datetime(2026, 8, 11, 22, 0, tzinfo=main.JST), True)
        self.assertEqual(result["mode"], "next-day-stock")
        self.assertEqual(result["target_date"], "2026-08-12")
        self.assertEqual(result["target_hours"], [7, 12, 15, 18, 21])
        self.assertEqual(len(saved), 5)
        self.assertEqual([p["scheduled_hour"] for p in saved], [7, 12, 15, 18, 21])
        self.assertEqual(len({p.get("item_code") for p in saved if p["type"] == "product"}), 2)

    def test_before_21_jst_replaces_only_remaining_today_slots(self):
        result, saved = self._run(datetime(2026, 8, 11, 16, 0, tzinfo=main.JST), True)
        self.assertEqual(result["mode"], "today-replace")
        self.assertEqual(result["target_date"], "2026-08-11")
        self.assertEqual([p["scheduled_hour"] for p in saved], [18, 21])

    def test_queue_product_code_is_passed_to_candidate_exclusion(self):
        queued = [{"type": "product", "item_code": "shop:queued", "scheduled_at": "2026-08-13T07:00+09:00"}]
        seen = {}

        def shortlist(*args, **kwargs):
            seen["excluded"] = kwargs["excluded_item_codes"]
            return [], _items(), _items()

        with patch.object(main, "load_queue", return_value={"posts": queued}), \
             patch.object(main, "get_shortlist", side_effect=shortlist), \
             patch.object(main, "get_active_rakuten_events", return_value=[]), \
             patch.object(main, "recent_entries", return_value=[]), \
             patch.object(main, "generate_mixed_stock", return_value=_posts()), \
             patch.object(main, "stock_count", return_value=1), \
             patch("sys.stdout", new_callable=io.StringIO):
            main.build_today_remaining(save=False, now=datetime(2026, 8, 11, 22, 0, tzinfo=main.JST))
        self.assertEqual(seen["excluded"], {"shop:queued"})


class InputSafetyTest(unittest.TestCase):
    def test_primary_filter_excludes_explicit_queue_codes(self):
        with patch.object(selector, "recent_product_codes", return_value=set()):
            passed = selector.primary_filter(_items(), excluded_item_codes={"shop:item1"})
        self.assertNotIn("shop:item1", {item["itemCode"] for item in passed})

    def test_rakuten_keyword_is_trimmed_to_128_characters(self):
        response = Mock(status_code=200)
        response.json.return_value = {"Items": []}
        with patch.object(rakuten, "_credentials", return_value={}), \
             patch.object(rakuten.requests, "get", return_value=response) as request_get:
            rakuten.search_items("あ" * 200)
        self.assertEqual(len(request_get.call_args.kwargs["params"]["keyword"]), 128)


if __name__ == "__main__":
    unittest.main()
