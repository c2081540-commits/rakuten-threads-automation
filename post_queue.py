import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

QUEUE_PATH = Path(__file__).with_name("queue.json")
JST = timezone(timedelta(hours=9))
SLOT_GRACE_MINUTES = 14


def load_queue():
    if not QUEUE_PATH.exists():
        return {"posts": []}
    with QUEUE_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("posts", [])
    return data


def save_queue(data):
    tmp = QUEUE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(QUEUE_PATH)


def _parse_scheduled(post):
    value = str(post.get("scheduled_at", "")).strip()
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def stock_count():
    now = datetime.now(JST)
    return sum(1 for p in load_queue()["posts"] if (_parse_scheduled(p) or now) >= now)


def append_posts(posts):
    data = load_queue()
    data["posts"].extend(posts)
    data["posts"].sort(key=lambda p: str(p.get("scheduled_at", "")))
    save_queue(data)
    return len(data["posts"])


def replace_slots(posts):
    data = load_queue()
    replacement_slots = {str(p.get("scheduled_at", "")) for p in posts if p.get("scheduled_at")}
    removed = [p for p in data["posts"] if str(p.get("scheduled_at", "")) in replacement_slots]
    data["posts"] = [p for p in data["posts"] if str(p.get("scheduled_at", "")) not in replacement_slots]
    data["posts"].extend(posts)
    data["posts"].sort(key=lambda p: str(p.get("scheduled_at", "")))
    save_queue(data)
    return len(data["posts"]), len(removed)


def update_post_progress(post_id, scheduled_at, thread_id=None, reply_id=None):
    data = load_queue()
    for post in data["posts"]:
        if post.get("post_id") == post_id and post.get("scheduled_at") == scheduled_at:
            if thread_id:
                post["published_thread_id"] = thread_id
                post["status"] = "parent_published"
            if reply_id:
                post["published_reply_id"] = reply_id
                post["status"] = "published"
            post["publish_progress_at"] = datetime.now(JST).isoformat(timespec="seconds")
            save_queue(data)
            return dict(post)
    raise RuntimeError("queue内の投稿進捗更新対象が見つかりません。")


def expire_missed(now=None):
    now = now or datetime.now(JST)
    data = load_queue()
    kept = []
    expired = []
    for post in data["posts"]:
        scheduled = _parse_scheduled(post)
        # 親投稿まで成功している商品は、返信リトライ対象なので期限切れにしない。
        if post.get("published_thread_id") and not post.get("published_reply_id"):
            kept.append(post)
            continue
        if scheduled and now > scheduled + timedelta(minutes=SLOT_GRACE_MINUTES):
            row = dict(post)
            row["status"] = "expired"
            row["expired_at"] = now.isoformat(timespec="seconds")
            expired.append(row)
        else:
            kept.append(post)
    if expired:
        data["posts"] = kept
        data.setdefault("expired", []).extend(expired)
        save_queue(data)
    return expired


def get_due_post(now=None):
    now = now or datetime.now(JST)
    expire_missed(now)
    data = load_queue()
    # 部分成功中の投稿は、二重親投稿を避けて返信だけを優先再試行する。
    for post in data["posts"]:
        if post.get("published_thread_id") and not post.get("published_reply_id"):
            return post
    for post in data["posts"]:
        scheduled = _parse_scheduled(post)
        if scheduled and scheduled <= now <= scheduled + timedelta(minutes=SLOT_GRACE_MINUTES):
            return post
    return None


def get_post(post_id=None, scheduled_at=None):
    for post in load_queue()["posts"]:
        if post_id and post.get("post_id") == post_id:
            return post
        if scheduled_at and post.get("scheduled_at") == scheduled_at:
            return post
    return None


def remove_post(post_id, scheduled_at):
    data = load_queue()
    for index, post in enumerate(data["posts"]):
        if post.get("post_id") == post_id and post.get("scheduled_at") == scheduled_at:
            removed = data["posts"].pop(index)
            save_queue(data)
            return removed
    return None
