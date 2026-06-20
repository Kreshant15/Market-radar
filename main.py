import json
import os
import time
import psycopg2
import requests
import yfinance as yf
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import news_fetcher
import analyzer

# Load environment variables
load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
COOLDOWN_HOURS = 6

def is_market_open():
    """Checks if the Indian stock market is currently open (Mon-Fri, 9:00 AM - 3:30 PM IST)."""
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist)
    
    # Check for weekends (5 = Saturday, 6 = Sunday)
    if now_ist.weekday() >= 5:
        print(f"🛡️ Market Shield: It is the weekend ({now_ist.strftime('%A')}). Sleeping...")
        return False
        
    # Check for market hours (09:00 to 15:30)
    market_open = time(9, 0)
    market_close = time(15, 30)
    
    if not (market_open <= now_ist.time() <= market_close):
        print(f"🛡️ Market Shield: Outside market hours ({now_ist.strftime('%H:%M')} IST). Sleeping...")
        return False
        
    return True

def init_database():
    """Connects to Neon PostgreSQL, creates table, and performs auto-migration for new columns."""
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    
    # 1. Ensure core table exists
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
    
    # 2. Migration check: Automatically add new F&O tracking columns if they don't exist
    columns_to_add = {
        "nifty_spot": "NUMERIC",
        "banknifty_spot": "NUMERIC",
        "vix_level": "NUMERIC",
        "suggested_strategy": "TEXT"
    }
    
    for column, col_type in columns_to_add.items():
        cursor.execute(f"""
            SELECT COUNT(*) 
            FROM information_schema.columns 
            WHERE table_name='events' AND column_name='{column}'
        """)
        exists = cursor.fetchone()[0]
        if not exists:
            print(f"Database Migration: Adding missing column '{column}' to events table...")
            cursor.execute(f"ALTER TABLE events ADD COLUMN {column} {col_type};")
            conn.commit()
            
    return conn

def get_live_market_prices():
    """Fetches real-time spot prices for Nifty 50, Bank Nifty, and India VIX."""
    try:
        nifty = yf.Ticker("^NSEI")
        banknifty = yf.Ticker("^NSEBANK")
        vix = yf.Ticker("^INDIAVIX")
        
        nifty_history = nifty.history(period="1d")
        banknifty_history = banknifty.history(period="1d")
        vix_history = vix.history(period="1d")
        
        # FIX: Using .item() to safely strip ALL numpy/pandas data types into pure Python types
        nifty_price = float(round(nifty_history['Close'].iloc[-1].item(), 2)) if not nifty_history.empty else 0.0
        banknifty_price = float(round(banknifty_history['Close'].iloc[-1].item(), 2)) if not banknifty_history.empty else 0.0
        vix_level = float(round(vix_history['Close'].iloc[-1].item(), 2)) if not vix_history.empty else 0.0
        
        return nifty_price, banknifty_price, vix_level
    except Exception as e:
        print(f"Warning: Failed to fetch live market spot prices: {e}")
        return 0.0, 0.0, 0.0

def is_duplicate_event(cursor, event_name):
    """Checks the cloud DB for duplicates within the 6-hour cooldown."""
    threshold_time = datetime.now() - timedelta(hours=COOLDOWN_HOURS)
    cursor.execute('''
        SELECT timestamp FROM events 
        WHERE event = %s AND timestamp > %s 
        ORDER BY timestamp DESC LIMIT 1
    ''', (event_name, threshold_time))
    return cursor.fetchone() is not None

