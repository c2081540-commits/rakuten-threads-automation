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
DAILY_SLOTS = [("E1", 7), ("P1", 12), ("E2", 15), ("P2", 18), ("E3", 21)]

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


def _source_text(item):
    return " ".join(f'{item.get("itemName", "")} {item.get("itemCaption", "")}'.split())


def _extract_verified_product_facts(items):
    """Select products and accept only evidence copied verbatim from their source."""
    candidates = [{"itemCode": x["itemCode"], "source": _source_text(x)[:1600]} for x in items]
    result = _json_response(f"""
候補から用途の異なるThreads向きの商品を2件選ぶ。
各商品のfactsには、その候補のsource内に一字一句そのまま存在する短い根拠部分だけを2〜4個抜き出す。
要約、言い換え、補完、別商品の仕様混入は禁止。sourceにない語をfactsへ足さない。
候補:{json.dumps(candidates, ensure_ascii=False)}
JSONのみ: {{"products":[{{"selected_item_code":"itemCode","facts":["sourceからの原文抜粋"]}}]}}
""")
    products = result.get("products", [])
    if len(products) != 2 or len({x.get("selected_item_code") for x in products}) != 2:
        raise RuntimeError("商品事実抽出は重複なしの2商品である必要があります。")
    by_code = {x["itemCode"]: x for x in items}
    verified = []
    for product in products:
        code = product.get("selected_item_code")
        if code not in by_code:
            raise RuntimeError(f"候補外の商品コード: {code}")
        source = _source_text(by_code[code])
        facts = [" ".join(str(x).split()) for x in product.get("facts", []) if str(x).strip()]
        if not 2 <= len(facts) <= 4:
            raise RuntimeError(f"確認済み事実数が不正です: {code}")
        for fact in facts:
            if len(fact) < 2 or fact not in source:
                raise RuntimeError(f"商品情報に存在しない事実が抽出されました: {code}: {fact}")
        verified.append({"selected_item_code": code, "facts": facts})
    return verified


def _validate_slot_language(text, hour, target_date=None):
    compact = str(text).replace(" ", "")
    if hour == 7 and any(x in compact for x in ("午後", "昼休み", "夕方", "今夜")):
        raise RuntimeError(f"07時枠と時刻表現が矛盾しています: {text}")
    if hour in (12, 15, 18) and any(x in compact for x in ("今朝", "朝起き", "起きたら")):
        raise RuntimeError(f"{hour:02d}時枠と時刻表現が矛盾しています: {text}")
    if target_date and "週末" in compact and target_date.weekday() not in (4, 5, 6):
        raise RuntimeError(f"平日枠に週末表現があります: {text}")


def _validate_empathy_text(text, hour, target_date=None):
    _validate_parent(text)
    compact = str(text).replace("\n", "").strip()
    sentence_count = sum(compact.count(mark) for mark in ("。", "！", "？", "!", "?"))
    if len(compact) < 70:
        raise RuntimeError("共感投稿が短すぎます。具体的な日常場面と共感の着地点を2〜4文で書いてください。")
    if sentence_count < 2 or sentence_count > 4:
        raise RuntimeError("共感投稿は2〜4文で書いてください。")
    _validate_slot_language(text, hour, target_date)
    product_leak = ("段差や縁", "ワンタッチ", "パッキン", "折りたた", "収納時", "定位置", "場所を取らない", "持ち運びやす")
    if any(x in text for x in product_leak):
        raise RuntimeError(f"共感投稿が商品訴求に寄っています: {text}")


