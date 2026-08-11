import json
import os
import time
from pathlib import Path

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

ROOT = Path(__file__).resolve().parent
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini").strip()
MAX_OUTPUT_TOKENS = 7000
MAX_GENERATION_ATTEMPTS = 3
MAX_API_ATTEMPTS = 3
OPENAI_REQUEST_COUNT = 0

WEAK_PARENT_ENDINGS = ("便利そう", "良さそう", "いいかも", "欲しい", "便利かも", "使えそう", "使いやすそう")
WEAK_PARENT_PHRASES = ("ちょっと掛けたいもの", "ちょっと置きたいもの", "あると便利そう", "あると良さそう")
PRODUCT_REVIEW_PHRASES = ("確認したい", "チェックしたい", "判断しやすい", "購入判断", "購入前に")
EMPATHY_PRODUCT_PITCH_PHRASES = ("便利グッズ", "収納グッズ", "小さなトレー", "があると便利", "一つでスムーズ", "ひとつでスムーズ")
TIME_WORDS = {
    7: ("夜", "夕方", "晩"),
    12: ("朝一", "早朝", "寝る前"),
    15: ("朝一", "早朝", "寝る前"),
    18: ("朝一", "早朝"),
    21: ("朝", "昼休み", "夕方"),
}


def _api_key():
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY が設定されていません。")
    return key


def _load_prompt(name):
    return (ROOT / "prompts" / name).read_text(encoding="utf-8")


def _json_response(prompt):
    global OPENAI_REQUEST_COUNT
    client = OpenAI(api_key=_api_key(), max_retries=0, timeout=90.0)
    for attempt in range(1, MAX_API_ATTEMPTS + 1):
        try:
            OPENAI_REQUEST_COUNT += 1
            response = client.responses.create(model=MODEL, input=prompt, max_output_tokens=MAX_OUTPUT_TOKENS, reasoning={"effort": "low"}, text={"format": {"type": "json_object"}})
            text = (response.output_text or "").strip()
            if not text:
                raise RuntimeError("OpenAI応答本文が空です")
            return json.loads(text)
        except (APITimeoutError, APIConnectionError, RateLimitError) as exc:
            if attempt == MAX_API_ATTEMPTS:
                raise RuntimeError(f"OpenAI APIが{MAX_API_ATTEMPTS}回失敗しました: {exc}") from exc
            wait_seconds = 2 ** (attempt - 1)
            print(f"OpenAI API一時エラー: {wait_seconds}秒後に再試行します ({attempt}/{MAX_API_ATTEMPTS})")
            time.sleep(wait_seconds)


def reset_openai_request_count():
    global OPENAI_REQUEST_COUNT
    OPENAI_REQUEST_COUNT = 0


def get_openai_request_count():
    return OPENAI_REQUEST_COUNT


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
        if any(x in compact for x in WEAK_PARENT_ENDINGS):
            raise RuntimeError("商品親投稿に曖昧な感想表現があります。具体的な生活上の変化へ書き換えてください。")
        if any(x in compact for x in PRODUCT_REVIEW_PHRASES):
            raise RuntimeError("商品親投稿が購入ガイド・比較記事調です。")
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
    if any(x in compact for x in PRODUCT_REVIEW_PHRASES):
        raise RuntimeError("商品補足文が購入ガイド・比較記事調です。")
    if compact.count("、") >= 5 or compact.count("・") >= 4:
        raise RuntimeError("商品補足文が仕様の列挙になっています。具体情報を1〜2点に絞ってください。")
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
    return len(posts) == 5 and sum(p.get("type") == "empathy" for p in posts) == 3 and sum(p.get("type") == "product" for p in posts) == 2


def _normalize_mixed_stock(posts):
    empathy = [p for p in posts if p.get("type") == "empathy"]
    products = [p for p in posts if p.get("type") == "product"]
    if len(empathy) < 3 or len(products) < 2:
        raise RuntimeError(f"ストック生成不足: empathy={len(empathy)}, product={len(products)}")
    return empathy[:3] + products[:2]


