import os
import time
import requests

API_URL = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"

# 家具・インテリア特化アカウントの商品候補検索群。
# 見るだけでも楽しい家具と、実際に買いやすい小物・寝具を混ぜる。
SEARCH_GROUPS = [
    {"id":"sofa_chair","label":"ソファ・チェア","weekly_slots":4,"keywords":["ソファ おしゃれ 北欧","一人掛け チェア おしゃれ","ダイニングチェア 北欧","韓国 インテリア チェア"]},
    {"id":"table_desk","label":"テーブル・デスク","weekly_slots":4,"keywords":["ローテーブル おしゃれ","サイドテーブル 韓国 インテリア","ダイニングテーブル 北欧","デスク おしゃれ 一人暮らし"]},
    {"id":"lighting","label":"照明","weekly_slots":3,"keywords":["フロアライト おしゃれ","テーブルライト 韓国 インテリア","ペンダントライト 北欧","間接照明 ホテルライク"]},
    {"id":"rug_curtain","label":"ラグ・カーテン","weekly_slots":3,"keywords":["ラグ おしゃれ 北欧","ラグ 韓国 インテリア","カーテン おしゃれ ナチュラル","カーペット 一人暮らし おしゃれ"]},
    {"id":"bed_bedding","label":"ベッド・寝具","weekly_slots":3,"keywords":["ベッドフレーム おしゃれ","ベッド 一人暮らし 韓国","掛け布団カバー おしゃれ","ベッドカバー ホテルライク"]},
    {"id":"storage_furniture","label":"収納家具","weekly_slots":3,"keywords":["キャビネット おしゃれ","シェルフ 北欧 おしゃれ","テレビボード おしゃれ","チェスト 韓国 インテリア"]},
    {"id":"mirror_dresser","label":"ミラー・ドレッサー","weekly_slots":2,"keywords":["全身ミラー おしゃれ","ウェーブミラー 韓国","ドレッサー おしゃれ コンパクト","卓上ミラー インテリア"]},
    {"id":"interior_decor","label":"インテリア雑貨","weekly_slots":3,"keywords":["フラワーベース おしゃれ","クッション 北欧 おしゃれ","壁掛け時計 おしゃれ","インテリア雑貨 韓国"]},
]

DEFAULT_KEYWORDS = [keyword for group in SEARCH_GROUPS for keyword in group["keywords"]]


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
    seen_urls = set()
    for image in images:
        url = image.get("imageUrl") if isinstance(image, dict) else str(image)
        if not url:
            continue
        normalized_url = url.split("?")[0]
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        image_urls.append(normalized_url)
    return {
        "itemCode": item.get("itemCode", ""), "itemName": item.get("itemName", ""),
        "itemCaption": item.get("itemCaption", ""), "itemPrice": int(item.get("itemPrice") or 0),
        "reviewAverage": float(item.get("reviewAverage") or 0), "reviewCount": int(item.get("reviewCount") or 0),
        "affiliateUrl": item.get("affiliateUrl", ""), "itemUrl": item.get("itemUrl", ""),
        "shopName": item.get("shopName", ""), "genreId": str(item.get("genreId", "")),
        "imageUrls": image_urls, "imageCount": len(image_urls),
    }


def search_items(keyword, page=1, hits=30, sort="-reviewCount", timeout=20):
    keyword = " ".join(str(keyword).split())[:128]
    if not keyword:
        raise ValueError("楽天APIの検索キーワードが空です。")
    params = {**_credentials(), "keyword": keyword, "hits": hits, "page": page, "sort": sort, "format": "json"}
    for attempt in range(2):
        response = requests.get(API_URL, params=params, timeout=timeout)
        if response.status_code == 429 and attempt == 0:
            retry_after = response.headers.get("Retry-After")
            try: wait_seconds = max(1.0, min(float(retry_after), 5.0)) if retry_after else 2.0
            except (TypeError, ValueError): wait_seconds = 2.0
            print(f"楽天API 429: {wait_seconds:g}秒待って1回だけ再試行します。")
            time.sleep(wait_seconds); continue
        if response.status_code != 200:
            raise RuntimeError(f"楽天API通信エラー HTTP {response.status_code}: {response.text[:1000]}")
        data = response.json()
        if "error" in data: raise RuntimeError(f"楽天APIレスポンスエラー: {data}")
        return [normalize_item(item) for item in data.get("Items", [])]
    raise RuntimeError("楽天API 429: 1回再試行してもレート制限が解除されませんでした。")


def fetch_candidate_groups(search_groups=None, pages_per_keyword=1):
    groups = search_groups or SEARCH_GROUPS
    grouped = {}; request_count = 0
    for group in groups:
        collected = {}
        for keyword in group["keywords"]:
            for page in range(1, pages_per_keyword + 1):
                if request_count > 0: time.sleep(1.1)
                items = search_items(keyword=keyword, page=page, hits=30); request_count += 1
                for item in items:
                    code = item.get("itemCode")
                    if not code or code in collected: continue
                    enriched = dict(item); enriched["candidateCategory"] = group["id"]
                    enriched["candidateCategoryLabel"] = group["label"]; enriched["matchedKeyword"] = keyword
                    collected[code] = enriched
        grouped[group["id"]] = list(collected.values())
    return grouped


def fetch_candidate_pool(keywords=None, target_raw=50, max_pages_per_keyword=3):
    keywords = keywords or DEFAULT_KEYWORDS; collected = {}; request_count = 0
    for keyword in keywords:
        for page in range(1, max_pages_per_keyword + 1):
            if request_count > 0: time.sleep(1.1)
            items = search_items(keyword=keyword, page=page, hits=30); request_count += 1
            for item in items:
                code = item.get("itemCode")
                if code: collected[code] = item
            if len(collected) >= target_raw: return list(collected.values())
    return list(collected.values())
