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
    if len(compact) > 140:
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
    return f"楽天市場公式で現在開催中と確認済みのイベント: {json.dumps(names, ensure_ascii=False)}。必要なら自然に触れて構いません。ただし個別商品の値下げ・クーポン・ポイント対象とは断定しないでください。"


def select_product(items):
    prompt = f"以下の楽天商品候補からThreads向きの1件を選択。候補外禁止。数値や仕様の推測禁止。候補:{json.dumps(_candidate_data(items), ensure_ascii=False)}\nJSONのみ: {{\"selected_item_code\":\"itemCode\",\"reason\":\"短い理由\",\"preferred_image_index\":0}}"
    result = _json_response(prompt)
    code = result.get("selected_item_code")
    selected = next((x for x in items if x["itemCode"] == code), None)
    if selected is None:
        raise RuntimeError(f"OpenAIが候補外の商品コードを返しました: {code}")
    return selected, result.get("reason", ""), 0


def generate_product_copy(item, recent_posts=None, events=None):
    base = _load_prompt("product.txt")
    facts = {"itemCode": item["itemCode"], "itemName": item["itemName"], "itemCaption": item.get("itemCaption", "")[:1000]}
    prompt = f"{base}\n【今回の商品情報】\n{json.dumps(facts, ensure_ascii=False)}\n【楽天公式イベント情報】\n{_event_instruction(events)}\n【直近の商品投稿】\n{json.dumps(recent_posts or [], ensure_ascii=False)}\nJSONのみ: {{\"parent_text\":\"親投稿1〜3行\",\"child_text_base\":\"返信用補足1〜2文\"}}"
    result = _json_response(prompt)
    parent, child = str(result.get("parent_text", "")).strip(), str(result.get("child_text_base", "")).strip()
    if not parent or not child:
        raise RuntimeError(f"OpenAI文章生成結果が不足しています: {result}")
    _validate_parent(parent, recent_posts)
    return parent, child


def generate_sample_batch(items, count=5, recent_posts=None, events=None):
    count = max(1, min(count, len(items)))
    base = _load_prompt("product.txt")
    prompt = f"{base}\n候補から重複なしで{count}件選び親投稿と返信を生成。候補:{json.dumps(_candidate_data(items), ensure_ascii=False)}\nイベント:{_event_instruction(events)}\n直近:{json.dumps(recent_posts or [], ensure_ascii=False)}\nJSONのみ: {{\"samples\":[{{\"selected_item_code\":\"itemCode\",\"reason\":\"短い理由\",\"parent_text\":\"親投稿\",\"child_text_base\":\"返信\"}}]}}"
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
    _validate_batch_parents(samples, recent_posts)
    return samples


def _valid_daily_mix(posts):
    if len(posts) != 10:
        return False
    for start in (0, 5):
        day = posts[start:start + 5]
        if sum(1 for p in day if p.get("type") == "empathy") != 3:
            return False
        if sum(1 for p in day if p.get("type") == "product") != 2:
            return False
    return True


def _normalize_mixed_stock(posts):
    """掲載順を壊さず10件に正規化する。まず先頭10件を優先し、比率が崩れる場合のみ順序維持で余剰を除く。"""
    if len(posts) < 10:
        raise RuntimeError(f"ストック生成不足です: total={len(posts)}")
    first_ten = posts[:10]
    if _valid_daily_mix(first_ten):
        return first_ten
    kept = []
    empathy_count = 0
    product_count = 0
    for post in posts:
        post_type = post.get("type")
        if post_type == "empathy" and empathy_count < 6:
            kept.append(post)
            empathy_count += 1
        elif post_type == "product" and product_count < 4:
            kept.append(post)
            product_count += 1
        if len(kept) == 10:
            break
    if empathy_count < 6 or product_count < 4:
        raise RuntimeError(f"ストック生成不足です: total={len(posts)}, empathy={empathy_count}, product={product_count}")
    if not _valid_daily_mix(kept):
        raise RuntimeError("OpenAIが生成した掲載順では1日あたり共感3・商品2を満たせません。")
    return kept


