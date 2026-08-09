import re
from datetime import datetime
from html import unescape
from zoneinfo import ZoneInfo

import requests

JST = ZoneInfo("Asia/Tokyo")
USER_AGENT = "Mozilla/5.0 RakutenThreadsAutomation/1.0"

# 投稿に使うイベントは楽天市場の公式ページだけで確認する。
OFFICIAL_CAMPAIGNS = [
    {
        "name": "お買い物マラソン",
        "url": "https://event.rakuten.co.jp/campaign/point-up/marathon/",
        "period_labels": ("ポイントアップ期間", "対象期間"),
    },
    {
        "name": "楽天スーパーSALE",
        "url": "https://event.rakuten.co.jp/campaign/supersale/",
        "period_labels": ("ポイントアップ期間", "対象期間", "開催期間"),
    },
]

POINT_DAY = {
    "name": "5と0のつく日",
    "url": "https://event.rakuten.co.jp/card/pointday/",
}

# 公式ページが非開催時に表示する文言。これが確認できた場合は
# 「解析失敗」ではなく正常な非開催判定として扱う。
INACTIVE_MARKERS = (
    "は終了しました",
    "終了しました",
    "キャンペーンは終了",
    "次回の開催をお待ちください",
)


def _plain_text(html):
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _fetch_official_text(url):
    response = requests.get(url, timeout=12, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return _plain_text(response.text)


def _parse_japanese_datetime(year, month, day, hour, minute):
    return datetime(int(year), int(month), int(day), int(hour), int(minute), tzinfo=JST)


def _extract_period_after_label(text, labels):
    # 例: ポイントアップ期間 2026年8月4日(火)20:00～2026年8月11日(火)01:59
    date_range = (
        r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日[^0-9]{0,16}(\d{1,2}):(\d{2})"
        r"\s*[～〜~\-－–—]\s*"
        r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日[^0-9]{0,16}(\d{1,2}):(\d{2})"
    )
    for label in labels:
        match = re.search(re.escape(label) + r"\s*" + date_range, text)
        if match:
            g = match.groups()
            return _parse_japanese_datetime(*g[:5]), _parse_japanese_datetime(*g[5:])
    return None


def _official_page_says_inactive(text, campaign_name):
    # 例: 「楽天スーパーSALEは終了しました。」
    compact = re.sub(r"\s+", "", text)
    name = re.sub(r"\s+", "", campaign_name)
    for marker in INACTIVE_MARKERS:
        marker_compact = re.sub(r"\s+", "", marker)
        if f"{name}{marker_compact}" in compact:
            return True
    # 名前直結でなくても、公式キャンペーンページ全体が終了表示なら非開催扱い。
    return any(re.sub(r"\s+", "", marker) in compact for marker in INACTIVE_MARKERS)


def _fetch_official_campaign(campaign, now):
    try:
        text = _fetch_official_text(campaign["url"])
        if campaign["name"] not in text:
            return None

        # 終了済み公式ページには開催期間が残っていない場合がある。
        # その場合は解析エラーにせず「現在非開催」と確定する。
        if _official_page_says_inactive(text, campaign["name"]):
            return None

        period = _extract_period_after_label(text, campaign["period_labels"])
        if not period:
            # 開催を示すページなのに期間が読めない時だけ警告する。
            print(f"楽天公式キャンペーン期間を解析できません: {campaign['name']}")
            return None

        start, end = period
        if start <= now <= end:
            return {
                "name": campaign["name"],
                "active": True,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "source_url": campaign["url"],
                "source": "楽天市場公式",
            }
    except (requests.RequestException, ValueError) as exc:
        print(f"楽天公式キャンペーン確認失敗: {campaign['name']}: {exc}")
    return None


def _fetch_point_day(now):
    # 日付だけで決めず、当日に楽天公式ページ自体を取得・確認できた場合だけ採用する。
    if now.day not in {5, 10, 15, 20, 25, 30}:
        return None
    try:
        text = _fetch_official_text(POINT_DAY["url"])
        normalized = text.replace(" ", "")
        if "5と0のつく日" not in normalized:
            print("楽天公式5と0のつく日ページを確認できません")
            return None
        return {
            "name": POINT_DAY["name"],
            "active": True,
            "start": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
            "end": now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat(),
            "source_url": POINT_DAY["url"],
            "source": "楽天市場公式",
            "note": "楽天公式ページ確認済み。エントリーや楽天カード利用など条件があるため、商品自体の値下げとは表現しない。",
        }
    except requests.RequestException as exc:
        print(f"楽天公式5と0のつく日確認失敗: {exc}")
        return None


def get_active_rakuten_events(now=None):
    """現在開催中と楽天市場公式で確認できたイベントだけ返す。失敗時は推測しない。"""
    now = now or datetime.now(JST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=JST)
    else:
        now = now.astimezone(JST)

    events = []
    for campaign in OFFICIAL_CAMPAIGNS:
        event = _fetch_official_campaign(campaign, now)
        if event:
            events.append(event)

    point_day = _fetch_point_day(now)
    if point_day:
        events.append(point_day)

    return events
