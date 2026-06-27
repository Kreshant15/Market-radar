import os
import psycopg2
import requests
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
DB_URL        = os.getenv("DATABASE_URL")
WEBHOOK_URL   = os.getenv("DISCORD_WEBHOOK_LEDGER")

# ── STRATEGY P&L PROFILES ─────────────────────────────────────────────────────
# Realistic estimated options P&L per lot based on strategy type.
# These are conservative market averages — not futures math.
STRATEGY_PROFILES = {
    # Directional
    "bull call spread":  {"win": 3500,  "loss": -1800, "type": "DIRECTIONAL"},
    "bear put spread":   {"win": 3500,  "loss": -1800, "type": "DIRECTIONAL"},
    "buy call":          {"win": 5000,  "loss": -2500, "type": "DIRECTIONAL"},
    "buy put":           {"win": 5000,  "loss": -2500, "type": "DIRECTIONAL"},
    # Neutral / Volatility
    "iron condor":       {"win": 2000,  "loss": -4000, "type": "NEUTRAL"},
    "straddle":          {"win": 6000,  "loss": -3000, "type": "VOLATILITY"},
    "strangle":          {"win": 5000,  "loss": -2500, "type": "VOLATILITY"},
    # Credit / Conservative
    "sell call":         {"win": 1500,  "loss": -3500, "type": "CREDIT"},
    "sell put":          {"win": 1500,  "loss": -3500, "type": "CREDIT"},
    "covered call":      {"win": 1200,  "loss": -2000, "type": "CREDIT"},
    # Default fallback
    "default":           {"win": 2000,  "loss": -2000, "type": "DIRECTIONAL"},
}

def get_strategy_profile(strategy_str):
    """Match strategy string to a P&L profile. Falls back to default."""
    if not strategy_str or strategy_str in ("N/A", "None", ""):
        return "default", STRATEGY_PROFILES["default"]
    s = strategy_str.lower()
    for key in STRATEGY_PROFILES:
        if key in s:
            return key, STRATEGY_PROFILES[key]
    return "default", STRATEGY_PROFILES["default"]

def was_direction_correct(direction, true_move, strategy_type):
    """
    Core accuracy check — did the market move the way we predicted?
    Returns True (HIT) or False (MISS).
    """
    direction = (direction or "NONE").upper()
    if direction == "BULLISH":
        return true_move > 0
    elif direction == "BEARISH":
        return true_move < 0
    elif direction == "NEUTRAL":
        # Iron Condor / credit spreads win when move is small
        return abs(true_move) < 75
    return False

def get_live_nifty():
    try:
        hist = yf.Ticker("^NSEI").history(period="1d")
        return float(round(hist['Close'].iloc[-1].item(), 2)) if not hist.empty else 0.0
    except Exception as e:
        print(f"Nifty fetch failed: {e}")
        return 0.0

