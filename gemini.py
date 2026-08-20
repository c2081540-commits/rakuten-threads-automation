import json
import os
import time
from pathlib import Path

from history import (
    product_performance_feedback,
    recent_product_strategy_entries,
)

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

ROOT = Path(__file__).resolve().parent
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini").strip()
MAX_OUTPUT_TOKENS = 7000
MAX_GENERATION_ATTEMPTS = 3
MAX_API_ATTEMPTS = 3
OPENAI_REQUEST_COUNT = 0
DAILY_SLOTS = [("E1", 7), ("P1", 12), ("E2", 15), ("P2", 18), ("E3", 21)]

WEAK_PARENT_ENDINGS = ("便利そう", "良さそう", "いいかも", "便利かも", "使えそう", "使いやすそう")
WEAK_PARENT_PHRASES = ("ちょっと掛けたいもの", "ちょっと置きたいもの", "あると便利そう", "あると良さそう")
PRODUCT_REVIEW_PHRASES = ("確認したい", "チェックしたい", "判断しやすい", "購入判断", "購入前に")
EMPATHY_PRODUCT_PITCH_PHRASES = ("便利グッズ", "収納グッズ", "小さなトレー", "があると便利", "一つでスムーズ", "ひとつでスムーズ")
AI_WRAPPED_EMOTION_PHRASES = ("嬉しい発見", "なら助かる", "理想的", "使い勝手がいい", "使い勝手が良い", "日常導入のハードル", "導入のハードル", "便利な発見", "うれしい発見")


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


def _select_grounded_products(items, target_date=None):
    """Choose only products with a strong, natural emotion-first Threads hook."""
    usable = [item for item in items if item.get("itemCode") and _source_text(item)]
    if len({item["itemCode"] for item in usable}) < 2:
        raise RuntimeError("商品候補が2件未満のため投稿を作成できません。")
    candidates = _candidate_data(usable)
    emotion_rules = _load_prompt("emotion_first_product.txt")

    def build(attempt, last_error):
        retry = "" if last_error is None else f"\n前回の不合格理由:{last_error}\n弱い商品を選ばず、別の商品を評価する。"
        return _json_response(f"""
{emotion_rules}

候補商品をThreadsで見た瞬間の「人間の反応」で評価し、最も強い2商品を選ぶ。
商品説明の充実度ではなく、親投稿で短く自然な第一声が出るかを最重要視する。

強い例の方向性: 「これ何？」「こういうの欲しかった」「その発想はなかった」「これでいいじゃん」など。
ただし例文のコピーは禁止。商品固有の事実から自然に出る反応にする。
「便利そう」「助かる」「時短になる」しか出ない商品は弱い。
未使用なのに使用経験を前提にしない。

各選択商品について以下を必ず返す。
- hook_strength: 1〜10。7未満は選択禁止。
- natural_reaction: その商品を画像で見た人が口にしそうな短い第一声。
- why_shareable: なぜThreadsで人に見せたくなる商品なのか。
- hook_evidence: その反応を支える商品説明中の具体的事実。

対象日:{target_date.isoformat() if target_date else "未指定"}
候補:{json.dumps(candidates, ensure_ascii=False)}
{retry}
JSONのみ: {{"selected_products":[{{"itemCode":"...","hook_strength":8,"natural_reaction":"...","why_shareable":"...","hook_evidence":"..."}},{{...}}]}}
""")

    valid_codes = {item["itemCode"] for item in usable}

    def validate(result):
        rows = result.get("selected_products", [])
        codes = [row.get("itemCode") for row in rows]
        if len(rows) != 2 or len(set(codes)) != 2 or any(code not in valid_codes for code in codes):
            raise RuntimeError("商品選択が候補内の重複なし2件になっていません。")
        for row in rows:
            try:
                strength = int(row.get("hook_strength", 0))
            except (TypeError, ValueError):
                strength = 0
            if strength < 7:
                raise RuntimeError(f"感情フック強度が不足しています: {row.get('itemCode')}={strength}")
            for key in ("natural_reaction", "why_shareable", "hook_evidence"):
                if not str(row.get(key, "")).strip():
                    raise RuntimeError(f"商品選択評価の{key}が空です。")
        return rows

    selected = _generate_with_validation("感情フック商品選択", build, validate)
    by_code = {item["itemCode"]: item for item in usable}
    grounded = []
    for choice in selected:
        code = choice["itemCode"]
        item = by_code[code]
        name = " ".join(str(item.get("itemName", "")).split())
        caption = " ".join(str(item.get("itemCaption", "")).split())[:1200]
        grounded.append({
            "selected_item_code": code,
            "facts": [text for text in (name, caption) if text],
            "selection_hook_strength": int(choice["hook_strength"]),
            "selection_natural_reaction": str(choice["natural_reaction"]).strip(),
            "selection_why_shareable": str(choice["why_shareable"]).strip(),
            "selection_hook_evidence": str(choice["hook_evidence"]).strip(),
        })
    return grounded

