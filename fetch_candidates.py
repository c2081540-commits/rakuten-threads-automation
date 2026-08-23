import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rakuten import SEARCH_GROUPS, fetch_candidate_groups
from selector import primary_filter

JST = timezone(timedelta(hours=9))
DEFAULT_OUTPUT = Path("data/candidates/latest.json")


def _category_targets(target_count):
    total_slots = sum(group["weekly_slots"] for group in SEARCH_GROUPS)
    targets = {}; assigned = 0
    for group in SEARCH_GROUPS:
        count = math.floor(target_count * group["weekly_slots"] / total_slots)
        targets[group["id"]] = count; assigned += count
    for group in sorted(SEARCH_GROUPS, key=lambda x: x["weekly_slots"], reverse=True):
        if assigned >= target_count: break
        targets[group["id"]] += 1; assigned += 1
    return targets


def _image_count_summary(items):
    counts = [int(item.get("imageCount") or len(item.get("imageUrls") or [])) for item in items]
    if not counts: return {"minimum":0,"maximum":0,"average":0.0,"buckets":{"1":0,"2":0,"3":0,"4-5":0,"6-10":0,"11+":0}}
    buckets={"1":0,"2":0,"3":0,"4-5":0,"6-10":0,"11+":0}
    for count in counts:
        if count<=1:buckets["1"]+=1
        elif count==2:buckets["2"]+=1
        elif count==3:buckets["3"]+=1
        elif count<=5:buckets["4-5"]+=1
        elif count<=10:buckets["6-10"]+=1
        else:buckets["11+"]+=1
    return {"minimum":min(counts),"maximum":max(counts),"average":round(sum(counts)/len(counts),2),"buckets":buckets}


def build_payload(target_count=80, minimum_count=50):
    raw_groups=fetch_candidate_groups(pages_per_keyword=1); category_targets=_category_targets(target_count)
    selected=[]; selected_codes=set(); category_counts={}; reserves=[]; total_raw=0; total_filtered=0
    for group in SEARCH_GROUPS:
        gid=group["id"]; raw_items=raw_groups.get(gid,[]); total_raw+=len(raw_items)
        # 家具は数万円が通常なので旧便利グッズ向け5000円上限を撤廃。
        # 小物から大型家具まで候補に残し、最終選定で価格帯を分散する。
        filtered=primary_filter(raw_items,min_rating=4.2,min_reviews=30,min_price=1000,max_price=100000,history_days=30,excluded_item_codes=selected_codes)
        total_filtered+=len(filtered); wanted=category_targets[gid]; chosen=filtered[:wanted]
        selected.extend(chosen); selected_codes.update(i["itemCode"] for i in chosen); reserves.extend(filtered[wanted:])
        category_counts[gid]={"label":group["label"],"weeklySlots":group["weekly_slots"],"targetCandidates":wanted,"rawItems":len(raw_items),"filteredItems":len(filtered),"savedItems":len(chosen)}
    for item in reserves:
        if len(selected)>=target_count:break
        code=item["itemCode"]
        if code in selected_codes:continue
        selected.append(item);selected_codes.add(code);category_counts[item["candidateCategory"]]["savedItems"]+=1
    if len(selected)<minimum_count:raise RuntimeError(f"候補不足: 条件通過後に保存できる候補は{len(selected)}件です。最低{minimum_count}件必要なため、latest.jsonは更新しません。")
    image_summary=_image_count_summary(selected)
    return {"generatedAt":datetime.now(JST).isoformat(timespec="seconds"),"source":"Rakuten Ichiba Item Search API","personaReference":"docs/poster_persona.md","searchGroups":SEARCH_GROUPS,"filters":{"minimumRating":4.2,"minimumReviewCount":30,"minimumPrice":1000,"maximumPrice":100000,"historyExclusionDays":30,"requiresImage":True,"requiresAffiliateUrl":True,"niche":"furniture_interiors"},"counts":{"rawItemsAcrossCategories":total_raw,"filteredItemsAcrossCategories":total_filtered,"savedUniqueItems":len(selected),"imageCandidates":image_summary,"byCategory":category_counts},"products":selected}


def main():
    parser=argparse.ArgumentParser(description="家具・インテリア特化ペルソナに合わせて楽天APIから商品候補を取得します。")
    parser.add_argument("--target-count",type=int,default=80);parser.add_argument("--minimum-count",type=int,default=50);parser.add_argument("--output",type=Path,default=DEFAULT_OUTPUT);args=parser.parse_args()
    if args.target_count<1:raise ValueError("--target-countは1以上にしてください。")
    if args.minimum_count<1:raise ValueError("--minimum-countは1以上にしてください。")
    if args.minimum_count>args.target_count:raise ValueError("--minimum-countは--target-count以下にしてください。")
    payload=build_payload(args.target_count,args.minimum_count);args.output.parent.mkdir(parents=True,exist_ok=True)
    temp=args.output.with_suffix(args.output.suffix+".tmp");temp.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");temp.replace(args.output)
    counts=payload["counts"];print(f"候補ファイルを保存しました: {args.output} (カテゴリ横断取得{counts['rawItemsAcrossCategories']}件 / 条件通過{counts['filteredItemsAcrossCategories']}件 / 重複除外後保存{counts['savedUniqueItems']}件)")
    for details in counts["byCategory"].values():print(f"- {details['label']}: 取得{details['rawItems']} / 条件通過{details['filteredItems']} / 保存{details['savedItems']}")

if __name__=="__main__":main()