def _arrange_editorial_order(posts, order):
    by_id = {p.get("post_id"): p for p in posts}
    expected = {f"E{i}" for i in range(1, 4)} | {f"P{i}" for i in range(1, 3)}
    if set(by_id) != expected or len(by_id) != 5:
        raise RuntimeError("投稿IDが不足または重複しています。")
    if len(order) != 5 or len(set(order)) != 5 or set(order) != expected:
        raise RuntimeError(f"掲載順IDが不正です: {order}")
    arranged = [by_id[x] for x in order]
    if not _valid_daily_mix(arranged):
        raise RuntimeError("掲載順が1日あたり共感3・商品2を満たしていません。")
    return arranged


def _post_errors(post, recent, valid_codes, scheduled_hour=None):
    errors = []
    is_product = post.get("type") == "product"
    parent = str(post.get("parent_text", "")).strip()
    try:
        _validate_parent(parent, recent, product=is_product)
    except RuntimeError as exc:
        errors.append(str(exc))
    if is_product:
        if post.get("selected_item_code") not in valid_codes:
            errors.append("候補外の商品コードです。")
        try:
            _validate_child(post.get("child_text_base", ""), parent)
        except RuntimeError as exc:
            errors.append(str(exc))
    else:
        if any(x in parent for x in EMPATHY_PRODUCT_PITCH_PHRASES):
            errors.append("共感投稿が便利グッズによる解決や商品投稿の前振りになっています。")
        if "なりそう" in parent or "できそう" in parent:
            errors.append("共感ではなく、未使用の商品効果を想像する文章になっています。")
    if scheduled_hour and any(x in parent for x in TIME_WORDS.get(scheduled_hour, ())):
        errors.append(f"{scheduled_hour}時枠と本文の時間表現が矛盾しています。")
    return errors


def _collect_mixed_errors(result, items, history):
    posts = _normalize_mixed_stock(result.get("posts", []))
    expected_ids = {f"E{i}" for i in range(1, 4)} | {f"P{i}" for i in range(1, 3)}
    if {p.get("post_id") for p in posts} != expected_ids:
        raise RuntimeError("投稿IDが不正です。")
    arranged = _arrange_editorial_order(posts, result.get("editorial_order", []))
    valid_codes = {x["itemCode"] for x in items}
    recent = [h.get("parent_text", "") for h in history if h.get("parent_text")]
    hours = [7, 12, 15, 18, 21]
    errors = {}
    seen = {}
    for hour, post in zip(hours, arranged):
        post_id = post["post_id"]
        current = _post_errors(post, recent, valid_codes, scheduled_hour=hour)
        normalized = _normalize_for_compare(post.get("parent_text", ""))
        if normalized in seen:
            current.append(f"{seen[normalized]}と本文が完全一致しています。")
        seen[normalized] = post_id
        if current:
            errors[post_id] = current

    empathy = [p for p in posts if p.get("type") == "empathy"]
    groups = [str(p.get("theme_group", "")).strip() for p in empathy]
    if len(set(x for x in groups if x)) < 3 or sum(x in {"掃除", "収納", "水回り"} for x in groups) > 1:
        for post in empathy:
            errors.setdefault(post["post_id"], []).append("共感3件のテーマ分散条件を満たしていません。")
    if sum(str(p.get("tone", "")) in {"neutral", "positive"} for p in empathy) < 1:
        for post in empathy:
            errors.setdefault(post["post_id"], []).append("共感3件がネガティブに偏っています。")
    products = [p for p in posts if p.get("type") == "product"]
    codes = [p.get("selected_item_code") for p in products]
    if len(set(codes)) != 2:
        for post in products:
            errors.setdefault(post["post_id"], []).append("2商品が重複しています。")
    return posts, errors


