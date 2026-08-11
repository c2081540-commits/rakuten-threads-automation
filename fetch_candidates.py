import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rakuten import DEFAULT_KEYWORDS, fetch_candidate_pool
from selector import primary_filter

JST = timezone(timedelta(hours=9))
DEFAULT_OUTPUT = Path("data/candidates/latest.json")


def build_payload(target_count=80, minimum_count=50):
    raw_items = fetch_candidate_pool(
        keywords=DEFAULT_KEYWORDS,
        target_raw=max(target_count * 4, 240),
        max_pages_per_keyword=3,
    )
    filtered_items = primary_filter(
        raw_items,
        min_rating=4.3,
        min_reviews=100,
        min_price=1000,
        max_price=5000,
        history_days=30,
    )

    selected = filtered_items[:target_count]
    if len(selected) < minimum_count:
        raise RuntimeError(
            f"候補不足: 条件通過は{len(selected)}件です。最低{minimum_count}件必要なため、"
            "latest.jsonは更新しません。"
        )

    return {
        "generatedAt": datetime.now(JST).isoformat(timespec="seconds"),
        "source": "Rakuten Ichiba Item Search API",
        "keywords": DEFAULT_KEYWORDS,
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
            "rawUniqueItems": len(raw_items),
            "filteredItems": len(filtered_items),
            "savedItems": len(selected),
        },
        "products": selected,
    }


def main():
    parser = argparse.ArgumentParser(
        description="楽天APIから翌週投稿用の商品候補を取得してJSONへ保存します。"
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
        f"(取得{counts['rawUniqueItems']}件 / 条件通過{counts['filteredItems']}件 / "
        f"保存{counts['savedItems']}件)"
    )


if __name__ == "__main__":
    main()
