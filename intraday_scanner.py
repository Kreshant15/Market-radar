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
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_INTRADAY")

TICKERS = [
    "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "TCS.NS",
    "ITC.NS",      "SBIN.NS",     "TATAMOTORS.NS","ZOMATO.NS","TATASTEEL.NS",
    "AXISBANK.NS", "KOTAKBANK.NS","LT.NS",         "BHARTIARTL.NS",
    "M%26M.NS",    # M&M — ampersand must be URL-encoded for Yahoo Finance
]

# Only scan after market has enough candles to be meaningful
MINIMUM_CANDLES = 12  # ~1 hour of 5m data

class ScalpSetup(BaseModel):
    ticker:       str = Field(description="Stock ticker e.g. RELIANCE.NS")
    action:       str = Field(description="BUY or SELL")
    entry_price:  str = Field(description="Suggested entry near current spot")
    target_price: str = Field(description="Take profit target (0.8%–1.5% move)")
    stop_loss:    str = Field(description="Strict stop loss (max 0.5%–1% risk)")
    risk_reward:  str = Field(description="R:R ratio e.g. 1:2")
    logic:        str = Field(description="One sentence — why this setup has momentum today")

class IntradayReport(BaseModel):
    top_setups: list[ScalpSetup]

def get_market_movers():
    print("Scanning intraday momentum...")
    movers = []

    for ticker in TICKERS:
        try:
            hist = yf.Ticker(ticker).history(period="1d", interval="5m")

            # Skip if not enough candles — market just opened
            if len(hist) < MINIMUM_CANDLES:
                print(f"{ticker}: only {len(hist)} candles — too early, skipping.")
                continue

            open_price    = float(hist["Open"].iloc[0])
            current_price = float(hist["Close"].iloc[-1])
            high_of_day   = float(hist["High"].max())
            low_of_day    = float(hist["Low"].min())
            pct_change    = ((current_price - open_price) / open_price) * 100

            # Volume check — skip if volume is suspiciously thin
            avg_volume = hist["Volume"].mean()
            if avg_volume < 1000:
                continue

            movers.append({
                "ticker":         ticker,
                "spot":           round(current_price, 2),
                "open":           round(open_price, 2),
                "high":           round(high_of_day, 2),
                "low":            round(low_of_day, 2),
                "change_pct":     round(pct_change, 2),
                "avg_vol":        int(avg_volume),
            })

        except Exception as e:
            print(f"Skipping {ticker}: {e}")

    movers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    return movers[:5]

def generate_scalp_setups(movers):
    if not movers:
        return None

    client     = genai.Client()
    now_ist    = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%I:%M %p IST")
    movers_txt = "\n".join([
        f"{m['ticker']} | Spot: ₹{m['spot']} | Open: ₹{m['open']} | "
        f"H: ₹{m['high']} | L: ₹{m['low']} | Change: {m['change_pct']:+.2f}%"
        for m in movers
    ])

    prompt = (
        f"You are a strict, risk-averse Intraday Equity Scalper. Current time: {now_ist}.\n\n"
        f"Top momentum stocks right now:\n{movers_txt}\n\n"
        "Generate 2-3 precise intraday scalping setups. Rules:\n"
        "- BUY if stock is up with momentum, on a slight pullback to HOD - 0.3%\n"
        "- SELL if stock is down with momentum, on a slight bounce to LOD + 0.3%\n"
        "- Target: 0.8% to 1.5% from entry\n"
        "- Stop loss: 0.4% to 0.7% max — tight. Capital protection first.\n"
        "- Minimum 1:1.5 risk-reward, prefer 1:2\n"
        "- All prices must be realistic — within 1% of current spot\n"
        "- Only pick stocks with absolute change > 0.8% — ignore flat movers"
    )

    try:
        response = genai.Client().models.generate_content(
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
        print(f"Gemini error: {e}")
        return None

def send_intraday_alert(report, movers):
    embeds = []
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")

    for setup in report.get("top_setups", []):
        is_buy = setup.get("action", "").upper() == "BUY"
        color  = 5763719 if is_buy else 15548997
        icon   = "🟢" if is_buy else "🔴"

        # Pull HOD/LOD from movers for context
        ticker  = setup.get("ticker", "")
        mover   = next((m for m in movers if m["ticker"] == ticker), None)
        hod_lod = f"HOD: ₹{mover['high']:,}  ·  LOD: ₹{mover['low']:,}" if mover else "N/A"

        embed = {
            "title":       f"⚡ {icon} {ticker.replace('.NS', '')} · Intraday Scalp",
            "description": f"**{setup.get('action').upper()}** · R:R {setup.get('risk_reward', 'N/A')}",
            "color":       color,
            "fields": [
                {"name": "Entry",      "value": f"₹{setup.get('entry_price')}",  "inline": True},
                {"name": "🎯 Target",  "value": f"₹{setup.get('target_price')}", "inline": True},
                {"name": "🛑 SL",      "value": f"₹{setup.get('stop_loss')}",    "inline": True},
                {"name": "Day Range",  "value": hod_lod,                          "inline": False},
                {"name": "Logic",      "value": setup.get("logic", "N/A"),        "inline": False},
            ],
            "footer": {"text": f"Bade Sahab · Intraday Scalper · {now_ist}"}
        }
        embeds.append(embed)

    if embeds:
        try:
            requests.post(DISCORD_WEBHOOK, json={"embeds": embeds}, timeout=10)
            print(f"Intraday setups sent: {len(embeds)}")
        except Exception as e:
            print(f"Discord send failed: {e}")

def main():
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)

    if now.weekday() >= 5:
        print("Weekend — Intraday Scalper skipped.")
        return

    # Don't run before 10:00 AM IST — not enough candles
    if now.hour < 10:
        print(f"Too early ({now.strftime('%H:%M IST')}) — waiting for market to settle.")
        return

    movers = get_market_movers()
    if not movers:
        print("No significant movers found.")
        return

    print(f"Top movers: {[m['ticker'] for m in movers]}")
    report = generate_scalp_setups(movers)

    if report and report.get("top_setups"):
        send_intraday_alert(report, movers)
    else:
        print("No safe setups generated.")

if __name__ == "__main__":
    main()