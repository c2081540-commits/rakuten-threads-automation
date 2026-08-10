import json
import os
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).resolve().parent
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini").strip()
MAX_OUTPUT_TOKENS = 7000
MAX_GENERATION_ATTEMPTS = 3

WEAK_PARENT_ENDINGS = ("便利そう", "良さそう", "いいかも", "欲しい", "便利かも", "使えそう")
WEAK_PARENT_PHRASES = ("ちょっと掛けたいもの", "ちょっと置きたいもの", "あると便利そう", "あると良さそう")


def _api_key():
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY が設定されていません。")
    return key


def _load_prompt(name):
    return (ROOT / "prompts" / name).read_text(encoding="utf-8")


def _json_response(prompt):
    client = OpenAI(api_key=_api_key(), max_retries=0, timeout=45.0)
    response = client.responses.create(model=MODEL, input=prompt, max_output_tokens=MAX_OUTPUT_TOKENS, reasoning={"effort": "low"}, text={"format": {"type": "json_object"}})
    text = (response.output_text or "").strip()
    if not text:
        raise RuntimeError("OpenAI応答本文が空です")
    return json.loads(text)


def _candidate_data(items):
    return [{"itemCode": i["itemCode"], "itemName": i["itemName"], "itemCaption": i.get("itemCaption", "")[:500], "price": i["itemPrice"], "rating": i["reviewAverage"], "reviews": i["reviewCount"], "shopName": i.get("shopName", "")} for i in items]


def _normalize_for_compare(text):
    return "".join(str(text).replace("\n", "").replace(" ", "").split()).lower()


def _validate_parent(parent, recent_posts=None, product=False):
    parent = str(parent).strip()
    compact = parent.replace("\n", "")
    if not parent:
        raise RuntimeError("親投稿が空です。")
    if len(compact) > 140:
        raise RuntimeError("親投稿が長すぎます。")
    if any(x in parent for x in ["http://", "https://", "【PR】", "レビュー", "価格:"]):
        raise RuntimeError("親投稿に禁止要素があります。")
    if product:
        if len(compact) < 45:
            raise RuntimeError("商品親投稿が短すぎて魅力・使用場面を伝えられていません。")
        if any(x in compact for x in WEAK_PARENT_PHRASES):
            raise RuntimeError("商品親投稿が曖昧な定型表現に寄りすぎています。")
        stripped = compact.rstrip("。！？!? ")
        if any(stripped.endswith(x) for x in WEAK_PARENT_ENDINGS) and len(compact) < 70:
            raise RuntimeError("商品親投稿が抽象的な感想だけで終わっています。")
        # 商品投稿は最低限、場面・問題・変化を説明できる情報量を要求する。
        if compact.count("、") + compact.count("。") < 2 and len(compact) < 65:
            raise RuntimeError("商品親投稿の具体性が不足しています。")
    normalized = _normalize_for_compare(parent)
    for old in recent_posts or []:
        if normalized == _normalize_for_compare(old):
            raise RuntimeError("直近投稿と完全一致しています。")


def _validate_child(child, parent=""):
    child = str(child).strip()
    compact = child.replace("\n", "")
    if not child:
        raise RuntimeError("商品補足文が空です。")
    if len(compact) > 180:
        raise RuntimeError("商品補足文が長すぎます。")
    if len(compact) < 45:
        raise RuntimeError("商品補足文が短すぎて購入判断の補足になっていません。")
    if any(x in child for x in ["http://", "https://", "【PR】", "レビュー", "価格:"]):
        raise RuntimeError("商品補足文に禁止要素があります。")
    if parent and _normalize_for_compare(child) == _normalize_for_compare(parent):
        raise RuntimeError("親投稿と商品補足文が同一です。")


def _event_instruction(events):
    if not events:
        return "確認済み楽天公式イベントなし。開催中とは書かない。"
    return "楽天市場公式で現在開催中と確認済み: " + json.dumps([e["name"] for e in events], ensure_ascii=False) + "。ただしストック本文には固定の開催中表現を原則入れない。"


def select_product(items):
    result = _json_response(f"候補からThreads向きの1件を選択。候補外禁止。{json.dumps(_candidate_data(items), ensure_ascii=False)}\nJSONのみ: {{\"selected_item_code\":\"itemCode\",\"reason\":\"理由\"}}")
    selected = next((x for x in items if x["itemCode"] == result.get("selected_item_code")), None)
    if selected is None:
        raise RuntimeError("候補外の商品コードです。")
    return selected, result.get("reason", ""), 0


