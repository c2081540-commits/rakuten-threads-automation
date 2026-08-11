import os
import time
import requests

API_URL = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"

# 投稿者ペルソナの商品配分に対応する検索群。
# 各カテゴリを最後まで検索し、収納・キッチンだけに偏らせない。
SEARCH_GROUPS = [
    {
        "id": "after_work_kitchen",
        "label": "仕事終わり・キッチン",
        "weekly_slots": 3,
        "keywords": [
            "レンジ調理器 一人暮らし",
            "冷凍ご飯 容器",
            "キッチン 時短 便利グッズ",
            "食器洗い 便利グッズ",
        ],
    },
    {
        "id": "beauty_bath",
        "label": "美容・お風呂",
        "weekly_slots": 3,
        "keywords": [
            "吸水 ヘアタオル",
            "ドライヤー 収納",
            "ヘアアイロン 収納 ポーチ",
            "コスメ 収納 ポーチ",
        ],
    },
    {
        "id": "morning_fashion_bag",
        "label": "朝の支度・服・バッグ",
        "weekly_slots": 2,
        "keywords": [
            "バッグインバッグ レディース",
            "アクセサリー 収納 持ち運び",
            "衣類スチーマー コンパクト",
            "毛玉取り 電動",
        ],
    },
    {
        "id": "storage_cleaning",
        "label": "収納・掃除",
        "weekly_slots": 2,
        "keywords": [
            "一人暮らし 収納 便利グッズ",
            "コード 収納 おしゃれ",
            "ランドリー 省スペース",
            "掃除 便利グッズ コンパクト",
        ],
    },
    {
        "id": "travel_outing",
        "label": "旅行・外出・推し活",
        "weekly_slots": 2,
        "keywords": [
            "旅行 圧縮ポーチ",
            "吊り下げ トラベルポーチ",
            "折りたたみ傘 軽量 レディース",
            "推し活 ポーチ 収納",
        ],
    },
    {
        "id": "sleep_relax",
        "label": "睡眠・リラックス",
        "weekly_slots": 2,
        "keywords": [
            "アイマスク 睡眠",
            "シルク 枕カバー",
            "ホットアイマスク 充電式",
            "リラックス グッズ デスク",
        ],
    },
]

DEFAULT_KEYWORDS = [
    keyword
    for group in SEARCH_GROUPS
    for keyword in group["keywords"]
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
        "imageUrls": image_urls[:3],
    }


def search_items(keyword, page=1, hits=30, sort="-reviewCount", timeout=20):
    keyword = " ".join(str(keyword).split())[:128]
    if not keyword:
        raise ValueError("楽天APIの検索キーワードが空です。")
    params = {
        **_credentials(),
        "keyword": keyword,
        "hits": hits,
        "page": page,
        "sort": sort,
        "format": "json",
    }

    for attempt in range(2):
        response = requests.get(API_URL, params=params, timeout=timeout)
        if response.status_code == 429 and attempt == 0:
            retry_after = response.headers.get("Retry-After")
            try:
                wait_seconds = max(1.0, min(float(retry_after), 5.0)) if retry_after else 2.0
            except (TypeError, ValueError):
                wait_seconds = 2.0
            print(f"楽天API 429: {wait_seconds:g}秒待って1回だけ再試行します。")
            time.sleep(wait_seconds)
            continue
        if response.status_code != 200:
            raise RuntimeError(
                f"楽天API通信エラー HTTP {response.status_code}: {response.text[:1000]}"
            )
        data = response.json()
        if "error" in data:
            raise RuntimeError(f"楽天APIレスポンスエラー: {data}")
        return [normalize_item(item) for item in data.get("Items", [])]

    raise RuntimeError("楽天API 429: 1回再試行してもレート制限が解除されませんでした。")


def fetch_candidate_groups(search_groups=None, pages_per_keyword=1):
    """全検索カテゴリを省略せずに取得し、商品へ検索カテゴリを付与する。"""
    groups = search_groups or SEARCH_GROUPS
    grouped = {}
    request_count = 0

    for group in groups:
        collected = {}
        for keyword in group["keywords"]:
            for page in range(1, pages_per_keyword + 1):
                if request_count > 0:
                    time.sleep(1.1)
                items = search_items(keyword=keyword, page=page, hits=30)
                request_count += 1
                for item in items:
                    code = item.get("itemCode")
                    if not code or code in collected:
                        continue
                    enriched = dict(item)
                    enriched["candidateCategory"] = group["id"]
                    enriched["candidateCategoryLabel"] = group["label"]
                    enriched["matchedKeyword"] = keyword
                    collected[code] = enriched
        grouped[group["id"]] = list(collected.values())

    return grouped


def fetch_candidate_pool(keywords=None, target_raw=50, max_pages_per_keyword=3):
    """既存処理との互換用。新しい候補出力はfetch_candidate_groupsを使う。"""
    keywords = keywords or DEFAULT_KEYWORDS
    collected = {}
    request_count = 0
    for keyword in keywords:
        for page in range(1, max_pages_per_keyword + 1):
            if request_count > 0:
                time.sleep(1.1)
            items = search_items(keyword=keyword, page=page, hits=30)
            request_count += 1
            for item in items:
                code = item.get("itemCode")
                if code:
                    collected[code] = item
            if len(collected) >= target_raw:
                return list(collected.values())
    return list(collected.values())
