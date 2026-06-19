import json
import os
import psycopg2
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
import news_fetcher
import analyzer

# Load environment variables
load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
COOLDOWN_HOURS = 6

def init_database():
    """Connects to Neon PostgreSQL and creates the table if it doesn't exist."""
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            headline TEXT NOT NULL,
            event TEXT NOT NULL,
            event_type TEXT NOT NULL,
            impact_score INTEGER NOT NULL,
            confidence INTEGER NOT NULL,
            timestamp TIMESTAMP NOT NULL,
            reasoning TEXT NOT NULL
        )
    ''')
    conn.commit()
    return conn

def is_duplicate_event(cursor, event_name):
    """Checks the cloud DB for duplicates within the 6-hour cooldown."""
    threshold_time = datetime.now() - timedelta(hours=COOLDOWN_HOURS)
    cursor.execute('''
        SELECT timestamp FROM events 
        WHERE event = %s AND timestamp > %s 
        ORDER BY timestamp DESC LIMIT 1
    ''', (event_name, threshold_time))
    return cursor.fetchone() is not None

def save_to_database(conn, cursor, headline, data):
    """Saves the analysis to Neon PostgreSQL."""
    cursor.execute('''
        INSERT INTO events (headline, event, event_type, impact_score, confidence, timestamp, reasoning)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    ''', (
        headline,
        data.get("event", "Unknown Event"),
        data.get("event_type", "OTHER"),
        data.get("impact_score", 0),
        data.get("confidence", 0),
        datetime.now(),
        data.get("reasoning", "")
    ))
    conn.commit()

def send_discord_alert(headline, data):
    """Sends a color-coded Rich Embed to your Discord channel."""
    color = 8421504 # Default Grey
    nifty_dir = data.get('nifty_direction', '').upper()
    if nifty_dir == 'BULLISH':
        color = 5763719 # Green
    elif nifty_dir == 'BEARISH':
        color = 15548997 # Red

    embed = {
        "title": f"🚨 [{data.get('event_type')}] {data.get('event')}",
        "description": f"**Headline:** {headline}",
        "color": color,
        "fields": [
            {"name": "Impact Score", "value": f"{data.get('impact_score')}/100", "inline": True},
            {"name": "Nifty", "value": data.get('nifty_direction'), "inline": True},
            {"name": "BankNifty", "value": data.get('banknifty_direction'), "inline": True},
            {"name": "VIX", "value": data.get('vix_impact'), "inline": True},
            {"name": "Reasoning", "value": data.get('reasoning'), "inline": False}
        ]
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
        print("Discord alert sent successfully.")
    except Exception as e:
        print(f"Failed to send Discord alert: {e}")

def main():
    print("Connecting to Cloud Database...")
    conn = init_database()
    cursor = conn.cursor()

    try:
        headlines = news_fetcher.fetch_top_headlines()
    except Exception as e:
        print(f"Error fetching headlines: {e}")
        return

    for headline in headlines:
        try:
            raw_analysis = analyzer.analyze_headline(headline)
            data = json.loads(raw_analysis)
            event_name = data.get("event", "Unknown Event")
            
            if is_duplicate_event(cursor, event_name):
                print(f"Skipped duplicate: {event_name}")
                continue

            print(f"Processing NEW event: {event_name}")
            save_to_database(conn, cursor, headline, data)
            send_discord_alert(headline, data)
            
        except Exception as e:
            print(f"Error processing {headline}: {e}")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()