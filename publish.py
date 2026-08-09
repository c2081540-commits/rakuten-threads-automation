import json

from history import record_success
from post_queue import expire_missed, get_due_post, remove_post, stock_count
from threads import publish_post


def publish_due():
    expired = expire_missed()
    post = get_due_post()
    if not post:
        print(json.dumps({"status": "skip", "reason": "no post due in current slot", "expired_now": len(expired), "remaining": stock_count()}, ensure_ascii=False))
        return False

    # 投稿成功前にはキューを削除しない。失敗時は同じ投稿枠内で再試行可能。
    thread_id, reply_id = publish_post(post)
    removed = remove_post(post.get("post_id"), post.get("scheduled_at"))
    if not removed:
        raise RuntimeError("投稿成功後のqueue削除に失敗しました。")
    record_success(post, thread_id, reply_id)
    print(json.dumps({"status": "published", "post_id": post.get("post_id"), "scheduled_at": post.get("scheduled_at"), "type": post.get("type"), "thread_id": thread_id, "reply_id": reply_id, "remaining": stock_count()}, ensure_ascii=False))
    return True


if __name__ == "__main__":
    publish_due()
