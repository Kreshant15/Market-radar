import os
import psycopg2
import requests
from datetime import datetime
from dotenv import load_dotenv
from google import genai

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def fetch_overnight_events():
    """Fetches all high-impact events from the last 24 hours."""
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    
    # Removed 'nifty_direction' from the SELECT query to match our database schema
    cursor.execute('''
        SELECT event_type, headline, impact_score, reasoning
        FROM events
        WHERE timestamp >= NOW() - INTERVAL '24 hours'
        ORDER BY impact_score DESC
    ''')
    
    events = cursor.fetchall()
    cursor.close()
    conn.close()
    return events

def generate_briefing(events):
    """Passes the events to Gemini to generate an opening bell summary."""
    if not events:
        return "**Expected Open:** Flat\n**Market Sentiment:** Neutral\n\nNo significant macroeconomic or market events recorded overnight. Expect a technically driven open."

    # Updated the index mapping: e[0]=type, e[1]=headline, e[2]=score, e[3]=reasoning
    events_text = "\n".join([f"- [{e[0]}] (Score: {e[2]}/100): {e[1]} - Context: {e[3]}" for e in events])

    client = genai.Client()
    prompt = (
        "You are an elite Indian Stock Market Analyst. Based on the following events from the last 24 hours, "
        "provide a concise 'Pre-Market Briefing' for F&O traders.\n\n"
        f"Overnight Events:\n{events_text}\n\n"
        "Format your response EXACTLY like this (use emojis to make it look good):\n"
        "**Expected Open:** [Gap Up / Gap Down / Flat]\n"
        "**Market Sentiment:** [Bullish / Bearish / Neutral]\n"
        "**Key Sectors in Focus:** [List 2-3 sectors to watch today]\n\n"
        "**Overnight Summary:**\n[Write 3-4 sentences summarizing the global/domestic cues and what traders should watch today based strictly on the provided events.]"
    )

    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=prompt
    )
    return response.text

def send_discord_briefing(briefing_text):
    """Sends the briefing as a special Gold-colored embed to Discord."""
    embed = {
        "title": "🌅 Morning Pre-Market Briefing",
        "description": briefing_text,
        "color": 16766720, # A nice Gold/Yellow color for mornings
        "footer": {"text": f"Bade Sahab • Generated at {datetime.now().strftime('%Y-%m-%d')} for Indian Markets"}
    }
    
    payload = {"embeds": [embed]}
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload)
    except Exception as e:
        print(f"Failed to send Discord alert: {e}")

def main():
    print("Fetching overnight events for Pre-Market Briefing...")
    events = fetch_overnight_events()
    
    print(f"Found {len(events)} events from the last 24 hours. Generating AI synthesis...")
    briefing = generate_briefing(events)
    
    print("Pushing briefing to Discord...")
    send_discord_briefing(briefing)
    print("Briefing sent successfully!")

if __name__ == "__main__":
    main()