from history import recent_product_codes


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
        passed.append(item)

    # 定番だけに寄りすぎないよう、レビュー数だけでなく評価も加味。
    # 初期版の暫定スコア。成果データ蓄積後に置換する。
    passed.sort(
        key=lambda x: (
            x.get("reviewAverage", 0),
            min(x.get("reviewCount", 0), 5000),
        ),
        reverse=True,
    )
    return passed


def build_shortlist(items, limit=10):
    """Gemini二次選定へ渡す候補数を制限する。"""
    return items[:limit]
