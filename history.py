import json
from collections import defaultdict
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


def update_post_insights(thread_id, metrics, collected_at=None):
    """Attach a latest Threads insight snapshot to a published history entry."""
    data = load_history()
    snapshot = {
        "collected_at": collected_at or datetime.now(JST).isoformat(timespec="seconds"),
        "metrics": {str(k): int(v or 0) for k, v in (metrics or {}).items()},
    }
    updated = False
    for key in ("product_history", "empathy_history"):
        for entry in data[key]:
            if str(entry.get("thread_id", "")) != str(thread_id):
                continue
            entry["insights"] = snapshot
            updated = True
            break
        if updated:
            break
    if updated:
        save_history(data)
    return updated


def _recent_product_entries(days=30):
    history = load_history()["product_history"]
    cutoff = datetime.now(JST) - timedelta(days=days)
    recent = []
    for entry in history:
        raw_date = entry.get("posted_at")
        if not raw_date:
            continue
        try:
            posted = datetime.fromisoformat(raw_date)
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=JST)
            if posted >= cutoff:
                recent.append(entry)
        except ValueError:
            continue
    return recent


def recent_product_codes(days=30):
    codes = set()
    for entry in _recent_product_entries(days=days):
        code = entry.get("selected_item_code") or entry.get("item_code")
        if code:
            codes.add(code)
    return codes


def recent_product_axes(days=30):
    axes = {
        "problem_axis": set(),
        "benefit_axis": set(),
        "sales_structure": set(),
    }
    for entry in _recent_product_entries(days=days):
        for key in axes:
            value = str(entry.get(key, "")).strip()
            if value:
                axes[key].add(value)
    return {key: sorted(values) for key, values in axes.items()}


def recent_product_strategy_entries(days=30, limit=30):
    rows = []
    for entry in _recent_product_entries(days=days)[-limit:]:
        insights = entry.get("insights") or {}
        rows.append({
            "selected_item_code": entry.get("selected_item_code") or entry.get("item_code", ""),
            "parent_text": entry.get("parent_text", ""),
            "problem_axis": entry.get("problem_axis", ""),
            "benefit_axis": entry.get("benefit_axis", ""),
            "sales_structure": entry.get("sales_structure", ""),
            "scheduled_hour": entry.get("scheduled_hour"),
            "posted_at": entry.get("posted_at", ""),
            "insights": insights.get("metrics", {}),
        })
    return rows


def _engagement_score(metrics):
    """Conservative interaction score; views remain a separate reach signal."""
    return (
        int(metrics.get("likes", 0) or 0)
        + int(metrics.get("replies", 0) or 0) * 2
        + int(metrics.get("reposts", 0) or 0) * 3
        + int(metrics.get("quotes", 0) or 0) * 3
        + int(metrics.get("shares", 0) or 0) * 3
    )


def product_performance_feedback(days=60, min_samples=3, limit=12):
    """Return only sufficiently sampled product-performance tendencies.

    The result is deliberately advisory. A dimension/value is omitted until it
    has at least min_samples posts carrying both the metadata and Insights.
    This prevents one lucky or unlucky post from steering generation.
    """
    dimensions = ("problem_axis", "benefit_axis", "sales_structure", "scheduled_hour")
    grouped = {dimension: defaultdict(list) for dimension in dimensions}
    eligible_posts = 0

    for entry in _recent_product_entries(days=days):
        metrics = ((entry.get("insights") or {}).get("metrics") or {})
        if not metrics:
            continue
        eligible_posts += 1
        views = int(metrics.get("views", 0) or 0)
        interactions = _engagement_score(metrics)
        for dimension in dimensions:
            value = entry.get(dimension)
            if value in (None, ""):
                continue
            grouped[dimension][str(value)].append((views, interactions))

    tendencies = []
    for dimension, values in grouped.items():
        for value, samples in values.items():
            if len(samples) < min_samples:
                continue
            total_views = sum(row[0] for row in samples)
            total_interactions = sum(row[1] for row in samples)
            tendencies.append({
                "dimension": dimension,
                "value": value,
                "samples": len(samples),
                "average_views": round(total_views / len(samples), 1),
                "average_interaction_score": round(total_interactions / len(samples), 2),
                "interaction_per_100_views": round((total_interactions / total_views * 100), 2) if total_views else 0.0,
            })

    tendencies.sort(
        key=lambda row: (row["samples"], row["interaction_per_100_views"], row["average_views"]),
        reverse=True,
    )
    return {
        "days": days,
        "minimum_samples": min_samples,
        "eligible_posts_with_insights": eligible_posts,
        "ready": bool(tendencies),
        "note": "サンプル不足の軸は評価に使わない。傾向は生成時の補助情報であり強制ルールではない。",
        "tendencies": tendencies[:limit],
    }


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
