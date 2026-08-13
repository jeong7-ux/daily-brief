#!/usr/bin/env python3
"""RSS 뉴스 + arXiv 논문을 모아 Markdown으로 저장하고 텔레그램으로 보냅니다.

환경변수:
  TELEGRAM_BOT_TOKEN  BotFather가 발급한 토큰 (없으면 전송을 건너뛰고 본문만 출력)
  TELEGRAM_CHAT_ID    받을 대화방 ID
  DRY_RUN             "1"이면 파일 저장/전송 없이 결과만 출력
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
import yaml

KST = timezone(timedelta(hours=9), "KST")
ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = ROOT / "sources.yml"
BRIEF_DIR = ROOT / "briefs"
STATE_FILE = ROOT / "state" / "seen.json"

USER_AGENT = "daily-brief-bot/1.0 (+https://github.com/jeong7-ux/daily-brief)"
ARXIV_API = "http://export.arxiv.org/api/query"
TELEGRAM_CHUNK = 3500  # 텔레그램 한도는 4096자. 여유를 둠.
SEEN_RETENTION_DAYS = 30
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- 유틸


def log(msg: str) -> None:
    print(msg, flush=True)


def strip_html(text: str, limit: int = 220) -> str:
    """RSS 요약에 섞여 있는 태그와 공백을 정리하고 잘라냅니다."""
    if not text:
        return ""
    clean = WS_RE.sub(" ", TAG_RE.sub(" ", text)).strip()
    if len(clean) > limit:
        clean = clean[: limit - 1].rstrip() + "…"
    return clean


def entry_time(entry) -> datetime | None:
    """피드마다 제각각인 발행 시각 필드를 UTC datetime으로 통일합니다."""
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = getattr(entry, field, None)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


def load_sources() -> dict:
    with SOURCES_FILE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_seen() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        log("  ! seen.json을 읽지 못해 빈 상태로 시작합니다")
        return {}


def save_seen(seen: dict[str, str], today: datetime) -> None:
    """오래된 기록은 버려서 파일이 무한정 커지지 않게 합니다."""
    cutoff = (today - timedelta(days=SEEN_RETENTION_DAYS)).strftime("%Y-%m-%d")
    pruned = {url: day for url, day in seen.items() if day >= cutoff}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as fh:
        json.dump(pruned, fh, ensure_ascii=False, indent=0, sort_keys=True)
    log(f"  seen.json: {len(pruned)}건 유지 (이전 {len(seen)}건)")


def matches_keywords(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    lowered = text.lower()
    return any(kw.lower() in lowered for kw in keywords)


# --------------------------------------------------------------------------- 수집


def parse_feed(url: str) -> feedparser.FeedParserDict | None:
    """feedparser에 직접 URL을 주지 않고 requests로 받아옵니다.

    일부 언론사가 기본 UA를 차단하고, 타임아웃 제어도 이쪽이 확실합니다.
    """
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log(f"  ! 요청 실패: {exc}")
        return None
    return feedparser.parse(resp.content)


def collect_rss(feed_cfg: dict, cutoff: datetime, seen: dict) -> list[dict]:
    label = feed_cfg.get("label", feed_cfg["url"])
    parsed = parse_feed(feed_cfg["url"])
    if parsed is None:
        return []
    if parsed.bozo and not parsed.entries:
        log(f"  ! {label}: 파싱 실패 ({parsed.bozo_exception})")
        return []

    keywords = feed_cfg.get("keywords", [])
    items, skipped_old, skipped_seen = [], 0, 0

    for entry in parsed.entries:
        link = getattr(entry, "link", "")
        if not link:
            continue
        if link in seen:
            skipped_seen += 1
            continue

        published = entry_time(entry)
        # 발행 시각이 없는 피드는 버리지 않고 통과시킵니다(seen이 중복을 막아줌).
        if published and published < cutoff:
            skipped_old += 1
            continue

        title = strip_html(getattr(entry, "title", "(제목 없음)"), limit=200)
        summary = strip_html(getattr(entry, "summary", ""))
        if not matches_keywords(f"{title} {summary}", keywords):
            continue

        items.append(
            {
                "title": title,
                "link": link,
                "summary": summary,
                "source": label,
                "published": published,
            }
        )
        if len(items) >= feed_cfg.get("max_items", 10):
            break

    log(f"  {label}: {len(items)}건 (기간초과 {skipped_old}, 중복 {skipped_seen})")
    return items


def collect_arxiv(feed_cfg: dict, cutoff: datetime, seen: dict) -> list[dict]:
    label = feed_cfg.get("label", "arXiv")
    categories = feed_cfg.get("categories", ["cs.AI"])
    query = " OR ".join(f"cat:{cat}" for cat in categories)
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": feed_cfg.get("max_items", 40),
    }

    try:
        resp = requests.get(
            ARXIV_API, params=params, headers={"User-Agent": USER_AGENT}, timeout=45
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        log(f"  ! {label} 요청 실패: {exc}")
        return []

    parsed = feedparser.parse(resp.content)
    keywords = feed_cfg.get("keywords", [])
    items, skipped_old, skipped_seen = [], 0, 0

    for entry in parsed.entries:
        link = getattr(entry, "link", "")
        if not link:
            continue
        if link in seen:
            skipped_seen += 1
            continue

        published = entry_time(entry)
        if published and published < cutoff:
            skipped_old += 1
            continue

        title = strip_html(getattr(entry, "title", "(제목 없음)"), limit=200)
        summary = strip_html(getattr(entry, "summary", ""), limit=300)
        if not matches_keywords(f"{title} {summary}", keywords):
            continue

        # arXiv 카테고리 태그를 출처 라벨에 붙여 어느 분야인지 보이게 합니다.
        primary = ""
        tags = getattr(entry, "tags", None)
        if tags:
            primary = tags[0].get("term", "")

        items.append(
            {
                "title": title,
                "link": link,
                "summary": summary,
                "source": f"{label} {primary}".strip(),
                "published": published,
            }
        )

    log(f"  {label}: {len(items)}건 (기간초과 {skipped_old}, 중복 {skipped_seen})")
    time.sleep(3)  # arXiv 권장 요청 간격
    return items


def collect_section(section: dict, default_lookback: int, seen: dict) -> list[dict]:
    log(f"[{section['name']}]")
    now_utc = datetime.now(timezone.utc)
    items: list[dict] = []
    for feed_cfg in section.get("feeds", []):
        # arXiv는 공지 지연이 하루 이상이라 피드별로 기간을 늘릴 수 있게 했습니다.
        hours = feed_cfg.get("lookback_hours", default_lookback)
        cutoff = now_utc - timedelta(hours=hours)

        kind = feed_cfg.get("type", "rss")
        if kind == "arxiv":
            items.extend(collect_arxiv(feed_cfg, cutoff, seen))
        elif kind == "rss":
            items.extend(collect_rss(feed_cfg, cutoff, seen))
        else:
            log(f"  ! 알 수 없는 type: {kind}")

    # 최신순 정렬. 발행 시각이 없는 항목은 뒤로 보냅니다.
    items.sort(key=lambda it: it["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items


# --------------------------------------------------------------------------- 렌더링


def format_date_header(now: datetime) -> str:
    return f"{now.strftime('%Y-%m-%d')} ({WEEKDAYS[now.weekday()]})"


def render_markdown(sections: list[tuple[dict, list[dict]]], now: datetime) -> str:
    lines = [f"# 📅 {format_date_header(now)} 모닝 브리프", ""]
    total = sum(len(items) for _, items in sections)
    lines.append(f"> 총 {total}건 · 생성 {now.strftime('%Y-%m-%d %H:%M')} KST")
    lines.append("")

    for section, items in sections:
        lines.append(f"## {section.get('emoji', '•')} {section['name']} ({len(items)})")
        lines.append("")
        if not items:
            lines.append("_새로운 항목이 없습니다._")
            lines.append("")
            continue
        for item in items:
            when = item["published"].astimezone(KST).strftime("%m/%d %H:%M") if item["published"] else "시각 미상"
            lines.append(f"- [{item['title']}]({item['link']})")
            lines.append(f"  `{item['source']}` · {when}")
            if item["summary"]:
                lines.append(f"  > {item['summary']}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_telegram(sections: list[tuple[dict, list[dict]]], now: datetime, limit: int) -> str:
    esc = html.escape
    parts = [f"<b>📅 {format_date_header(now)} 모닝 브리프</b>", ""]

    for section, items in sections:
        parts.append(f"<b>{section.get('emoji', '•')} {section['name']}</b>")
        if not items:
            parts.append("  <i>새로운 항목 없음</i>")
            parts.append("")
            continue
        for item in items[:limit]:
            parts.append(f"• <a href=\"{esc(item['link'], quote=True)}\">{esc(item['title'])}</a>")
            parts.append(f"  <i>{esc(item['source'])}</i>")
        if len(items) > limit:
            parts.append(f"  <i>… 외 {len(items) - limit}건</i>")
        parts.append("")

    return "\n".join(parts).strip()


def chunk_message(text: str, size: int = TELEGRAM_CHUNK) -> list[str]:
    """줄 경계를 지켜 자릅니다. HTML 태그가 중간에 끊기면 전송이 실패합니다."""
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > size and current:
            chunks.append(current.rstrip())
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


# --------------------------------------------------------------------------- 전송


def send_telegram(text: str, token: str, chat_id: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok = True
    for idx, chunk in enumerate(chunk_message(text), start=1):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code != 200:
                # 토큰이 로그에 남지 않도록 응답 본문만 출력합니다.
                log(f"  ! 텔레그램 전송 실패 (chunk {idx}): {resp.status_code} {resp.text[:300]}")
                ok = False
            else:
                log(f"  텔레그램 전송 완료 (chunk {idx}, {len(chunk)}자)")
        except requests.RequestException as exc:
            log(f"  ! 텔레그램 요청 오류 (chunk {idx}): {exc}")
            ok = False
        time.sleep(1)  # 초당 여러 건 보내면 429가 납니다.
    return ok


# --------------------------------------------------------------------------- 메인


def main() -> int:
    dry_run = os.environ.get("DRY_RUN") == "1"
    now = datetime.now(KST)
    config = load_sources()
    settings = config.get("settings", {})
    lookback = settings.get("lookback_hours", 26)
    tg_limit = settings.get("telegram_max_per_section", 8)

    log(f"수집 시작: {now.strftime('%Y-%m-%d %H:%M')} KST (기본 최근 {lookback}시간)")
    seen = load_seen()
    log(f"기존 seen 기록: {len(seen)}건\n")

    sections: list[tuple[dict, list[dict]]] = []
    for section in config.get("sections", []):
        items = collect_section(section, lookback, seen)
        sections.append((section, items))
        log("")

    total = sum(len(items) for _, items in sections)
    log(f"총 {total}건 수집")

    if total == 0:
        log("새 항목이 없어 종료합니다.")
        return 0

    markdown = render_markdown(sections, now)
    message = render_telegram(sections, now, tg_limit)

    if dry_run:
        log("\n--- DRY RUN: 저장/전송 없음 ---\n")
        print(markdown)
        return 0

    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    brief_path = BRIEF_DIR / f"{now.strftime('%Y-%m-%d')}.md"
    brief_path.write_text(markdown, encoding="utf-8")
    log(f"저장: {brief_path.relative_to(ROOT)}")

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    sent = True
    if token and chat_id:
        sent = send_telegram(message, token, chat_id)
    else:
        log("  ! TELEGRAM_BOT_TOKEN/CHAT_ID가 없어 전송을 건너뜁니다")

    # 전송이 실패했으면 seen에 기록하지 않습니다. 다음 실행에서 다시 시도됩니다.
    if sent:
        today = now.strftime("%Y-%m-%d")
        for _, items in sections:
            for item in items:
                seen[item["link"]] = today
        save_seen(seen, now)
    else:
        log("  전송 실패로 seen 기록을 건너뜁니다 (다음 실행에서 재시도)")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
