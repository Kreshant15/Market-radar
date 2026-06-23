import os
import json
import psycopg2
import requests
import re
import time
import yfinance as yf
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google import genai
from gtts import gTTS

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_PREMARKET")

def fetch_overnight_events():
    try:
        conn = psycopg2.connect(DB_URL)
        cursor = conn.cursor()
        yesterday = datetime.now() - timedelta(hours=24)
        cursor.execute('''
            SELECT headline, event, event_type, impact_score, reasoning 
            FROM events 
            WHERE timestamp >= %s
            ORDER BY timestamp DESC
        ''', (yesterday,))
        events = cursor.fetchall()
        cursor.close()
        conn.close()
        return events
    except Exception as e:
        print(f"Database fetch error: {e}")
        return []

def fetch_global_cues():
    print("Fetching live global market cues...")
    cues = {}
    tickers = {"S&P 500 (US)": "^GSPC", "Nasdaq (US)": "^IXIC", "Brent Crude": "BZ=F", "USD/INR": "INR=X"}
    
    for name, symbol in tickers.items():
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="2d")
            if len(hist) > 1:
                prev_close = hist['Close'].iloc[0]
                close_price = hist['Close'].iloc[-1]
                pct_change = ((close_price - prev_close) / prev_close) * 100
                cues[name] = f"{close_price:,.2f} ({pct_change:+.2f}%)"
            elif not hist.empty:
                cues[name] = f"{hist['Close'].iloc[-1]:,.2f}"
            else:
                cues[name] = "Data Unavailable"
        except Exception:
            cues[name] = "Error"
    return cues

def generate_briefing(events, global_cues):
    client = genai.Client()
    cues_text = "\n".join([f"- {k}: {v}" for k, v in global_cues.items()])
    
    if not events:
        prompt = (
            "You are an Elite Options Strategist for an Indian trading desk. "
            "There is no major breaking domestic market news logged in the last 24 hours. "
            "However, here are the live global market cues from this morning:\n\n"
            f"{cues_text}\n\n"
            "Provide a professional, highly analytical morning market briefing summarizing what Indian traders "
            "should expect at the 9:15 AM open based on these specific global cues. Include a likely gap-up/gap-down prediction, "
            "the sentiment impact of the US markets/Crude, and a standard risk management plan for today."
        )
    else:
        events_summary = ""
        for i, ev in enumerate(events, 1):
            events_summary += f"{i}. [{ev[2]}] {ev[1]} (Impact: {ev[3]}/100) - Headline: {ev[0]}\n"
            
        prompt = (
            f"You are an Elite Options Strategist. Summarize the following domestic overnight events "
            f"AND the live global cues for the Indian stock market open today:\n\n"
            f"Live Global Cues:\n{cues_text}\n\n"
            f"Domestic Overnight Events:\n{events_summary}\n"
            f"Generate a professional, structured morning brief with the following sections:\n"
            f"**Global Cues & Overnight Summary:** [Digest of global cues and domestic news]\n"
            f"**Expected Market Open:** [Gap Up/Down/Flat, and key sentiment]\n"
            f"**Recommended Action Plan:** [How to play current options strategies and manage risk]"
        )

    # 🛡️ EXPONENTIAL BACKOFF: Retry up to 5 times with progressive delays to dodge rate limits
    delays = [1, 2, 4, 8, 16]
    for attempt, delay in enumerate(delays):
        try:
            response = client.models.generate_content(model="gemini-3.1-flash-lite", contents=prompt)
            return response.text
        except Exception as e:
            if attempt == len(delays) - 1:
                print(f"AI generation completely failed after {len(delays)} attempts: {e}")
                return "Failed to generate AI analysis. Please check global cues manually."
            time.sleep(delay)

def create_audio_file(briefing_text, global_cues):
    """Converts the text briefing into a professional podcast-style audio file."""
    print("Generating Bade Sahab Radio audio...")
    
    # Clean the text for the AI voice (remove asterisks and markdown)
    clean_text = re.sub(r'[*#_~]', '', briefing_text)
    
    # Add an intro and the global cues to the spoken text
    intro = "Good morning. This is Bade Sahab Radio with your pre-market briefing. Let's look at the live global radar. "
    for k, v in global_cues.items():
        clean_val = v.split('(')[0] if '(' in v else v 
        intro += f"{k} is trading at {clean_val}. "
        
    full_script = intro + "Now for the main briefing. " + clean_text + " Good luck trading today."
    
    filename = "bade_sahab_radio.mp3"
    try:
        tts = gTTS(text=full_script, lang='en', tld='co.in', slow=False)
        tts.save(filename)
        return filename
    except Exception as e:
        print(f"Audio generation failed: {e}")
        return None

def send_discord_briefing(briefing_text, global_cues, audio_file):
    cues_str = " | ".join([f"**{k}:** {v}" for k, v in global_cues.items()])
    
    embed = {
        "title": "🌅 Bade Sahab Pre-Market Briefing & Radio",
        "description": f"🎙️ **Hit Play on the Audio File Below!**\n\n🌍 **Live Global Radar:**\n{cues_str}\n\n{briefing_text}",
        "color": 16744192,
        "footer": {"text": "Bade Sahab Live Trading Desk • Pre-Market Synthesis"}
    }
    
    try:
        if audio_file and os.path.exists(audio_file):
            print("Uploading MP3 to Discord...")
            with open(audio_file, "rb") as f:
                response = requests.post(
                    DISCORD_WEBHOOK_URL,
                    data={"payload_json": json.dumps({"embeds": [embed]})},
                    files={"file": ("bade_sahab_radio.mp3", f, "audio/mpeg")}
                )
            os.remove(audio_file) 
        else:
            response = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
            
        if response.status_code in [200, 204]:
            print("Briefing successfully dispatched!")
        else:
            print(f"Discord error: {response.status_code}")
    except Exception as e:
        print(f"Failed to send Discord alert: {e}")

def main():
    print("Initializing Pre-Market Engine...")
    global_cues = fetch_global_cues()
    events = fetch_overnight_events()
    
    briefing = generate_briefing(events, global_cues)
    audio_file = create_audio_file(briefing, global_cues)
    
    print("Pushing briefing and audio to Discord...")
    send_discord_briefing(briefing, global_cues, audio_file)

if __name__ == "__main__":
    main()