def _validate_empathy_text(text, hour, target_date=None):
    _validate_parent(text)
    compact = str(text).replace("\n", "").strip()
    sentence_count = sum(compact.count(mark) for mark in ("。", "！", "？", "!", "?"))
    if len(compact) < 45:
        raise RuntimeError("共感投稿が短すぎます。具体的な日常場面と共感の着地点を2〜4文で書いてください。")
    if sentence_count < 2 or sentence_count > 4:
        raise RuntimeError("共感投稿は2〜4文で書いてください。")
    # The slot is only the publishing time. It does not constrain when the
    # event described in an empathy post happened.
    if target_date and "週末" in compact and target_date.weekday() not in (4, 5, 6):
        raise RuntimeError(f"平日枠に週末表現があります: {text}")
    # 「定位置」は「鍵の定位置を決める」のような通常の日常表現にも使うため、
    # 単語だけで商品訴求と判定しない。ここでは商品仕様に特有の表現だけを弾く。
    product_leak = ("段差や縁", "ワンタッチ", "パッキン", "折りたた", "収納時", "場所を取らない", "持ち運びやす")
    if any(x in text for x in product_leak):
        raise RuntimeError(f"共感投稿が商品訴求に寄っています: {text}")


def _generate_with_validation(label, build, validate, fallback=None):
    """Regenerate model output when the deterministic quality gate rejects it."""
    last_error = None
    last_result = None
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        try:
            last_result = build(attempt, last_error)
            return validate(last_result)
        except RuntimeError as exc:
            last_error = exc
            if attempt < MAX_GENERATION_ATTEMPTS:
                print(f"{label}の検品不合格: 自動再生成します ({attempt}/{MAX_GENERATION_ATTEMPTS}): {exc}")
    if fallback is not None:
        print(f"{label}は検品を通過しなかったため、安全な代替処理で続行します: {last_error}")
        return fallback(last_result, last_error)
    raise RuntimeError(f"{label}を{MAX_GENERATION_ATTEMPTS}回再生成しても検品を通過しませんでした: {last_error}")


EMPATHY_FALLBACKS = (
    ("買い物", "買い物", "neutral", "買い物へ行くと、予定になかった物までついカゴに入れてしまう。帰宅してレシートを見ると少し反省するけど、結局ちゃんと使うならいいかと思ってしまう。"),
    ("食事", "食事/料理", "positive", "冷蔵庫にあるものだけで食事を作れた日は、いつもより少し得した気分になる。買い物へ行く手間も減って、残っていた食材も使い切れるとちょっと嬉しい。"),
    ("休憩", "休憩", "neutral", "少し休むつもりでスマホを見始めたのに、気づくと思ったより時間がたっている。休んだはずなのに、立ち上がると前より疲れている感じがすることがある。"),
    ("洗濯", "洗濯/衣類", "negative", "洗濯を終えた後にポケットから紙が出てくると、干す前から一気に疲れる。確認しておけばよかったと思いながら、細かい紙を取る作業が始まる。"),
    ("天気", "季節/天気", "neutral", "朝は晴れていたのに、外出してから雨が降りそうな空に変わると落ち着かない。大丈夫だと思いたいのに、結局何度も天気予報を開いてしまう。"),
    ("休日", "朝夜/休日", "positive", "休みの日に予定を入れず、時間を気にせずに過ごせるとそれだけで楽になる。何かをたくさんしたわけではないのに、そういう日の方がしっかり休んだ感じがする。"),
)


