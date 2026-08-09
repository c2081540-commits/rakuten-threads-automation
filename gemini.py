import json
import os
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).resolve().parent
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini").strip()
# GPT-5系は推論トークンも出力上限を消費するため、2200では本文が空になる場合がある。
MAX_OUTPUT_TOKENS = 5000


def _api_key():
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY が設定されていません。")
    return key


def _load_prompt(name):
    return (ROOT / "prompts" / name).read_text(encoding="utf-8")


def _json_response(prompt):
    # 自動リトライは禁止。APIエラー時に課金リクエストが連鎖しないよう1回で停止する。
    client = OpenAI(api_key=_api_key(), max_retries=0, timeout=45.0)
    response = client.responses.create(
        model=MODEL,
        input=prompt,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        reasoning={"effort": "low"},
        text={"format": {"type": "json_object"}},
    )
    text = (response.output_text or "").strip()
    if not text:
        status = getattr(response, "status", None)
        incomplete = getattr(response, "incomplete_details", None)
        usage = getattr(response, "usage", None)
        raise RuntimeError(
            f"OpenAI応答本文が空です: status={status}, incomplete_details={incomplete}, usage={usage}"
        )
    try:
        return json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"OpenAI JSON解析失敗: {text}") from exc


def _candidate_data(items):
    return [{
        "itemCode": item["itemCode"],
        "itemName": item["itemName"],
        "itemCaption": item.get("itemCaption", "")[:500],
        "price": item["itemPrice"],
        "rating": item["reviewAverage"],
        "reviews": item["reviewCount"],
        "shopName": item.get("shopName", ""),
    } for item in items]


def select_product(items):
    prompt = f"""
以下は楽天APIから取得し、機械条件を通過した商品候補です。
Threadsアカウント「これ、家に欲しい」で1件だけ紹介する商品を選んでください。
重視する順序: 用途の分かりやすさ、生活の小さな不満の解決、短いリアクションの作りやすさ、価格、評価・レビュー件数。
ブランド名やショップ名だけで優遇せず、数値や仕様を推測・変更しないでください。
候補: {json.dumps(_candidate_data(items), ensure_ascii=False)}
JSONのみ: {{"selected_item_code":"itemCode","reason":"短い選定理由","preferred_image_index":0}}
画像内容は見たとは扱わず preferred_image_index は0にしてください。
"""
    result = _json_response(prompt)
    selected_code = result.get("selected_item_code")
    selected = next((x for x in items if x["itemCode"] == selected_code), None)
    if selected is None:
        raise RuntimeError(f"OpenAIが候補外の商品コードを返しました: {selected_code}")
    return selected, result.get("reason", ""), 0


def generate_product_copy(item, recent_posts=None):
    base = _load_prompt("product.txt")
    facts = {"itemCode": item["itemCode"], "itemName": item["itemName"], "itemCaption": item.get("itemCaption", "")[:1000]}
    prompt = f"""{base}\n【今回の商品情報】\n{json.dumps(facts, ensure_ascii=False)}\n【直近の商品投稿】\n{json.dumps(recent_posts or [], ensure_ascii=False)}\nJSONのみ: {{"parent_text":"親投稿1〜3行","child_text_base":"返信用補足1〜3行"}}"""
    result = _json_response(prompt)
    parent = str(result.get("parent_text", "")).strip()
    child = str(result.get("child_text_base", "")).strip()
    if not parent or not child:
        raise RuntimeError(f"OpenAI文章生成結果が不足しています: {result}")
    return parent, child


def generate_sample_batch(items, count=5, recent_posts=None):
    """品質確認用。1回のOpenAI API呼び出しで複数商品の選定と文章生成を行う。"""
    count = max(1, min(count, len(items)))
    base = _load_prompt("product.txt")
    prompt = f"""
{base}

以下の候補から、Threads向きの商品を重複なしで{count}件選んでください。
商品ごとに親投稿と返信用補足文も作成してください。
ブランドやショップだけで優遇せず、用途の分かりやすさ、生活の小さな不満の解決、短い反応の作りやすさ、価格、評価・レビュー数を総合判断してください。
候補外の商品は絶対に選ばないでください。数値や仕様は推測しないでください。

【候補】
{json.dumps(_candidate_data(items), ensure_ascii=False)}

【既存の直近投稿】
{json.dumps(recent_posts or [], ensure_ascii=False)}

同一バッチ内でも親投稿の導入・構文・語尾・着眼点を重複させないでください。
次のJSONだけを返してください。
{{"samples":[{{"selected_item_code":"itemCode","reason":"短い選定理由","parent_text":"親投稿1〜3行","child_text_base":"返信用補足1〜3行"}}]}}
必ず samples を{count}件返してください。
"""
    result = _json_response(prompt)
    samples = result.get("samples", [])
    if len(samples) != count:
        raise RuntimeError(f"OpenAIバッチ生成件数が不正です: expected={count}, actual={len(samples)}")
    valid_codes = {x["itemCode"] for x in items}
    seen = set()
    for sample in samples:
        code = sample.get("selected_item_code")
        if code not in valid_codes or code in seen:
            raise RuntimeError(f"OpenAIバッチ選定の商品コードが不正または重複です: {code}")
        seen.add(code)
        if not str(sample.get("parent_text", "")).strip() or not str(sample.get("child_text_base", "")).strip():
            raise RuntimeError(f"OpenAIバッチ文章生成結果が不足しています: {sample}")
    return samples
