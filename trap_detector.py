import os
import psycopg2
import requests
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_TRAP")

def get_live_metrics():
    try:
        nifty, vix = yf.Ticker("^NSEI"), yf.Ticker("^INDIAVIX")
        return (
            float(round(nifty.history(period="1d")['Close'].iloc[-1].item(), 2)) if not nifty.history(period="1d").empty else 0.0,
            float(round(vix.history(period="1d")['Close'].iloc[-1].item(), 2)) if not vix.history(period="1d").empty else 0.0
        )
    except Exception:
        return 0.0, 0.0

def fetch_untested_events():
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    cursor.execute('''SELECT id, event, headline, nifty_direction, nifty_spot, vix_level FROM events 
                      WHERE timestamp <= NOW() - INTERVAL '30 minutes' AND timestamp >= NOW() - INTERVAL '90 minutes' 
                      AND (trap_checked IS NULL OR trap_checked = FALSE) AND nifty_spot IS NOT NULL''')
    return conn, cursor, cursor.fetchall()

def analyze_and_alert_trap(conn, cursor, event_data, current_nifty, current_vix):
    event_id, event_name, headline, direction, entry_nifty, entry_vix = event_data
    nifty_change = current_nifty - float(entry_nifty)
    vix_change = current_vix - (float(entry_vix) if entry_vix else current_vix)
    
    is_trap, trap_type, trap_message = False, "", ""
    if direction.upper() == "BULLISH" and nifty_change < -30 and vix_change > 0.2:
        is_trap, trap_type, trap_message = True, "🚨 BULL TRAP DETECTED", "Smart Money is SELLING the news. Market reversing against catalyst."
    elif direction.upper() == "BEARISH" and nifty_change > 30:
        is_trap, trap_type, trap_message = True, "🚨 BEAR TRAP DETECTED (Short Squeeze)", "Smart Money is BUYING the dip. Squeezing shorts."

    if is_trap:
        embed = {
            "title": trap_type, "description": f"**Event:** {event_name}\n**Original Catalyst:** {headline}", "color": 16711680,
            "fields": [
                {"name": "Predicted", "value": direction, "inline": True}, {"name": "Actual Move", "value": f"{nifty_change:+.2f} pts", "inline": True},
                {"name": "⚠️ Action", "value": f"**{trap_message}**\nCancel '{direction}' Spreads immediately.", "inline": False}
            ]
        }
        try:
            # THIS IS THE @HERE PING!
            requests.post(DISCORD_WEBHOOK_URL, json={"content": "@here", "embeds": [embed]}) 
        except Exception: pass

    cursor.execute('UPDATE events SET trap_checked = TRUE WHERE id = %s', (event_id,))
    conn.commit()

def main():
    if datetime.now(ZoneInfo("Asia/Kolkata")).weekday() >= 5: return
    conn, cursor, events = fetch_untested_events()
    if not events:
        cursor.close(); conn.close()
        return
        
    current_nifty, current_vix = get_live_metrics()
    if current_nifty != 0.0:
        for event in events: analyze_and_alert_trap(conn, cursor, event, current_nifty, current_vix)
    cursor.close(); conn.close()

if __name__ == "__main__":
    main()