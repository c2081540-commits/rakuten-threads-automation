import unittest
import sys
import types
from datetime import date
from unittest.mock import patch

try:
    import openai  # noqa: F401
except ModuleNotFoundError:
    fake_openai = types.ModuleType("openai")
    fake_openai.APIConnectionError = type("APIConnectionError", (Exception,), {})
    fake_openai.APITimeoutError = type("APITimeoutError", (Exception,), {})
    fake_openai.RateLimitError = type("RateLimitError", (Exception,), {})
    fake_openai.OpenAI = object
    sys.modules["openai"] = fake_openai

import gemini


def _items():
    return [
        {
            "itemCode": "shop:box",
            "itemName": "保存容器 2L パッキン付き",
            "itemCaption": "小麦粉1kgを袋ごと収納できるワンタッチ容器です。",
            "itemPrice": 2000,
            "reviewAverage": 4.5,
            "reviewCount": 100,
        },
        {
            "itemCode": "shop:bucket",
            "itemName": "四角い大型バケツ 35L",
            "itemCaption": "ウェットスーツの洗浄や水運びに使えます。",
            "itemPrice": 3000,
            "reviewAverage": 4.6,
            "reviewCount": 200,
        },
    ]


class EvidenceGateTest(unittest.TestCase):
    def test_model_selects_but_python_attaches_untouched_source(self):
        response = {"selected_item_codes": ["shop:bucket", "shop:box"]}
        with patch.object(gemini, "_json_response", return_value=response):
            verified = gemini._select_grounded_products(_items(), date(2026, 8, 13))
        self.assertEqual([x["selected_item_code"] for x in verified], ["shop:bucket", "shop:box"])
        self.assertIn("四角い大型バケツ 35L", verified[0]["facts"])
        self.assertIn("ウェットスーツの洗浄や水運びに使えます。", verified[0]["facts"])

    def test_selection_fallback_rotates_instead_of_fixing_products(self):
        with patch.object(gemini, "_json_response", return_value={"selected_item_codes": []}):
            verified = gemini._select_grounded_products(_items(), date(2026, 8, 13))
        self.assertEqual(set(x["selected_item_code"] for x in verified), {"shop:box", "shop:bucket"})

    def test_requires_two_usable_distinct_products(self):
        with self.assertRaisesRegex(RuntimeError, "2件未満"):
            gemini._select_grounded_products(_items()[:1])


class EmpathyGateTest(unittest.TestCase):
    def _valid(self, first="買い物へ行くと、予定になかった物までついカゴに入れてしまう。"):
        return first + "帰宅してレシートを見ると少し反省するけど、結局ちゃんと使うならいいかと思ってしまう。次に行くとまた同じことを繰り返す。"

    def test_rejects_one_line_poem(self):
        with self.assertRaisesRegex(RuntimeError, "短すぎます"):
            gemini._validate_empathy_text("夜ごはんのあと、食器を並べた瞬間だけ小さな満足が来る。", 21, date(2026, 8, 13))

    def test_accepts_two_sentence_scene_and_empathy(self):
        gemini._validate_empathy_text(self._valid(), 15, date(2026, 8, 13))

    def test_accepts_key_fixed_place_as_normal_daily_scene(self):
        text = (
            "朝、出かける直前に鍵が見つからなくて靴をひっくり返す時間が嫌い。"
            "定位置を決めればいいと分かっているのに、その30秒でイライラして結局探し物をして遅刻しそうになる。"
        )
        gemini._validate_empathy_text(text, 7, date(2026, 8, 12))

    def test_publishing_hour_does_not_restrict_story_time(self):
        gemini._validate_empathy_text(
            self._valid("午後の休憩に窓の外を見ると、仕事の手を止めたくなる。"),
            7,
            date(2026, 8, 13),
        )

    def test_rejects_weekend_language_on_thursday(self):
        with self.assertRaisesRegex(RuntimeError, "週末表現"):
            gemini._validate_empathy_text(self._valid("週末に床を掃除すると、部屋の空気まで変わった気がする。"), 15, date(2026, 8, 13))

    def test_rejects_product_feature_leak(self):
        with self.assertRaisesRegex(RuntimeError, "商品訴求"):
            gemini._validate_empathy_text(self._valid("段差や縁があるだけで瓶が安定して、置き場所が決まる。"), 21, date(2026, 8, 13))


