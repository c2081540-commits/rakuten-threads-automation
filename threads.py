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


def create_carousel_container(text, image_urls):
    user_id, token = _credentials()
    urls = [str(x).strip() for x in image_urls if str(x).strip()]
    if len(urls) < 2:
        return create_image_container(text, urls[0])

    children = []
    for image_url in urls[:3]:
        data = {
            "media_type": "IMAGE",
            "image_url": image_url,
            "is_carousel_item": "true",
            "access_token": token,
        }
        resp = requests.post(f"{BASE}/{user_id}/threads", data=data, timeout=30)
        children.append(_check(resp, "carousel child create"))

    data = {
        "media_type": "CAROUSEL",
        "children": ",".join(children),
        "text": text,
        "access_token": token,
    }
    resp = requests.post(f"{BASE}/{user_id}/threads", data=data, timeout=30)
    return _check(resp, "carousel container create")


def publish_container(container_id):
    user_id, token = _credentials()
    resp = requests.post(f"{BASE}/{user_id}/threads_publish", data={"creation_id": container_id, "access_token": token}, timeout=30)
    return _check(resp, "publish")


def publish_post(post):
    if post.get("type") == "product":
        image_urls = post.get("image_urls") or [post.get("image_url")]
        image_urls = [x for x in image_urls if x]
        if not image_urls:
            raise RuntimeError("商品投稿の画像がありません。")
        container = create_carousel_container(post["parent_text"], image_urls)
    else:
        container = create_text_container(post["parent_text"])

    time.sleep(3)
    thread_id = publish_container(container)

    reply_id = None
    if post.get("type") == "product":
        child = str(post.get("child_text_base", "")).strip()
        if not child:
            raise RuntimeError("商品投稿の child_text_base が空です。生成データを確認してください。")
        reply = f"{child}\n\n【PR】\n{post['affiliate_url']}"
        reply_container = create_text_container(reply, reply_to_id=thread_id)
        time.sleep(3)
        reply_id = publish_container(reply_container)
    return thread_id, reply_id
