import os
import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_FORADAR")

# ADD YOUR LEVELS HERE: Just update this list whenever you draw a new level.
# Logic: Price is checked every X minutes. If within 0.1% of level, alert!
WATCHLIST = [
    {"ticker": "^NSEI", "name": "Nifty 50", "level": 24150, "type": "Resistance"},
    {"ticker": "^NSEBANK", "name": "Bank Nifty", "level": 58500, "type": "Resistance"},
    {"ticker": "^NSEBANK", "name": "Bank Nifty", "level": 58000, "type": "Support"},
]

def check_levels():
    """Loops through the watchlist and checks if the spot price is near the target."""
    for item in WATCHLIST:
        ticker = item['ticker']
        target = item['level']
        
        try:
            # Fetch 1 day of historical data to get the latest close
            data = yf.Ticker(ticker).history(period="1d")
            if data.empty: 
                continue
            
            # The last available close price is our current spot price
            current_price = data['Close'].iloc[-1]
            
            # Logic: If we are within 0.1% of the target, alert the desk!
            diff = abs(current_price - target)
            threshold = target * 0.001 
            
            if diff <= threshold:
                alert_discord(item, current_price)
        except Exception as e:
            print(f"Error checking {ticker}: {e}")

def alert_discord(item, price):
    """Formats and sends the alert to Discord."""
    
    # Set color based on Support (Green) or Resistance (Red)
    color = 5763719 if item['type'].upper() == "SUPPORT" else 15548997
    
    embed = {
        "title": f"🎯 LEVEL ALERT: {item['name']}",
        "description": f"Price is interacting with your marked **{item['type']}** level.",
        "color": color,
        "fields": [
            {"name": "Marked Level", "value": f"₹{item['level']:,.2f}", "inline": True},
            {"name": "Current Spot Price", "value": f"₹{price:,.2f}", "inline": True},
            {"name": "Action Plan", "value": f"Watch for a potential bounce (if support) or rejection (if resistance). Look for confirmation candles.", "inline": False}
        ],
        "footer": {"text": "Bade Sahab Technical Monitor • Indian Market Radar"}
    }
    
    try:
        requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]})
        print(f"Alert sent for {item['name']} at {item['level']}")
    except Exception as e:
        print(f"Failed to send Discord alert: {e}")

if __name__ == "__main__":
    print("Initiating Technical Level Radar...")
    check_levels()