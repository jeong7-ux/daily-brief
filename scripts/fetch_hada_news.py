import html
import json
import os
import re
import time
from pathlib import Path

import feedparser
import requests

FEED_URL = "https://news.hada.io/rss/news"
STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "seen_hada.json"
MAX_SEEN = 500

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def load_seen():
    if not STATE_PATH.exists():
        return None  # None marks "first run, never seeded"
    return set(json.loads(STATE_PATH.read_text(encoding="utf-8")))


def save_seen(seen_ids):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    trimmed = list(seen_ids)[-MAX_SEEN:]
    STATE_PATH.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")


def strip_html(raw):
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def send_telegram(text):
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=20,
    )
    resp.raise_for_status()


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise SystemExit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 필요합니다.")

    feed = feedparser.parse(FEED_URL)
    entries = list(reversed(feed.entries))  # 오래된 글부터 순서대로 전송

    seen = load_seen()
    first_run = seen is None
    if first_run:
        seen = set()

    new_entries = [e for e in entries if e.id not in seen]

    if first_run:
        # 최초 실행은 스팸 방지를 위해 현재 피드를 seen 처리만 하고 발송은 건너뜀
        print(f"첫 실행: {len(new_entries)}개 글을 seen 처리하고 발송은 생략합니다.")
    else:
        for entry in new_entries:
            summary = strip_html(entry.get("summary", ""))[:300]
            text = f"<b>{html.escape(entry.title)}</b>\n{summary}\n{entry.link}"
            send_telegram(text)
            time.sleep(1)  # 텔레그램 rate limit 여유
        print(f"{len(new_entries)}개 새 글 전송 완료.")

    for e in entries:
        seen.add(e.id)
    save_seen(seen)


if __name__ == "__main__":
    main()