def fetch_pending_verdicts():
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, event, headline, nifty_direction, nifty_spot, suggested_strategy, confidence
        FROM events
        WHERE timestamp <= NOW() - INTERVAL '24 hours'
        AND   timestamp >= NOW() - INTERVAL '5 days'
        AND   (verdict_issued IS NULL OR verdict_issued = FALSE)
        AND   nifty_spot IS NOT NULL
        AND   event_type != 'IGNORE'
        AND   nifty_direction IN ('BULLISH', 'BEARISH', 'NEUTRAL')
    ''')
    return conn, cursor, cursor.fetchall()

def fetch_portfolio(cursor):
    try:
        cursor.execute('SELECT current_balance FROM portfolio WHERE id = 1')
        row = cursor.fetchone()
        return float(row[0]) if row else 500000.0
    except Exception:
        return 500000.0

def fetch_accuracy_stats(cursor):
    """Pull all-time hit/miss record for the summary line."""
    try:
        cursor.execute('''
            SELECT
                COUNT(*) FILTER (WHERE pnl_inr > 0) AS hits,
                COUNT(*) FILTER (WHERE pnl_inr <= 0) AS misses
            FROM events
            WHERE verdict_issued = TRUE AND pnl_inr IS NOT NULL
        ''')
        row = cursor.fetchone()
        hits, misses = (row[0] or 0), (row[1] or 0)
        total = hits + misses
        accuracy = round((hits / total) * 100, 1) if total > 0 else 0.0
        return hits, misses, total, accuracy
    except Exception:
        return 0, 0, 0, 0.0

def process_and_send_verdict(conn, cursor, event_data, current_nifty):
    event_id, event_name, headline, direction, entry_price, strategy, confidence = event_data
    entry_price = float(entry_price)
    confidence  = int(confidence or 50)
    direction   = (direction or "NONE").upper()

    # ── 1. Core calculation ───────────────────────────────────────────────────
    true_move       = current_nifty - entry_price
    strategy_key, profile = get_strategy_profile(strategy)
    hit             = was_direction_correct(direction, true_move, profile["type"])
    pnl_inr         = profile["win"] if hit else profile["loss"]

    # ── 2. Portfolio update ───────────────────────────────────────────────────
    current_balance = fetch_portfolio(cursor)
    new_balance     = current_balance + pnl_inr

    cursor.execute(
        'UPDATE portfolio SET current_balance = %s, updated_at = NOW() WHERE id = 1',
        (new_balance,)
    )
    cursor.execute(
        'UPDATE events SET verdict_issued = TRUE, pnl_inr = %s WHERE id = %s',
        (pnl_inr, event_id)
    )
    conn.commit()

    # ── 3. Accuracy stats ─────────────────────────────────────────────────────
    hits, misses, total, accuracy = fetch_accuracy_stats(cursor)

    # ── 4. Build embed ────────────────────────────────────────────────────────
    color      = 5763719 if hit else 15548997
    status     = "✅ HIT" if hit else "❌ MISS"
    pnl_str    = f"+₹{pnl_inr:,}" if hit else f"-₹{abs(pnl_inr):,}"
    move_arrow = "▲" if true_move > 0 else "▼"
    dir_icon   = "🟢" if direction == "BULLISH" else "🔴" if direction == "BEARISH" else "⚪"

    # Confidence badge
    if confidence >= 75:
        conf_badge = f"🔥 {confidence}% — High conviction"
    elif confidence >= 55:
        conf_badge = f"⚡ {confidence}% — Moderate"
    else:
        conf_badge = f"🌫️ {confidence}% — Low conviction"

    # Strategy display — show matched key if strategy string was messy
    strategy_display = strategy if strategy and strategy != "N/A" else "No strategy tagged"

    embed = {
        "title": f"{status} · {event_name}",
        "description": (
            f"{dir_icon} Predicted **{direction}** · {conf_badge}\n"
            f">>> {headline[:200] + '…' if len(headline) > 200 else headline}"
        ),
        "color": color,
        "fields": [
            # Row 1: Price context
            {"name": "Entry Spot",   "value": f"₹{entry_price:,.2f}", "inline": True},
            {"name": "Exit Spot",    "value": f"₹{current_nifty:,.2f}", "inline": True},
            {"name": "Index Move",   "value": f"{move_arrow} {abs(true_move):.1f} pts", "inline": True},

            # Row 2: P&L + balance
            {"name": "Strategy",     "value": f"`{strategy_display}`", "inline": False},
            {"name": "Est. P&L",     "value": f"**{pnl_str}**", "inline": True},
            {"name": "Fund Balance", "value": f"**₹{new_balance:,.0f}**", "inline": True},

            # Row 3: Running accuracy
            {
                "name": "📊 All-Time Accuracy",
                "value": f"`{accuracy}%` · {hits}W / {misses}L of {total} calls",
                "inline": False
            },
        ],
        "footer": {
            "text": f"Bade Sahab · Virtual Ledger · {datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%d %b %Y, %I:%M %p IST')}"
        }
    }

    try:
        requests.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
        print(f"Verdict sent: {event_name} | {status} | {pnl_str}")
    except Exception as e:
        print(f"Discord send failed: {e}")

def main():
    ist     = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist)

    if now_ist.weekday() >= 5:
        print("Weekend — verdicts held until Monday.")
        return

    print(f"Verdict Engine starting — {now_ist.strftime('%H:%M IST')}")
    conn, cursor, events = fetch_pending_verdicts()

    if not events:
        print("No pending verdicts.")
        cursor.close()
        conn.close()
        return

    print(f"{len(events)} verdicts pending. Fetching Nifty...")
    current_nifty = get_live_nifty()

    if current_nifty == 0.0:
        print("Market data unavailable — aborting.")
        cursor.close()
        conn.close()
        return

    for event_data in events:
        process_and_send_verdict(conn, cursor, event_data, current_nifty)

    cursor.close()
    conn.close()
    print("Verdict Engine complete.")

if __name__ == "__main__":
    main()
