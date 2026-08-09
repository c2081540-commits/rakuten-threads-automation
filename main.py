import argparse
import json

from gemini import generate_product_copy, generate_sample_batch, select_product
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
        safe_output.append({"rank": index, "itemCode": item["itemCode"], "itemName": item["itemName"], "price": item["itemPrice"], "rating": item["reviewAverage"], "reviews": item["reviewCount"], "shopName": item["shopName"], "imageCount": len(item["imageUrls"]), "firstImage": item["imageUrls"][0] if item["imageUrls"] else None})
    print(json.dumps(safe_output, ensure_ascii=False, indent=2))


def assemble_preview(selected, reason, parent, child_base, image_index=0):
    image_url = selected["imageUrls"][image_index]
    child_final = f"{child_base}\n\n★{selected['reviewAverage']}（レビュー {selected['reviewCount']:,}件）\n価格: {selected['itemPrice']:,}円\n\n【PR】詳細はこちら\n{selected['affiliateUrl']}"
    return {"selected_item": selected["itemName"], "selected_item_code": selected["itemCode"], "selection_reason": reason, "image_url": image_url, "parent_post": parent, "reply_post": child_final, "topic": "未設定（Threads実投稿接続時に追加）", "publish": False}


def preview_full_post():
    _, _, shortlist = get_shortlist()
    selected, reason, image_index = select_product(shortlist)
    parent, child_base = generate_product_copy(selected, recent_posts=recent_texts("product", limit=5))
    print(json.dumps(assemble_preview(selected, reason, parent, child_base, image_index), ensure_ascii=False, indent=2))


def preview_samples(count=5):
    """複数サンプルを1回のOpenAI API呼び出しで生成。投稿・履歴更新はしない。"""
    _, _, shortlist = get_shortlist()
    count = max(1, min(count, len(shortlist)))
    batch = generate_sample_batch(shortlist, count=count, recent_posts=recent_texts("product", limit=5))
    by_code = {x["itemCode"]: x for x in shortlist}
    outputs = []
    for sample_no, generated in enumerate(batch, start=1):
        selected = by_code[generated["selected_item_code"]]
        preview = assemble_preview(selected, generated.get("reason", ""), str(generated["parent_text"]).strip(), str(generated["child_text_base"]).strip())
        preview["sample"] = sample_no
        outputs.append(preview)
    print(json.dumps({"sample_count": len(outputs), "openai_requests": 1, "note": "品質確認用。Threads投稿・history更新は行いません。", "samples": outputs}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="楽天Threads自動投稿システム")
    parser.add_argument("--mode", choices=["preview", "full-preview", "samples"], default="preview")
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()
    if args.mode == "preview": preview_candidates()
    elif args.mode == "full-preview": preview_full_post()
    elif args.mode == "samples": preview_samples(count=max(1, min(args.count, 10)))


if __name__ == "__main__":
    main()
