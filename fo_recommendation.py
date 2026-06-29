import os
import json
import time
import psycopg2
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()
DB_URL            = os.getenv("DATABASE_URL")
WEBHOOK_PREMARKET = os.getenv("DISCORD_WEBHOOK_PREMARKET")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY")

# ── STRUCTURED OUTPUT SCHEMA ──────────────────────────────────────────────────
class FORecommendation(BaseModel):
    index:           str = Field(description="NIFTY, BANKNIFTY, or SENSEX")
    bias:            str = Field(description="BULLISH, BEARISH, or NEUTRAL")
    strategy:        str = Field(description="Specific options strategy name e.g. Bear Put Spread")
    leg_1:           str = Field(description="First leg — e.g. 'Buy 24000 PE @ ₹120'")
    leg_2:           str = Field(description="Second leg — e.g. 'Sell 23800 PE @ ₹60'. 'NONE' if single leg.")
    expiry:          str = Field(description="Expiry date of the recommended trade")
    target_pts:      str = Field(description="Target in index points — e.g. '+120 pts'")
    stop_loss_pts:   str = Field(description="Stop loss in index points — e.g. '-60 pts'")
    max_profit:      str = Field(description="Estimated max profit in INR for 1 lot")
    max_loss:        str = Field(description="Estimated max loss in INR for 1 lot")
    confidence:      int = Field(description="AI confidence in this trade 0-100")
    reasoning:       str = Field(description="2-3 sentences — why this trade, what OI/global data supports it")
    key_risk:        str = Field(description="One sentence — what would invalidate this trade")

class FOReport(BaseModel):
    recommendations: list[FORecommendation]

def fetch_latest_oi(cursor, symbol):
    """Pull most recent OI snapshot for a symbol."""
    cursor.execute("""
        SELECT spot, expiry, pcr, max_pain, total_ce_oi, total_pe_oi,
               top_ce_walls, top_pe_walls, ce_buildup, pe_buildup, oi_bias, oi_read
        FROM oi_snapshots
        WHERE symbol = %s
        ORDER BY timestamp DESC LIMIT 1
    """, (symbol,))
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "symbol":       symbol,
        "spot":         float(row[0]),
        "expiry":       row[1],
        "pcr":          float(row[2]),
        "max_pain":     float(row[3]),
        "total_ce_oi":  row[4],
        "total_pe_oi":  row[5],
        "top_ce_walls": row[6],
        "top_pe_walls": row[7],
        "ce_buildup":   row[8],
        "pe_buildup":   row[9],
        "oi_bias":      row[10],
        "oi_read":      row[11],
    }

def fetch_overnight_events(cursor):
    """Pull high-impact events from last 24h for context."""
    cursor.execute("""
        SELECT event, nifty_direction, impact_score, suggested_strategy
        FROM events
        WHERE timestamp >= NOW() - INTERVAL '24 hours'
        AND impact_score >= 50
        ORDER BY impact_score DESC
        LIMIT 5
    """)
    return cursor.fetchall()

def build_prompt(oi_data_list, events):
    """Constructs the Gemini prompt from OI + overnight events."""
    today = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%A, %d %B %Y")

    # OI context block
    oi_block = ""
    for d in oi_data_list:
        if not d:
            continue
        ce_walls = ", ".join([f"{int(s):,} ({int(o):,} OI)" for s, o in d["top_ce_walls"]])
        pe_walls = ", ".join([f"{int(s):,} ({int(o):,} OI)" for s, o in d["top_pe_walls"]])
        oi_block += f"""
{d['symbol']}:
  Spot: {d['spot']:,.2f} | Expiry: {d['expiry']} | PCR: {d['pcr']}
  Max Pain: {int(d['max_pain']):,} | OI Bias: {d['oi_bias']}
  CE Walls (Resistance): {ce_walls}
  PE Walls (Support): {pe_walls}
  OI Read: {d['oi_read']}
"""

    # Overnight events block
    events_block = ""
    if events:
        for ev in events:
            events_block += f"- {ev[0]} → {ev[1]} (Impact: {ev[2]}/100)\n"
    else:
        events_block = "No major overnight events.\n"

    return f"""You are an Elite F&O Options Strategist for an Indian trading desk.
Today is {today}.

LIVE OI DATA (just fetched from NSE/BSE):
{oi_block}

OVERNIGHT MACRO EVENTS (last 24h, sorted by impact):
{events_block}

Based on the OI structure and overnight events, generate ONE specific F&O trade recommendation 
for EACH of the following: NIFTY, BANKNIFTY, SENSEX.

Rules:
- Use NEAREST expiry only
- Recommend spread strategies (Bull Call Spread, Bear Put Spread, Iron Condor) over naked options
- Strike selection must be near the CE/PE walls from OI data — not random
- If PCR > 1.3 → lean BULLISH. If PCR < 0.7 → lean BEARISH. Between → NEUTRAL/Iron Condor
- Max pain acts as a magnet — price tends to drift toward it by expiry
- Be specific: exact strikes, exact expiry, estimated premium
- Keep reasoning tight — 2-3 sentences max
- key_risk must be ONE specific price level that invalidates the trade
"""

