import os
import psycopg2
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google import genai

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
# FIX: Updated to match our new premarket webhook secret
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_PREMARKET")

def fetch_overnight_events():
    """Fetches events saved in the last 24 hours to summarize."""
    try:
        conn = psycopg2.connect(DB_URL)
        cursor = conn.cursor()
        
        # Look back 24 hours for any events to summarize
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

def generate_briefing(events):
    """Summarizes collected events using Gemini 3.1 Flash-Lite."""
    client = genai.Client()
    
    if not events:
        # Prompt if there is no major local news to summarize
        prompt = (
            "You are an Elite Options Strategist. There is no major breaking domestic "
            "market news logged in the last 24 hours. Provide a standard, professional, "
            "concise morning market layout summarizing what traders should focus on today "
            "for Nifty/BankNifty, global sentiment indicators, and basic risk management rules."
        )
    else:
        # Prompt with event logs
        events_summary = ""
        for i, ev in enumerate(events, 1):
            events_summary += f"{i}. [{ev[2]}] {ev[1]} (Impact: {ev[3]}/100) - Headline: {ev[0]}\n"
            
        prompt = (
            f"You are an Elite Options Strategist. Summarize the following overnight events "
            f"for the Indian stock market open today:\n\n{events_summary}\n"
            f"Generate a professional, structured morning brief with the following sections:\n"
            f"**Overnight Summary:** [3-4 sentence concise digest of global/domestic cues]\n"
            f"**Expected Market Open:** [Slightly Bullish/Bearish/Flat, and key levels to watch]\n"
            f"**Recommended Action Plan:** [How to play current options strategies and manage risk]"
        )

    response = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=prompt
    )
    return response.text

def send_discord_briefing(briefing_text):
    """Sends the briefing cleanly formatted to the #pre-market-briefing channel."""
    payload = {
        "embeds": [{
            "title": "🌅 Bade Sahab Pre-Market Briefing",
            "description": briefing_text,
            "color": 3447003, # Premium Blue Accent
            "footer": {"text": "Bade Sahab Live Trading Desk • Sent at 8:30 AM IST"}
        }]
    }
    
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        if response.status_code == 204 or response.status_code == 200:
            print("Briefing successfully dispatched to Discord!")
        else:
            print(f"Discord returned error status: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Failed to send Discord alert: {e}")

def main():
    print("Fetching overnight events for Pre-Market Briefing...")
    events = fetch_overnight_events()
    print(f"Found {len(events)} events from the last 24 hours. Generating AI synthesis...")
    briefing = generate_briefing(events)
    print("Pushing briefing to Discord...")
    send_discord_briefing(briefing)

if __name__ == "__main__":
    main()