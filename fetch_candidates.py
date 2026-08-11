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
    targets = {}
    assigned = 0
    for group in SEARCH_GROUPS:
        count = math.floor(target_count * group["weekly_slots"] / total_slots)
        targets[group["id"]] = count
        assigned += count

    # 端数は週の商品枠が多いカテゴリから順に配る。
    for group in sorted(SEARCH_GROUPS, key=lambda x: x["weekly_slots"], reverse=True):
        if assigned >= target_count:
            break
        targets[group["id"]] += 1
        assigned += 1
    return targets


def build_payload(target_count=80, minimum_count=50):
    raw_groups = fetch_candidate_groups(pages_per_keyword=1)
    category_targets = _category_targets(target_count)
    selected = []
    selected_codes = set()
    category_counts = {}
    reserves = []
    total_raw = 0
    total_filtered = 0

    for group in SEARCH_GROUPS:
        group_id = group["id"]
        raw_items = raw_groups.get(group_id, [])
        total_raw += len(raw_items)
        filtered_items = primary_filter(
            raw_items,
            min_rating=4.3,
            min_reviews=100,
            min_price=1000,
            max_price=5000,
            history_days=30,
            excluded_item_codes=selected_codes,
        )
        total_filtered += len(filtered_items)

        wanted = category_targets[group_id]
        chosen = filtered_items[:wanted]
        selected.extend(chosen)
        selected_codes.update(item["itemCode"] for item in chosen)
        reserves.extend(filtered_items[wanted:])
        category_counts[group_id] = {
            "label": group["label"],
            "weeklySlots": group["weekly_slots"],
            "targetCandidates": wanted,
            "rawItems": len(raw_items),
            "filteredItems": len(filtered_items),
            "savedItems": len(chosen),
        }

    # 一部カテゴリが不足しても全体数を確保できる場合は、他カテゴリの予備で補う。
    for item in reserves:
        if len(selected) >= target_count:
            break
        code = item["itemCode"]
        if code in selected_codes:
            continue
        selected.append(item)
        selected_codes.add(code)
        category_counts[item["candidateCategory"]]["savedItems"] += 1

    if len(selected) < minimum_count:
        raise RuntimeError(
            f"候補不足: 条件通過後に保存できる候補は{len(selected)}件です。"
            f"最低{minimum_count}件必要なため、latest.jsonは更新しません。"
        )

    return {
        "generatedAt": datetime.now(JST).isoformat(timespec="seconds"),
        "source": "Rakuten Ichiba Item Search API",
        "personaReference": "docs/poster_persona.md",
        "searchGroups": SEARCH_GROUPS,
        "filters": {
            "minimumRating": 4.3,
            "minimumReviewCount": 100,
            "minimumPrice": 1000,
            "maximumPrice": 5000,
            "historyExclusionDays": 30,
            "requiresImage": True,
            "requiresAffiliateUrl": True,
        },
        "counts": {
            "rawItemsAcrossCategories": total_raw,
            "filteredItemsAcrossCategories": total_filtered,
            "savedUniqueItems": len(selected),
            "byCategory": category_counts,
        },
        "products": selected,
    }


def main():
    parser = argparse.ArgumentParser(
        description="投稿者ペルソナに合わせて楽天APIから翌週の商品候補を取得します。"
    )
    parser.add_argument("--target-count", type=int, default=80)
    parser.add_argument("--minimum-count", type=int, default=50)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if args.target_count < 1:
        raise ValueError("--target-countは1以上にしてください。")
    if args.minimum_count < 1:
        raise ValueError("--minimum-countは1以上にしてください。")
    if args.minimum_count > args.target_count:
        raise ValueError("--minimum-countは--target-count以下にしてください。")

    payload = build_payload(
        target_count=args.target_count,
        minimum_count=args.minimum_count,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)

    temp_path = args.output.with_suffix(args.output.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(args.output)

    counts = payload["counts"]
    print(
        f"候補ファイルを保存しました: {args.output} "
        f"(カテゴリ横断取得{counts['rawItemsAcrossCategories']}件 / "
        f"条件通過{counts['filteredItemsAcrossCategories']}件 / "
        f"重複除外後保存{counts['savedUniqueItems']}件)"
    )
    for details in counts["byCategory"].values():
        print(
            f"- {details['label']}: 取得{details['rawItems']} / "
            f"条件通過{details['filteredItems']} / 保存{details['savedItems']}"
        )


if __name__ == "__main__":
    main()
