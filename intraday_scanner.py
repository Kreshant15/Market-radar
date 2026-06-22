import os
import json
import requests
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()
# Make sure to add this new secret to GitHub and your .env!
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_INTRADAY") 

# List of highly liquid Indian stocks for safe intraday trading
TICKERS = [
    "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "TCS.NS",
    "ITC.NS", "SBIN.NS", "TATAMOTORS.NS", "ZOMATO.NS", "TATASTEEL.NS",
    "AXISBANK.NS", "KOTAKBANK.NS", "LT.NS", "BHARTIARTL.NS", "M&M.NS"
]

class ScalpSetup(BaseModel):
    ticker: str = Field(description="The stock ticker (e.g., RELIANCE.NS)")
    action: str = Field(description="BUY (Go Long) or SELL (Short Intraday)")
    entry_price: str = Field(description="Suggested entry price near current spot")
    target_price: str = Field(description="Take profit target (aim for 0.8% to 1.5% move)")
    stop_loss: str = Field(description="Strict stop loss (maximum 0.5% to 1% risk)")
    logic: str = Field(description="Why this stock has momentum today (1 short sentence)")

class IntradayReport(BaseModel):
    top_setups: list[ScalpSetup]

def get_market_movers():
    """Fetches today's data for the basket and finds the top movers."""
    print("Scanning high-liquidity stocks for intraday momentum...")
    movers = []
    
    for ticker in TICKERS:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1d", interval="5m")
            if len(hist) > 1:
                open_price = hist['Open'].iloc[0]
                current_price = hist['Close'].iloc[-1]
                pct_change = ((current_price - open_price) / open_price) * 100
                
                movers.append({
                    "ticker": ticker,
                    "spot": round(current_price, 2),
                    "change_pct": round(pct_change, 2)
                })
        except Exception as e:
            print(f"Skipping {ticker}: {e}")
            
    # Sort by absolute momentum (highest gainers and biggest losers)
    movers.sort(key=lambda x: abs(x['change_pct']), reverse=True)
    return movers[:5] # Return top 5 most volatile stocks today

def generate_scalp_setups(movers):
    """Passes the top movers to Gemini to generate strict Entry/Target/SL levels."""
    if not movers:
        return None

    client = genai.Client()
    movers_text = "\n".join([f"{m['ticker']} | Spot: ₹{m['spot']} | Change from Open: {m['change_pct']}%" for m in movers])
    
    prompt = (
        "You are a strict, risk-averse Intraday Equity Scalper. "
        "Review the following highly liquid Indian stocks showing the most momentum today:\n\n"
        f"{movers_text}\n\n"
        "Generate 2-3 precise intraday scalping setups from this list. "
        "If a stock is up heavily, suggest a BUY on a slight pullback. If down heavily, suggest an intraday SHORT. "
        "Provide strict entry points, realistic small targets to cover brokerage fees, and very tight stop-losses to protect capital."
    )
    
    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=IntradayReport,
                temperature=0.2, 
            ),
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Error generating scalps: {e}")
        return None

def send_intraday_alert(report):
    """Sends the scalping setups to Discord."""
    embeds = []
    
    for setup in report.get("top_setups", []):
        is_buy = setup.get('action').upper() == 'BUY'
        color = 5763719 if is_buy else 15548997 # Green for Buy, Red for Short
        
        embed = {
            "title": f"⚡ INTRADAY SCALP: {setup.get('ticker')}",
            "color": color,
            "fields": [
                {"name": "Action", "value": f"**{setup.get('action').upper()}**", "inline": True},
                {"name": "Entry Zone", "value": f"₹{setup.get('entry_price')}", "inline": True},
                {"name": "🎯 Target", "value": f"₹{setup.get('target_price')}", "inline": True},
                {"name": "🛡️ Strict Stop Loss", "value": f"**₹{setup.get('stop_loss')}**", "inline": True},
                {"name": "Logic", "value": setup.get('logic'), "inline": False}
            ],
            "footer": {"text": "Bade Sahab Equity Desk • Intraday Momentum Scanner"}
        }
        embeds.append(embed)
    
    if embeds:
        try:
            requests.post(DISCORD_WEBHOOK_URL, json={"embeds": embeds})
            print("Intraday setups sent successfully!")
        except Exception as e:
            print(f"Failed to send Discord alert: {e}")

def main():
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist)
    if now_ist.weekday() >= 5:
        print("Weekend. No intraday trading today.")
        return

    movers = get_market_movers()
    if not movers:
        print("No movers found.")
        return
        
    print("Generating safe scalp setups...")
    report = generate_scalp_setups(movers)
    
    if report and report.get("top_setups"):
        send_intraday_alert(report)
    else:
        print("AI did not find any safe setups.")

if __name__ == "__main__":
    main()