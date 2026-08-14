# 📅 Daily Brief

하루 두 번(아침 06:20, 저녁 18:20 KST), RSS 뉴스와 arXiv 신규 논문을 모아
**텔레그램으로** 보냅니다. GitHub Actions에서 돌기 때문에 PC를 켜 둘 필요가 없습니다.

```
GitHub Actions (21:20 / 09:20 UTC = 06:20 / 18:20 KST)
  └─ scripts/collect.py
       ├─ arXiv API      → cs.AI / cs.CL / cs.LG 신규 논문 (키워드 필터)
       ├─ RSS            → Hacker News, GeekNews, TechCrunch
       ├─ RSS            → 연합뉴스, 한겨레, 경향신문, 매일경제, 한국경제
       ├─ DeepL          → 영문 제목을 한글로 번역 (원문 병기)
       ├─ briefs/YYYY-MM-DD-HHMM.md 로 저장 후 커밋
       └─ 텔레그램 봇 API로 전송
```

## 번역

영문 제목만 DeepL로 번역하고 원문을 함께 보여줍니다. 국내 언론사 기사는 이미
한글이라 건너뜁니다(제목에 한글이 2자 미만이면 번역 대상으로 판단).

```
• 협업적 대화 결과 달성을 위한 다중 LLM 에이전트 시스템의 동적 거버넌스
  Dynamic Governance of Multi-LLM Agent Systems… · arXiv cs.AI
```

`DEEPL_API_KEY` 시크릿이 없으면 번역만 건너뛰고 원문 그대로 발송합니다.
번역이 실패해도(한도 소진, 인증 오류, 네트워크) 브리프 발송은 계속됩니다 —
있으면 좋은 기능이지 발송을 막을 이유는 아니기 때문입니다.

| 상황 | 동작 |
|---|---|
| 키 없음 | 번역 생략, 원문 발송 |
| 남은 한도 부족 | 보이는 항목만 번역하거나, 아예 원문 발송 |
| 사용량 조회 실패 | `fallback_chars_per_run`(1,500자)까지만 번역 |
| HTTP 456 (월 한도 소진) | 로그에 남기고 원문 발송 |
| HTTP 403 (키 오류) | 로그에 남기고 원문 발송 |
| 번역 개수 불일치 | 항목이 밀려 짝이 어긋나므로 전부 원문 유지 |

### 무료 한도를 넘지 않는 방법

DeepL Free는 **월 50만 자**가 무료입니다. 초과하면 `456`이 떨어지고 그 달 내내
번역이 멈춥니다. 그걸 만나기 전에 스스로 멈추도록 두 겹으로 막아 뒀습니다.

**1. 실행 전에 남은 한도를 조회합니다.** 매 실행마다 `/v2/usage`를 먼저 호출해
실제 사용량을 확인하고, 거기서 예산을 계산합니다. 추정이 아니라 실측값입니다.

```
남은 가용 = 한도 - 예약분(10%) - 사용량        ← 절대 상한
배분      = 남은 가용 ÷ 이번 달 남은 실행 횟수   ← 실행당 균등 배분
```

예약분 10%를 남기므로 최대로 써도 45만 자에서 멈춥니다. 균등 배분은 월초에
몰아 쓰고 월말에 굶는 것을 막습니다.

**2. 보이는 것부터 번역합니다.** 텔레그램에는 섹션당 8건만 나가는데, 예전에는
수집한 영문 전체(49~79건)를 번역했습니다. **실제로 읽는 부분은 그중 11건**뿐이라
76%가 낭비였습니다.

| | 건수 | 실행당 | 월 환산 | 한도 대비 |
|---|---|---|---|---|
| 텔레그램에 보이는 항목 | 11건 | 879자 | 52,740자 | **11%** |
| 마크다운 보관본까지 전부 | 49건 | 3,594자 | 215,640자 | 43% |

보이는 항목은 **절대 상한 안에서 항상** 번역하고, 보관본에만 남는 항목은
**균등 배분 안에서만** 번역합니다. 둘을 나눈 이유는 한도가 찰수록 배분이
제목 한 줄보다 작아져, 정작 읽는 부분이 번역되지 않기 때문입니다.

한도가 빠듯해지면 이 순서로 자연스럽게 줄어듭니다:

```
여유 충분  →  보이는 항목 + 보관용 항목 전부
여유 부족  →  보이는 항목만
한도 소진  →  전부 원문 (발송은 계속)
```

`sources.yml`에서 조절할 수 있습니다:

| 설정 | 기본값 | 설명 |
|---|---|---|
| `translate_hidden` | `true` | `false`로 두면 보이는 항목만 번역 (월 사용량 11%) |
| `monthly_reserve_ratio` | `0.10` | 손대지 않고 남길 한도 비율 |
| `runs_per_day` | `2` | 크론 횟수와 맞추세요. 균등 배분 계산에 씁니다 |
| `fallback_chars_per_run` | `1500` | 사용량 조회 실패 시 보수적 상한 |

사용량 직접 확인:

