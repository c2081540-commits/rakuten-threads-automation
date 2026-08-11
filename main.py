import argparse
import json
from datetime import datetime, timedelta, timezone

from gemini import generate_product_copy, generate_sample_batch, generate_mixed_stock, select_product
from history import recent_entries, recent_texts
from post_queue import load_queue, replace_slots, stock_count
from rakuten import fetch_candidate_pool
from rakuten_events import get_active_rakuten_events
from selector import build_shortlist, primary_filter

JST = timezone(timedelta(hours=9))
DAILY_HOURS = [7, 12, 15, 18, 21]
MAX_PRODUCT_IMAGES = 3


def get_shortlist(limit=10, excluded_item_codes=None):
    raw_items = fetch_candidate_pool(target_raw=50)
    filtered = primary_filter(raw_items, excluded_item_codes=excluded_item_codes)
    if len(filtered) < 5:
        filtered = primary_filter(raw_items, max_price=8000, excluded_item_codes=excluded_item_codes)
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
        safe_output.append({"rank": index, "itemCode": item["itemCode"], "itemName": item["itemName"], "price": item["itemPrice"], "rating": item["reviewAverage"], "reviews": item["reviewCount"], "shopName": item["shopName"], "imageCount": len(item["imageUrls"]), "images": item["imageUrls"][:MAX_PRODUCT_IMAGES]})
    print(json.dumps(safe_output, ensure_ascii=False, indent=2))


def assemble_preview(selected, reason, parent, child_base="", image_index=0, events=None):
    image_urls = selected["imageUrls"][:MAX_PRODUCT_IMAGES]
    child_final = f"{child_base}\n\n【PR】\n{selected['affiliateUrl']}" if child_base else "ERROR: child_text_base is empty"
    return {"selected_item": selected["itemName"], "selected_item_code": selected["itemCode"], "selection_reason": reason, "image_urls": image_urls, "parent_post": parent, "reply_post": child_final, "active_rakuten_events": [e["name"] for e in (events or [])], "topic": "未設定（Threads実投稿接続時に追加）", "publish": False}


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
        preview = assemble_preview(selected, generated.get("reason", ""), str(generated["parent_text"]).strip(), str(generated.get("child_text_base", "")).strip(), events=events)
        preview["sample"] = sample_no
        outputs.append(preview)
    print(json.dumps({"sample_count": len(outputs), "openai_requests": 1, "active_rakuten_events": [e["name"] for e in events], "samples": outputs}, ensure_ascii=False, indent=2))


def arrange_stock_posts(posts):
    if len(posts) != 5:
        raise RuntimeError(f"投稿順を構成できません: total={len(posts)}")
    empathy_count = sum(1 for p in posts if p.get("type") == "empathy")
    product_count = sum(1 for p in posts if p.get("type") == "product")
    if empathy_count != 3 or product_count != 2:
        raise RuntimeError(f"投稿比率が不正です: empathy={empathy_count}, product={product_count}")
    return posts


def _batch_start_date(queue_posts):
    scheduled = []
    for post in queue_posts:
        value = str(post.get("scheduled_at", "")).strip()
        if value:
            try:
                scheduled.append(datetime.fromisoformat(value).astimezone(JST))
            except ValueError:
                pass
    if scheduled:
        return max(scheduled).date() + timedelta(days=1)
    now = datetime.now(JST)
    return now.date() if now.hour < DAILY_HOURS[0] else now.date() + timedelta(days=1)


def _schedule_for_day(target_date, hours=None):
    return [datetime(target_date.year, target_date.month, target_date.day, hour, 0, tzinfo=JST) for hour in (hours or DAILY_HOURS)]


def _complete_product_row(row, item):
    child = str(row.get("child_text_base", "")).strip()
    if not child:
        raise RuntimeError(f"商品補足文が空です: {row.get('post_id')}")
    image_urls = item["imageUrls"][:MAX_PRODUCT_IMAGES]
    if not image_urls:
        raise RuntimeError(f"商品画像がありません: {item['itemCode']}")
    row.update({"item_name": item["itemName"], "item_code": item["itemCode"], "image_url": image_urls[0], "image_urls": image_urls, "affiliate_url": item["affiliateUrl"], "price": item["itemPrice"], "rating": item["reviewAverage"], "review_count": item["reviewCount"]})
    return row


