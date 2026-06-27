import os
import json
import requests
import yfinance as yf
import psycopg2
from dotenv import load_dotenv
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

load_dotenv()

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_FORADAR")
DB_URL = os.getenv("DATABASE_URL")

# ── CONFIG ─────────────────────────────────────────────────────────────────────
POINT_MOVE_ALERT = {
    "^NSEI":    50,   # Alert if Nifty moves ±50 pts from last snapshot
    "^NSEBANK": 150,  # Alert if BankNifty moves ±150 pts from last snapshot
}

LEVEL_PROXIMITY_PCT = {
    "^NSEI":    0.0015,  # 0.15% for Nifty (~36 pts at 24000)
    "^NSEBANK": 0.0012,  # 0.12% for BankNifty (~70 pts at 58000)
}

ALERT_COOLDOWN_MINUTES = 30  # Same level won't re-alert within this window

# ── WATCHLIST: Update this whenever you draw new levels ───────────────────────
WATCHLIST = [
    {"ticker": "^NSEI",    "name": "Nifty 50",    "level": 24150, "type": "Resistance"},
    {"ticker": "^NSEI",    "name": "Nifty 50",    "level": 23800, "type": "Support"},
    {"ticker": "^NSEBANK", "name": "Bank Nifty",  "level": 58500, "type": "Resistance"},
    {"ticker": "^NSEBANK", "name": "Bank Nifty",  "level": 58000, "type": "Support"},
]