def _fallback_empathy_posts(target_date=None):
    """Return vetted copy instead of failing the entire preview workflow."""
    offset = target_date.toordinal() if target_date else 0
    indexes = [(offset + step * 2) % len(EMPATHY_FALLBACKS) for step in range(3)]
    posts = []
    for post_id, index in zip(("E1", "E2", "E3"), indexes):
        theme, group, tone, parent = EMPATHY_FALLBACKS[index]
        posts.append({"post_id": post_id, "type": "empathy", "parent_text": parent,
                      "theme": theme, "theme_group": group, "tone": tone,
                      "context_note": "fallback"})
    return posts


def _final_editorial_pass(posts, verified, recent=None, target_date=None):
    """Turn the five drafts into publishable copy without changing their identity.

    This is deliberately an editor, not a pass/fail classifier.  It receives the
    complete day and the original Rakuten evidence, corrects only the copy, and
    returns every post.  Python pins IDs, types and product codes afterwards.
    """
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

    expected_ids = [p["post_id"] for p in posts]
    last_error = None
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        retry = "" if last_error is None else f"\n前回の校閲結果の不備:{last_error}\nこの不備を直し、5件をすべて返す。"
        result = _json_response(f"""
あなたは生活系Threads投稿の校閲編集者。入力は初稿であり、合否判定だけをしてはいけない。
5件を実際に投稿できる自然な完成文へ直して返す。問題がない文は無理に変えない。
post_id、type、selected_item_code、商品の組合せ、5件の順番は変更禁止。

全投稿の確認:
- 普通の人が読んで意味が一度で通る口語にする。記事調、説明調、気取った余韻を避ける。
- 行動と結果の因果、対比、主語と述語を確認する。意味のない驚きや不思議さを足さない。
- 同じ意味の語を重ねない。事実以上に効果を強めない。
- 投稿時刻は配信時刻にすぎない。本文の出来事を朝・昼・夜に制限しない。

共感投稿:
- 具体的な日常場面と、その場で自然に出る本音・あるあるを2〜3文、45〜140字で書く。
- 教訓、改善方法、商品への前振り、作ったような結論を加えない。

商品投稿:
- verified_factsは楽天の商品名・説明という根拠資料。転載せず自然に言い換える。
- 根拠にない機能・素材・付属品・効果を追加しない。数値や対象条件を変えない。
- 「幅広」を「幅を広げられる」、「容積を小さくする」を「荷物が減る」のように、言い換えで意味を変えない。
- 親文は困り事・使用場面・何が楽になるか。補足文は親と重ならない根拠事実を1〜2点だけ使う。
- 宣伝文句や仕様の羅列を、そのまま文章へ持ち込まない。

対象日:{target_date.isoformat() if target_date else "未指定"}
直近投稿（同じ本文を作らない）:{json.dumps(recent or [], ensure_ascii=False)}
初稿と商品根拠:{json.dumps(review_input, ensure_ascii=False)}
{retry}
JSONのみ: {{"edits":[
{{"post_id":"E1","parent_text":"完成文"}},
{{"post_id":"P1","parent_text":"完成文","child_text_base":"完成補足文"}},
{{"post_id":"E2","parent_text":"完成文"}},
{{"post_id":"P2","parent_text":"完成文","child_text_base":"完成補足文"}},
{{"post_id":"E3","parent_text":"完成文"}}
]}}
入力順の5件を過不足なく返す。
""")
        edits = result.get("edits", [])
        if [x.get("post_id") for x in edits] != expected_ids:
            last_error = "投稿IDまたは順序が不正です。"
            continue

        candidate = []
        try:
            for original, edit in zip(posts, edits):
                row = dict(original)
                row["parent_text"] = str(edit.get("parent_text", "")).strip()
                if row["type"] == "product":
                    row["child_text_base"] = str(edit.get("child_text_base", "")).strip()
                    _validate_product_copy_against_facts(
                        row["parent_text"], row["child_text_base"],
                        facts_by_code[row["selected_item_code"]],
                    )
                else:
                    _validate_empathy_text(row["parent_text"], 0, None)
                candidate.append(row)
            normalized = [_normalize_for_compare(p["parent_text"]) for p in candidate]
            if len(normalized) != len(set(normalized)):
                raise RuntimeError("完成文内に同一本文があります。")
            return candidate
        except RuntimeError as exc:
            last_error = str(exc)

    raise RuntimeError(f"最終校閲を{MAX_GENERATION_ATTEMPTS}回行っても完成しませんでした: {last_error}")


def _candidate_key(post_id, variant):
    return f"{post_id}-{variant}"


