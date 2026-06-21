import os
import json
import feedparser
import requests
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()
DISCORD_WEBHOOK_MACRO = os.getenv("DISCORD_WEBHOOK_MACRO")

# Define the strict structured output requested by your friends
class MacroEvent(BaseModel):
    event_name: str = Field(description="Name of the event (e.g., US Fed Rate Decision, India CPI, RBI Repo Rate, Bitcoin Halving)")
    days_away: str = Field(description="When it happens (e.g., 'In 2 Days', 'Tomorrow')")
    outcome_prob_1: str = Field(description="Primary outcome and probability (e.g., 'Rate Cut: 70%')")
    outcome_prob_2: str = Field(description="Secondary outcome and probability (e.g., 'Hold/Pause: 20%')")
    outcome_prob_3: str = Field(description="Tertiary outcome and probability (e.g., 'Rate Hike: 10%')")
    historical_bullish: str = Field(description="Based on past years, % chance the market reacts bullishly to the likely outcome")
    historical_bearish: str = Field(description="Based on past years, % chance the market reacts bearishly")
    fii_dii_context: str = Field(description="Brief note on FIIs/DIIs or Global Whales positioning")
    analysis: str = Field(description="1-2 sentences on what to expect.")

class MacroReport(BaseModel):
    major_events_found: bool = Field(description="Set to true ONLY if there is a major macro event in the next 1-4 days")
    events: list[MacroEvent]

def fetch_macro_previews():
    """Fetches news specifically looking for upcoming macroeconomic and crypto expectations."""
    queries = [
        # Indian Macro (RBI, Repo Rates, GDP)
        "Upcoming (RBI OR Repo Rate OR Reverse Repo OR India CPI OR India GDP) (expectations OR preview OR poll) when:48h",
        # Global Macro (Fed, NFP, Crude) - US Region targeted
        "Upcoming (US Fed OR FOMC OR US CPI OR NFP OR Non-Farm Payrolls OR Crude Oil) (expectations OR preview) when:48h",
        # Crypto & Bitcoin
        "Upcoming (Bitcoin OR Crypto OR Ethereum) (expectations OR forecast OR options expiry OR SEC) when:48h"
    ]
    
    headlines = []
    
    # 1. Fetch from Google News (Splitting into US and IN regions for better local results)
    for i, query in enumerate(queries):
        region = "en-IN&gl=IN&ceid=IN:en" if i == 0 else "en-US&gl=US&ceid=US:en"
        url = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl={region}"
        feed = feedparser.parse(url)
        headlines.extend([entry.title for entry in feed.entries[:6]])

    # 2. Add CoinDesk specifically for crypto macro previews
    coindesk = feedparser.parse("https://www.coindesk.com/arc/outboundfeeds/rss/")
    headlines.extend([entry.title for entry in coindesk.entries[:4]])
    
    # Return unique headlines
    return "\n".join(list(set(headlines)))

def generate_macro_probabilities(headlines_text):
    """Passes the previews to Gemini to calculate quant probabilities."""
    if not headlines_text:
        return None

    client = genai.Client()
    prompt = (
        "You are an Elite Quantitative Macro Analyst for an Indian Hedge Fund. "
        "Review the following news headlines covering the next few days.\n\n"
        f"Headlines:\n{headlines_text}\n\n"
        "Identify if there are any MAJOR macroeconomic events happening in the next 1 to 4 days "
        "(Focus on: US Fed, RBI Repo Rates, CPI, NFP, GDP, Crude Oil, FII data, Geopolitics, and Major Bitcoin/Crypto movements).\n"
        "If yes, estimate the exact probabilities for the outcomes based on market consensus, "
        "and estimate the historical win rate (Bullish vs Bearish) of this type of event."
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
        print(f"Error generating prediction: {e}")
        return None

def send_macro_alert(event):
# ... existing send_macro_alert code from before ...
    embed = {
        "title": f"🔮 ADVANCE WARNING: {event.get('event_name')}",
        "description": f"**Timing:** {event.get('days_away')}\n*Institutional predictive model activated.*",
        "color": 10181046, # Deep Purple for Quant/Macro alerts
        "fields": [
            {"name": "📊 Outcome Probabilities", "value": f"1️⃣ {event.get('outcome_prob_1')}\n2️⃣ {event.get('outcome_prob_2')}\n3️⃣ {event.get('outcome_prob_3')}", "inline": False},
            {"name": "📈 Historical Market Reaction", "value": f"🟩 **Bullish Probability:** {event.get('historical_bullish')}\n🟥 **Bearish Probability:** {event.get('historical_bearish')}", "inline": False},
            {"name": "💼 Whales/FII Positioning", "value": event.get('fii_dii_context', 'N/A'), "inline": False},
            {"name": "🧠 AI Quant Analysis", "value": event.get('analysis', ''), "inline": False}
        ],
        "footer": {"text": "Bade Sahab Quant Desk • Predictive Advance Radar"}
    }
    
    try:
        requests.post(DISCORD_WEBHOOK_MACRO, json={"embeds": [embed]})
        print(f"Sent macro advance warning for {event.get('event_name')}")
    except Exception as e:
        print(f"Failed to send Discord alert: {e}")

def main():
# ... existing main code ...
    print("Running Macro Advance Radar...")
    headlines = fetch_macro_previews()
    
    if not headlines:
        print("No macro news found today.")
        return
        
    print("Analyzing upcoming events and calculating probabilities...")
    report = generate_macro_probabilities(headlines)
    
    if not report:
        print("Failed to parse AI response.")
        return
        
    if not report.get("major_events_found"):
        print("No major macro events detected in the next 1-4 days. Staying silent.")
        return
        
    print(f"Found {len(report.get('events', []))} upcoming events! Sending warnings...")
    for event in report.get("events", []):
        send_macro_alert(event)

if __name__ == "__main__":
    main()