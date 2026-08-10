import argparse
import json

from history import record_success
from post_queue import expire_missed, get_due_post, get_post, remove_post, stock_count, update_post_progress
from threads import publish_post, validate_post


def _publish_selected(post, mode):
    if not post:
        raise RuntimeError("投稿対象が見つかりません。")
    validate_post(post)
    post_id = post.get("post_id")
    scheduled_at = post.get("scheduled_at")
    if not post_id or not scheduled_at:
        raise RuntimeError("投稿対象に post_id / scheduled_at がありません。")

    existing_thread_id = post.get("published_thread_id")
    existing_reply_id = post.get("published_reply_id")

    def progress_callback(thread_id=None, reply_id=None):
        update_post_progress(post_id, scheduled_at, thread_id=thread_id, reply_id=reply_id)

    thread_id, reply_id = publish_post(
        post,
        existing_thread_id=existing_thread_id,
        existing_reply_id=existing_reply_id,
        progress_callback=progress_callback,
    )
    removed = remove_post(post_id, scheduled_at)
    if not removed:
        raise RuntimeError("投稿成功後のqueue削除に失敗しました。")
    final_post = dict(removed)
    final_post.pop("published_thread_id", None)
    final_post.pop("published_reply_id", None)
    final_post.pop("publish_progress_at", None)
    final_post["status"] = "published"
    record_success(final_post, thread_id, reply_id)
    print(json.dumps({"status": "published", "mode": mode, "post_id": post_id, "scheduled_at": scheduled_at, "type": post.get("type"), "thread_id": thread_id, "reply_id": reply_id, "remaining": stock_count()}, ensure_ascii=False))
    return True


def publish_due():
    expired = expire_missed()
    post = get_due_post()
    if not post:
        print(json.dumps({"status": "skip", "reason": "no post due in current slot", "expired_now": len(expired), "remaining": stock_count()}, ensure_ascii=False))
        return False
    return _publish_selected(post, "scheduled")


def publish_manual(post_id=None, scheduled_at=None):
    if not post_id and not scheduled_at:
        raise RuntimeError("manual publish には --post-id または --scheduled-at が必要です。")
    post = get_post(post_id=post_id, scheduled_at=scheduled_at)
    return _publish_selected(post, "manual")


def main():
    parser = argparse.ArgumentParser(description="Threads publisher")
    parser.add_argument("--post-id", default="")
    parser.add_argument("--scheduled-at", default="")
    args = parser.parse_args()
    if args.post_id or args.scheduled_at:
        publish_manual(post_id=args.post_id or None, scheduled_at=args.scheduled_at or None)
    else:
        publish_due()


if __name__ == "__main__":
    main()