def _generate_three_way_candidates(verified, recent=None, events=None, target_date=None,
                                   only_ids=None, rejection_reasons=None):
    """Create three complete, independently conceived drafts for each requested slot."""
    requested = list(only_ids or ["P1", "P2"])
    facts_by_code = {x["selected_item_code"]: x["facts"] for x in verified}
    product_code_by_id = {
        "P1": verified[0]["selected_item_code"],
        "P2": verified[1]["selected_item_code"],
    }
    emotion_rules = _load_prompt("emotion_first_product.txt")
    result = _json_response(f"""
{emotion_rules}

生活系Threadsの完成原稿候補を作る。対象日と直近投稿を踏まえ、指定された各枠につきA・B・Cの3案を作る。
3案は同じ初稿の言い換えではなく、場面・切り口・文章の運びをそれぞれ独立して考える。

対象日:{target_date.isoformat() if target_date else "未指定"}
対象枠:{json.dumps(requested, ensure_ascii=False)}
投稿枠:E1=共感、P1=商品1、E2=共感、P2=商品2、E3=共感
商品と根拠:{json.dumps(verified, ensure_ascii=False)}
直近投稿:{json.dumps(recent or [], ensure_ascii=False)}
楽天イベント:{_event_instruction(events)}
前回3案が全滅した理由:{json.dumps(rejection_reasons or {}, ensure_ascii=False)}

共感投稿:
- 誰にでも起こり得る具体的な日常場面と、その場で自然に出る本音・あるあるを書く。
- 2〜3文、45〜140字。教訓、改善方法、商品への前振り、作ったようなオチは禁止。
- 同時に作る共感枠は別テーマにし、掃除・収納・水回りは合計1件まで。

商品投稿:
- 指定商品のfactsだけを根拠にする。根拠にない機能・素材・効果を作らない。
- 最上位ルールは「感情先行」。親文は商品を見た人が最初に口にしそうな自然な反応から始める。
- 親文で仕様を説明し切らない。感情・驚き・発見・欲望・解放感など＋その反応が生まれた最低限の理由までに絞る。
- 返信文で初めて、その感情の理由になる具体的な機能・使い方・仕様を1〜2点示す。
- 「悩み→解決」を毎回強制しない。悩みが弱い商品に人工的な困り事を作らない。
- 未使用商品の架空体験は禁止。「使った」「愛用」「戻れない」「買ってよかった」は書かない。
- 商品候補には emotional_reaction（第一感情）、hook_type（感情フックの型）、purchase_trigger（欲しくなる理由）を必ず付ける。
- problem_axis / benefit_axis / sales_structure は分析用の補助メタデータ。空でもよく、A/B/Cで無理に変えなくてよい。
- 同じ商品枠のA/B/Cは emotional_reaction、hook_type、purchase_trigger をそれぞれ別の仮説にする。単なる言い換えは禁止。
- A/B/Cは、強い感想型・比較/乗り換え型・具体場面/欲望型など、商品に自然に合う異なる反応を競わせる。合わない型を無理に使わない。
- 直近30日の感情反応・フック型・購買トリガーと同じ組合せを避ける。
- 「便利そう」「良さそう」で逃げず、何に反応したのかが一読で分かる言葉にする。
- 「嬉しい発見」「〜なら助かる」「理想的」「使い勝手がいい」「導入のハードル」のような、説明文を感情語で包んだだけのAI表現は禁止。
- 親投稿は友達に画像を見せながら一言言う感覚を優先し、二文目で商品説明に戻らない。
- 「幅広」を「幅を広げられる」、「容積を抑える」を「荷物が減る」のように意味を変えない。

直近30日の商品戦略履歴:{json.dumps(recent_product_strategy_entries(days=30, limit=30), ensure_ascii=False)}
実績フィードバック:{json.dumps(product_performance_feedback(days=60, min_samples=3), ensure_ascii=False)}
- 実績フィードバックは ready=true の場合だけ参考にする。
- ready=false の場合は実績による優劣を付けない。
- ready=true でも高実績軸を強制しない。商品根拠、自然さ、訴求の具体性、直近との重複回避を優先し、同程度の案なら実績の良い傾向を補助的に優先する。
- サンプル数が少ない傾向を一般化しない。

JSONのみ: {{"candidates":[
{{"candidate_id":"E1-A","post_id":"E1","variant":"A","type":"empathy","parent_text":"完成文","theme":"テーマ","theme_group":"分類","tone":"neutral|positive|negative","context_note":""}},
{{"candidate_id":"P1-A","post_id":"P1","variant":"A","type":"product","selected_item_code":"指定コード","parent_text":"感情フック中心の完成文","child_text_base":"理由と具体仕様の補足文","theme":"テーマ","product_group":"用途","emotional_reaction":"自然な第一感情","hook_type":"発見|驚き|理想|比較|解放|これでいい|意外性|欲望|その他","purchase_trigger":"欲しくなる具体的理由","problem_axis":"任意","benefit_axis":"任意","sales_structure":"任意","context_note":""}}
]}}
対象枠ごとにA、B、Cを1件ずつ、合計「対象枠数×3件」を返す。
""")
    candidates = result.get("candidates", [])
    expected = {_candidate_key(post_id, variant) for post_id in requested for variant in "ABC"}
    by_key = {str(x.get("candidate_id", "")): x for x in candidates}
    if set(by_key) != expected or len(candidates) != len(expected):
        raise RuntimeError("3案生成の候補IDが不足または重複しています。")
    allowed_structures = {"直接型", "発見型", "用途型", "比較型", "疑問→解決型", "願望→商品型", "具体場面型"}
    for candidate_id, row in by_key.items():
        post_id, variant = candidate_id.rsplit("-", 1)
        if row.get("post_id") != post_id or row.get("variant") != variant:
            raise RuntimeError(f"候補IDの対応が不正です: {candidate_id}")
        if post_id.startswith("P"):
            expected_code = product_code_by_id[post_id]
            if row.get("type") != "product" or row.get("selected_item_code") != expected_code:
                raise RuntimeError(f"商品候補の商品コードが不正です: {candidate_id}")
            if not str(row.get("parent_text", "")).strip() or not str(row.get("child_text_base", "")).strip():
                raise RuntimeError(f"商品候補の本文が空です: {candidate_id}")
            for axis in ("emotional_reaction", "hook_type", "purchase_trigger"):
                if not str(row.get(axis, "")).strip():
                    raise RuntimeError(f"商品候補の{axis}が空です: {candidate_id}")
            if row.get("sales_structure") and row.get("sales_structure") not in allowed_structures:
                raise RuntimeError(f"商品候補のsales_structureが不正です: {candidate_id}")
        else:
            if row.get("type") != "empathy":
                raise RuntimeError(f"共感候補のtypeが不正です: {candidate_id}")
            if not str(row.get("parent_text", "")).strip():
                raise RuntimeError(f"共感候補の本文が空です: {candidate_id}")
    # 同じ商品枠のA/B/Cが同じ訴求仮説へ収束するのを機械的に防ぐ。
    for post_id in ("P1", "P2"):
        if post_id not in requested:
            continue
        rows = [by_key[_candidate_key(post_id, variant)] for variant in "ABC"]
        for axis in ("emotional_reaction", "hook_type", "purchase_trigger"):
            values = [str(row.get(axis, "")).strip() for row in rows]
            if len(set(values)) != 3:
                raise RuntimeError(f"{post_id}のA/B/Cで{axis}が重複しています: {values}")
    return by_key


