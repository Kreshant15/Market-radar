import os
import psycopg2
import requests
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def get_live_nifty():
    """Fetches the current real-time spot price for Nifty 50."""
    try:
        nifty = yf.Ticker("^NSEI")
        nifty_history = nifty.history(period="1d")
        return float(round(nifty_history['Close'].iloc[-1].item(), 2)) if not nifty_history.empty else 0.0
    except Exception as e:
        print(f"Failed to fetch Nifty: {e}")
        return 0.0

def fetch_pending_verdicts():
    """Fetches events older than 24 hours that haven't been reviewed yet."""
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    
    # We look for events older than 24 hours but newer than 5 days (to ignore ancient news)
    cursor.execute('''
        SELECT id, event, headline, nifty_direction, nifty_spot, suggested_strategy
        FROM events
        WHERE timestamp <= NOW() - INTERVAL '24 hours'
        AND timestamp >= NOW() - INTERVAL '5 days'
        AND (verdict_issued IS NULL OR verdict_issued = FALSE)
        AND nifty_spot IS NOT NULL
    ''')
    
    events = cursor.fetchall()
    return conn, cursor, events

def send_verdict_alert(event_data, current_nifty):
    """Calculates point movement and sends the HIT/MISS card to Discord."""
    event_id, event_name, headline, direction, entry_price, strategy = event_data
    entry_price = float(entry_price)
    direction = direction.upper()
    
    # Calculate performance
    if direction == "BULLISH":
        points_captured = current_nifty - entry_price
        hit = points_captured > 0
    elif direction == "BEARISH":
        points_captured = entry_price - current_nifty
        hit = points_captured > 0
    else: # NEUTRAL
        points_captured = abs(current_nifty - entry_price)
        hit = points_captured < 100 # Considered a hit if it stayed within a tight 100pt range
        
    # Formatting the visual embed
    color = 5763719 if hit else 15548997 # Green if Hit, Red if Miss
    status_icon = "✅ HIT" if hit else "❌ MISS"
    point_str = f"+{points_captured:,.2f}" if hit else f"{points_captured:,.2f}"
    
    embed = {
        "title": f"{status_icon}! Verdict: {event_name}",
        "description": f"**Original Headline:** {headline}",
        "color": color,
        "fields": [
            {"name": "Strategy Triggered", "value": strategy, "inline": False},
            {"name": "Predicted Direction", "value": direction, "inline": True},
            {"name": "Entry Nifty Spot", "value": f"₹{entry_price:,.2f}", "inline": True},
            {"name": "Current Nifty Spot", "value": f"₹{current_nifty:,.2f}", "inline": True},
            {"name": "Index Movement", "value": f"**{point_str} points** in direction of trade", "inline": False}
        ],
        "footer": {"text": "Bade Sahab Performance Tracker • 24h Review"}
    }
    
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
        print(f"Sent verdict for {event_name}")
    except Exception as e:
        print(f"Failed to send verdict Discord alert: {e}")

def main():
    # 1. Market Shield check (Don't run verdicts on weekends)
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist)
    if now_ist.weekday() >= 5:
        print("Weekend. Holding pending verdicts until Monday.")
        return

    print("Fetching pending verdicts...")
    conn, cursor, events = fetch_pending_verdicts()
    
    if not events:
        print("No pending verdicts found.")
        cursor.close()
        conn.close()
        return
        
    print(f"Found {len(events)} events ready for review. Fetching live index...")
    current_nifty = get_live_nifty()
    
    if current_nifty == 0.0:
        print("Market data unavailable. Aborting verdict.")
        return
        
    for event_data in events:
        send_verdict_alert(event_data, current_nifty)
        
        # Mark as issued so we don't spam it tomorrow
        cursor.execute('UPDATE events SET verdict_issued = TRUE WHERE id = %s', (event_data[0],))
        conn.commit()
        
    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()