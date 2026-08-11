import sys
import types
import unittest


class _OpenAIError(Exception):
    pass


sys.modules.setdefault(
    "openai",
    types.SimpleNamespace(
        APIConnectionError=_OpenAIError,
        APITimeoutError=_OpenAIError,
        OpenAI=object,
        RateLimitError=_OpenAIError,
    ),
)

import gemini


class QualityGateTests(unittest.TestCase):
    def setUp(self):
        self.codes = {"item-a", "item-b"}

    def test_rejects_empathy_product_foreword(self):
        post = {
            "type": "empathy",
            "parent_text": "朝の支度でメイク道具が散らかる。小さなトレー一つで準備がスムーズになりそう。",
        }
        errors = gemini._post_errors(post, [], self.codes, scheduled_hour=7)
        self.assertTrue(any("前振り" in error for error in errors))

    def test_rejects_weak_product_parent_anywhere(self):
        post = {
            "type": "product",
            "selected_item_code": "item-a",
            "parent_text": "浴室のボトルをまとめて浮かせられる幅広ラック。床に物を置かずに済んで掃除が楽になるし、幅が広くて使いやすそう。",
            "child_text_base": "磁石がつく浴室壁へ工具なしで設置でき、付属フックにはスポンジを掛けられます。",
        }
        errors = gemini._post_errors(post, [], self.codes, scheduled_hour=12)
        self.assertTrue(any("曖昧" in error for error in errors))

    def test_rejects_specification_dump(self):
        post = {
            "type": "product",
            "selected_item_code": "item-a",
            "parent_text": "調理中にザルとボウルを何度も替える場面で、重ねて使えるセットなら洗い物を増やさず作業できます。",
            "child_text_base": "深型、浅型、ザル、ボウル、プレート、電子レンジ対応、食洗機対応で、使わない時は入れ子にして引き出しへ収納できます。",
        }
        errors = gemini._post_errors(post, [], self.codes, scheduled_hour=18)
        self.assertTrue(any("列挙" in error for error in errors))

    def test_publishing_hour_does_not_restrict_story_time(self):
        post = {
            "type": "empathy",
            "parent_text": "夕方に一息ついて、冷たい飲み物を飲む時間だけは少し落ち着く。",
        }
        errors = gemini._post_errors(post, [], self.codes, scheduled_hour=21)
        self.assertEqual(errors, [])

    def test_accepts_focused_product_copy(self):
        post = {
            "type": "product",
            "selected_item_code": "item-a",
            "parent_text": "浴室の床に並ぶボトルを壁へまとめて浮かせれば、持ち上げながら掃除する手間がなくなって床を一気に流せる。",
            "child_text_base": "磁石がつく浴室壁へ工具なしで設置でき、付属フックにはスポンジや掃除用品をまとめて掛けられます。",
        }
        self.assertEqual(gemini._post_errors(post, [], self.codes, scheduled_hour=12), [])


if __name__ == "__main__":
    unittest.main()
