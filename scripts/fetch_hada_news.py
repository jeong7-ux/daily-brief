import html
import json
import os
import re
import time
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup

FEED_URL = "https://news.hada.io/rss/news"
STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "seen_hada.json"
MAX_SEEN = 500

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://news.hada.io/",
}


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


def get_original_url(topic_url):
    """GeekNews 토픽 페이지에서 원문(외부 출처) 링크를 찾는다. 실패하면 None."""
    try:
        resp = requests.get(topic_url, headers=BROWSER_HEADERS, timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        link = soup.select_one("a.topic-title-link")
        return link["href"] if link and link.get("href") else None
    except Exception:
        return None


def extract_article_text(url):
    """원문 페이지 본문을 <p> 태그 기준으로 긁어온다."""
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")
        paragraphs = soup.find_all("p")
        text = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
        return text[:15000]
    except Exception:
        return ""


def summarize_with_llm(text):
    """OpenRouter로 본문을 요약. 실패하면 None을 반환해 폴백을 유도."""
    prompt = f"다음 기사 본문을 읽고 핵심 내용을 3~5줄 이내로 명확하게 한글로 요약해줘:\n\n{text}"
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except requests.exceptions.HTTPError:
            if resp.status_code == 429 and attempt < max_retries - 1:
                time.sleep(3)
                continue
            return None
        except Exception:
            return None
    return None


def build_summary(entry):
    """원문을 가져와 LLM으로 요약하고, 실패 시 GeekNews 자체 요약으로 폴백한다."""
    original_url = get_original_url(entry.link) or entry.link
    article_text = extract_article_text(original_url)

    summary = None
    if len(article_text) >= 200:
        summary = summarize_with_llm(article_text)

    if not summary:
        summary = strip_html(entry.get("summary", ""))[:300]

    return original_url, summary


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
    if not OPENROUTER_API_KEY:
        raise SystemExit("OPENROUTER_API_KEY 환경변수가 필요합니다.")

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
            original_url, summary = build_summary(entry)
            text = f"<b>{html.escape(entry.title)}</b>\n{html.escape(summary)}\n{original_url}"
            send_telegram(text)
            time.sleep(2)  # 원문 사이트/텔레그램 rate limit 여유
        print(f"{len(new_entries)}개 새 글 전송 완료.")

    for e in entries:
        seen.add(e.id)
    save_seen(seen)


if __name__ == "__main__":
    main()