def _final_editorial_pass(posts, verified, target_date=None):
    """Read the full day as an editor and rewrite text only; IDs and facts stay pinned."""
    facts_by_code = {x["selected_item_code"]: x["facts"] for x in verified}
    review_input = []
    for (_, hour), post in zip(DAILY_SLOTS, posts):
        row = {
            "post_id": post["post_id"],
            "type": post["type"],
            "scheduled_hour": hour,
            "parent_text": post.get("parent_text", ""),
        }
        if post["type"] == "product":
            row["selected_item_code"] = post["selected_item_code"]
            row["child_text_base"] = post.get("child_text_base", "")
            row["verified_facts"] = facts_by_code[post["selected_item_code"]]
        review_input.append(row)

    result = _json_response(f"""
あなたはThreads投稿の最終編集者。以下の1日5投稿を、普通の日本語として声に出して読み、必要な文章だけ直す。
全5件について完成文を返す。post_id、type、商品コード、商品そのものは変更禁止。

共感投稿:
- 2〜4文、80〜140字程度。具体的な日常場面→多くの人が分かる感情・あるあるまで書く。
- 一文ポエム、気取った余韻、説明不足、狭すぎる体験、商品への前振りは禁止。
- 行動と結果を現実に照らして確認する。因果の逆転や飛躍があれば必ず直す。

商品投稿:
- verified_facts以外の物理仕様、付属品、材質、機能を追加禁止。
- 商品説明のコピーではなく、確認済み事実から具体的な使用場面と便益を書く。
- 主語・動作・対象の係り受けを確認し、「壁ごと動かす」のような誤読が起きる文を直す。
- 「おしゃれだから荷物管理が快適」のように無関係な特徴と便益を結びつけない。
- 親と返信で同じ情報を繰り返さない。

対象日:{target_date.isoformat() if target_date else "未指定"}
投稿:{json.dumps(review_input, ensure_ascii=False)}
JSONのみ: {{"edits":[{{"post_id":"E1","parent_text":"完成文"}},{{"post_id":"P1","parent_text":"完成文","child_text_base":"完成補足文"}}]}}
全5件をpost_id順ではなく入力順のまま返す。
""")
    edits = result.get("edits", [])
    expected_ids = [p["post_id"] for p in posts]
    if [x.get("post_id") for x in edits] != expected_ids:
        raise RuntimeError("最終編集結果の投稿IDまたは順序が不正です。")

    edited = []
    for original, edit in zip(posts, edits):
        row = dict(original)
        row["parent_text"] = str(edit.get("parent_text", "")).strip()
        if row["type"] == "product":
            row["child_text_base"] = str(edit.get("child_text_base", "")).strip()
        edited.append(row)
    return edited


def _longest_common_run(a, b):
    a, b = _normalize_for_compare(a), _normalize_for_compare(b)
    previous = [0] * (len(b) + 1)
    best = 0
    for ca in a:
        current = [0]
        for index, cb in enumerate(b, start=1):
            value = previous[index - 1] + 1 if ca == cb else 0
            current.append(value)
            best = max(best, value)
        previous = current
    return best


def _validate_product_copy_against_facts(parent, child, facts):
    _validate_parent(parent, product=True)
    _validate_child(child, parent)
    source = " ".join(facts)
    if _longest_common_run(parent, source) >= 28 or _longest_common_run(child, source) >= 32:
        raise RuntimeError("商品説明の原文コピーに寄りすぎています。")


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