def generate_product_copy(item, recent_posts=None, events=None):
    base = _load_prompt("product.txt")
    facts = {"itemCode": item["itemCode"], "itemName": item["itemName"], "itemCaption": item.get("itemCaption", "")[:1000]}
    last_error = None
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        retry_note = "" if attempt == 1 else f"\n再生成{attempt}回目。直前の出力は品質検証で不採用。商品情報の事実だけを使い、具体的な生活場面→特徴→生活上の変化が伝わる別の文章へ書き直す。"
        result = _json_response(f"{base}\n商品:{json.dumps(facts, ensure_ascii=False)}\nイベント:{_event_instruction(events)}\n直近:{json.dumps(recent_posts or [], ensure_ascii=False)}{retry_note}\nJSONのみ: {{\"parent_text\":\"親投稿\",\"child_text_base\":\"具体的な補足文\"}}")
        parent = str(result.get("parent_text", "")).strip()
        child = str(result.get("child_text_base", "")).strip()
        try:
            _validate_parent(parent, recent_posts, product=True)
            _validate_child(child, parent)
            return parent, child
        except RuntimeError as exc:
            last_error = exc
    raise RuntimeError(f"商品文章を{MAX_GENERATION_ATTEMPTS}回再生成しても検証を通過しませんでした: {last_error}")


def generate_sample_batch(items, count=5, recent_posts=None, events=None):
    count = max(1, min(count, len(items)))
    base = _load_prompt("product.txt")
    result = _json_response(f"{base}\n候補から重複なしで{count}件。{json.dumps(_candidate_data(items), ensure_ascii=False)}\nJSONのみ: {{\"samples\":[{{\"selected_item_code\":\"itemCode\",\"reason\":\"理由\",\"parent_text\":\"親投稿\",\"child_text_base\":\"具体的な補足文\"}}]}}")
    samples = result.get("samples", [])
    if len(samples) != count:
        raise RuntimeError("バッチ生成件数が不正です。")
    for sample in samples:
        _validate_parent(sample.get("parent_text", ""), recent_posts, product=True)
        _validate_child(sample.get("child_text_base", ""), sample.get("parent_text", ""))
    return samples


def _valid_daily_mix(posts):
    if len(posts) != 10:
        return False
    for start in (0, 5):
        day = posts[start:start + 5]
        if sum(p.get("type") == "empathy" for p in day) != 3 or sum(p.get("type") == "product" for p in day) != 2:
            return False
    return True


def _normalize_mixed_stock(posts):
    empathy = [p for p in posts if p.get("type") == "empathy"]
    products = [p for p in posts if p.get("type") == "product"]
    if len(empathy) < 6 or len(products) < 4:
        raise RuntimeError(f"ストック生成不足: empathy={len(empathy)}, product={len(products)}")
    return empathy[:6] + products[:4]


def _arrange_editorial_order(posts, order):
    by_id = {p.get("post_id"): p for p in posts}
    expected = {f"E{i}" for i in range(1, 7)} | {f"P{i}" for i in range(1, 5)}
    if set(by_id) != expected or len(by_id) != 10:
        raise RuntimeError("投稿IDが不足または重複しています。")
    if len(order) != 10 or len(set(order)) != 10 or set(order) != expected:
        raise RuntimeError(f"掲載順IDが不正です: {order}")
    arranged = [by_id[x] for x in order]
    if not _valid_daily_mix(arranged):
        raise RuntimeError("掲載順が1日あたり共感3・商品2を満たしていません。")
    return arranged


