import argparse
import json

from history import record_success
from post_queue import load_queue, remove_post
from threads import publish_post


def find_post(post_id):
    for post in load_queue().get("posts", []):
        if str(post.get("post_id", "")) == post_id:
            return post
    return None


def main():
    parser = argparse.ArgumentParser(description="楽天Threads 障害復旧用手動投稿")
    parser.add_argument("--post-id", required=True)
    args = parser.parse_args()

    post = find_post(args.post_id)
    if not post:
        raise RuntimeError(f"queue内に未投稿のpost_idがありません: {args.post_id}")

    # 通常の時刻/期限判定を通さず、指定した未投稿1件だけを投稿する。
    # queueに残っていること自体を二重投稿防止条件にする。
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
