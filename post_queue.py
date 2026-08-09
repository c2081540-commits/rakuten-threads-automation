import json
from pathlib import Path

QUEUE_PATH = Path(__file__).with_name("queue.json")


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


def stock_count():
    return len(load_queue()["posts"])


def append_posts(posts):
    data = load_queue()
    data["posts"].extend(posts)
    save_queue(data)
    return len(data["posts"])


def peek_next():
    posts = load_queue()["posts"]
    return posts[0] if posts else None


def remove_first():
    data = load_queue()
    if not data["posts"]:
        return None
    post = data["posts"].pop(0)
    save_queue(data)
    return post
