import argparse
import json

from gemini import generate_product_copy, generate_sample_batch, generate_mixed_stock, select_product
from history import recent_entries, recent_texts
from post_queue import append_posts, load_queue, stock_count
from rakuten import fetch_candidate_pool
from rakuten_events import get_active_rakuten_events
from selector import build_shortlist, primary_filter

STOCK_TARGET = 10
REFILL_THRESHOLD = 3


def get_shortlist(limit=10):
    raw_items = fetch_candidate_pool(target_raw=50)
    filtered = primary_filter(raw_items)
    if len(filtered) < 5:
        filtered = primary_filter(raw_items, max_price=8000)
    shortlist = build_shortlist(filtered, limit=limit)
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


def assemble_preview(selected, reason, parent, child_base, image_index=0, events=None):
    image_url = selected["imageUrls"][image_index]
    child_final = f"{child_base}\n\n★{selected['reviewAverage']}（レビュー {selected['reviewCount']:,}件）\n価格: {selected['itemPrice']:,}円\n\n【PR】詳細はこちら\n{selected['affiliateUrl']}"
    return {"selected_item": selected["itemName"], "selected_item_code": selected["itemCode"], "selection_reason": reason, "image_url": image_url, "parent_post": parent, "reply_post": child_final, "active_rakuten_events": [e["name"] for e in (events or [])], "topic": "未設定（Threads実投稿接続時に追加）", "publish": False}


def preview_events():
    events = get_active_rakuten_events()
    print(json.dumps({"active_rakuten_events": events, "note": "楽天市場公式で現在開催中と確認できたイベントのみ。"}, ensure_ascii=False, indent=2))


def preview_full_post():
    _, _, shortlist = get_shortlist()
    events = get_active_rakuten_events()
    selected, reason, image_index = select_product(shortlist)
    parent, child_base = generate_product_copy(selected, recent_posts=recent_texts("product", limit=5), events=events)
    print(json.dumps(assemble_preview(selected, reason, parent, child_base, image_index, events), ensure_ascii=False, indent=2))


def preview_samples(count=5):
    _, _, shortlist = get_shortlist()
    events = get_active_rakuten_events()
    count = max(1, min(count, len(shortlist)))
    batch = generate_sample_batch(shortlist, count=count, recent_posts=recent_texts("product", limit=5), events=events)
    by_code = {x["itemCode"]: x for x in shortlist}
    outputs = []
    for sample_no, generated in enumerate(batch, start=1):
        selected = by_code[generated["selected_item_code"]]
        preview = assemble_preview(selected, generated.get("reason", ""), str(generated["parent_text"]).strip(), str(generated["child_text_base"]).strip(), events=events)
        preview["sample"] = sample_no
        outputs.append(preview)
    print(json.dumps({"sample_count": len(outputs), "openai_requests": 1, "active_rakuten_events": [e["name"] for e in events], "samples": outputs}, ensure_ascii=False, indent=2))


def build_stock(save=False, force=False):
    current = stock_count()
    if not force and current > REFILL_THRESHOLD:
        print(json.dumps({"status": "skip", "stock_count": current, "threshold": REFILL_THRESHOLD, "reason": "ストックが十分あるためOpenAIを呼びません。"}, ensure_ascii=False, indent=2))
        return
    _, _, shortlist = get_shortlist(limit=10)
    events = get_active_rakuten_events()
    queue_data = load_queue()["posts"]
    history = recent_entries(limit=20)
    posts = generate_mixed_stock(shortlist, recent_history=history, existing_queue=queue_data, events=events)
    by_code = {x["itemCode"]: x for x in shortlist}
    completed = []
    for post in posts:
        row = dict(post)
        if row["type"] == "product":
            item = by_code[row["selected_item_code"]]
            row.update({"item_name": item["itemName"], "item_code": item["itemCode"], "image_url": item["imageUrls"][0], "affiliate_url": item["affiliateUrl"], "price": item["itemPrice"], "rating": item["reviewAverage"], "review_count": item["reviewCount"]})
        completed.append(row)
    if save:
        total = append_posts(completed)
        status = {"status": "saved", "added": len(completed), "stock_count": total}
    else:
        status = {"status": "preview", "added": 0, "stock_count": current}
    print(json.dumps({**status, "openai_requests": 1, "ratio": {"empathy": 6, "product": 4}, "active_rakuten_events": [e["name"] for e in events], "posts": completed}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="楽天Threads自動投稿システム")
    parser.add_argument("--mode", choices=["preview", "full-preview", "samples", "events", "stock-preview", "stock-refill"], default="preview")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.mode == "preview": preview_candidates()
    elif args.mode == "full-preview": preview_full_post()
    elif args.mode == "samples": preview_samples(count=max(1, min(args.count, 10)))
    elif args.mode == "events": preview_events()
    elif args.mode == "stock-preview": build_stock(save=False, force=True)
    elif args.mode == "stock-refill": build_stock(save=True, force=args.force)


if __name__ == "__main__":
    main()
