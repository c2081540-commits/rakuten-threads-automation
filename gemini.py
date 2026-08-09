import json
import os
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).resolve().parent
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini").strip()
MAX_OUTPUT_TOKENS = 5000


def _api_key():
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY が設定されていません。")
    return key


def _load_prompt(name):
    return (ROOT / "prompts" / name).read_text(encoding="utf-8")


def _json_response(prompt):
    # 自動リトライは禁止。失敗時にAPI課金リクエストを連鎖させない。
    client = OpenAI(api_key=_api_key(), max_retries=0, timeout=45.0)
    response = client.responses.create(model=MODEL, input=prompt, max_output_tokens=MAX_OUTPUT_TOKENS, reasoning={"effort": "low"}, text={"format": {"type": "json_object"}})
    text = (response.output_text or "").strip()
    if not text:
        raise RuntimeError(f"OpenAI応答本文が空です: status={getattr(response, 'status', None)}, incomplete_details={getattr(response, 'incomplete_details', None)}, usage={getattr(response, 'usage', None)}")
    try:
        return json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"OpenAI JSON解析失敗: {text}") from exc


def _candidate_data(items):
    return [{"itemCode": i["itemCode"], "itemName": i["itemName"], "itemCaption": i.get("itemCaption", "")[:500], "price": i["itemPrice"], "rating": i["reviewAverage"], "reviews": i["reviewCount"], "shopName": i.get("shopName", "")} for i in items]


def _normalize_for_compare(text):
    return "".join(str(text).replace("\n", "").replace(" ", "").split()).lower()


def _validate_parent(parent, recent_posts=None):
    parent = str(parent).strip()
    if not parent:
        raise RuntimeError("親投稿が空です。")
    compact = parent.replace("\n", "")
    if len(compact) > 100:
        raise RuntimeError(f"親投稿が長すぎます({len(compact)}文字): {parent}")
    forbidden = ["http://", "https://", "【PR】", "レビュー", "価格:"]
    if any(word in parent for word in forbidden):
        raise RuntimeError(f"親投稿に禁止要素があります: {parent}")
    normalized = _normalize_for_compare(parent)
    for old in recent_posts or []:
        if normalized == _normalize_for_compare(old):
            raise RuntimeError(f"直近投稿と完全一致しています: {parent}")


def _validate_batch_parents(samples, recent_posts=None):
    seen, starts = set(), set()
    for sample in samples:
        parent = str(sample.get("parent_text", "")).strip()
        _validate_parent(parent, recent_posts)
        normalized = _normalize_for_compare(parent)
        if normalized in seen:
            raise RuntimeError(f"同一バッチ内で親投稿が重複しています: {parent}")
        seen.add(normalized)
        start = normalized[:8]
        if len(start) >= 8 and start in starts:
            raise RuntimeError(f"同一バッチ内で書き出しが酷似しています: {parent}")
        starts.add(start)


def _event_instruction(events):
    if not events:
        return "現在、投稿文で訴求してよい楽天公式イベントは確認されていません。セール・キャンペーン開催中とは書かないでください。"
    names = [e["name"] for e in events]
    return f"楽天市場公式で現在開催中と確認済みのイベント: {json.dumps(names, ensure_ascii=False)}。必要なら自然に1つだけ触れて構いません。ただし商品自体の値下げ・クーポン対象・ポイント対象とは確認していないため、それらを断定しないでください。特に『5と0のつく日』は楽天カード利用等の条件付きポイント施策であり『商品がセール中』とは書かないでください。"


