import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

HISTORY_PATH = Path(__file__).with_name("history.json")
JST = timezone(timedelta(hours=9))


def load_history():
    if not HISTORY_PATH.exists():
        return {"product_history": [], "empathy_history": []}
    with HISTORY_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("product_history", [])
    data.setdefault("empathy_history", [])
    return data


def save_history(data):
    tmp = HISTORY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(HISTORY_PATH)


def record_success(post, thread_id, reply_id=None):
    data = load_history()
    entry = dict(post)
    entry["posted_at"] = datetime.now(JST).isoformat()
    entry["thread_id"] = thread_id
    if reply_id:
        entry["reply_id"] = reply_id
    key = "product_history" if post.get("type") == "product" else "empathy_history"
    data[key].append(entry)
    save_history(data)


def recent_product_codes(days=30):
    history = load_history()["product_history"]
    cutoff = datetime.now(JST) - timedelta(days=days)
    codes = set()
    for entry in history:
        code = entry.get("item_code")
        raw_date = entry.get("posted_at")
        if not code or not raw_date:
            continue
        try:
            posted = datetime.fromisoformat(raw_date)
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=JST)
            if posted >= cutoff:
                codes.add(code)
        except ValueError:
            continue
    return codes


def recent_texts(kind, limit=5):
    key = "product_history" if kind == "product" else "empathy_history"
    entries = load_history()[key][-limit:]
    return [entry.get("parent_text", "") for entry in entries if entry.get("parent_text")]


def recent_entries(limit=20):
    data = load_history()
    combined = []
    for kind, key in (("product", "product_history"), ("empathy", "empathy_history")):
        for entry in data[key]:
            row = dict(entry)
            row["type"] = kind
            combined.append(row)
    combined.sort(key=lambda x: x.get("posted_at", ""))
    return combined[-limit:]