class RegenerationTest(unittest.TestCase):
    def test_regenerates_after_validation_failure(self):
        outputs = iter(["短い。", "具体的な日常場面と共感の着地点を含む十分な長さの完成文です。"])

        def build(attempt, last_error):
            return next(outputs)

        def validate(value):
            if len(value) < 20:
                raise RuntimeError("短すぎます")
            return value

        result = gemini._generate_with_validation("共感投稿", build, validate)
        self.assertEqual(result, "具体的な日常場面と共感の着地点を含む十分な長さの完成文です。")

    def test_stops_after_three_failed_attempts(self):
        calls = []

        def build(attempt, last_error):
            calls.append((attempt, last_error))
            return "短い。"

        with self.assertRaisesRegex(RuntimeError, "3回再生成"):
            gemini._generate_with_validation(
                "共感投稿", build, lambda value: (_ for _ in ()).throw(RuntimeError("短すぎます"))
            )
        self.assertEqual(len(calls), 3)
        self.assertIsInstance(calls[1][1], RuntimeError)

    def test_quality_fallback_continues_after_three_failures(self):
        result = gemini._generate_with_validation(
            "商品投稿",
            lambda attempt, last_error: {"draft": attempt},
            lambda value: (_ for _ in ()).throw(RuntimeError("品質不合格")),
            fallback=lambda last_result, last_error: {"used": last_result["draft"]},
        )
        self.assertEqual(result, {"used": 3})


class EmpathyFallbackTest(unittest.TestCase):
    def test_fallback_always_returns_three_valid_distinct_posts(self):
        posts = gemini._fallback_empathy_posts(date(2026, 8, 13))
        self.assertEqual([post["post_id"] for post in posts], ["E1", "E2", "E3"])
        self.assertEqual(len({post["theme_group"] for post in posts}), 3)
        for post, hour in zip(posts, (7, 15, 21)):
            gemini._validate_empathy_text(post["parent_text"], hour, date(2026, 8, 13))

class ThreeWayComparisonTest(unittest.TestCase):
    def test_retries_only_slots_whose_three_candidates_are_rejected(self):
        verified = [
            {"selected_item_code": "shop:box", "facts": ["保存容器", "小麦粉1kgを袋ごと収納"]},
            {"selected_item_code": "shop:bucket", "facts": ["四角い大型バケツ 35L", "水運びに使える"]},
        ]
        calls = []

        def generate(verified, recent, events, target_date, only_ids, rejection_reasons):
            calls.append(list(only_ids))
            return {f"{post_id}-A": {"candidate_id": f"{post_id}-A", "post_id": post_id,
                    "type": "product" if post_id.startswith("P") else "empathy"}
                    for post_id in only_ids}

        comparisons = iter([
            ({"E1": {"post_id": "E1"}, "P1": {"post_id": "P1"}, "P2": {"post_id": "P2"}, "E3": {"post_id": "E3"}}, {"E2": "3案とも不自然"}),
            ({"E2": {"post_id": "E2"}}, {}),
        ])
        with patch.object(gemini, "_generate_three_way_candidates", side_effect=generate), \
             patch.object(gemini, "_compare_three_way_candidates", side_effect=lambda *args: next(comparisons)):
            result = gemini._generate_by_comparison(verified)

        self.assertEqual(calls, [["E1", "P1", "E2", "P2", "E3"], ["E2"]])
        self.assertEqual([post["post_id"] for post in result], ["E1", "P1", "E2", "P2", "E3"])

    def test_comparer_cannot_return_rewritten_or_unknown_candidate(self):
        candidates = {"E1-A": {"candidate_id": "E1-A", "post_id": "E1", "type": "empathy", "parent_text": "十分な長さの具体的な日常場面がここにある。そこから自然に感じた本音も続けて書かれている。"}}
        response = {"selections": [{"post_id": "E1", "selected_candidate_id": "E1-X", "rejection_reason": ""}]}
        with patch.object(gemini, "_json_response", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "候補外"):
                gemini._compare_three_way_candidates(candidates, [], [])


if __name__ == "__main__":
    unittest.main()
