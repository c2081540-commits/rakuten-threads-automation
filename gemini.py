import json
import os
from pathlib import Path

from google import genai

ROOT = Path(__file__).resolve().parent


def _client():
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY が設定されていません。")
    return genai.Client(api_key=key)


def _load_prompt(name):
    return (ROOT / "prompts" / name).read_text(encoding="utf-8")


def _json_response(prompt):
    response = _client().models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    try:
        return json.loads(response.text)
    except Exception as exc:
        raise RuntimeError(f"Gemini JSON解析失敗: {response.text}") from exc


def select_product(items):
    candidates = []
    for item in items:
        candidates.append({
            "itemCode": item["itemCode"],
            "itemName": item["itemName"],
            "itemCaption": item.get("itemCaption", "")[:500],
            "price": item["itemPrice"],
            "rating": item["reviewAverage"],
            "reviews": item["reviewCount"],
            "shopName": item.get("shopName", ""),
            "imageUrls": item.get("imageUrls", [])[:3],
        })

    prompt = f"""
以下は楽天APIから取得し、機械条件を通過した商品候補です。
Threadsアカウント「これ、家に欲しい」で1件だけ紹介する商品を選んでください。

重視する順序:
1. 商品画像を見ただけで用途や便利さが伝わりやすい
2. 日常の小さな不満・面倒を分かりやすく解決する
3. Threadsで短いリアクションを作りやすい
4. 衝動買いを検討しやすい価格
5. 評価・レビュー件数による安心感

ブランド名やショップ名だけを理由に優遇しないでください。
同じブランドが強くても、その商品のSNS適性を個別に評価してください。
数値や商品仕様を推測・変更しないでください。

候補:
{json.dumps(candidates, ensure_ascii=False)}

JSONのみ返してください:
{{
  "selected_item_code": "itemCode",
  "reason": "選定理由を短く",
  "preferred_image_index": 0
}}

preferred_image_index は候補の imageUrls の0始まりの番号です。
文字や広告要素が少なく、商品・用途が一目で伝わる画像を優先してください。
画像内容を十分判断できない場合は0にしてください。
"""
    result = _json_response(prompt)
    selected_code = result.get("selected_item_code")
    selected = next((x for x in items if x["itemCode"] == selected_code), None)
    if selected is None:
        raise RuntimeError(f"Geminiが候補外の商品コードを返しました: {selected_code}")

    image_index = result.get("preferred_image_index", 0)
    if not isinstance(image_index, int) or image_index < 0 or image_index >= len(selected["imageUrls"]):
        image_index = 0

    return selected, result.get("reason", ""), image_index


def generate_product_copy(item, recent_posts=None):
    base = _load_prompt("product.txt")
    recent_posts = recent_posts or []
    facts = {
        "itemCode": item["itemCode"],
        "itemName": item["itemName"],
        "itemCaption": item.get("itemCaption", "")[:1000],
    }
    prompt = f"""
{base}

【今回の商品情報】
{json.dumps(facts, ensure_ascii=False)}

【直近の商品投稿】
{json.dumps(recent_posts, ensure_ascii=False)}

次のJSONだけを返してください。
{{
  "parent_text": "親投稿1〜3行",
  "child_text_base": "返信用補足1〜3行"
}}
"""
    result = _json_response(prompt)
    parent = str(result.get("parent_text", "")).strip()
    child = str(result.get("child_text_base", "")).strip()
    if not parent or not child:
        raise RuntimeError(f"Gemini文章生成結果が不足しています: {result}")
    return parent, child
