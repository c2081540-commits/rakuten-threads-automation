import argparse
import json

from rakuten import fetch_candidate_pool
from selector import build_shortlist, primary_filter


def preview_candidates():
    raw_items = fetch_candidate_pool(target_raw=50)
    filtered = primary_filter(raw_items)

    # 候補不足時は価格上限だけ段階的に緩和する。
    # 評価・レビュー・画像・履歴条件は初期運用では維持する。
    if len(filtered) < 5:
        filtered = primary_filter(raw_items, max_price=8000)

    shortlist = build_shortlist(filtered, limit=10)
    if not shortlist:
        raise RuntimeError("一次フィルタを通過する商品がありませんでした。")

    print(f"楽天取得件数: {len(raw_items)}")
    print(f"一次フィルタ通過件数: {len(filtered)}")
    print(f"二次選定候補: {len(shortlist)}件\n")

    safe_output = []
    for index, item in enumerate(shortlist, start=1):
        safe_output.append({
            "rank": index,
            "itemCode": item["itemCode"],
            "itemName": item["itemName"],
            "price": item["itemPrice"],
            "rating": item["reviewAverage"],
            "reviews": item["reviewCount"],
            "shopName": item["shopName"],
            "imageCount": len(item["imageUrls"]),
            "firstImage": item["imageUrls"][0] if item["imageUrls"] else None,
        })

    print(json.dumps(safe_output, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="楽天Threads自動投稿システム")
    parser.add_argument(
        "--mode",
        choices=["preview"],
        default="preview",
        help="現在は安全のため候補プレビューのみ有効",
    )
    args = parser.parse_args()

    if args.mode == "preview":
        preview_candidates()


if __name__ == "__main__":
    main()