# ── DB SETUP ──────────────────────────────────────────────────────────────────
def get_db():
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pulse_snapshots (
            id SERIAL PRIMARY KEY,
            ticker TEXT,
            price NUMERIC,
            timestamp TIMESTAMP DEFAULT NOW()
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pulse_alerts (
            id SERIAL PRIMARY KEY,
            ticker TEXT,
            alert_type TEXT,
            level NUMERIC,
            timestamp TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()
    return conn, cursor

def get_last_snapshot(cursor, ticker):
    """Get the price from the previous run to calculate point move."""
    cursor.execute("""
        SELECT price, timestamp FROM pulse_snapshots
        WHERE ticker = %s ORDER BY timestamp DESC LIMIT 1
    """, (ticker,))
    row = cursor.fetchone()
    return (float(row[0]), row[1]) if row else (None, None)

def save_snapshot(conn, cursor, ticker, price):
    cursor.execute(
        "INSERT INTO pulse_snapshots (ticker, price) VALUES (%s, %s)",
        (ticker, price)
    )
    # Keep only last 50 snapshots per ticker
    cursor.execute("""
        DELETE FROM pulse_snapshots WHERE id IN (
            SELECT id FROM pulse_snapshots WHERE ticker = %s
            ORDER BY timestamp DESC OFFSET 50
        )
    """, (ticker,))
    conn.commit()

def was_recently_alerted(cursor, ticker, alert_type, level=None):
    """Anti-spam: block re-alerts within cooldown window."""
    cutoff = datetime.now() - timedelta(minutes=ALERT_COOLDOWN_MINUTES)
    if level:
        cursor.execute("""
            SELECT 1 FROM pulse_alerts
            WHERE ticker = %s AND alert_type = %s AND level = %s AND timestamp > %s
        """, (ticker, alert_type, level, cutoff))
    else:
        cursor.execute("""
            SELECT 1 FROM pulse_alerts
            WHERE ticker = %s AND alert_type = %s AND timestamp > %s
        """, (ticker, alert_type, cutoff))
    return cursor.fetchone() is not None

def log_alert(conn, cursor, ticker, alert_type, level=None):
    cursor.execute(
        "INSERT INTO pulse_alerts (ticker, alert_type, level) VALUES (%s, %s, %s)",
        (ticker, alert_type, level)
    )
    conn.commit()

# ── PRICE FETCH ───────────────────────────────────────────────────────────────
def get_price(ticker):
    try:
        data = yf.Ticker(ticker).history(period="1d", interval="5m")
        if data.empty:
            return None
        return float(round(data['Close'].iloc[-1].item(), 2))
    except Exception as e:
        print(f"Price fetch error for {ticker}: {e}")
        return None

# ── DISCORD ALERTS ────────────────────────────────────────────────────────────
def send_level_alert(item, current_price):
    is_support = item['type'].upper() == "SUPPORT"
    color = 5763719 if is_support else 15548997
    icon  = "🟢" if is_support else "🔴"

    diff_pts = current_price - item['level']
    side = "above" if diff_pts > 0 else "below"
    action = (
        "Watch for bounce confirmation — bullish engulfing or hammer candle."
        if is_support else
        "Watch for rejection confirmation — bearish engulfing or shooting star."
    )

    embed = {
        "title": f"{icon} {item['name']} · {item['type']} Zone",
        "description": (
            f"Price is **{abs(diff_pts):.1f} pts {side}** your marked level.\n"
            f">>> {action}"
        ),
        "color": color,
        "fields": [
            {"name": "Marked Level", "value": f"₹{item['level']:,.2f}", "inline": True},
            {"name": "Current Spot", "value": f"₹{current_price:,.2f}", "inline": True},
            {"name": "Type", "value": item['type'], "inline": True},
        ],
        "footer": {"text": f"Bade Sahab · Technical Monitor · {datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%d %b %Y, %I:%M %p IST')}"}
    }

    _send(embed)

def send_point_move_alert(ticker, name, prev_price, current_price, move_pts):
    is_up = move_pts > 0
    icon  = "⬆️" if is_up else "⬇️"
    color = 5763719 if is_up else 15548997
    direction = "UP" if is_up else "DOWN"
    pct = abs(move_pts / prev_price * 100)

    embed = {
        "title": f"{icon} {name} · Point Move Alert",
        "description": (
            f"**{name}** moved **{direction} {abs(move_pts):.0f} pts** ({pct:.2f}%) "
            f"since last check."
        ),
        "color": color,
        "fields": [
            {"name": "Previous",  "value": f"₹{prev_price:,.2f}",   "inline": True},
            {"name": "Current",   "value": f"₹{current_price:,.2f}", "inline": True},
            {"name": "Move",      "value": f"{'+' if is_up else ''}{move_pts:.0f} pts", "inline": True},
        ],
        "footer": {"text": f"Bade Sahab · Live Pulse · {datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%d %b %Y, %I:%M %p IST')}"}
    }

    _send(embed)

def _send(embed):
    try:
        requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
    except Exception as e:
        print(f"Discord send failed: {e}")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    conn, cursor = get_db()

    # Deduplicate tickers so we only fetch each price once
    tickers_seen = {}
    for item in WATCHLIST:
        t = item['ticker']
        if t not in tickers_seen:
            tickers_seen[t] = get_price(t)

    for ticker, current_price in tickers_seen.items():
        if current_price is None:
            continue

        name = next(i['name'] for i in WATCHLIST if i['ticker'] == ticker)

        # ── 1. POINT MOVE CHECK ───────────────────────────────────────────────
        prev_price, _ = get_last_snapshot(cursor, ticker)
        if prev_price is not None:
            move_pts = current_price - prev_price
            threshold = POINT_MOVE_ALERT.get(ticker, 100)
            if abs(move_pts) >= threshold:
                if not was_recently_alerted(cursor, ticker, "POINT_MOVE"):
                    send_point_move_alert(ticker, name, prev_price, current_price, move_pts)
                    log_alert(conn, cursor, ticker, "POINT_MOVE")

        # ── 2. SAVE CURRENT SNAPSHOT ──────────────────────────────────────────
        save_snapshot(conn, cursor, ticker, current_price)

    # ── 3. LEVEL PROXIMITY CHECK ─────────────────────────────────────────────
    for item in WATCHLIST:
        ticker = item['ticker']
        current_price = tickers_seen.get(ticker)
        if current_price is None:
            continue

        pct_threshold = LEVEL_PROXIMITY_PCT.get(ticker, 0.001)
        diff = abs(current_price - item['level'])
        if diff <= item['level'] * pct_threshold:
            level_key = f"{item['type']}_{item['level']}"
            if not was_recently_alerted(cursor, ticker, "LEVEL", item['level']):
                send_level_alert(item, current_price)
                log_alert(conn, cursor, ticker, "LEVEL", item['level'])

    cursor.close()
    conn.close()
    print(f"Pulse check complete — {datetime.now().strftime('%H:%M:%S IST')}")

if __name__ == "__main__":
    main()
