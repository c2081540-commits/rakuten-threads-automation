import json

from history import record_success
from post_queue import load_queue, remove_first, stock_count
from threads import publish_post


def publish_next():
    data = load_queue()
    posts = data.get("posts", [])
    if not posts:
        print(json.dumps({"status": "skip", "reason": "queue empty"}, ensure_ascii=False))
        return False

    post = posts[0]
    # 重要: 投稿成功前にはキューを削除しない。失敗時はそのまま残す。
    thread_id, reply_id = publish_post(post)
    removed = remove_first()
    if not removed:
        raise RuntimeError("投稿成功後のqueue削除に失敗しました。")
    record_success(post, thread_id, reply_id)
    print(json.dumps({"status": "published", "type": post.get("type"), "thread_id": thread_id, "reply_id": reply_id, "remaining": stock_count()}, ensure_ascii=False))
    return True


if __name__ == "__main__":
    publish_next()
