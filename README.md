# 텔레그램 뉴스/요약 봇

텔레그램 봇 하나(`@treewind_wiki_news_bot`)로 두 가지를 합니다.

- **URL 보내면 요약**: 대화방에 링크를 보내면 본문을 긁어와 OpenRouter LLM으로 요약해 답장 (`bot.py`, PC에서 직접 실행해야 동작)
- **주기적 자동 발송**: GitHub Actions가 정해진 주기로 뉴스/트렌드를 모아 먼저 말을 걸어옴 (PC 꺼져 있어도 동작)

아키텍처와 데이터 흐름도는 [docs/architecture.md](docs/architecture.md)에 정리되어 있습니다.

## 자동 발송 목록

| 워크플로우 | 주기 | 내용 |
|---|---|---|
| `hada-news` | KST 07:00 / 19:00 | GeekNews(news.hada.io) 새 글, 원문 요약 |
| `jtbc-news` | 30분마다 | JTBC 속보, 상위 5건 요약 |
| `ai-news` | 2시간마다 | 더에이아이 AI 인기뉴스, 상위 5건 원문 요약 |
| `github-daily` | KST 00:00 | GitHub 트렌딩 TOP5·신규 저장소 TOP3 + HuggingFace 트렌딩 모델 TOP5·신규 TOP3, DeepL 번역 병기 |

## 최초 설정

### 1. 텔레그램 봇 만들기

[@BotFather](https://t.me/BotFather)에서 `/newbot`으로 봇을 만들고 토큰을 받습니다.
이게 `TELEGRAM_BOT_TOKEN`입니다.

### 2. 내 chat_id 알아내기

1. 만든 봇에게 아무 메시지나 먼저 보냅니다 (`/start` 등)
2. 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates` 접속
3. 응답 JSON의 `"chat":{"id":...}` 숫자가 `TELEGRAM_CHAT_ID`

### 3. GitHub Secrets 등록

```bash
gh secret set TELEGRAM_BOT_TOKEN
gh secret set TELEGRAM_CHAT_ID
gh secret set OPENROUTER_API_KEY   # hada-news / jtbc-news / ai-news / bot.py에 필요
gh secret set DEEPL_API_KEY        # 선택 — github-daily 번역용, 없으면 원문만 발송
```

### 4. 동작 확인

```bash
gh workflow run hada-news.yml
gh workflow run jtbc-news.yml
gh workflow run ai-news.yml
gh workflow run github-daily.yml
gh run watch
```

## 로컬에서 대화형 봇(bot.py) 실행

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
```

`.env` 파일에 아래 값을 채운 뒤 실행합니다.

```
TELEGRAM_BOT_TOKEN=...
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
```

```bash
.venv/Scripts/python bot.py
```

콘솔을 닫거나 PC가 꺼지면 멈춥니다. 24시간 상시 응답이 필요하면 서버/VPS 등
항상 켜진 곳에 배포해야 합니다.

## 중복 방지

`hada-news` / `jtbc-news` / `ai-news`는 `state/seen_*.json`에 처리한 글의 ID를 기록해
같은 글을 다시 보내지 않습니다. 최초 실행은 스팸 방지를 위해 seen 목록만 채우고
발송은 건너뜁니다. 자세한 동작은 [docs/architecture.md](docs/architecture.md) 참고.
