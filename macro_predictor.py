import os
import json
import feedparser
import psycopg2
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()
DISCORD_WEBHOOK_MACRO = os.getenv("DISCORD_WEBHOOK_MACRO")
DB_URL                = os.getenv("DATABASE_URL")

class MacroEvent(BaseModel):
    event_name:         str = Field(description="Name of the event e.g. US Fed Rate Decision, India CPI, RBI Repo Rate")
    days_away:          str = Field(description="When it happens e.g. 'In 2 Days', 'Tomorrow'")
    outcome_prob_1:     str = Field(description="Primary outcome and probability e.g. 'Rate Cut: 70%'")
    outcome_prob_2:     str = Field(description="Secondary outcome and probability e.g. 'Hold/Pause: 20%'")
    outcome_prob_3:     str = Field(description="Tertiary outcome and probability e.g. 'Rate Hike: 10%'")
    historical_bullish: str = Field(description="% chance market reacts bullishly based on past years")
    historical_bearish: str = Field(description="% chance market reacts bearishly based on past years")
    fii_dii_context:    str = Field(description="Brief note on FII/DII or global whale positioning")
    analysis:           str = Field(description="1-2 sentences on what to expect")

class MacroReport(BaseModel):
    major_events_found: bool           = Field(description="True ONLY if major macro event in next 1-4 days")
    events:             list[MacroEvent]

# ── DEDUP ─────────────────────────────────────────────────────────────────────
def get_db():
    conn   = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS macro_alerts (
            id         SERIAL PRIMARY KEY,
            event_name TEXT,
            timestamp  TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()
    return conn, cursor

def was_recently_alerted(cursor, event_name):
    """Block re-alerting the same macro event within 20 hours."""
    cutoff = datetime.now() - timedelta(hours=20)
    cursor.execute(
        "SELECT 1 FROM macro_alerts WHERE event_name = %s AND timestamp > %s",
        (event_name, cutoff)
    )
    return cursor.fetchone() is not None

def log_macro_alert(conn, cursor, event_name):
    cursor.execute(
        "INSERT INTO macro_alerts (event_name) VALUES (%s)", (event_name,)
    )
    # Keep table lean — only last 100 rows
    cursor.execute("""
        DELETE FROM macro_alerts WHERE id IN (
            SELECT id FROM macro_alerts ORDER BY timestamp DESC OFFSET 100
        )
    """)
    conn.commit()

# ── FETCH ─────────────────────────────────────────────────────────────────────
def fetch_macro_previews():
    queries = [
        ("Upcoming (RBI OR Repo Rate OR India CPI OR India GDP) (expectations OR preview OR poll) when:48h",
         "en-IN&gl=IN&ceid=IN:en"),
        ("Upcoming (US Fed OR FOMC OR US CPI OR NFP OR Non-Farm Payrolls OR Crude Oil) (expectations OR preview) when:48h",
         "en-US&gl=US&ceid=US:en"),
        ("Upcoming (Bitcoin OR Crypto OR Ethereum) (expectations OR forecast OR options expiry) when:48h",
         "en-US&gl=US&ceid=US:en"),
    ]

    headlines = []
    for query, region in queries:
        url  = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl={region}"
        feed = feedparser.parse(url)
        headlines.extend([e.title for e in feed.entries[:6]])

    coindesk = feedparser.parse("https://www.coindesk.com/arc/outboundfeeds/rss/")
    headlines.extend([e.title for e in coindesk.entries[:4]])

    return "\n".join(list(set(headlines)))

# ── GENERATE ──────────────────────────────────────────────────────────────────
def generate_macro_probabilities(headlines_text):
    if not headlines_text:
        return None

    client = genai.Client()
    prompt = (
        "You are an Elite Quantitative Macro Analyst for an Indian Hedge Fund.\n"
        f"Today is {datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%A, %d %B %Y')}.\n\n"
        f"Review these headlines:\n{headlines_text}\n\n"
        "Identify MAJOR macroeconomic events in the next 1-4 days "
        "(US Fed, RBI, CPI, NFP, GDP, Crude Oil, FII data, Geopolitics, major Crypto events).\n"
        "Estimate exact outcome probabilities from market consensus and historical win rates.\n"
        "Be specific with event names — avoid vague names like 'Market Event'."
    )

    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=MacroReport,
                temperature=0.1,
            ),
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Gemini error: {e}")
        return None

# ── SEND ──────────────────────────────────────────────────────────────────────
def send_macro_alert(event):
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")
    embed = {
        "title":       f"🔮 Advance Warning · {event.get('event_name')}",
        "description": f"**Timing:** {event.get('days_away')}\n*Institutional predictive model activated.*",
        "color":       10181046,
        "fields": [
            {
                "name":  "📊 Outcome Probabilities",
                "value": (
                    f"1️⃣ {event.get('outcome_prob_1')}\n"
                    f"2️⃣ {event.get('outcome_prob_2')}\n"
                    f"3️⃣ {event.get('outcome_prob_3')}"
                ),
                "inline": False
            },
            {
                "name":  "📈 Historical Market Reaction",
                "value": (
                    f"🟩 **Bullish:** {event.get('historical_bullish')}\n"
                    f"🟥 **Bearish:** {event.get('historical_bearish')}"
                ),
                "inline": False
            },
            {"name": "💼 Whales / FII Positioning", "value": event.get("fii_dii_context", "N/A"), "inline": False},
            {"name": "🧠 AI Quant Analysis",         "value": event.get("analysis", "N/A"),         "inline": False},
        ],
        "footer": {"text": f"Bade Sahab · Quant Desk · {now_ist}"}
    }

    try:
        requests.post(DISCORD_WEBHOOK_MACRO, json={"embeds": [embed]}, timeout=10)
        print(f"Macro alert sent: {event.get('event_name')}")
    except Exception as e:
        print(f"Discord send failed: {e}")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("Macro Advance Radar starting...")
    conn, cursor = get_db()

    headlines = fetch_macro_previews()
    if not headlines:
        print("No macro headlines found.")
        cursor.close(); conn.close()
        return

    report = generate_macro_probabilities(headlines)
    if not report:
        print("Gemini parse failed.")
        cursor.close(); conn.close()
        return

    if not report.get("major_events_found"):
        print("No major macro events in next 1-4 days — staying silent.")
        cursor.close(); conn.close()
        return

    events = report.get("events", [])
    print(f"Found {len(events)} events.")

    fired = 0
    for event in events:
        name = event.get("event_name", "Unknown")
        if was_recently_alerted(cursor, name):
            print(f"Skipped (already alerted today): {name}")
            continue
        send_macro_alert(event)
        log_macro_alert(conn, cursor, name)
        fired += 1

    print(f"Sent {fired} new macro warnings.")
    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()