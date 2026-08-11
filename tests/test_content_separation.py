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
    def test_rejects_fact_not_present_verbatim_in_selected_product(self):
        response = {
            "products": [
                {"selected_item_code": "shop:box", "facts": ["スプーン付き", "パッキン付き"]},
                {"selected_item_code": "shop:bucket", "facts": ["35L", "水運び"]},
            ]
        }
        with patch.object(gemini, "_json_response", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "存在しない事実"):
                gemini._extract_verified_product_facts(_items())

    def test_accepts_only_verbatim_source_evidence(self):
        response = {
            "products": [
                {"selected_item_code": "shop:box", "facts": ["パッキン付き", "小麦粉1kgを袋ごと収納"]},
                {"selected_item_code": "shop:bucket", "facts": ["35L", "水運び"]},
            ]
        }
        with patch.object(gemini, "_json_response", return_value=response):
            verified = gemini._extract_verified_product_facts(_items())
        self.assertEqual(verified[0]["facts"][0], "パッキン付き")


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

    def test_rejects_afternoon_language_in_7am_slot(self):
        with self.assertRaisesRegex(RuntimeError, "時刻表現"):
            gemini._validate_empathy_text(self._valid("午後の休憩に窓の外を見ると、仕事の手を止めたくなる。"), 7, date(2026, 8, 13))

    def test_rejects_weekend_language_on_thursday(self):
        with self.assertRaisesRegex(RuntimeError, "週末表現"):
            gemini._validate_empathy_text(self._valid("週末に床を掃除すると、部屋の空気まで変わった気がする。"), 15, date(2026, 8, 13))

    def test_rejects_product_feature_leak(self):
        with self.assertRaisesRegex(RuntimeError, "商品訴求"):
            gemini._validate_empathy_text(self._valid("段差や縁があるだけで瓶が安定して、置き場所が決まる。"), 21, date(2026, 8, 13))


if __name__ == "__main__":
    unittest.main()
