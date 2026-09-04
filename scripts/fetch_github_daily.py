import html
import os
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY")

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

TRENDING_LIMIT = 5
RECENT_LIMIT = 3
RECENT_MIN_STARS = 5

HF_TRENDING_LIMIT = 5
HF_RECENT_LIMIT = 3
HF_RECENT_WINDOW_DAYS = 14
HF_TRENDING_POOL = 100


def fetch_trending(limit=TRENDING_LIMIT):
    """github.com/trending 일간 급상승 순위를 스크래핑."""
    resp = requests.get("https://github.com/trending?since=daily", headers=BROWSER_HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for article in soup.select("article.Box-row")[:limit]:
        h2 = article.select_one("h2 a")
        full_name = h2["href"].strip("/")
        desc_tag = article.select_one("p")
        description = desc_tag.get_text(strip=True) if desc_tag else ""
        star_tag = article.select_one("span.d-inline-block.float-sm-right")
        stars_today = star_tag.get_text(strip=True) if star_tag else ""
        results.append(
            {
                "name": full_name,
                "url": f"https://github.com/{full_name}",
                "description": description,
                "stars_today": stars_today,
            }
        )
    return results


def fetch_recent(limit=RECENT_LIMIT, min_stars=RECENT_MIN_STARS):
    """최근 24시간 내 생성되고 별 min_stars개 이상인 저장소 중 최신순."""
    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    resp = requests.get(
        "https://api.github.com/search/repositories",
        headers=headers,
        params={
            "q": f"created:>{since} stars:>={min_stars}",
            "sort": "created",
            "order": "desc",
            "per_page": limit,
        },
        timeout=15,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])[:limit]
    return [
        {
            "name": it["full_name"],
            "url": it["html_url"],
            "description": it.get("description") or "",
            "stars": it["stargazers_count"],
        }
        for it in items
    ]


def _hf_model_item(model):
    return {
        "name": model["id"],
        "url": f"https://huggingface.co/{model['id']}",
        "description": model.get("pipeline_tag") or "",
        "likes": model.get("likes", 0),
        "downloads": model.get("downloads", 0),
    }


def fetch_hf_trending(limit=HF_TRENDING_LIMIT):
    """huggingface.co 트렌딩 스코어 기준 상위 모델."""
    resp = requests.get(
        "https://huggingface.co/api/models",
        params={"sort": "trendingScore", "direction": -1, "limit": limit},
        timeout=15,
    )
    resp.raise_for_status()
    return [_hf_model_item(m) for m in resp.json()]


def fetch_hf_recent_trending(limit=HF_RECENT_LIMIT, window_days=HF_RECENT_WINDOW_DAYS, pool=HF_TRENDING_POOL):
    """업로드량이 압도적으로 많아 '방금 등록 + 좋아요'로는 결과가 거의 없으므로,
    트렌딩 중인 모델 중 최근 window_days 이내에 등록된 것만 최신순으로 뽑는다."""
    resp = requests.get(
        "https://huggingface.co/api/models",
        params={"sort": "trendingScore", "direction": -1, "limit": pool},
        timeout=15,
    )
    resp.raise_for_status()
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    candidates = [
        m for m in resp.json()
        if datetime.fromisoformat(m["createdAt"].replace("Z", "+00:00")) > cutoff
    ]
    candidates.sort(key=lambda m: m["createdAt"], reverse=True)
    return [_hf_model_item(m) for m in candidates[:limit]]


def deepl_endpoint():
    is_free_key = DEEPL_API_KEY and DEEPL_API_KEY.endswith(":fx")
    return "https://api-free.deepl.com/v2/translate" if is_free_key else "https://api.deepl.com/v2/translate"


def translate_batch(texts):
    """DeepL로 일괄 번역. 키가 없거나 실패하면 None을 반환해 원문 유지를 유도."""
    if not DEEPL_API_KEY or not texts:
        return None
    try:
        resp = requests.post(
            deepl_endpoint(),
            headers={"Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}"},
            data=[("text", t) for t in texts] + [("target_lang", "KO")],
            timeout=20,
        )
        resp.raise_for_status()
        translations = resp.json().get("translations", [])
        if len(translations) != len(texts):
            return None
        return [t["text"] for t in translations]
    except Exception:
        return None


def attach_translations(items):
    """설명이 있는 항목만 모아 한 번의 요청으로 번역하고 결과를 병기용으로 붙인다."""
    idxs = [i for i, it in enumerate(items) if it["description"]]
    translated = translate_batch([items[i]["description"] for i in idxs])
    if not translated:
        return
    for i, ko in zip(idxs, translated):
        items[i]["description_ko"] = ko


def format_line(index, item, star_label):
    original = item["description"]
    ko = item.get("description_ko")
    if ko and ko.strip() and ko.strip() != original.strip():
        desc = f" - {html.escape(ko)} ({html.escape(original)})"
    elif original:
        desc = f" - {html.escape(original)}"
    else:
        desc = ""
    return f"{index}. <a href=\"{item['url']}\">{html.escape(item['name'])}</a> ({star_label}){desc}"


def send_telegram(text):
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    resp.raise_for_status()


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise SystemExit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 필요합니다.")

    trending = fetch_trending()
    recent = fetch_recent()
    attach_translations(trending + recent)

    hf_trending = fetch_hf_trending()
    hf_recent = fetch_hf_recent_trending()

    lines = ["<b>\U0001F525 오늘의 GitHub 트렌딩 TOP 5</b>"]
    lines += [format_line(i, r, r["stars_today"]) for i, r in enumerate(trending, 1)]

    lines.append("")
    lines.append(f"<b>\U0001F195 최근 등록된 저장소 (★{RECENT_MIN_STARS}+) TOP 3</b>")
    lines += [format_line(i, r, f"★{r['stars']}") for i, r in enumerate(recent, 1)]

    lines.append("")
    lines.append("<b>\U0001F917 오늘의 HuggingFace 트렌딩 모델 TOP 5</b>")
    lines += [format_line(i, r, f"\U0001F44D{r['likes']} ⬇{r['downloads']:,}") for i, r in enumerate(hf_trending, 1)]

    lines.append("")
    lines.append(f"<b>\U0001F195 최근 {HF_RECENT_WINDOW_DAYS}일 이내 등록된 주목받는 모델 TOP 3</b>")
    lines += [format_line(i, r, f"\U0001F44D{r['likes']} ⬇{r['downloads']:,}") for i, r in enumerate(hf_recent, 1)]

    send_telegram("\n".join(lines))
    print(
        f"GitHub 트렌딩 {len(trending)}개, 신규 저장소 {len(recent)}개, "
        f"HF 트렌딩 {len(hf_trending)}개, HF 신규 {len(hf_recent)}개 전송 완료."
    )


if __name__ == "__main__":
    main()