def _compare_three_way_candidates(candidates, verified, recent=None, target_date=None):
    """Rank complete drafts. The comparison model may select, but never rewrite."""
    comparison_input = [candidates[key] for key in sorted(candidates)]
    emotion_rules = _load_prompt("emotion_first_product.txt")
    result = _json_response(f"""
{emotion_rules}

同じ投稿枠のA・B・Cを横並びで比較し、投稿に最も適した1案を選ぶ。文章の修正・合成・新規作成は禁止。

評価基準:
1. 普通の日本語として自然で、一度で意味が通る
2. 行動、対比、原因と結果にねじれがない
3. 商品投稿は楽天の根拠の意味を変えず、未記載の機能や効果を加えていない
4. 実際の生活場面として成立し、広告文・説明書・作り話のようになっていない
5. 直近投稿と内容や着地が重なっていない
6. 商品投稿は、商品を見た一般ユーザーの第一反応として自然な感情が出ている
7. 親文が説明文ではなく、感情＋最低限の理由で「何それ」「それ欲しい」と続きを見たくなる
8. child_text_base は親の感情を裏付ける具体的な商品事実を増やし、親と役割分担できている
9. emotional_reaction / hook_type / purchase_trigger が商品固有の事実から自然に成立している
10. 直近30日の感情反応・フック型・購買トリガーの繰り返しを避けている
11. A/B/Cでは、説明が最も整った案ではなく、感情が自然で強く、商品をもっと見たくなる案を優先する

直近30日の商品戦略:{json.dumps(recent_product_strategy_entries(days=30, limit=30), ensure_ascii=False)}
実績フィードバック:{json.dumps(product_performance_feedback(days=60, min_samples=3), ensure_ascii=False)}
実績の扱い:
- ready=false なら実績を選考理由に使わない。
- ready=true でも実績は補助評価に限定する。商品根拠、感情反応の自然さと強さ、親と返信の役割分担、重複回避を先に評価する。
- 上記が同程度の候補同士でのみ、十分なサンプルがある高実績傾向を優先材料にする。
商品根拠:{json.dumps(verified, ensure_ascii=False)}
対象日:{target_date.isoformat() if target_date else "未指定"}
直近投稿:{json.dumps(recent or [], ensure_ascii=False)}
候補:{json.dumps(comparison_input, ensure_ascii=False)}

各枠で3案すべてに明確な問題がある場合だけselected_candidate_idをnullにし、3案共通の具体的な問題をrejection_reasonへ書く。
少し好みが分かれる程度なら最良案を選び、全滅扱いにしない。
JSONのみ: {{"selections":[{{"post_id":"E1","selected_candidate_id":"E1-Bまたはnull","rejection_reason":"null時のみ理由"}}]}}
候補に含まれる各post_idを1件ずつ返す。
""")
    selections = result.get("selections", [])
    expected_ids = {row["post_id"] for row in candidates.values()}
    if {x.get("post_id") for x in selections} != expected_ids or len(selections) != len(expected_ids):
        raise RuntimeError("比較結果の投稿IDが不足または重複しています。")
    chosen, rejected = {}, {}
    for selection in selections:
        post_id = selection["post_id"]
        selected_id = selection.get("selected_candidate_id")
        if selected_id is None:
            rejected[post_id] = str(selection.get("rejection_reason", "3案すべて不採用"))
        elif selected_id not in candidates or candidates[selected_id].get("post_id") != post_id:
            raise RuntimeError(f"比較AIが候補外を選択しました: {selected_id}")
        else:
            candidate = candidates[selected_id]
            try:
                if post_id.startswith("P"):
                    facts_by_code = {x["selected_item_code"]: x["facts"] for x in verified}
                    _validate_product_copy_against_facts(
                        candidate.get("parent_text", ""), candidate.get("child_text_base", ""),
                        facts_by_code[candidate["selected_item_code"]],
                    )
                else:
                    _validate_empathy_text(candidate.get("parent_text", ""), 0, target_date)
                chosen[post_id] = candidate
            except RuntimeError as exc:
                rejected[post_id] = f"選択案が必須形式を満たさない: {exc}"
    return chosen, rejected