def _repair_invalid_posts(result, errors, items, history, queued, product_prompt, empathy_prompt, events):
    invalid_ids = sorted(errors)
    prompt = f"""
翌日分5投稿のうち、品質検査で不合格になった投稿だけを書き直す。
合格投稿は変更禁止。不合格IDのtypeとpost_idは維持する。商品コードは候補内で選び、他の商品投稿と重複させない。

【現在の5投稿】
{json.dumps(result, ensure_ascii=False)}
【不合格IDと理由】
{json.dumps(errors, ensure_ascii=False)}
【書き直すID】
{json.dumps(invalid_ids, ensure_ascii=False)}
【商品候補】
{json.dumps(_candidate_data(items), ensure_ascii=False)}
【履歴】
{json.dumps(history, ensure_ascii=False)}
【未投稿キュー】
{json.dumps(queued, ensure_ascii=False)}
【イベント】
{_event_instruction(events)}
【商品ルール】
{product_prompt}
【共感ルール】
{empathy_prompt}

出力前に各修正文を自己審査し、不合格理由が一つでも残る場合は内部で書き直す。
JSONのみ: {{"replacements":[不合格IDに対応する完成投稿オブジェクト]}}
"""
    repaired = _json_response(prompt).get("replacements", [])
    by_id = {p.get("post_id"): p for p in repaired}
    if set(by_id) != set(invalid_ids):
        raise RuntimeError(f"部分再生成IDが不正です: {sorted(by_id)}")
    merged = []
    for post in result.get("posts", []):
        merged.append(by_id.get(post.get("post_id"), post))
    return {"posts": merged, "editorial_order": result.get("editorial_order", [])}


def _validate_mixed_result(result, items, history):
    posts, errors = _collect_mixed_errors(result, items, history)
    if errors:
        raise RuntimeError(json.dumps(errors, ensure_ascii=False))
    return _arrange_editorial_order(posts, result.get("editorial_order", []))


def generate_mixed_stock(items, recent_history=None, existing_queue=None, events=None):
    product_prompt = _load_prompt("product.txt")
    empathy_prompt = _load_prompt("empathy.txt")
    history = recent_history or []
    queued = existing_queue or []
    base_prompt = f"""
Threadsアカウント「これ、家に欲しい」の翌日分5投稿を作成する。
最重要: 5件は1日分の固定投稿枠。1番=07:00、2番=12:00、3番=15:00、4番=18:00、5番=21:00。

- empathy 3件、product 2件。
- empathyは楽天商品候補を見て前振りを作らず単体で自然な日常投稿。
- productは商品情報だけから独立して作る。
- productは自然さだけで合格にしない。具体的な生活場面・困りごと、商品特徴、生活上のメリットのうち最低2要素を親投稿に入れる。
- productの返信は親の言い換えではなく、購入判断に役立つ新しい具体情報を最低1つ加える。
- 「便利そう」「良さそう」「いいかも」だけで商品の魅力を済ませない。
- 架空の購入・使用経験は禁止。
- empathyはE1〜E3、productはP1〜P2。
- empathyは3テーマに分散。掃除・収納・水回りは合計1件まで。最低1件はneutral/positive。
- productは2商品重複禁止。用途を分散。商品同士を連続させない。
- 必ずempathy 3件、product 2件。同系統テーマを固めない。
- 時刻に不自然な本文は禁止。
- 実投稿履歴と未投稿キューにある本文をそのまま再利用しない。意味が近くても同一文のコピーは禁止。
- まず下書きを作り、5件すべてをルールごとに自己審査する。不合格項目が1つでもある投稿は内部で書き直し、合格した完成稿だけをJSONへ入れる。
- 共感投稿は「商品による解決の前振り」「前後が別の悩み」「無理なエッセイ化」「時刻との矛盾」を不合格にする。
- 商品親投稿は最も強い便益を1つ中心にし、仕様の均等な羅列と曖昧な感想を不合格にする。
- 商品返信は親にない具体情報を1〜2点だけ加え、商品仕様欄のような全列挙を不合格にする。

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
],"editorial_order":["E1","P1","E2","P2","E3"]}}
postsはE1〜E3とP1〜P2を各1件。editorial_orderも同じ5IDを重複なく1回ずつ使う。
"""
    result = _json_response(base_prompt)
    last_errors = None
    for repair_round in range(MAX_GENERATION_ATTEMPTS):
        posts, errors = _collect_mixed_errors(result, items, history + queued)
        if not errors:
            return _arrange_editorial_order(posts, result.get("editorial_order", []))
        last_errors = errors
        if repair_round == MAX_GENERATION_ATTEMPTS - 1:
            break
        result = _repair_invalid_posts(
            result,
            errors,
            items,
            history,
            queued,
            product_prompt,
            empathy_prompt,
            events,
        )
    raise RuntimeError(
        "不合格投稿だけを再生成しましたが品質検査を通過しませんでした: "
        + json.dumps(last_errors, ensure_ascii=False)
    )
