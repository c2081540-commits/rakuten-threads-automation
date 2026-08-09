import argparse
import json

from gemini import generate_product_copy, select_product
from history import recent_texts
from rakuten import fetch_candidate_pool
from selector import build_shortlist, primary_filter


def get_shortlist():
    raw_items = fetch_candidate_pool(target_raw=50)
    filtered = primary_filter(raw_items)
    if len(filtered) < 5:
        filtered = primary_filter(raw_items, max_price=8000)
    shortlist = build_shortlist(filtered, limit=10)
    if not shortlist:
        raise RuntimeError("一次フィルタを通過する商品がありませんでした。")
    return raw_items, filtered, shortlist


def preview_candidates():
    raw_items, filtered, shortlist = get_shortlist()
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


def preview_full_post():
    _, _, shortlist = get_shortlist()
    selected, reason, image_index = select_product(shortlist)
    parent, child_base = generate_product_copy(
        selected, recent_posts=recent_texts("product", limit=5)
    )
    image_url = selected["imageUrls"][image_index]

    # 数値はGeminiに生成させず、楽天APIの値をここで結合する。
    child_final = (
        f"{child_base}\n\n"
        f"★{selected['reviewAverage']}（レビュー {selected['reviewCount']:,}件）\n"
        f"価格: {selected['itemPrice']:,}円\n\n"
        f"【PR】詳細はこちら\n{selected['affiliateUrl']}"
    )

    output = {
        "selected_item": selected["itemName"],
        "selected_item_code": selected["itemCode"],
        "selection_reason": reason,
        "image_url": image_url,
        "parent_post": parent,
        "reply_post": child_final,
        "topic": "未設定（Threads実投稿接続時に追加）",
        "publish": False,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="楽天Threads自動投稿システム")
    parser.add_argument(
        "--mode",
        choices=["preview", "full-preview"],
        default="preview",
        help="安全のため現在はプレビューのみ。Threadsには投稿しません。",
    )
    args = parser.parse_args()
    if args.mode == "preview":
        preview_candidates()
    elif args.mode == "full-preview":
        preview_full_post()


if __name__ == "__main__":
    main()
