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


def expire_missed(now=None):
    now = now or datetime.now(JST)
    data = load_queue()
    kept = []
    expired = []
    for post in data["posts"]:
        scheduled = _parse_scheduled(post)
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
    for post in data["posts"]:
        scheduled = _parse_scheduled(post)
        if not scheduled:
            continue
        if scheduled <= now <= scheduled + timedelta(minutes=SLOT_GRACE_MINUTES):
            return post
    return None


def remove_post(post_id, scheduled_at):
    data = load_queue()
    for index, post in enumerate(data["posts"]):
        if post.get("post_id") == post_id and post.get("scheduled_at") == scheduled_at:
            return data["posts"].pop(index) if not save_queue_and_return(data) else None
    return None


def save_queue_and_return(data):
    save_queue(data)
    return False
