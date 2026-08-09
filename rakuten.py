import os
import requests

API_URL = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"

DEFAULT_KEYWORDS = [
    "収納 便利グッズ",
    "キッチン 便利グッズ",
    "掃除 便利グッズ",
    "洗濯 便利グッズ",
    "隙間収納",
    "生活雑貨 便利",
]


def _credentials():
    values = {
        "applicationId": os.environ.get("RAKUTEN_APP_ID", "").strip(),
        "accessKey": os.environ.get("RAKUTEN_ACCESS_KEY", "").strip(),
        "affiliateId": os.environ.get("RAKUTEN_AFFILIATE_ID", "").strip(),
    }
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"楽天API認証情報が不足しています: {', '.join(missing)}")
    return values


def normalize_item(wrapper):
    item = wrapper.get("Item", wrapper)
    images = item.get("mediumImageUrls") or []
    image_urls = []
    for image in images:
        if isinstance(image, dict):
            url = image.get("imageUrl")
        else:
            url = str(image)
        if url:
            image_urls.append(url.split("?")[0])

    return {
        "itemCode": item.get("itemCode", ""),
        "itemName": item.get("itemName", ""),
        "itemCaption": item.get("itemCaption", ""),
        "itemPrice": int(item.get("itemPrice") or 0),
        "reviewAverage": float(item.get("reviewAverage") or 0),
        "reviewCount": int(item.get("reviewCount") or 0),
        "affiliateUrl": item.get("affiliateUrl", ""),
        "itemUrl": item.get("itemUrl", ""),
        "shopName": item.get("shopName", ""),
        "genreId": str(item.get("genreId", "")),
        "imageUrls": image_urls,
    }


def search_items(keyword, page=1, hits=30, sort="-reviewCount", timeout=20):
    params = {
        **_credentials(),
        "keyword": keyword,
        "hits": hits,
        "page": page,
        "sort": sort,
        "format": "json",
    }
    response = requests.get(API_URL, params=params, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(
            f"楽天API通信エラー HTTP {response.status_code}: {response.text[:1000]}"
        )
    data = response.json()
    if "error" in data:
        raise RuntimeError(f"楽天APIレスポンスエラー: {data}")
    return [normalize_item(item) for item in data.get("Items", [])]


def fetch_candidate_pool(keywords=None, target_raw=50, max_pages_per_keyword=3):
    keywords = keywords or DEFAULT_KEYWORDS
    collected = {}
    for keyword in keywords:
        for page in range(1, max_pages_per_keyword + 1):
            for item in search_items(keyword=keyword, page=page, hits=30):
                code = item.get("itemCode")
                if code:
                    collected[code] = item
            if len(collected) >= target_raw:
                return list(collected.values())
    return list(collected.values())