def _generate_by_comparison(verified, recent=None, events=None, target_date=None):
    """Generate/compare three drafts; retry only slots whose three drafts all lose."""
    pending = ["P1", "P2"]
    selected = {}
    rejection_reasons = {}
    for round_no in range(1, 3):
        candidates = _generate_three_way_candidates(
            verified, recent, events, target_date, only_ids=pending,
            rejection_reasons=rejection_reasons,
        )
        chosen, rejected = _compare_three_way_candidates(candidates, verified, recent, target_date)
        selected.update(chosen)
        pending = [post_id for post_id in pending if post_id in rejected]
        rejection_reasons = rejected
        if not pending:
            return [selected[slot_id] for slot_id in ("P1", "P2")]
        print(f"3案すべて不採用の枠を新しい3案で再生成します ({round_no}/2): {pending}")
    raise RuntimeError(f"2回の3案比較でも採用候補がありません: {rejection_reasons}")


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
        if len(compact) < 18:
            raise RuntimeError("商品親投稿が短すぎて感情フックとして成立していません。")
        if any(x in compact for x in WEAK_PARENT_PHRASES):
            raise RuntimeError("商品親投稿が曖昧な定型表現に寄りすぎています。")
        if any(x in compact for x in WEAK_PARENT_ENDINGS):
            raise RuntimeError("商品親投稿に曖昧な感想表現があります。具体的な生活上の変化へ書き換えてください。")
        if any(x in compact for x in PRODUCT_REVIEW_PHRASES):
            raise RuntimeError("商品親投稿が購入ガイド・比較記事調です。")
        if any(x in compact for x in AI_WRAPPED_EMOTION_PHRASES):
            raise RuntimeError("商品親投稿が説明文を感情語で包んだAI表現になっています。")
        # 感情先行型では親投稿を短く保つ。具体仕様は返信側で補う。
        if len(compact) > 110:
            raise RuntimeError("商品親投稿が説明過多です。感情フックと最低限の理由に絞ってください。")
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