def generate_fo_recommendations(oi_data_list, events):
    client = genai.Client()
    prompt = build_prompt(oi_data_list, events)

    delays = [2, 4, 8, 16, 32]
    for attempt, delay in enumerate(delays):
        try:
            response = client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=FOReport,
                    temperature=0.2,
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            if attempt == len(delays) - 1:
                print(f"F&O rec generation failed: {e}")
                return None
            print(f"Retrying in {delay}s... ({e})")
            time.sleep(delay)

def build_fo_embed(rec):
    """Builds a single Discord embed for one F&O recommendation."""
    bias  = rec.get("bias", "NEUTRAL").upper()
    icon  = "🟢" if bias == "BULLISH" else "🔴" if bias == "BEARISH" else "⚪"
    color = 5763719 if bias == "BULLISH" else 15548997 if bias == "BEARISH" else 8421504

    conf = int(rec.get("confidence", 50))
    if conf >= 75:
        conf_badge = f"🔥 {conf}% confidence"
    elif conf >= 55:
        conf_badge = f"⚡ {conf}% confidence"
    else:
        conf_badge = f"🌫️ {conf}% confidence"

    leg_2_val = rec.get("leg_2", "NONE")

    fields = [
        {"name": "📌 Leg 1", "value": f"`{rec.get('leg_1', 'N/A')}`", "inline": True},
    ]
    if leg_2_val and leg_2_val != "NONE":
        fields.append({"name": "📌 Leg 2", "value": f"`{leg_2_val}`", "inline": True})

    fields.extend([
        {"name": "📅 Expiry",     "value": rec.get("expiry", "N/A"),      "inline": True},
        {"name": "🎯 Target",     "value": rec.get("target_pts", "N/A"),   "inline": True},
        {"name": "🛑 Stop Loss",  "value": rec.get("stop_loss_pts", "N/A"),"inline": True},
        {"name": "💰 Max Profit", "value": rec.get("max_profit", "N/A"),   "inline": True},
        {"name": "💸 Max Loss",   "value": rec.get("max_loss", "N/A"),     "inline": True},
        {"name": "🧠 Reasoning",  "value": rec.get("reasoning", "N/A"),   "inline": False},
        {"name": "⚠️ Key Risk",   "value": rec.get("key_risk", "N/A"),    "inline": False},
    ])

    now_ist = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")

    return {
        "title": f"{icon} {rec.get('index')} F&O Play · {rec.get('strategy')}",
        "description": f"{bias} · {conf_badge}\nExpiry: **{rec.get('expiry', 'N/A')}**",
        "color": color,
        "fields": fields,
        "footer": {"text": f"Bade Sahab · F&O Desk · {now_ist}"}
    }

def send_fo_report(embeds):
    if not embeds:
        print("No F&O recommendations to send.")
        return
    # Send header message first
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y")
    try:
        requests.post(
            WEBHOOK_PREMARKET,
            json={"content": f"📊 **F&O Trade Recommendations — {now_ist}**\n*Based on live OI data + overnight macro events*"},
            timeout=10
        )
        time.sleep(1)
        r = requests.post(
            WEBHOOK_PREMARKET,
            json={"embeds": embeds},
            timeout=10
        )
        if r.status_code in [200, 204]:
            print(f"F&O report sent — {len(embeds)} trades.")
        else:
            print(f"Discord error: {r.status_code} — {r.text}")
    except Exception as e:
        print(f"F&O send failed: {e}")

def ensure_tables(cursor, conn):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS oi_snapshots (
            id SERIAL PRIMARY KEY,
            symbol TEXT,
            spot NUMERIC,
            expiry TEXT,
            pcr NUMERIC,
            max_pain NUMERIC,
            total_ce_oi BIGINT,
            total_pe_oi BIGINT,
            top_ce_walls JSONB,
            top_pe_walls JSONB,
            ce_buildup JSONB,
            pe_buildup JSONB,
            oi_bias TEXT,
            oi_read TEXT,
            timestamp TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()

def main():
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)

    if now.weekday() >= 5:
        print("Weekend — F&O rec engine skipped.")
        return

    print(f"F&O Recommendation Engine starting — {now.strftime('%H:%M IST')}")
    conn   = psycopg2.connect(DB_URL)
    cursor = conn.cursor()

    ensure_tables(cursor, conn)  # ← The function is called here!

    oi_data_list = [
        fetch_latest_oi(cursor, "NIFTY"),
        fetch_latest_oi(cursor, "BANKNIFTY"),
        fetch_latest_oi(cursor, "SENSEX"),
    ]

    available = [d for d in oi_data_list if d is not None]
    if not available:
        print("No OI data in DB — run oi_engine.py first.")
        cursor.close()
        conn.close()
        return

    events = fetch_overnight_events(cursor)
    cursor.close()
    conn.close()

    print(f"OI data loaded for: {[d['symbol'] for d in available]}")
    print(f"Overnight events: {len(events)}")

    report = generate_fo_recommendations(oi_data_list, events)
    if not report:
        print("Gemini returned no recommendations.")
        return

    embeds = []
    for rec in report.get("recommendations", []):
        embeds.append(build_fo_embed(rec))

    send_fo_report(embeds)
    print("F&O Engine complete.")

if __name__ == "__main__":
    main()