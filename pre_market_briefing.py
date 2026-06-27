import os
import json
import psycopg2
import requests
import re
import time
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from google import genai

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_PREMARKET")

# ── GLOBAL CUES CONFIG ────────────────────────────────────────────────────────
GLOBAL_TICKERS = {
    "S&P 500":    "^GSPC",
    "Nasdaq":     "^IXIC",
    "Dow Jones":  "^DJI",
    "Brent Crude":"BZ=F",
    "USD/INR":    "INR=X",
    "US 10Y":     "^TNX",
    "India VIX":  "^INDIAVIX",
}

def fetch_overnight_events():
    try:
        conn = psycopg2.connect(DB_URL)
        cursor = conn.cursor()
        yesterday = datetime.now() - timedelta(hours=24)
        cursor.execute('''
            SELECT headline, event, event_type, impact_score, nifty_direction, reasoning
            FROM events
            WHERE timestamp >= %s AND impact_score >= 40
            ORDER BY impact_score DESC
            LIMIT 10
        ''', (yesterday,))
        events = cursor.fetchall()
        cursor.close()
        conn.close()
        return events
    except Exception as e:
        print(f"Database fetch error: {e}")
        return []

def fetch_global_cues():
    print("Fetching live global cues...")
    cues = {}
    for name, symbol in GLOBAL_TICKERS.items():
        try:
            hist = yf.Ticker(symbol).history(period="2d")
            if len(hist) >= 2:
                prev  = hist['Close'].iloc[-2]
                close = hist['Close'].iloc[-1]
                pct   = ((close - prev) / prev) * 100
                arrow = "▲" if pct > 0 else "▼"
                cues[name] = {"value": f"{close:,.2f}", "change": f"{arrow} {abs(pct):.2f}%", "bullish": pct > 0}
            elif not hist.empty:
                cues[name] = {"value": f"{hist['Close'].iloc[-1]:,.2f}", "change": "—", "bullish": None}
            else:
                cues[name] = {"value": "N/A", "change": "—", "bullish": None}
        except Exception:
            cues[name] = {"value": "Error", "change": "—", "bullish": None}
    return cues

def score_global_sentiment(cues):
    """Quick bull/bear score from global cues to drive verdict color."""
    bull, bear = 0, 0
    for name, d in cues.items():
        if d["bullish"] is None:
            continue
        # Crude up = slightly bearish for India; USD/INR up = bearish for India
        if name in ("Brent Crude", "USD/INR"):
            if d["bullish"]: bear += 1
            else: bull += 1
        elif name == "India VIX":
            if d["bullish"]: bear += 1  # VIX rising = fear
            else: bull += 1
        else:
            if d["bullish"]: bull += 1
            else: bear += 1
    if bull > bear + 1: return "BULLISH", 5763719
    if bear > bull + 1: return "BEARISH", 15548997
    return "NEUTRAL", 16744192

def generate_briefing(events, cues):
    client = genai.Client()

    cues_text = "\n".join([
        f"- {k}: {d['value']} ({d['change']})" for k, d in cues.items()
    ])

    events_summary = ""
    if events:
        for i, ev in enumerate(events, 1):
            direction = f" → {ev[4]}" if ev[4] else ""
            events_summary += f"{i}. [{ev[2]}] {ev[1]} (Impact: {ev[3]}/100){direction}\n   {ev[0]}\n"
    else:
        events_summary = "No major domestic events logged in last 24 hours."

    prompt = f"""You are an elite Indian options desk strategist delivering the morning briefing.
Today is {datetime.now().strftime('%A, %d %B %Y')}.

LIVE GLOBAL CUES:
{cues_text}

OVERNIGHT DOMESTIC EVENTS (sorted by impact):
{events_summary}

Generate a crisp, professional morning briefing in EXACTLY this structure:

**OPEN CALL:** [One line — Gap Up / Gap Down / Flat + expected Nifty range e.g. 24100-24350]

**GLOBAL READ:** [2-3 sentences on what US markets, crude, and USD/INR mean for today's open]

**KEY RISK TODAY:** [1-2 sentences — what is the single biggest risk to watch]

**STRATEGY:** [Specific options play — e.g. "Sell 24200 CE if Nifty opens below 24100, target 24000, SL 24250"]

**BIAS:** [BULLISH / BEARISH / SIDEWAYS with one-line reasoning]

Keep total response under 300 words. Be direct. No fluff."""

    delays = [2, 4, 8, 16, 32]
    for attempt, delay in enumerate(delays):
        try:
            response = client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=prompt
            )
            return response.text
        except Exception as e:
            if attempt == len(delays) - 1:
                return "⚠️ AI briefing failed. Check global cues manually."
            print(f"Retrying in {delay}s... ({e})")
            time.sleep(delay)

def send_discord_briefing(briefing_text, cues, sentiment, color):
    now = datetime.now(ZoneInfo("Asia/Kolkata")).strftime('%d %b %Y, %I:%M %p IST')

    # ── Global cues as compact 2-col fields ───────────────────────────────────
    cue_fields = []
    for name, d in cues.items():
        icon = "🟢" if d["bullish"] else "🔴" if d["bullish"] is False else "⚪"
        cue_fields.append({
            "name": f"{icon} {name}",
            "value": f"{d['value']} `{d['change']}`",
            "inline": True
        })

    # ── Sentiment badge ───────────────────────────────────────────────────────
    sent_icon = "🟢" if sentiment == "BULLISH" else "🔴" if sentiment == "BEARISH" else "⚪"

    # ── Briefing text — split if over 1000 chars to avoid embed limits ────────
    briefing_trimmed = briefing_text[:1900] + "…" if len(briefing_text) > 1900 else briefing_text

    embed = {
        "title": f"🌅 Pre-Market Briefing  ·  {now}",
        "description": f"{sent_icon} **Global Sentiment: {sentiment}**\n\n{briefing_trimmed}",
        "color": color,
        "fields": cue_fields,
        "footer": {"text": "Bade Sahab · Pre-Market Desk · Powered by Gemini"}
    }

    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=10
        )
        if response.status_code in [200, 204]:
            print("Briefing dispatched successfully.")
        else:
            print(f"Discord error: {response.status_code} — {response.text}")
    except Exception as e:
        print(f"Failed to send briefing: {e}")

def main():
    print(f"Pre-Market Engine starting — {datetime.now().strftime('%H:%M:%S')}")
    cues      = fetch_global_cues()
    events    = fetch_overnight_events()
    sentiment, color = score_global_sentiment(cues)
    briefing  = generate_briefing(events, cues)

    print(f"Global sentiment scored: {sentiment}")
    send_discord_briefing(briefing, cues, sentiment, color)
    print("Done.")

if __name__ == "__main__":
    main()