def _generate_empathy_only(recent=None, target_date=None):
    """Generate empathy posts in a request that never receives product data."""
    empathy_prompt = _load_prompt("empathy.txt")
    hours = {"E1": 7, "E2": 15, "E3": 21}

    def build(attempt, last_error):
        retry = "" if last_error is None else f"\n前回の不合格理由:{last_error}\n商品への前振りを作らず、3件とも別テーマで書き直す。"
        return _json_response(f"""
{empathy_prompt}

共感投稿だけを3件作る。このリクエストには商品情報は存在しない。
E1/E2/E3は配信枠のIDにすぎず、朝昼夜の実況連作にしない。
3件は互いに独立した生活テーマにする。
商品の機能を連想させる悩み→解決、便利グッズへの前振り、買い物誘導は禁止。
一般論や教訓ではなく、その瞬間に本人が感じた本音として2〜4文、45〜140字。
対象日:{target_date.isoformat() if target_date else "未指定"}
直近の投稿（重複回避のみ）:{json.dumps(recent or [], ensure_ascii=False)}
{retry}
JSONのみ: {{"posts":[
{{"post_id":"E1","type":"empathy","parent_text":"本文","theme":"テーマ","theme_group":"分類","tone":"neutral|positive|negative","context_note":""}},
{{"post_id":"E2","type":"empathy","parent_text":"本文","theme":"テーマ","theme_group":"分類","tone":"neutral|positive|negative","context_note":""}},
{{"post_id":"E3","type":"empathy","parent_text":"本文","theme":"テーマ","theme_group":"分類","tone":"neutral|positive|negative","context_note":""}}
]}}
""")

    def validate(result):
        posts = result.get("posts", [])
        if [p.get("post_id") for p in posts] != ["E1", "E2", "E3"]:
            raise RuntimeError("共感投稿IDがE1/E2/E3になっていません。")
        groups = []
        for post in posts:
            if post.get("type") != "empathy":
                raise RuntimeError("共感生成に商品投稿が混入しました。")
            if str(post.get("tone", "")) not in {"neutral", "positive", "negative"}:
                raise RuntimeError("共感投稿のtoneが不正です。")
            _validate_empathy_text(post.get("parent_text", ""), hours[post["post_id"]], target_date)
            groups.append(str(post.get("theme_group", "")).strip())
        if len(set(groups)) != 3:
            raise RuntimeError(f"共感3件のテーマが重複しています: {groups}")
        return posts

    try:
        return _generate_with_validation("独立共感投稿", build, validate)
    except RuntimeError as exc:
        print(f"独立共感投稿の生成が不安定なため検品済み文面へ切替: {exc}")
        return _fallback_empathy_posts(target_date)


def generate_mixed_stock(items, recent_history=None, existing_queue=None, events=None, target_date=None):
    history = recent_history or []
    queued = existing_queue or []
    recent = history + queued

    # 1) Empathy is generated in complete isolation: no product names, facts or selected items.
    empathy = _generate_empathy_only(recent=recent, target_date=target_date)

    # 2) Products are selected by emotion-hook strength, then only product slots get A/B/C drafts.
    verified = _select_grounded_products(items, target_date)
    products = _generate_by_comparison(
        verified, recent=recent, events=events, target_date=target_date
    )

    by_id = {post["post_id"]: post for post in empathy + products}
    arranged = [by_id[slot_id] for slot_id, _ in DAILY_SLOTS]
    if [p["post_id"] for p in arranged] != ["E1", "P1", "E2", "P2", "E3"]:
        raise RuntimeError("E/P/E/P/Eの固定構成を作れませんでした。")
    return arranged