def generate_mixed_stock(items, recent_history=None, existing_queue=None, events=None, target_date=None):
    product_prompt = _load_prompt("product.txt")
    empathy_prompt = _load_prompt("empathy.txt")
    history = recent_history or []
    queued = existing_queue or []
    recent = history + queued

    # Product candidates are deliberately absent from this request.
    empathy_result = _json_response(f"""
{empathy_prompt}
次の固定枠に1件ずつ作る: E1=07:00、E2=15:00、E3=21:00。
対象日:{target_date.isoformat() if target_date else "未指定"}
3件は別テーマ。掃除・収納・水回りは合計1件まで。曜日や時刻と矛盾する表現は禁止。
商品、道具の機能、形状、収納方法を連想させる前振りは禁止。
履歴:{json.dumps(recent, ensure_ascii=False)}
JSONのみ: {{"posts":[{{"post_id":"E1","type":"empathy","parent_text":"本文","theme":"テーマ","theme_group":"季節/天気|食事/料理|買い物|洗濯/衣類|朝夜/休日|休憩|掃除|収納|水回り|その他生活","tone":"neutral|positive|negative","context_note":""}}]}}
E1、E2、E3を各1件だけ返す。
""")
    empathy = empathy_result.get("posts", [])
    if {x.get("post_id") for x in empathy} != {"E1", "E2", "E3"} or len(empathy) != 3:
        raise RuntimeError("共感投稿IDが不足または重複しています。")
    empathy_hours = {"E1": 7, "E2": 15, "E3": 21}
    groups = []
    for post in empathy:
        if post.get("type") != "empathy":
            raise RuntimeError("共感生成に商品投稿が混入しました。")
        _validate_empathy_text(post.get("parent_text", ""), empathy_hours[post["post_id"]], target_date)
        groups.append(str(post.get("theme_group", "")).strip())
    if len(set(groups)) != 3 or sum(x in {"掃除", "収納", "水回り"} for x in groups) > 1:
        raise RuntimeError(f"共感テーマ分散不足: {groups}")

    # First pin facts to verbatim source evidence; the copy request never sees full captions.
    verified = _extract_verified_product_facts(items)
    product_result = _json_response(f"""
{product_prompt}
以下は商品ページ原文との完全一致をコードで確認済みの事実抜粋だけである。
このfacts以外の部品、機能、材質、付属品、収納方法、効果を追加してはいけない。
factsはコピーせず、事実を根拠に「具体的な使用場面＋生活上の便益」を自然に表現する。
便益の推論は許可するが、新しい物理仕様を作ってはいけない。
確認済み事実:{json.dumps(verified, ensure_ascii=False)}
履歴:{json.dumps(recent, ensure_ascii=False)}
イベント:{_event_instruction(events)}
JSONのみ: {{"posts":[{{"post_id":"P1","type":"product","selected_item_code":"itemCode","parent_text":"使用場面と便益","child_text_base":"親と異なる確認済み事実1〜2点の補足","theme":"テーマ","product_group":"用途","context_note":""}}]}}
P1とP2を、確認済み事実の商品順に各1件返す。
""")
    products = product_result.get("posts", [])
    expected_codes = [x["selected_item_code"] for x in verified]
    if [x.get("post_id") for x in products] != ["P1", "P2"] or [x.get("selected_item_code") for x in products] != expected_codes:
        raise RuntimeError("商品投稿IDまたは商品コードが確認済み事実と一致しません。")
    facts_by_code = {x["selected_item_code"]: x["facts"] for x in verified}
    for post in products:
        _validate_product_copy_against_facts(post.get("parent_text", ""), post.get("child_text_base", ""), facts_by_code[post["selected_item_code"]])

    by_id = {x["post_id"]: x for x in empathy + products}
    arranged = [by_id[x] for x, _ in DAILY_SLOTS]
    arranged = _final_editorial_pass(arranged, verified, target_date)
    recent_text = [h.get("parent_text", "") for h in recent if h.get("parent_text")]
    seen = set()
    for (_, hour), post in zip(DAILY_SLOTS, arranged):
        if post["type"] == "empathy":
            _validate_empathy_text(post.get("parent_text", ""), hour, target_date)
        else:
            _validate_product_copy_against_facts(
                post.get("parent_text", ""),
                post.get("child_text_base", ""),
                facts_by_code[post["selected_item_code"]],
            )
        _validate_parent(post.get("parent_text", ""), recent_text, product=post["type"] == "product")
        normalized = _normalize_for_compare(post["parent_text"])
        if normalized in seen:
            raise RuntimeError("今回生成した5投稿内で本文が完全一致しています。")
        seen.add(normalized)
    return arranged
