import os
import time
import requests

BASE = "https://graph.threads.net/v1.0"
MAX_PRODUCT_IMAGES = 3
CONTAINER_TIMEOUT_SECONDS = 90
CONTAINER_POLL_SECONDS = 2


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


def wait_until_finished(container_id, label="container"):
    _, token = _credentials()
    deadline = time.time() + CONTAINER_TIMEOUT_SECONDS
    last = None
    while time.time() < deadline:
        resp = requests.get(
            f"{BASE}/{container_id}",
            params={"fields": "status,error_message", "access_token": token},
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(f"Threads {label} status failed: HTTP {resp.status_code} {resp.text[:500]}")
        last = resp.json()
        status = str(last.get("status", "")).upper()
        if status == "FINISHED":
            return
        if status in {"ERROR", "EXPIRED"}:
            raise RuntimeError(f"Threads {label} processing failed: {last}")
        time.sleep(CONTAINER_POLL_SECONDS)
    raise RuntimeError(f"Threads {label} processing timeout: {last}")


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
    container_id = _check(resp, "image container create")
    wait_until_finished(container_id, "image container")
    return container_id


def create_carousel_container(text, image_urls):
    user_id, token = _credentials()
    urls = [str(x).strip() for x in image_urls if str(x).strip()][:MAX_PRODUCT_IMAGES]
    if not urls:
        raise RuntimeError("商品投稿の画像URLがありません。")
    if len(urls) == 1:
        return create_image_container(text, urls[0])

    children = []
    for index, image_url in enumerate(urls, start=1):
        data = {"media_type": "IMAGE", "image_url": image_url, "is_carousel_item": "true", "access_token": token}
        resp = requests.post(f"{BASE}/{user_id}/threads", data=data, timeout=30)
        child_id = _check(resp, f"carousel child {index} create")
        # 子コンテナは作成直後には親CAROUSELのchildrenとして使えない場合がある。
        # FINISHEDになるまで待ってから親を作る。
        wait_until_finished(child_id, f"carousel child {index}")
        children.append(child_id)

    if len(children) != len(urls):
        raise RuntimeError(f"カルーセル子要素数が不一致です: urls={len(urls)} children={len(children)}")
    data = {"media_type": "CAROUSEL", "children": ",".join(children), "text": text, "access_token": token}
    resp = requests.post(f"{BASE}/{user_id}/threads", data=data, timeout=30)
    parent_id = _check(resp, "carousel container create")
    wait_until_finished(parent_id, "carousel container")
    return parent_id


def publish_container(container_id):
    user_id, token = _credentials()
    resp = requests.post(f"{BASE}/{user_id}/threads_publish", data={"creation_id": container_id, "access_token": token}, timeout=30)
    return _check(resp, "publish")


def validate_post(post):
    parent = str(post.get("parent_text", "")).strip()
    if not parent:
        raise RuntimeError("parent_text が空です。")
    if post.get("type") != "product":
        return
    urls = post.get("image_urls") or [post.get("image_url")]
    urls = [str(x).strip() for x in urls if x and str(x).strip()]
    if not urls:
        raise RuntimeError("商品投稿の画像がありません。")
    child = str(post.get("child_text_base", "")).strip()
    if not child:
        raise RuntimeError("商品投稿の child_text_base が空です。")
    if not str(post.get("affiliate_url", "")).strip():
        raise RuntimeError("商品投稿の affiliate_url が空です。")


def publish_parent(post):
    validate_post(post)
    if post.get("type") == "product":
        urls = post.get("image_urls") or [post.get("image_url")]
        container = create_carousel_container(post["parent_text"], urls)
    else:
        container = create_text_container(post["parent_text"])
    return publish_container(container)


def publish_reply(post, thread_id):
    if post.get("type") != "product":
        return None
    child = str(post.get("child_text_base", "")).strip()
    affiliate_url = str(post.get("affiliate_url", "")).strip()
    if not child or not affiliate_url:
        raise RuntimeError("商品返信に必要な child_text_base / affiliate_url がありません。")
    reply = f"{child}\n\n【PR】\n{affiliate_url}"
    reply_container = create_text_container(reply, reply_to_id=thread_id)
    return publish_container(reply_container)


def publish_post(post, existing_thread_id=None, existing_reply_id=None, progress_callback=None):
    validate_post(post)
    thread_id = existing_thread_id
    reply_id = existing_reply_id
    if not thread_id:
        thread_id = publish_parent(post)
        if progress_callback:
            progress_callback(thread_id=thread_id, reply_id=None)
    if post.get("type") == "product" and not reply_id:
        reply_id = publish_reply(post, thread_id)
        if progress_callback:
            progress_callback(thread_id=thread_id, reply_id=reply_id)
    return thread_id, reply_id
