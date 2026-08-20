import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import requests

from history import load_history, update_post_insights

BASE = "https://graph.threads.net/v1.0"
JST = timezone(timedelta(hours=9))
POST_METRICS = ("views", "likes", "replies", "reposts", "quotes", "shares")


def _token():
    token = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("THREADS_ACCESS_TOKEN が未設定です。")
    return token


def _metric_value(row):
    if "total_value" in row and isinstance(row["total_value"], dict):
        return int(row["total_value"].get("value") or 0)
    values = row.get("values") or []
    if values and isinstance(values[-1], dict):
        return int(values[-1].get("value") or 0)
    return 0


def fetch_post_insights(thread_id, timeout=30):
    response = requests.get(
        f"{BASE}/{thread_id}/insights",
        params={
            "metric": ",".join(POST_METRICS),
            "access_token": _token(),
        },
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(
            f"Threads insights failed for {thread_id}: "
            f"HTTP {response.status_code} {response.text[:500]}"
        )
    data = response.json().get("data", [])
    metrics = {name: 0 for name in POST_METRICS}
    for row in data:
        name = str(row.get("name", ""))
        if name in metrics:
            metrics[name] = _metric_value(row)
    return metrics


def _published_entries(days=30, limit=200):
    data = load_history()
    cutoff = datetime.now(JST) - timedelta(days=days)
    rows = []
    for kind, key in (("product", "product_history"), ("empathy", "empathy_history")):
        for entry in data.get(key, []):
            thread_id = str(entry.get("thread_id", "")).strip()
            raw_date = str(entry.get("posted_at", "")).strip()
            if not thread_id or not raw_date:
                continue
            try:
                posted = datetime.fromisoformat(raw_date)
                if posted.tzinfo is None:
                    posted = posted.replace(tzinfo=JST)
            except ValueError:
                continue
            if posted < cutoff:
                continue
            rows.append((posted, kind, thread_id, entry))
    rows.sort(key=lambda x: x[0])
    return rows[-limit:]


def collect(days=30, limit=200, continue_on_error=True):
    entries = _published_entries(days=days, limit=limit)
    results = []
    failures = []
    collected_at = datetime.now(JST).isoformat(timespec="seconds")

    for _, kind, thread_id, entry in entries:
        try:
            metrics = fetch_post_insights(thread_id)
            update_post_insights(thread_id, metrics, collected_at=collected_at)
            results.append({
                "thread_id": thread_id,
                "type": kind,
                "post_id": entry.get("post_id", ""),
                "metrics": metrics,
            })
        except RuntimeError as exc:
            failures.append({"thread_id": thread_id, "error": str(exc)})
            if not continue_on_error:
                raise

    summary = {
        "status": "ok" if not failures else "partial",
        "collected_at": collected_at,
        "requested": len(entries),
        "updated": len(results),
        "failed": len(failures),
        "metrics": list(POST_METRICS),
        "failures": failures[:10],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def main():
    parser = argparse.ArgumentParser(description="Collect Threads post insights into history.json")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    if args.days < 1 or args.limit < 1:
        raise ValueError("--days / --limit は1以上にしてください。")
    collect(days=args.days, limit=args.limit, continue_on_error=not args.strict)


if __name__ == "__main__":
    main()
