import html
import json
import os
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup

FEED_URL = "https://www.newstheai.com/rss/clickTop.xml"
STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "seen_ai_news.json"
MAX_SEEN = 300
TOP_N = 5
TITLE_SIMILARITY_THRESHOLD = 0.6  # 이 이상 비슷한 제목은 중복 기사로 보고 먼저 나온 것만 남긴다

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}


def load_seen():
    if not STATE_PATH.exists():
        return None  # None marks "first run, never seeded"
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))  # 등록 순서를 유지하는 리스트


def save_seen(seen_ids):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    trimmed = seen_ids[-MAX_SEEN:]  # 오래된 것부터 잘라내 최신 항목을 보존
    STATE_PATH.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")


def strip_html(raw):
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def dedupe_by_title(entries, threshold=TITLE_SIMILARITY_THRESHOLD):
    """제목이 비슷하면 중복 기사로 보고, 먼저 나온 것만 남긴다."""
    kept = []
    kept_titles = []
    for entry in entries:
        title = strip_html(entry.title)
        if any(SequenceMatcher(None, title, kt).ratio() >= threshold for kt in kept_titles):
            continue
        kept.append(entry)
        kept_titles.append(title)
    return kept


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
    """원문을 가져와 LLM으로 요약하고, 실패 시 RSS 자체 설명으로 폴백한다."""
    article_text = extract_article_text(entry.link)

    summary = None
    if len(article_text) >= 200:
        summary = summarize_with_llm(article_text)

    if not summary:
        summary = strip_html(entry.get("summary", ""))[:300]

    return summary


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
    entries = feed.entries  # 인기순으로 내려온다 (시간순 아님)

    seen = load_seen()
    first_run = seen is None
    if first_run:
        seen = []

    new_entries = [e for e in entries if e.link not in seen]

    if first_run:
        # 최초 실행은 스팸 방지를 위해 현재 피드를 seen 처리만 하고 발송은 건너뜀
        print(f"첫 실행: {len(new_entries)}개 글을 seen 처리하고 발송은 생략합니다.")
    else:
        deduped = dedupe_by_title(new_entries)[:TOP_N]
        for entry in deduped:
            title = strip_html(entry.title)
            summary = build_summary(entry)
            text = f"<b>{html.escape(title)}</b>\n{html.escape(summary)}\n{entry.link}"
            send_telegram(text)
            time.sleep(2)  # 원문 사이트/텔레그램 rate limit 여유
        print(f"새 글 {len(new_entries)}개 중 중복 제거 후 {len(deduped)}개 전송 완료.")

    for e in entries:
        if e.link not in seen:
            seen.append(e.link)
    save_seen(seen)


if __name__ == "__main__":
    main()