def _validate_mixed_result(result, items, history):
    posts = _normalize_mixed_stock(result.get("posts", []))
    empathy = [p for p in posts if p.get("type") == "empathy"]
    expected_ids = {f"E{i}" for i in range(1, 7)} | {f"P{i}" for i in range(1, 5)}
    if {p.get("post_id") for p in posts} != expected_ids:
        raise RuntimeError("投稿IDが不正です。")
    groups = [str(p.get("theme_group", "")).strip() for p in empathy]
    if len(set(x for x in groups if x)) < 5:
        raise RuntimeError(f"共感テーマ分散不足: {groups}")
    if sum(x in {"掃除", "収納", "水回り"} for x in groups) > 2:
        raise RuntimeError("掃除・収納・水回りに偏りすぎています。")
    if sum(str(p.get("tone", "")) in {"neutral", "positive"} for p in empathy) < 2:
        raise RuntimeError("共感投稿がネガティブに偏っています。")
    valid_codes = {x["itemCode"] for x in items}
    codes = []
    recent = [h.get("parent_text", "") for h in history if h.get("parent_text")]
    seen_current = set()
    for post in posts:
        is_product = post["type"] == "product"
        _validate_parent(post.get("parent_text", ""), recent, product=is_product)
        normalized = _normalize_for_compare(post.get("parent_text", ""))
        if normalized in seen_current:
            raise RuntimeError("今回生成した10投稿内で本文が完全一致しています。")
        seen_current.add(normalized)
        if is_product:
            code = post.get("selected_item_code")
            if code not in valid_codes:
                raise RuntimeError(f"候補外の商品コード: {code}")
            codes.append(code)
            _validate_child(post.get("child_text_base", ""), post.get("parent_text", ""))
    if len(set(codes)) != 4:
        raise RuntimeError("商品が重複しています。")
    return _arrange_editorial_order(posts, result.get("editorial_order", []))


def generate_mixed_stock(items, recent_history=None, existing_queue=None, events=None):
    product_prompt = _load_prompt("product.txt")
    empathy_prompt = _load_prompt("empathy.txt")
    history = recent_history or []
    queued = existing_queue or []
    base_prompt = f"""
Threadsアカウント「これ、家に欲しい」の10投稿を作成する。
最重要: 10件は2日分の固定投稿枠。1番=07:00、2番=12:00、3番=15:00、4番=18:00、5番=21:00。6〜10番も翌日同順。

- empathy 6件、product 4件。
- empathyは楽天商品候補を見て前振りを作らず単体で自然な日常投稿。
- productは商品情報だけから独立して作る。
- productは自然さだけで合格にしない。具体的な生活場面・困りごと、商品特徴、生活上のメリットのうち最低2要素を親投稿に入れる。
- productの返信は親の言い換えではなく、購入判断に役立つ新しい具体情報を最低1つ加える。
- 「便利そう」「良さそう」「いいかも」だけで商品の魅力を済ませない。
- 架空の購入・使用経験は禁止。
- empathyはE1〜E6、productはP1〜P4。
- empathyは最低5テーマ。掃除・収納・水回りは合計2件まで。最低2件はneutral/positive。
- productは4商品重複禁止。用途を分散。掛ける/収納系は最大2件。同系統ブランド3件以上は禁止。
- 各日必ずempathy 3件、product 2件。商品同士を連続させない。同系統テーマも固めない。
- 時刻に不自然な本文は禁止。
- 実投稿履歴と未投稿キューにある本文をそのまま再利用しない。意味が近くても同一文のコピーは禁止。

【商品ルール】
{product_prompt}
【日常ルール】
{empathy_prompt}
【商品候補】
{json.dumps(_candidate_data(items), ensure_ascii=False)}
【実投稿履歴】
{json.dumps(history, ensure_ascii=False)}
【未投稿キュー】
{json.dumps(queued, ensure_ascii=False)}
【イベント】
{_event_instruction(events)}

JSONのみ:
{{"posts":[
{{"post_id":"E1","type":"empathy","parent_text":"本文","theme":"テーマ","theme_group":"季節/天気|食事/料理|買い物|洗濯/衣類|朝夜/休日|休憩|掃除|収納|水回り|その他生活","tone":"neutral|positive|negative","context_note":""}},
{{"post_id":"P1","type":"product","selected_item_code":"itemCode","parent_text":"親投稿","child_text_base":"具体的な補足文","theme":"テーマ","product_group":"用途","context_note":""}}
],"editorial_order":["E1","P1","E2","P2","E3","E4","P3","E5","P4","E6"]}}
postsはE1〜E6とP1〜P4を各1件。editorial_orderも同じ10IDを重複なく1回ずつ使う。
"""
    last_error = None
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        retry_note = "" if attempt == 1 else f"\n重要: 再生成{attempt}回目。直前の10投稿は品質検証で不採用。全10件をゼロから作り直し、履歴と完全一致する本文を絶対に出さない。商品投稿は具体的な使用場面と生活上のメリットを明確にする。"
        result = _json_response(base_prompt + retry_note)
        try:
            return _validate_mixed_result(result, items, history)
        except RuntimeError as exc:
            last_error = exc
    raise RuntimeError(f"10投稿を{MAX_GENERATION_ATTEMPTS}回再生成しても検証を通過しませんでした: {last_error}")