def build_today_remaining(save=False, now=None):
    now = (now or datetime.now(JST)).astimezone(JST)
    is_next_day = now.hour >= 21
    target_date = now.date() + timedelta(days=1) if is_next_day else now.date()
    target_hours = DAILY_HOURS if is_next_day else [h for h in DAILY_HOURS if h > now.hour]
    queue_data = load_queue()["posts"]
    # 置換対象の枠だけを生成時の重複比較から外す。他の日・枠の商品は候補から除外する。
    generation_queue = []
    for p in queue_data:
        try:
            dt = datetime.fromisoformat(str(p.get("scheduled_at", ""))).astimezone(JST)
        except (ValueError, TypeError):
            generation_queue.append(p)
            continue
        if dt.date() == target_date and dt.hour in target_hours:
            continue
        generation_queue.append(p)

    queued_product_codes = {p.get("item_code") or p.get("selected_item_code") for p in generation_queue if p.get("type") == "product"}
    queued_product_codes.discard(None)
    _, _, shortlist = get_shortlist(limit=10, excluded_item_codes=queued_product_codes)
    events = get_active_rakuten_events()
    history = recent_entries(limit=20)
    generated = arrange_stock_posts(generate_mixed_stock(shortlist, recent_history=history, existing_queue=generation_queue, events=events))
    by_code = {x["itemCode"]: x for x in shortlist}

    slot_by_hour = dict(zip(DAILY_HOURS, generated))
    completed = []
    for sequence, hour in enumerate(target_hours, start=1):
        post = dict(slot_by_hour[hour])
        scheduled = datetime(target_date.year, target_date.month, target_date.day, hour, 0, tzinfo=JST)
        post["post_id"] = f"DAY-{target_date.strftime('%Y%m%d')}-{hour:02d}"
        post["stock_sequence"] = sequence
        post["slot_in_day"] = DAILY_HOURS.index(hour) + 1
        post["scheduled_at"] = scheduled.isoformat(timespec="minutes")
        post["scheduled_hour"] = hour
        post["status"] = "scheduled"
        if post["type"] == "product":
            post = _complete_product_row(post, by_code[post["selected_item_code"]])
        completed.append(post)

    if save:
        total, replaced = replace_slots(completed)
        status = {"status": "saved", "added": len(completed), "replaced": replaced, "stock_count": total}
    else:
        existing_slots = []
        for p in queue_data:
            try:
                dt = datetime.fromisoformat(str(p.get("scheduled_at", ""))).astimezone(JST)
            except (ValueError, TypeError):
                continue
            if dt.date() == target_date and dt.hour in target_hours:
                existing_slots.append(dt.hour)
        status = {"status": "preview", "added": 0, "would_replace": len(existing_slots), "stock_count": stock_count()}
    print(json.dumps({**status, "mode": "next-day-stock" if is_next_day else "today-replace", "run_date": now.date().isoformat(), "target_date": target_date.isoformat(), "target_hours": target_hours, "openai_requests": 1, "ratio": {"empathy": 3, "product": 2}, "active_rakuten_events": [e["name"] for e in events], "posts": completed}, ensure_ascii=False, indent=2))


def build_stock(save=False, force=False):
    # 旧stock系モードも閾値補充を行わず、実行日の翌日5枠を作る。
    now = datetime.now(JST).replace(hour=21, minute=0, second=0, microsecond=0)
    build_today_remaining(save=save, now=now)


def main():
    parser = argparse.ArgumentParser(description="楽天Threads自動投稿システム")
    parser.add_argument("--mode", choices=["preview", "full-preview", "samples", "events", "stock-preview", "stock-refill", "today-preview", "today-refill"], default="preview")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.mode == "preview": preview_candidates()
    elif args.mode == "full-preview": preview_full_post()
    elif args.mode == "samples": preview_samples(count=max(1, min(args.count, 10)))
    elif args.mode == "events": preview_events()
    elif args.mode == "stock-preview": build_stock(save=False, force=True)
    elif args.mode == "stock-refill": build_stock(save=True, force=args.force)
    elif args.mode == "today-preview": build_today_remaining(save=False)
    elif args.mode == "today-refill": build_today_remaining(save=True)


if __name__ == "__main__":
    main()