def save_to_database(conn, cursor, headline, data, nifty_spot, banknifty_spot, vix_level):
    """Saves the comprehensive analysis and real-time entry spot levels to Neon PostgreSQL."""
    cursor.execute('''
        INSERT INTO events (
            headline, event, event_type, impact_score, confidence, 
            timestamp, reasoning, nifty_spot, banknifty_spot, vix_level, suggested_strategy
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (
        headline,
        data.get("event", "Unknown Event"),
        data.get("event_type", "OTHER"),
        data.get("impact_score", 0),
        data.get("confidence", 0),
        datetime.now(),
        data.get("reasoning", ""),
        nifty_spot if nifty_spot > 0 else None,
        banknifty_spot if banknifty_spot > 0 else None,
        vix_level if vix_level > 0 else None,
        data.get("suggested_strategy", "N/A")
    ))
    conn.commit()

def send_discord_alert(headline, data, nifty_spot, banknifty_spot, vix_level):
    """Sends a color-coded Rich Embed featuring Live Spot levels and suggested F&O Options structures."""
    color = 8421504 # Default Grey
    nifty_dir = data.get('nifty_direction', '').upper()
    if nifty_dir == 'BULLISH':
        color = 5763719 # Green
    elif nifty_dir == 'BEARISH':
        color = 15548997 # Red

    # Handle formatting spot strings beautifully
    nifty_spot_str = f"₹{nifty_spot:,}" if nifty_spot > 0 else "N/A (Closed)"
    banknifty_spot_str = f"₹{banknifty_spot:,}" if banknifty_spot > 0 else "N/A (Closed)"
    vix_str = f"{vix_level}%" if vix_level > 0 else "N/A"

    embed = {
        "title": f"🚨 [{data.get('event_type')}] {data.get('event')}",
        "description": f"**Headline:** {headline}",
        "color": color,
        "fields": [
            {"name": "Impact Score", "value": f"{data.get('impact_score')}/100", "inline": True},
            {"name": "Nifty Direction", "value": f"{data.get('nifty_direction')} ({nifty_spot_str})", "inline": True},
            {"name": "BankNifty Direction", "value": f"{data.get('banknifty_direction')} ({banknifty_spot_str})", "inline": True},
            {"name": "Expected India VIX", "value": f"{data.get('vix_impact')} (Spot: {vix_str})", "inline": True},
            {"name": "📈 Recommended F&O Strategy", "value": f"**{data.get('suggested_strategy', 'N/A')}**", "inline": False},
            {"name": "🛡️ Risk Management / Hedging Rule", "value": data.get('strategy_hedging', 'N/A'), "inline": False},
            {"name": "Reasoning", "value": data.get('reasoning'), "inline": False}
        ],
        "footer": {"text": "Bade Sahab Live Options Desk • 10m Pulse Check"}
    }
    
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
        print("Discord alert sent successfully.")
    except Exception as e:
        print(f"Failed to send Discord alert: {e}")

def main():
    if not is_market_open():
        print("Exiting script to preserve API quotas and prevent off-hours spam.")
        return

    print("Connecting to Cloud Database...")
    conn = init_database()
    cursor = conn.cursor()

    try:
        headlines = news_fetcher.fetch_top_headlines()
    except Exception as e:
        print(f"Error fetching headlines: {e}")
        return

    # Fetch live spot prices once at the start of execution
    nifty_spot, banknifty_spot, vix_level = get_live_market_prices()
    print(f"Live Market Check: Nifty={nifty_spot}, BankNifty={banknifty_spot}, VIX={vix_level}")

    for headline in headlines:
        try:
            raw_analysis = analyzer.analyze_headline(headline)
            data = json.loads(raw_analysis)
            event_name = data.get("event", "Unknown Event")
            
            if is_duplicate_event(cursor, event_name):
                print(f"Skipped duplicate: {event_name}")
                continue

            print(f"Processing NEW event: {event_name}")
            save_to_database(conn, cursor, headline, data, nifty_spot, banknifty_spot, vix_level)
            send_discord_alert(headline, data, nifty_spot, banknifty_spot, vix_level)
            
            # Take a 5-second breath to avoid hitting Gemini rate limits
            time.sleep(5)
            
        except Exception as e:
            print(f"Error processing {headline}: {e}")
            # FIX: Unlock the database if a transaction fails so the next headline can still be processed
            conn.rollback()

    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()