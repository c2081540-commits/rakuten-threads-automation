import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import datetime as RealDateTime, timezone, timedelta
from unittest.mock import patch

import main

JST = timezone(timedelta(hours=9))


class FrozenDateTime(RealDateTime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 11, 21, 30, tzinfo=tz or JST)


def fake_posts():
    posts = []
    for i in range(10):
        is_product = i in (1, 3, 6, 8)
        row = {
            "post_id": f"T{i+1}",
            "type": "product" if is_product else "empathy",
            "parent_text": f"test-{i+1}",
        }
        if is_product:
            row["selected_item_code"] = "item-1" if i == 1 else "item-2"
            row["child_text_base"] = "補足文"
        posts.append(row)
    return posts


class NextDayStockTest(unittest.TestCase):
    def setUp(self):
        self.items = [
            {
                "itemCode": "item-1", "itemName": "商品1", "imageUrls": ["img1"],
                "affiliateUrl": "aff1", "itemPrice": 1000, "reviewAverage": 4.5,
                "reviewCount": 100,
            },
            {
                "itemCode": "item-2", "itemName": "商品2", "imageUrls": ["img2"],
                "affiliateUrl": "aff2", "itemPrice": 2000, "reviewAverage": 4.6,
                "reviewCount": 200,
            },
        ]

    def _patches(self, append=None):
        return [
            patch.object(main, "datetime", FrozenDateTime),
            patch.object(main, "stock_count", return_value=3),
            patch.object(main, "load_queue", return_value={"posts": []}),
            patch.object(main, "get_shortlist", return_value=([], [], self.items)),
            patch.object(main, "get_active_rakuten_events", return_value=[]),
            patch.object(main, "recent_entries", return_value=[]),
            patch.object(main, "generate_mixed_stock", return_value=fake_posts()),
            patch.object(main, "append_posts", side_effect=append or (lambda posts: 13)),
        ]

    def test_after_21_generates_next_day_preview(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self._patches():
                main.build_today_remaining(save=False)

        result = json.loads(buf.getvalue())
        self.assertEqual(result["mode"], "next-day-stock")
        self.assertEqual(result["target_start_date"], "2026-08-12")
        self.assertEqual(len(result["posts"]), 10)
        self.assertEqual(
            [p["scheduled_at"] for p in result["posts"][:5]],
            [
                "2026-08-12T07:00+09:00",
                "2026-08-12T12:00+09:00",
                "2026-08-12T15:00+09:00",
                "2026-08-12T18:00+09:00",
                "2026-08-12T21:00+09:00",
            ],
        )
        self.assertEqual([p["day_in_batch"] for p in result["posts"]], [1] * 5 + [2] * 5)

    def test_after_21_refill_saves_next_day_stock(self):
        saved = []

        def capture(posts):
            saved.extend(posts)
            return 10

        with self._patches(append=capture):
            main.build_today_remaining(save=True)

        self.assertEqual(len(saved), 10)
        self.assertEqual(saved[0]["scheduled_at"], "2026-08-12T07:00+09:00")
        self.assertEqual(saved[-1]["scheduled_at"], "2026-08-13T21:00+09:00")


if __name__ == "__main__":
    unittest.main()