def generate_mixed_stock(items, recent_history=None, existing_queue=None, events=None):
    """10投稿を1回のAPI呼び出しで、本文だけでなく実際の掲載順まで一括設計する。"""
    product_prompt = _load_prompt("product.txt")
    empathy_prompt = _load_prompt("empathy.txt")
    history = recent_history or []
    queued = existing_queue or []
    prompt = f"""
あなたはThreadsアカウント「これ、家に欲しい」の10投稿分の編集計画、本文、実際の掲載順を一括作成します。
返すposts配列の順番が、そのまま実際の投稿順になります。後工程では原則並べ替えません。
投稿比率は厳守: empathy（日常・共感）6件、product（楽天商品紹介）4件。合計10件。
1〜5番を1日目、6〜10番を2日目として、各日必ず empathy 3件・product 2件にしてください。
10件全体を同じ一人が数日間投稿する自然なアカウントとして設計してください。ただし全投稿を商品購入へ誘導する筋書きにはしません。
過去への言及は【実投稿履歴】に存在する事実だけ使用可能です。未投稿キューは内容・テーマ・言い回しの重複回避に使います。
商品を実際に購入・使用したという架空経験は禁止です。

【掲載順の編集ルール・最重要】
- 先に10件の内容を作ってから、10件全体を見渡して最も自然な掲載順を決め、その順番でposts配列を返すこと。
- 関連性のある日常投稿と商品投稿は、自然なら近い位置に置いてよい。ただし毎回「悩み→解決商品」の直結構成にはしない。
- 商品投稿の直前の日常投稿が、その商品のために作られた広告前振りに見える配置を連発しない。
- 無関係な日常投稿も適度に挟み、普通の生活アカウントとして読める流れにする。
- 同系統テーマを連続させすぎない。掃除→掃除→収納→収納のような固まりを避ける。
- product同士の連続は避ける。
- 1日目と2日目で似た流れを機械的に繰り返さない。E-E-P-E-Pのような固定パターンにする必要はない。
- 朝から夜へ読むことを想定し、朝向き・昼向き・夕方向きなど明確な時間文脈がある投稿は不自然な位置に置かない。
- 実投稿履歴から自然につながる話題がある場合だけ連続性を利用する。架空の前回投稿を作らない。

【10件全体の分散ルール】
- empathy 6件は、最低5種類の異なるテーマ領域に分散すること。
- empathy 6件のうち掃除・収納・水回りを直接テーマにできるのは合計2件まで。
- 残りは季節/天気、食事/料理、買い物、洗濯/衣類、朝夜/休日、休憩、家事以外の生活あるある、軽い発見や満足などから分散すること。
- empathyをすべて「不便・面倒・イライラ」にしない。6件中最低2件はニュートラルまたは軽くポジティブな独り言にすること。
- product 4件は商品コードが異なるだけでは不十分。用途・利用場面も可能な限り分散すること。
- 4商品中、収納・フック・ラック・ハンガー等の「物を掛ける/収納する商品」は最大2件まで。候補に他カテゴリが存在するなら最大1件を優先すること。
- 同じブランドや同系統商品を4件中3件以上選ばないこと。候補の制約で不可能な場合のみ例外。
- 同じ語尾、同じ悩み、同じ導入を連発しない。

【商品投稿ルール】
{product_prompt}

【日常投稿ルール】
{empathy_prompt}

【楽天商品候補】
{json.dumps(_candidate_data(items), ensure_ascii=False)}

【実投稿履歴】
{json.dumps(history, ensure_ascii=False)}

【現在の未投稿キュー】
{json.dumps(queued, ensure_ascii=False)}

【現在確認済み楽天イベント】
{_event_instruction(events)}
注意: セール文言は投稿時にも再確認するため、ストック本文には原則として固定の開催中表現を埋め込まない。

JSONのみ返してください。
{{"posts":[
  {{"type":"empathy","parent_text":"本文","theme":"具体的で短いテーマ","theme_group":"季節|食事|買い物|洗濯|朝夜|休憩|掃除|収納|水回り|その他生活","tone":"neutral|positive|negative","context_note":"履歴との関係。なければ空文字"}},
  {{"type":"product","selected_item_code":"itemCode","parent_text":"親投稿","child_text_base":"返信補足","theme":"短いテーマ","product_group":"商品の用途カテゴリ","context_note":"履歴との関係。なければ空文字"}}
]}}
必ず10件。posts配列自体を最終掲載順にすること。1〜5番は共感3・商品2、6〜10番も共感3・商品2。
"""
    result = _json_response(prompt)
    raw_posts = result.get("posts", [])
    posts = _normalize_mixed_stock(raw_posts)
    empathy = [p for p in posts if p.get("type") == "empathy"]
    products = [p for p in posts if p.get("type") == "product"]
    if len(posts) != 10 or len(empathy) != 6 or len(products) != 4:
        raise RuntimeError(f"投稿比率が不正です: total={len(posts)}, empathy={len(empathy)}, product={len(products)}")
    if not _valid_daily_mix(posts):
        raise RuntimeError("日別投稿比率が不正です。各日 empathy 3・product 2 が必要です。")

    groups = [str(p.get("theme_group", "")).strip() for p in empathy]
    if len(set(g for g in groups if g)) < 5:
        raise RuntimeError(f"共感投稿のテーマ分散不足です: {groups}")
    home_problem_groups = {"掃除", "収納", "水回り"}
    if sum(1 for g in groups if g in home_problem_groups) > 2:
        raise RuntimeError(f"掃除・収納・水回りに偏りすぎています: {groups}")
    tones = [str(p.get("tone", "")).strip() for p in empathy]
    if sum(1 for t in tones if t in {"neutral", "positive"}) < 2:
        raise RuntimeError(f"共感投稿がネガティブに偏りすぎています: {tones}")

    valid_codes = {x["itemCode"] for x in items}
    product_codes = []
    for post in posts:
        _validate_parent(post.get("parent_text", ""), [h.get("parent_text", "") for h in history if h.get("parent_text")])
        if post["type"] == "product":
            code = post.get("selected_item_code")
            if code not in valid_codes:
                raise RuntimeError(f"候補外の商品コード: {code}")
            product_codes.append(code)
            if not str(post.get("child_text_base", "")).strip():
                raise RuntimeError("商品投稿の返信文が空です。")
    if len(set(product_codes)) != 4:
        raise RuntimeError("ストック内の商品が重複しています。")
    _validate_batch_parents(posts, [h.get("parent_text", "") for h in history if h.get("parent_text")])
    return posts
