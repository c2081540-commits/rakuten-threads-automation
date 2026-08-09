import json
import os
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).resolve().parent
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini").strip()
MAX_OUTPUT_TOKENS = 7000


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


def _validate_parent(parent, recent_posts=None):
    parent = str(parent).strip()
    if not parent:
        raise RuntimeError("親投稿が空です。")
    if len(parent.replace("\n", "")) > 140:
        raise RuntimeError("親投稿が長すぎます。")
    if any(x in parent for x in ["http://", "https://", "【PR】", "レビュー", "価格:"]):
        raise RuntimeError("親投稿に禁止要素があります。")
    normalized = _normalize_for_compare(parent)
    for old in recent_posts or []:
        if normalized == _normalize_for_compare(old):
            raise RuntimeError("直近投稿と完全一致しています。")


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
    result = _json_response(f"{base}\n商品:{json.dumps(facts, ensure_ascii=False)}\nイベント:{_event_instruction(events)}\n直近:{json.dumps(recent_posts or [], ensure_ascii=False)}\nJSONのみ: {{\"parent_text\":\"親投稿\",\"child_text_base\":\"返信\"}}")
    parent = str(result.get("parent_text", "")).strip()
    child = str(result.get("child_text_base", "")).strip()
    if not parent or not child:
        raise RuntimeError("文章生成結果が不足しています。")
    _validate_parent(parent, recent_posts)
    return parent, child


def generate_sample_batch(items, count=5, recent_posts=None, events=None):
    count = max(1, min(count, len(items)))
    base = _load_prompt("product.txt")
    result = _json_response(f"{base}\n候補から重複なしで{count}件。{json.dumps(_candidate_data(items), ensure_ascii=False)}\nJSONのみ: {{\"samples\":[{{\"selected_item_code\":\"itemCode\",\"reason\":\"理由\",\"parent_text\":\"親投稿\",\"child_text_base\":\"返信\"}}]}}")
    samples = result.get("samples", [])
    if len(samples) != count:
        raise RuntimeError("バッチ生成件数が不正です。")
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


def generate_mixed_stock(items, recent_history=None, existing_queue=None, events=None):
    product_prompt = _load_prompt("product.txt")
    empathy_prompt = _load_prompt("empathy.txt")
    history = recent_history or []
    queued = existing_queue or []
    prompt = f"""
Threadsアカウント「これ、家に欲しい」の10投稿を作成する。

最重要: 10件は2日分の固定投稿枠に入る。各日の掲載位置は必ず次の時刻に対応する。
1番=07:00、2番=12:00、3番=15:00、4番=18:00、5番=21:00。
6〜10番も翌日の同じ順番。
朝・昼・夕方・夜など時刻を限定する表現は、その掲載時刻に自然な場合だけ使う。特に21時枠に朝/昼の描写、07時枠に夜の描写を置かない。

【フェーズ1】
- empathy 6件、product 4件。
- empathyは楽天商品候補を見て前振りを作ってはいけない。商品との関連性を意図的に作らず、単体で自然な日常の一言にする。
- productはempathyを受けて書かず、商品情報だけから独立して作る。
- empathyに商品の用途、特徴、困りごとを仕込まない。
- 架空の購入・使用経験は禁止。
- empathyはE1〜E6、productはP1〜P4のpost_idを付ける。
- empathyは最低5テーマに分散。掃除・収納・水回りは合計2件まで。最低2件はneutral/positive。
- productは4商品重複禁止。用途を分散し、掛ける/収納系は最大2件、可能なら1件。同系統ブランド3件以上は禁止。

【フェーズ2】
- 1〜5番が1日目、6〜10番が2日目。各日必ずempathy 3件、product 2件。
- 掲載順を決める際は上記の固定時刻を最優先する。
- 商品同士を連続させない。同系統テーマも固めない。
- 毎日同じE-P-E-P-E等の固定型にする必要はない。
- editorial_order決定後、その位置の時刻に合わない本文があれば、その投稿本文だけ時刻に自然になるよう調整してよい。ただし商品事実を変えない。

【過去】
過去への言及は実投稿履歴に存在する事実だけ。未投稿キューは重複回避だけに使う。

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
{{"post_id":"P1","type":"product","selected_item_code":"itemCode","parent_text":"親投稿","child_text_base":"返信","theme":"テーマ","product_group":"用途","context_note":""}}
],"editorial_order":["E1","P1","E2","P2","E3","E4","P3","E5","P4","E6"]}}
postsはE1〜E6とP1〜P4を各1件。editorial_orderも同じ10IDを重複なく1回ずつ使う。
"""
    result = _json_response(prompt)
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
    for post in posts:
        _validate_parent(post.get("parent_text", ""), recent)
        if post["type"] == "product":
            code = post.get("selected_item_code")
            if code not in valid_codes:
                raise RuntimeError(f"候補外の商品コード: {code}")
            codes.append(code)
            if not str(post.get("child_text_base", "")).strip():
                raise RuntimeError("商品返信文が空です。")
    if len(set(codes)) != 4:
        raise RuntimeError("商品が重複しています。")
    return _arrange_editorial_order(posts, result.get("editorial_order", []))
