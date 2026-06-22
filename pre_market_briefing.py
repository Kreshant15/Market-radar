import os
import psycopg2
import requests
import yfinance as yf
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google import genai

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_PREMARKET")

def fetch_overnight_events():
    """Fetches domestic events saved in the last 24 hours."""
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
    """Fetches live global indices to provide context for the Indian open."""
    print("Fetching live global market cues...")
    cues = {}
    tickers = {
        "S&P 500 (US)": "^GSPC",
        "Nasdaq (US)": "^IXIC",
        "Brent Crude": "BZ=F",
        "USD/INR": "INR=X"
    }
    
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
    """Summarizes events AND live global data using Gemini."""
    client = genai.Client()
    
    # Format global cues for the AI
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

    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=prompt
        )
        return response.text
    except Exception as e:
        print(f"AI generation failed: {e}")
        return "Failed to generate AI analysis. Please check global cues manually."

def send_discord_briefing(briefing_text, global_cues):
    """Sends the briefing cleanly formatted with a vibrant Orange theme."""
    # Create a clean top-bar for the raw global data
    cues_str = " | ".join([f"**{k}:** {v}" for k, v in global_cues.items()])
    
    payload = {
        "embeds": [{
            "title": "🌅 Bade Sahab Pre-Market Briefing",
            "description": f"🌍 **Live Global Radar:**\n{cues_str}\n\n{briefing_text}",
            "color": 16744192, # Vibrant Trading Orange Hex (#FF9900)
            "footer": {"text": "Bade Sahab Live Trading Desk • Pre-Market Synthesis"}
        }]
    }
    
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        if response.status_code in [200, 204]:
            print("Briefing successfully dispatched to Discord!")
        else:
            print(f"Discord returned error status: {response.status_code}")
    except Exception as e:
        print(f"Failed to send Discord alert: {e}")

def main():
    print("Initializing Pre-Market Engine...")
    
    global_cues = fetch_global_cues()
    events = fetch_overnight_events()
    
    print(f"Found {len(events)} domestic events. Generating AI synthesis with global data...")
    briefing = generate_briefing(events, global_cues)
    
    print("Pushing briefing to Discord...")
    send_discord_briefing(briefing, global_cues)

if __name__ == "__main__":
    main()