```bash
curl -H "Authorization: DeepL-Auth-Key $DEEPL_API_KEY" \
  https://api-free.deepl.com/v2/usage
```

## 최초 설정

### 1. 텔레그램 봇 만들기

텔레그램에서 [@BotFather](https://t.me/BotFather)를 찾아 대화를 시작합니다.

1. `/newbot` 입력
2. 봇 이름 입력 (예: `내 모닝 브리프`)
3. 봇 아이디 입력 — **반드시 `bot`으로 끝나야 합니다** (예: `jeong7_brief_bot`)
4. `123456789:AAF...` 형태의 **토큰**을 받습니다 → 이게 `TELEGRAM_BOT_TOKEN`

> 토큰은 봇의 비밀번호입니다. 절대 코드나 커밋에 넣지 마세요.

### 2. 내 chat_id 알아내기

봇은 **먼저 말을 건 사람에게만** 메시지를 보낼 수 있습니다. 순서가 중요합니다.

1. 방금 만든 봇을 검색해 대화방을 열고 `/start`를 누릅니다 (아무 메시지나 보내도 됩니다)
2. 브라우저에서 아래 주소를 엽니다 (`<토큰>`을 바꿔서):

   ```
   https://api.telegram.org/bot<토큰>/getUpdates
   ```

3. 응답 JSON에서 `"chat":{"id":123456789` 부분의 숫자가 `TELEGRAM_CHAT_ID`입니다

`{"ok":true,"result":[]}` 처럼 비어 있으면 1번을 안 한 것입니다. 봇에게 먼저 메시지를 보내세요.

### 3. GitHub Secrets 등록

```bash
gh secret set TELEGRAM_BOT_TOKEN --repo jeong7-ux/daily-brief
gh secret set TELEGRAM_CHAT_ID  --repo jeong7-ux/daily-brief
gh secret set DEEPL_API_KEY     --repo jeong7-ux/daily-brief   # 선택 — 없으면 번역만 생략
```

각각 실행하면 값을 물어봅니다. 웹에서 하려면
`Settings → Secrets and variables → Actions → New repository secret`.

### 4. 동작 확인

```bash
gh workflow run daily-brief.yml --repo jeong7-ux/daily-brief
gh run watch --repo jeong7-ux/daily-brief
```

## 수집 대상 바꾸기

`sources.yml` 한 파일만 고치면 됩니다. 워크플로는 건드릴 필요 없습니다.

```yaml
sections:
  - name: "관심 분야"
    emoji: "🔬"
    feeds:
      - type: rss
        label: "출처 이름"
        url: "https://example.com/feed.xml"
        max_items: 10
        keywords: [키워드1, 키워드2]   # 제목·요약에 하나라도 있어야 채택. 생략하면 전부.
```

`type`은 `rss`와 `arxiv` 두 가지입니다.

| 설정 | 위치 | 설명 |
|---|---|---|
| `lookback_hours` | settings / 피드별 | 몇 시간 이내 항목까지 수집할지. 피드별 지정이 우선. |
| `telegram_max_per_section` | settings | 텔레그램 메시지에 섹션당 최대 몇 건. 나머지는 `…외 N건`. |
| `max_items` | 피드별 | 해당 피드에서 가져올 최대 건수 |
| `keywords` | 피드별 | 제목·요약 부분 문자열 매칭 (대소문자 무시) |

arXiv 피드의 `lookback_hours`가 72인 이유는 arXiv 공지가 하루 이상 지연되고
주말에는 아예 공지가 없기 때문입니다. 창을 넓혀도 중복은 아래 방식으로 걸러집니다.

## 중복 처리

한 번 보낸 링크는 `state/seen.json`에 30일간 기록되어 다시 보내지 않습니다.
그래서 수집 창을 넉넉히 잡아도 같은 기사가 반복해서 오지 않습니다.

전송이 실패하면 `seen.json`을 갱신하지 않으므로, 다음 실행 때 놓친 항목을 다시 시도합니다.

## 로컬에서 테스트

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
DRY_RUN=1 .venv/Scripts/python scripts/collect.py
```

`DRY_RUN=1`이면 파일 저장도 텔레그램 전송도 하지 않고 결과 Markdown만 출력합니다.
실제 전송까지 테스트하려면 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`를 환경변수로 주고
`DRY_RUN` 없이 실행하세요.

## 알아둘 점

- **정시 도착은 보장되지 않습니다.** GitHub Actions 스케줄은 부하에 따라 5~30분 밀립니다.
  06:20 / 18:20 KST에 시작하도록 잡아 두었지만 실제 도착은 그로부터 30분 이내입니다.
- **public 저장소는 60일간 활동이 없으면 스케줄이 자동 정지됩니다.**
  이 워크플로는 매일 브리프를 커밋하므로 자동으로 해결됩니다.
- **수집 실패 시 텔레그램으로 알림이 옵니다.** 조용히 멈추는 상황을 막기 위한 장치입니다.
- 워크플로 토큰 권한은 `contents: write` 하나뿐입니다.
