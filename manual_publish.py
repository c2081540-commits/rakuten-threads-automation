import argparse
import json

from history import record_success
from post_queue import load_queue, remove_post
from threads import publish_post


def find_post(target_date, hour):
    prefix = f"{target_date}T{int(hour):02d}:00"
    matches = [
        post for post in load_queue().get("posts", [])
        if str(post.get("scheduled_at", "")).startswith(prefix)
    ]
    if len(matches) > 1:
        raise RuntimeError(f"同じ日時の未投稿データが複数あります: {target_date} {hour}:00")
    return matches[0] if matches else None


def main():
    parser = argparse.ArgumentParser(description="楽天Threads 障害復旧用手動投稿")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--hour", required=True, type=int, choices=[7, 12, 15, 18, 21])
    args = parser.parse_args()

    post = find_post(args.date, args.hour)
    if not post:
        raise RuntimeError(f"queue内に未投稿データがありません: {args.date} {args.hour}:00")

    # 通常の時刻/期限判定を通さず、指定日時の未投稿1件だけを投稿する。
    thread_id, reply_id = publish_post(post)

    removed = remove_post(post.get("post_id"), post.get("scheduled_at"))
    if not removed:
        raise RuntimeError("投稿成功後のqueue削除に失敗しました。")

    record_success(post, thread_id, reply_id)
    print(json.dumps({
        "status": "manual_published",
        "post_id": post.get("post_id"),
        "scheduled_at": post.get("scheduled_at"),
        "thread_id": thread_id,
        "reply_id": reply_id,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
