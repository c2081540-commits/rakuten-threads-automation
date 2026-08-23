import re
from history import recent_product_codes

# 家具・インテリア候補の補助順位。API情報だけで判定できる要素に限定する。
INTERIOR_TERMS=("ソファ","チェア","テーブル","デスク","ベッド","ラグ","カーペット","照明","ライト","ミラー","ドレッサー","シェルフ","キャビネット","テレビボード","チェスト","カーテン","クッション","時計","フラワーベース","花瓶")
STYLE_TERMS=("北欧","ナチュラル","韓国","ホテルライク","モダン","シンプル","木目","天然木","アイボリー","ベージュ","ホワイト","グレー")
ROOM_TERMS=("リビング","寝室","ベッドサイド","ダイニング","玄関","ワンルーム","一人暮らし","模様替え","インテリア")
SPEC_PATTERN=re.compile(r"(?:約\s*)?\d+(?:\.\d+)?\s*(?:cm|mm|g|kg|枚|個|脚|段)",re.IGNORECASE)

def threads_sellability_score(item):
    name=" ".join(str(item.get("itemName","")).split());caption=" ".join(str(item.get("itemCaption","")).split());source=f"{name} {caption}"
    interior_hits=sum(t in source for t in INTERIOR_TERMS);style_hits=sum(t in source for t in STYLE_TERMS);room_hits=sum(t in source for t in ROOM_TERMS);spec_hits=len(SPEC_PATTERN.findall(source));image_count=len(item.get("imageUrls") or [])
    score=min(interior_hits,3)*1.2+min(style_hits,4)*0.8+min(room_hits,3)*0.7+min(spec_hits,3)*0.25+min(image_count,3)*0.8
    if len(caption)>=250:score+=0.4
    return round(score,3)

def primary_filter(items,min_rating=4.3,min_reviews=100,min_price=1000,max_price=5000,history_days=30,excluded_item_codes=None):
    excluded=recent_product_codes(days=history_days)|set(excluded_item_codes or []);passed=[]
    for item in items:
        if not item.get("itemCode") or item["itemCode"] in excluded:continue
        if item.get("reviewAverage",0)<min_rating or item.get("reviewCount",0)<min_reviews:continue
        price=item.get("itemPrice",0)
        if not(min_price<=price<=max_price):continue
        if not item.get("imageUrls") or not item.get("affiliateUrl"):continue
        enriched=dict(item);enriched["threadsSellabilityScore"]=threads_sellability_score(item);passed.append(enriched)
    passed.sort(key=lambda x:(x.get("threadsSellabilityScore",0),x.get("reviewAverage",0),min(x.get("reviewCount",0),5000)),reverse=True)
    return passed

def build_shortlist(items,limit=10):return items[:limit]