def select_product(items):
    prompt = f"""以下は楽天APIから取得し、機械条件を通過した商品候補です。Threadsアカウント「これ、家に欲しい」で1件だけ紹介する商品を選んでください。重視する順序: 用途の分かりやすさ、生活の小さな不満の解決、短いリアクションの作りやすさ、価格、評価・レビュー件数。ブランド名やショップ名だけで優遇せず、数値や仕様を推測・変更しないでください。候補: {json.dumps(_candidate_data(items), ensure_ascii=False)}\nJSONのみ: {{"selected_item_code":"itemCode","reason":"短い選定理由","preferred_image_index":0}}\n画像内容は見たとは扱わず preferred_image_index は0にしてください。"""
    result = _json_response(prompt)
    code = result.get("selected_item_code")
    selected = next((x for x in items if x["itemCode"] == code), None)
    if selected is None:
        raise RuntimeError(f"OpenAIが候補外の商品コードを返しました: {code}")
    return selected, result.get("reason", ""), 0


def generate_product_copy(item, recent_posts=None, events=None):
    base = _load_prompt("product.txt")
    facts = {"itemCode": item["itemCode"], "itemName": item["itemName"], "itemCaption": item.get("itemCaption", "")[:1000]}
    prompt = f"""{base}\n【今回の商品情報】\n{json.dumps(facts, ensure_ascii=False)}\n【楽天公式イベント情報】\n{_event_instruction(events)}\n【直近の商品投稿】\n{json.dumps(recent_posts or [], ensure_ascii=False)}\nJSONのみ: {{"parent_text":"親投稿1〜3行","child_text_base":"返信用補足1〜2文"}}"""
    result = _json_response(prompt)
    parent, child = str(result.get("parent_text", "")).strip(), str(result.get("child_text_base", "")).strip()
    if not parent or not child:
        raise RuntimeError(f"OpenAI文章生成結果が不足しています: {result}")
    _validate_parent(parent, recent_posts)
    return parent, child


def generate_sample_batch(items, count=5, recent_posts=None, events=None):
    count = max(1, min(count, len(items)))
    base = _load_prompt("product.txt")
    prompt = f"""{base}\n\n以下の候補から、Threads向きの商品を重複なしで{count}件選んでください。商品ごとに親投稿と返信用補足文も作成してください。ブランドやショップだけで優遇せず、用途の分かりやすさ、生活の小さな不満の解決、短い反応の作りやすさ、価格、評価・レビュー数を総合判断してください。候補外の商品は絶対に選ばないでください。数値や仕様は推測しないでください。\n【候補】\n{json.dumps(_candidate_data(items), ensure_ascii=False)}\n【楽天公式イベント情報】\n{_event_instruction(events)}\n【既存の直近投稿】\n{json.dumps(recent_posts or [], ensure_ascii=False)}\n重要: {count}件を別々の人間の独り言に見える程度まで散らしてください。同一バッチ内で導入パターン・冒頭・構文・語尾・着眼点を重複させないでください。「〜かも」「〜だなぁ」「地味に」「これ」を複数件で安易に反復しないでください。イベント名も全件に機械的に入れず、自然な場合だけ使ってください。\n次のJSONだけを返してください。\n{{"samples":[{{"selected_item_code":"itemCode","reason":"短い選定理由","parent_text":"親投稿1〜3行","child_text_base":"返信用補足1〜2文"}}]}}\n必ず samples を{count}件返してください。"""
    result = _json_response(prompt)
    samples = result.get("samples", [])
    if len(samples) != count:
        raise RuntimeError(f"OpenAIバッチ生成件数が不正です: expected={count}, actual={len(samples)}")
    valid_codes, seen_codes = {x["itemCode"] for x in items}, set()
    for sample in samples:
        code = sample.get("selected_item_code")
        if code not in valid_codes or code in seen_codes:
            raise RuntimeError(f"OpenAIバッチ選定の商品コードが不正または重複です: {code}")
        seen_codes.add(code)
        if not str(sample.get("parent_text", "")).strip() or not str(sample.get("child_text_base", "")).strip():
            raise RuntimeError(f"OpenAIバッチ文章生成結果が不足しています: {sample}")
    _validate_batch_parents(samples, recent_posts)
    return samples
