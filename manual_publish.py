import argparse
import json

from history import record_success
from post_queue import load_queue, remove_post
from threads import publish_post


def find_queued_post(target_date, hour):
    prefix = f"{target_date}T{int(hour):02d}:00"
    matches = [
        post for post in load_queue().get("posts", [])
        if str(post.get("scheduled_at", "")).startswith(prefix)
    ]
    if len(matches) > 1:
        raise RuntimeError(f"同じ日時の未投稿データが複数あります: {target_date} {hour}:00")
    return matches[0] if matches else None


def find_history_post(target_date, hour):
    prefix = f"{target_date}T{int(hour):02d}:00"
    try:
        with open("history.json", "r", encoding="utf-8") as f:
            history = json.load(f)
    except FileNotFoundError:
        return None

    matches = []
    for key in ("product_history", "empathy_history"):
        for post in history.get(key, []):
            if str(post.get("scheduled_at", "")).startswith(prefix):
                matches.append(post)

    if len(matches) > 1:
        raise RuntimeError(f"同じ日時の投稿履歴が複数あります: {target_date} {hour}:00")
    return matches[0] if matches else None


def main():
    parser = argparse.ArgumentParser(description="楽天Threads 障害復旧・再投稿用手動投稿")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--hour", required=True, type=int, choices=[7, 12, 15, 18, 21])
    args = parser.parse_args()

    # まず未投稿queueを探す。無ければ投稿済みhistoryを自動で探す。
    # これにより、同じ日付・時刻指定だけで障害復旧と再投稿の両方に対応する。
    post = find_queued_post(args.date, args.hour)
    source = "queue"

    if not post:
        post = find_history_post(args.date, args.hour)
        source = "history"

    if not post:
        raise RuntimeError(f"queue/historyのどちらにも対象データがありません: {args.date} {args.hour}:00")

    # 常に現在の投稿形式で投稿する。
    # 商品投稿は 親=短文+画像、返信=アフィリエイトURL + pr のみ。
    thread_id, reply_id = publish_post(post)

    # queue由来の未投稿だけ、通常どおりqueueから削除してhistoryへ記録する。
    # history由来の再投稿は元履歴を変更しない。
    if source == "queue":
        removed = remove_post(post.get("post_id"), post.get("scheduled_at"))
        if not removed:
            raise RuntimeError("投稿成功後のqueue削除に失敗しました。")
        record_success(post, thread_id, reply_id)

    print(json.dumps({
        "status": "manual_republished" if source == "history" else "manual_published",
        "source": source,
        "post_id": post.get("post_id"),
        "scheduled_at": post.get("scheduled_at"),
        "thread_id": thread_id,
        "reply_id": reply_id,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
