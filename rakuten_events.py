import re
from datetime import datetime
from html import unescape
from zoneinfo import ZoneInfo

import requests

JST = ZoneInfo("Asia/Tokyo")

# 楽天市場の公式キャンペーンページのみを参照する。
OFFICIAL_CAMPAIGNS = [
    {
        "name": "お買い物マラソン",
        "url": "https://event.rakuten.co.jp/campaign/point-up/marathon/",
    },
    {
        "name": "楽天スーパーSALE",
        "url": "https://event.rakuten.co.jp/campaign/supersale/",
    },
]


def _plain_text(html):
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_japanese_datetime(year, month, day, hour, minute):
    return datetime(int(year), int(month), int(day), int(hour), int(minute), tzinfo=JST)


def _extract_active_period(text):
    # 楽天公式ページの「ポイントアップ期間」を最優先で読む。
    patterns = [
        r"ポイントアップ期間\s*(20\d{2})年(\d{1,2})月(\d{1,2})日[^0-9]{0,8}(\d{1,2}):(\d{2})\s*[～〜~-]\s*(20\d{2})年(\d{1,2})月(\d{1,2})日[^0-9]{0,8}(\d{1,2}):(\d{2})",
        r"対象期間\s*(20\d{2})年(\d{1,2})月(\d{1,2})日[^0-9]{0,8}(\d{1,2}):(\d{2})\s*[～〜~-]\s*(20\d{2})年(\d{1,2})月(\d{1,2})日[^0-9]{0,8}(\d{1,2}):(\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            g = match.groups()
            return (
                _parse_japanese_datetime(*g[:5]),
                _parse_japanese_datetime(*g[5:]),
            )
    return None


def _fetch_official_campaign(campaign, now):
    try:
        response = requests.get(
            campaign["url"],
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0 RakutenThreadsAutomation/1.0"},
        )
        response.raise_for_status()
        text = _plain_text(response.text)
        period = _extract_active_period(text)
        if not period:
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
    except requests.RequestException as exc:
        print(f"楽天公式キャンペーン確認失敗: {campaign['name']}: {exc}")
    return None


def get_active_rakuten_events(now=None):
    """現在開催中と公式確認できたイベントだけ返す。確認不能なら推測しない。"""
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

    # 5と0のつく日は楽天公式が毎月5,10,15,20,25,30日 00:00-23:59 と明記。
    if now.day in {5, 10, 15, 20, 25, 30}:
        events.append({
            "name": "5と0のつく日",
            "active": True,
            "start": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
            "end": now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat(),
            "source_url": "https://event.rakuten.co.jp/card/pointday/",
            "source": "楽天市場公式",
            "note": "エントリーと楽天カード利用など条件あり。商品自体の値下げを意味しない。",
        })

    return events
