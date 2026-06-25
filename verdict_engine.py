import os
import psycopg2
import requests
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_LEDGER")

def get_live_nifty():
    """Fetches the current real-time spot price for Nifty 50."""
    try:
        nifty = yf.Ticker("^NSEI")
        nifty_history = nifty.history(period="1d")
        return float(round(nifty_history['Close'].iloc[-1].item(), 2)) if not nifty_history.empty else 0.0
    except Exception as e:
        print(f"Failed to fetch Nifty: {e}")
        return 0.0

# ... existing code ...
def fetch_pending_verdicts():
    """Fetches events older than 24 hours that haven't been reviewed yet."""
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, event, headline, nifty_direction, nifty_spot, suggested_strategy
        FROM events
        WHERE timestamp <= NOW() - INTERVAL '24 hours'
        AND timestamp >= NOW() - INTERVAL '5 days'
        AND (verdict_issued IS NULL OR verdict_issued = FALSE)
        AND nifty_spot IS NOT NULL
        AND event_type != 'IGNORE'
        AND nifty_direction IN ('BULLISH', 'BEARISH', 'NEUTRAL')
    ''')
    
    events = cursor.fetchall()
    return conn, cursor, events

def process_and_send_verdict(conn, cursor, event_data, current_nifty):
    """Calculates P&L, updates the portfolio, and sends the Discord card."""
    event_id, event_name, headline, direction, entry_price, strategy = event_data
    entry_price = float(entry_price)
    
    # 1. Calculate True Index Movement (For displaying on the card)
    true_index_move = current_nifty - entry_price
    
    # 2. SAFETY NET: Handle cases where direction is NULL or N/A
    direction = (direction or "NONE").upper()
    
    # 3. Calculate points captured based on trade direction
    if direction == "BULLISH":
        points_captured = true_index_move
    elif direction == "BEARISH":
        points_captured = -true_index_move
    elif direction == "NEUTRAL": 
        # Iron Condor math: Profit if move is small, loss if move is huge
        points_captured = 100 - abs(true_index_move)
    else:
        print(f"Skipping {event_name} - Invalid Direction: {direction}")
        return # Abort processing for this invalid row
        
    hit = points_captured > 0
    
    # Simulate P&L based on 1 Nifty Lot (25 qty) and 1.0 Delta (Futures eqv)
    lot_size = 25
    pnl_inr = round(points_captured * lot_size, 2)
    
    # Fetch current portfolio balance
    cursor.execute('SELECT current_balance FROM portfolio WHERE id = 1')
    current_balance = float(cursor.fetchone()[0])
    new_balance = current_balance + pnl_inr
    
    # Update Database
    cursor.execute('UPDATE portfolio SET current_balance = %s, updated_at = NOW() WHERE id = 1', (new_balance,))
    cursor.execute('UPDATE events SET verdict_issued = TRUE, pnl_inr = %s WHERE id = %s', (pnl_inr, event_id))
    conn.commit()

    color = 5763719 if hit else 15548997 # Green if Hit, Red if Miss
    status_icon = "✅ HIT" if hit else "❌ MISS"
    pnl_str = f"+₹{pnl_inr:,.2f}" if hit else f"-₹{abs(pnl_inr):,.2f}"
    
    embed = {
        "title": f"{status_icon}! Verdict: {event_name}",
        "description": f"**Original Headline:** {headline}",
        "color": color,
        "fields": [
            {"name": "Strategy Triggered", "value": strategy, "inline": False},
            {"name": "Entry Nifty Spot", "value": f"₹{entry_price:,.2f}", "inline": True},
            {"name": "Current Nifty Spot", "value": f"₹{current_nifty:,.2f}", "inline": True},
            {"name": "True Index Move", "value": f"{true_index_move:+.2f} points", "inline": True},
            {"name": "💸 Trade P&L (1 Lot)", "value": f"**{pnl_str}**", "inline": True},
            {"name": "🏦 Virtual Fund Balance", "value": f"**₹{new_balance:,.2f}**", "inline": True}
        ],
        "footer": {"text": "Bade Sahab Performance Tracker • Virtual Ledger"}
    }
    
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
        print(f"Sent verdict for {event_name} | P&L: {pnl_str}")
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
        process_and_send_verdict(conn, cursor, event_data, current_nifty)
        
    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()