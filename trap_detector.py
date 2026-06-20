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

def get_live_metrics():
    """Fetches real-time Nifty and VIX to check for market divergence."""
    try:
        nifty = yf.Ticker("^NSEI")
        vix = yf.Ticker("^INDIAVIX")
        
        nifty_history = nifty.history(period="1d")
        vix_history = vix.history(period="1d")
        
        current_nifty = float(round(nifty_history['Close'].iloc[-1].item(), 2)) if not nifty_history.empty else 0.0
        current_vix = float(round(vix_history['Close'].iloc[-1].item(), 2)) if not vix_history.empty else 0.0
        
        return current_nifty, current_vix
    except Exception as e:
        print(f"Failed to fetch live data for trap detection: {e}")
        return 0.0, 0.0

def fetch_untested_events():
    """Fetches events that are 30 to 90 minutes old to check for traps."""
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    
    # Grab events older than 30 mins, newer than 90 mins, that haven't been checked
    cursor.execute('''
        SELECT id, event, headline, nifty_direction, nifty_spot, vix_level
        FROM events
        WHERE timestamp <= NOW() - INTERVAL '30 minutes'
        AND timestamp >= NOW() - INTERVAL '90 minutes'
        AND (trap_checked IS NULL OR trap_checked = FALSE)
        AND nifty_spot IS NOT NULL
    ''')
    
    events = cursor.fetchall()
    return conn, cursor, events

def analyze_and_alert_trap(conn, cursor, event_data, current_nifty, current_vix):
    event_id, event_name, headline, direction, entry_nifty, entry_vix = event_data
    
    entry_nifty = float(entry_nifty)
    entry_vix = float(entry_vix) if entry_vix else current_vix
    
    nifty_change = current_nifty - entry_nifty
    vix_change = current_vix - entry_vix
    
    is_trap = False
    trap_type = ""
    trap_message = ""
    
    # TRAP LOGIC: Did Smart Money reverse the news?
    if direction.upper() == "BULLISH":
        # Dropped 30+ points despite bullish news, and VIX is spiking
        if nifty_change < -30 and vix_change > 0.2:
            is_trap = True
            trap_type = "🚨 BULL TRAP DETECTED"
            trap_message = "Smart Money is SELLING the news. Nifty has broken support against the bullish catalyst and VIX is spiking."
            
    elif direction.upper() == "BEARISH":
        # Rallied 30+ points despite bearish news (Short Squeeze)
        if nifty_change > 30:
            is_trap = True
            trap_type = "🚨 BEAR TRAP DETECTED (Short Squeeze)"
            trap_message = "Smart Money is BUYING the dip. Market is refusing to drop on bearish news, squeezing short sellers."

    # If it's a trap, fire the emergency alert!
    if is_trap:
        embed = {
            "title": trap_type,
            "description": f"**Event:** {event_name}\n**Original Catalyst:** {headline}",
            "color": 16711680, # Bright Red Warning
            "fields": [
                {"name": "Predicted Direction", "value": direction, "inline": True},
                {"name": "Actual Movement", "value": f"{nifty_change:+.2f} points", "inline": True},
                {"name": "VIX Reaction", "value": f"{vix_change:+.2f}%", "inline": True},
                {"name": "⚠️ AI Emergency Action", "value": f"**{trap_message}**\nCancel current '{direction}' F&O Spreads immediately.", "inline": False}
            ],
            "footer": {"text": "Bade Sahab Risk Management • Divergence Engine"}
        }
        
        try:
            requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
            print(f"TRAP DETECTED on {event_name}! Alert sent.")
        except Exception as e:
            print(f"Failed to send Discord alert: {e}")

    # Mark as checked so we don't scan it again
    cursor.execute('UPDATE events SET trap_checked = TRUE WHERE id = %s', (event_id,))
    conn.commit()

def main():
    # Shield check
    ist = ZoneInfo("Asia/Kolkata")
    if datetime.now(ist).weekday() >= 5:
        print("Weekend. No active traps to detect.")
        return

    print("Running Trap Detector Subroutine...")
    conn, cursor, events = fetch_untested_events()
    
    if not events:
        print("No pending 30-min events to check for traps.")
        cursor.close()
        conn.close()
        return
        
    current_nifty, current_vix = get_live_metrics()
    if current_nifty == 0.0:
        return
        
    for event in events:
        analyze_and_alert_trap(conn, cursor, event, current_nifty, current_vix)
        
    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()