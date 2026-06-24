```python
import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()
# Routing this to your Intraday Technical channel
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_INTRADAY")

def fetch_nse_oi_data():
    """Bypasses NSE blocks by simulating a real browser to fetch live Option Chain data."""
    url = 'https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.nseindia.com/get-quotes/derivatives?symbol=NIFTY'
    }
    
    session = requests.Session()
    try:
        # Step 1: Hit the main page to generate valid session cookies
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        # Step 2: Request the actual JSON data using the acquired cookie
        response = session.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f"NSE API Blocked/Error: {response.status_code}")
            return None
    except Exception as e:
        print(f"Failed to fetch NSE data: {e}")
        return None

def analyze_oi(data):
    """Scans the near-the-money options for massive institutional panic unwinding."""
    if not data or 'records' not in data:
        return None, []
        
    underlying_value = data['records']['underlyingValue']
    options = data['filtered']['data'] # NSE provides a pre-filtered list of near-the-money strikes
    
    # The Trigger Level: NSE reports OI in shares. 
    # Nifty lot size is 25. A drop of 1,000,000 shares = 40,000 lots exiting in panic.
    PANIC_THRESHOLD = -1000000 
    alerts = []
    
    for strike in options:
        strike_price = strike.get('strikePrice')
        
        # 1. Check Call Side (Bears panicking = Short Covering = Bullish Squeeze)
        ce = strike.get('CE', {})
        ce_change = ce.get('changeinOpenInterest', 0)
        if ce_change <= PANIC_THRESHOLD:
            alerts.append({
                "type": "🔥 SHORT COVERING DETECTED (Gamma Squeeze)",
                "color": 5763719, # Green (Market going UP)
                "strike": f"{strike_price} CE",
                "action": f"Massive Call Unwinding ({ce_change:,} shares dropped)",
                "setup": "Institutional bears are trapped and exiting in a panic. The market is squeezing UPWARDS.",
                "play": "Look for cheap OTM Call options or Bull Call Spreads to capture the volatility explosion."
            })
            
        # 2. Check Put Side (Bulls panicking = Long Liquidation = Bearish Dump)
        pe = strike.get('PE', {})
        pe_change = pe.get('changeinOpenInterest', 0)
        if pe_change <= PANIC_THRESHOLD:
            alerts.append({
                "type": "🩸 LONG UNWINDING DETECTED (Bull Trap)",
                "color": 15548997, # Red (Market going DOWN)
                "strike": f"{strike_price} PE",
                "action": f"Massive Put Unwinding ({pe_change:,} shares dropped)",
                "setup": "Institutional bulls are trapped and dumping their positions. The market is sliding DOWNWARDS.",
                "play": "Look for cheap OTM Put options or Bear Put Spreads."
            })
            
    return underlying_value, alerts

def send_alert(spot, alert):
    """Formats and fires the Discord ping."""
    embed = {
        "title": alert["type"],
        "description": f"**Asset:** Nifty 50\n**Spot Price:** ₹{spot:,.2f}",
        "color": alert["color"],
        "fields": [
            {"name": "Trapped Level", "value": f"**{alert['strike']}**", "inline": True},
            {"name": "Action", "value": alert["action"], "inline": True},
            {"name": "⚠️ The Setup", "value": alert["setup"], "inline": False},
            {"name": "🎯 Hero-Zero Play", "value": alert["play"], "inline": False}
        ],
        "footer": {"text": "Bade Sahab Flow Engine • Live NSE Option Chain Tracker"}
    }
    
    try:
        # This includes an @here ping because squeeze setups require immediate action
        requests.post(DISCORD_WEBHOOK_URL, json={"content": "@here", "embeds": [embed]})
        print(f"Successfully fired Discord alert for {alert['strike']} panic!")
    except Exception as e:
        print(f"Discord sending error: {e}")

def main():
    print("Scanning NSE Open Interest for Institutional Panic...")
    data = fetch_nse_oi_data()
    
    if data:
        spot, alerts = analyze_oi(data)
        if alerts:
            print(f"Found {len(alerts)} massive OI panic drops! Dispatching alerts...")
            for alert in alerts:
                send_alert(spot, alert)
                time.sleep(2) # Prevent Discord rate limit
        else:
            print("No massive unwinding detected right now. Big money is holding steady.")
    else:
        print("Could not retrieve data from NSE.")

if __name__ == "__main__":
    main()

```
