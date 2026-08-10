import os
import time
import requests

BASE = "https://graph.threads.net/v1.0"


def _credentials():
    user_id = os.environ.get("THREADS_USER_ID", "").strip()
    token = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
    if not user_id or not token:
        raise RuntimeError("THREADS_USER_ID / THREADS_ACCESS_TOKEN が未設定です。")
    return user_id, token


def _check(resp, label):
    if not resp.ok:
        raise RuntimeError(f"Threads {label} failed: HTTP {resp.status_code} {resp.text[:500]}")
    data = resp.json()
    if not data.get("id"):
        raise RuntimeError(f"Threads {label} response has no id: {data}")
    return data["id"]


def create_text_container(text, reply_to_id=None):
    user_id, token = _credentials()
    data = {"media_type": "TEXT", "text": text, "access_token": token}
    if reply_to_id:
        data["reply_to_id"] = reply_to_id
    resp = requests.post(f"{BASE}/{user_id}/threads", data=data, timeout=30)
    return _check(resp, "container create")


def create_image_container(text, image_url):
    user_id, token = _credentials()
    data = {"media_type": "IMAGE", "image_url": image_url, "text": text, "access_token": token}
    resp = requests.post(f"{BASE}/{user_id}/threads", data=data, timeout=30)
    return _check(resp, "image container create")


def publish_container(container_id):
    user_id, token = _credentials()
    resp = requests.post(f"{BASE}/{user_id}/threads_publish", data={"creation_id": container_id, "access_token": token}, timeout=30)
    return _check(resp, "publish")


def publish_post(post):
    if post.get("type") == "product":
        container = create_image_container(post["parent_text"], post["image_url"])
    else:
        container = create_text_container(post["parent_text"])

    # Meta側のコンテナ準備に短い待機を入れる。失敗時の自動リトライはしない。
    time.sleep(3)
    thread_id = publish_container(container)

    reply_id = None
    if post.get("type") == "product":
        # 商品投稿は、親=短い一言+商品画像、返信=リンク+pr のみ。
        # 商品説明・価格・評価・レビュー件数・定型PR文は返信に追加しない。
        reply = f"{post['affiliate_url']} pr"
        reply_container = create_text_container(reply, reply_to_id=thread_id)
        time.sleep(3)
        reply_id = publish_container(reply_container)
    return thread_id, reply_id
