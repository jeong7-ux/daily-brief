import os
import re
import requests
from bs4 import BeautifulSoup
import telebot
from dotenv import load_dotenv

# .env 파일에서 환경변수를 불러옵니다.
load_dotenv()

# 환경변수에서 키와 모델을 가져옵니다.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")

if not TELEGRAM_BOT_TOKEN or not OPENROUTER_API_KEY:
    print("Error: TELEGRAM_BOT_TOKEN and OPENROUTER_API_KEY environment variables are not set.")
    exit(1)

# 봇 초기화
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

def extract_text_from_url(url):
    """주어진 URL에 접속하여 본문 텍스트를 긁어옵니다."""
    try:
        # 봇 차단을 막기 위해 일반 브라우저처럼 User-Agent 설정
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 기사 본문은 주로 <p> 태그 안에 있으므로 추출
        paragraphs = soup.find_all('p')
        text = '\n'.join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
        
        # 텍스트가 너무 길면 LLM 토큰 한도를 초과할 수 있으므로 자름
        return text[:15000] 
    except Exception as e:
        return f"ERROR: {e}"

import time

def summarize_text(text):
    """OpenRouter API를 호출하여 텍스트를 요약합니다."""
    prompt = f"다음 기사 본문을 읽고 핵심 내용을 3~5줄 이내로 명확하게 한글로 요약해줘:\n\n{text}"
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": [{"role": "user", "content": prompt}]
                },
                timeout=60
            )
            response.raise_for_status()
            result = response.json()
            return result['choices'][0]['message']['content']
        except requests.exceptions.HTTPError as e:
            if response.status_code == 429:
                if attempt < max_retries - 1:
                    time.sleep(3)  # 3초 대기 후 재시도
                    continue
                return f"요약 실패: OpenRouter 무료 모델의 요청 한도(Rate Limit)를 초과했습니다. 잠시 후 다시 시도해주세요. (429 Error)"
            return f"요약 중 HTTP 오류 발생: {e}"
        except Exception as e:
            return f"요약 중 알 수 없는 오류 발생: {e}"

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """사용자가 보낸 메시지를 처리합니다."""
    text = message.text
    
    # 정규식으로 메시지 내의 URL 추출
    urls = re.findall(r'(https?://[^\s]+)', text)
    
    if not urls:
        bot.reply_to(message, "🤖 요약할 기사나 웹페이지의 URL(링크)을 보내주세요!")
        return
    
    bot.reply_to(message, "✅ URL을 확인했습니다. 본문을 추출하고 요약하는 중입니다... ⏳")
    
    for url in urls:
        # 1. URL에서 텍스트 추출
        article_text = extract_text_from_url(url)
        if article_text.startswith("ERROR:") or len(article_text) < 50:
            bot.send_message(message.chat.id, f"⚠️ 해당 URL({url})에서 본문을 제대로 읽어올 수 없습니다.\n보안이 걸려있거나 본문이 너무 짧은 페이지일 수 있습니다.")
            continue
            
        # 2. 텍스트 요약
        summary = summarize_text(article_text)
        
        # 3. 요약 결과 전송
        reply_msg = f"📝 **기사 요약 결과**:\n\n{summary}"
        bot.send_message(message.chat.id, reply_msg, parse_mode="Markdown")

if __name__ == "__main__":
    print(f"Bot is running! (Model: {OPENROUTER_MODEL})")
    print("Send a message to the bot on Telegram.")
    # 봇이 종료되지 않고 계속 메시지를 기다리도록 무한 폴링
    bot.infinity_polling()
