import os
import requests
import yfinance as yf
import psycopg2
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_INTRADAY")
DB_URL          = os.getenv("DATABASE_URL")

WATCHLIST = [
    {"ticker": "^NSEI",    "name": "Nifty 50",         "currency": "₹"},
    {"ticker": "^NSEBANK", "name": "Bank Nifty",        "currency": "₹"},
    {"ticker": "BTC-USD",  "name": "Bitcoin",           "currency": "$"},
]

EMA_PERIOD    = 200
COOLDOWN_MINS = 60  # Don't re-alert same ticker within this window

def was_recently_alerted(cursor, ticker):
    cutoff = datetime.now() - timedelta(minutes=COOLDOWN_MINS)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fib_alerts (
            id SERIAL PRIMARY KEY,
            ticker TEXT,
            direction TEXT,
            timestamp TIMESTAMP DEFAULT NOW()
        )
    """)
    cursor.execute(
        "SELECT 1 FROM fib_alerts WHERE ticker = %s AND timestamp > %s",
        (ticker, cutoff)
    )
    return cursor.fetchone() is not None

def log_alert(conn, cursor, ticker, direction):
    cursor.execute(
        "INSERT INTO fib_alerts (ticker, direction) VALUES (%s, %s)",
        (ticker, direction)
    )
    # Keep table lean
    cursor.execute("""
        DELETE FROM fib_alerts WHERE id IN (
            SELECT id FROM fib_alerts WHERE ticker = %s
            ORDER BY timestamp DESC OFFSET 50
        )
    """, (ticker,))
    conn.commit()

def calculate_setup(ticker):
    try:
        df = yf.Ticker(ticker).history(period="15d", interval="15m")
        if df.empty or len(df) < EMA_PERIOD:
            return None

        df["EMA_200"] = df["Close"].ewm(span=EMA_PERIOD, adjust=False).mean()

        # Dynamic swing window: last 3 days of 15m candles
        candles_per_day = 26  # ~6.5 hrs × 4 candles/hr
        recent_df   = df.tail(candles_per_day * 3)
        swing_high  = recent_df["High"].max()
        swing_low   = recent_df["Low"].min()
        diff        = swing_high - swing_low

        if diff == 0:
            return None

        current     = df.iloc[-1]
        close       = float(current["Close"])
        low         = float(current["Low"])
        high        = float(current["High"])
        ema_200     = float(current["EMA_200"])

        setup = None

        if close > ema_200:
            fib_618  = swing_high - (diff * 0.618)
            fib_680  = swing_high - (diff * 0.680)
            fib_786  = swing_high - (diff * 0.786)
            if fib_680 <= low <= fib_618:
                setup = {
                    "direction":   "BULLISH",
                    "action":      "Buy the Dip",
                    "color":       5763719,
                    "zone_low":    fib_680,
                    "zone_high":   fib_618,
                    "sl":          fib_786,
                    "spot":        close,
                    "ema":         ema_200,
                    "swing_high":  swing_high,
                    "swing_low":   swing_low,
                }

        elif close < ema_200:
            fib_618  = swing_low + (diff * 0.618)
            fib_680  = swing_low + (diff * 0.680)
            fib_786  = swing_low + (diff * 0.786)
            if fib_618 <= high <= fib_680:
                setup = {
                    "direction":   "BEARISH",
                    "action":      "Sell the Rally",
                    "color":       15548997,
                    "zone_low":    fib_618,
                    "zone_high":   fib_680,
                    "sl":          fib_786,
                    "spot":        close,
                    "ema":         ema_200,
                    "swing_high":  swing_high,
                    "swing_low":   swing_low,
                }

        return setup

    except Exception as e:
        print(f"Fib calc error ({ticker}): {e}")
        return None

def send_alert(item, setup):
    c  = item["currency"]
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")

    # Risk/reward estimate
    entry_mid = (setup["zone_low"] + setup["zone_high"]) / 2
    sl_dist   = abs(entry_mid - setup["sl"])
    if setup["direction"] == "BULLISH":
        target    = entry_mid + (sl_dist * 2)  # 1:2 R:R
    else:
        target    = entry_mid - (sl_dist * 2)

    embed = {
        "title":       f"🎯 Fib Sniper · {item['name']}",
        "description": (
            f"**{setup['action']}** · 0.618–0.68 Golden Pocket + 200 EMA confluence\n"
            f"Spot: **{c}{setup['spot']:,.2f}** · 200 EMA: **{c}{setup['ema']:,.2f}**"
        ),
        "color": setup["color"],
        "fields": [
            {"name": "🟡 Entry Zone",      "value": f"{c}{setup['zone_low']:,.2f} – {c}{setup['zone_high']:,.2f}", "inline": True},
            {"name": "🎯 Target (1:2 R:R)","value": f"{c}{target:,.2f}",   "inline": True},
            {"name": "🛑 Stop Loss",       "value": f"{c}{setup['sl']:,.2f} (78.6% level)", "inline": True},
            {"name": "📐 Swing Range",     "value": f"H: {c}{setup['swing_high']:,.2f}  ·  L: {c}{setup['swing_low']:,.2f}", "inline": False},
            {"name": "📌 Logic",           "value": "Price pulled into the 61.8–68% value area while trend is intact above/below 200 EMA. Trade invalidates if 78.6% level breaks.", "inline": False},
        ],
        "footer": {"text": f"Bade Sahab · Fib Sniper · {now_ist}"}
    }

    try:
        requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
        print(f"Fib alert sent: {item['name']} {setup['direction']}")
    except Exception as e:
        print(f"Discord send failed: {e}")

def main():
    ist = ZoneInfo("Asia/Kolkata")
    if datetime.now(ist).weekday() >= 5:
        print("Weekend — Fib Sniper skipped.")
        return

    conn   = psycopg2.connect(DB_URL)
    cursor = conn.cursor()

    for item in WATCHLIST:
        ticker = item["ticker"]
        setup  = calculate_setup(ticker)

        if setup:
            if was_recently_alerted(cursor, ticker):
                print(f"{item['name']}: Golden Pocket active but cooldown in effect.")
                continue
            send_alert(item, setup)
            log_alert(conn, cursor, ticker, setup["direction"])
        else:
            print(f"{item['name']}: No Golden Pocket setup currently.")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()