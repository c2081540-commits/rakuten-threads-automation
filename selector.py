import re

from history import recent_product_codes


# 楽天の商品説明だけから機械的に判定できる「Threadsで紹介しやすい商品」の補助指標。
# AIで文章を作る前の候補順位にだけ使い、商品効果そのものは推測しない。
SCENE_TERMS = (
    "朝", "出勤", "通勤", "仕事", "料理", "調理", "弁当", "洗面", "風呂", "旅行",
    "出張", "収納", "掃除", "洗濯", "バッグ", "化粧", "メイク", "寝る", "睡眠",
)
PROBLEM_TERMS = (
    "時短", "省スペース", "コンパクト", "整理", "仕分け", "吊り下げ", "片手", "軽量",
    "持ち運び", "収納", "圧縮", "食洗機", "コードレス", "充電", "折りたた", "自立",
)
SPEC_PATTERN = re.compile(
    r"(?:約\s*)?\d+(?:\.\d+)?\s*(?:cm|mm|g|kg|ml|L|℃|枚|個|本|段階|WAY|way)",
    re.IGNORECASE,
)


def threads_sellability_score(item):
    """Return a small deterministic tie-break score for Threads suitability.

    This does not claim that a product will sell.  It only rewards candidates
    whose Rakuten data makes a concrete problem, usage scene and purchase facts
    easier to communicate in a short Threads post.
    """
    name = " ".join(str(item.get("itemName", "")).split())
    caption = " ".join(str(item.get("itemCaption", "")).split())
    source = f"{name} {caption}"

    scene_hits = sum(term in source for term in SCENE_TERMS)
    problem_hits = sum(term in source for term in PROBLEM_TERMS)
    spec_hits = len(SPEC_PATTERN.findall(source))
    image_count = len(item.get("imageUrls") or [])

    score = 0.0
    score += min(scene_hits, 3) * 0.7
    score += min(problem_hits, 4) * 0.8
    score += min(spec_hits, 4) * 0.45
    score += min(image_count, 3) * 0.35

    # 説明が短すぎる商品は、根拠を保ったまま複数の訴求軸を作りにくい。
    if len(caption) >= 300:
        score += 0.6
    if len(caption) >= 700:
        score += 0.4

    return round(score, 3)


def primary_filter(
    items,
    min_rating=4.3,
    min_reviews=100,
    min_price=1000,
    max_price=5000,
    history_days=30,
    excluded_item_codes=None,
):
    excluded_codes = recent_product_codes(days=history_days) | set(excluded_item_codes or [])
    passed = []

    for item in items:
        if not item.get("itemCode") or item["itemCode"] in excluded_codes:
            continue
        if item.get("reviewAverage", 0) < min_rating:
            continue
        if item.get("reviewCount", 0) < min_reviews:
            continue
        price = item.get("itemPrice", 0)
        if not (min_price <= price <= max_price):
            continue
        if not item.get("imageUrls"):
            continue
        if not item.get("affiliateUrl"):
            continue
        enriched = dict(item)
        enriched["threadsSellabilityScore"] = threads_sellability_score(item)
        passed.append(enriched)

    # 評価・レビュー数だけでなく、短文SNSで「場面→悩み→具体的な便益」を
    # 根拠付きで説明しやすいかを補助順位に使う。
    passed.sort(
        key=lambda x: (
            x.get("threadsSellabilityScore", 0),
            x.get("reviewAverage", 0),
            min(x.get("reviewCount", 0), 5000),
        ),
        reverse=True,
    )
    return passed


def build_shortlist(items, limit=10):
    """OpenAI二次選定へ渡す候補数を制限する。"""
    return items[:limit]
