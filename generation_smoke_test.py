import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gemini import generate_mixed_stock, get_openai_request_count, reset_openai_request_count
from history import product_performance_feedback, recent_entries

JST = timezone(timedelta(hours=9))
CANDIDATES_PATH = Path("data/candidates/latest.json")
QUEUE_PATH = Path("queue.json")
OUTPUT_PATH = Path("data/tests/latest_generation_smoke.json")


def main():
    candidates = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    products = candidates.get("products", [])
    if len(products) < 2:
        raise RuntimeError("候補商品が2件未満です。")

    queue = json.loads(QUEUE_PATH.read_text(encoding="utf-8")) if QUEUE_PATH.exists() else []
    if isinstance(queue, dict):
        queued = queue.get("posts", queue.get("queue", []))
    else:
        queued = queue

    target_date = (datetime.now(JST) + timedelta(days=1)).date()
    reset_openai_request_count()
    posts = generate_mixed_stock(
        products,
        recent_history=recent_entries(limit=30),
        existing_queue=queued,
        events=None,
        target_date=target_date,
    )

    if len(posts) != 5:
        raise RuntimeError(f"生成件数が5件ではありません: {len(posts)}")
    product_posts = [p for p in posts if p.get("type") == "product"]
    empathy_posts = [p for p in posts if p.get("type") == "empathy"]
    if len(product_posts) != 2 or len(empathy_posts) != 3:
        raise RuntimeError("共感3・商品2の構成になっていません。")
    for post in product_posts:
        for key in ("problem_axis", "benefit_axis", "sales_structure"):
            if not str(post.get(key, "")).strip():
                raise RuntimeError(f"{post.get('post_id')} の {key} が空です。")

    payload = {
        "tested_at": datetime.now(JST).isoformat(timespec="seconds"),
        "target_date": target_date.isoformat(),
        "openai_request_count": get_openai_request_count(),
        "performance_feedback": product_performance_feedback(days=60, min_samples=3),
        "posts": posts,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
