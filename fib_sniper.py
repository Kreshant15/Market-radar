import os
import requests
import yfinance as yf
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
# Sending these highly technical alerts to the Intraday channel
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_INTRADAY") 

class GoldenPocketSniper:
    def __init__(self, ticker, name):
        self.ticker = ticker
        self.name = name
        self.ema_period = 200 # NERO'S SETTING: The Institutional Line in the Sand

    def fetch_and_calculate(self):
        """Fetches 15m data, calculates the 200 EMA, and maps the 0.68 Golden Pocket."""
        try:
            # 1. Fetch 15 days of 15m data (Required to calculate 200 EMA accurately)
            asset = yf.Ticker(self.ticker)
            df = asset.history(period="15d", interval="15m")
            if df.empty or len(df) < self.ema_period: 
                return None

            # 2. Calculate Nero's Trend Filter (200 EMA)
            df['EMA_200'] = df['Close'].ewm(span=self.ema_period, adjust=False).mean()

            # 3. Identify Swing High and Swing Low over the recent price action (Last 3 days / ~75 candles)
            recent_df = df.tail(75) 
            swing_high = recent_df['High'].max()
            swing_low = recent_df['Low'].min()
            diff = swing_high - swing_low

            # 4. Extract live current market conditions
            current = df.iloc[-1]
            close_price = current['Close']
            low_price = current['Low']
            high_price = current['High']
            ema_200 = current['EMA_200']

            setup = None

            # --- BULLISH SETUP (Price > 200 EMA) ---
            if close_price > ema_200:
                # Calculate Pullback Fibs from High to Low
                fib_0618 = swing_high - (diff * 0.618)
                fib_0680 = swing_high - (diff * 0.680) # Nero's custom boundary
                
                # Did the price dip into the Golden Pocket?
                if fib_0680 <= low_price <= fib_0618:
                    setup = {
                        "direction": "BULLISH (Buy the Dip)",
                        "color": 5763719, # Green
                        "zone": f"₹{fib_0680:,.2f} - ₹{fib_0618:,.2f}",
                        "sl": f"₹{fib_0680 - (close_price * 0.002):,.2f}", # SL slightly below 0.68
                        "spot": close_price,
                        "ema": ema_200
                    }

            # --- BEARISH SETUP (Price < 200 EMA) ---
            elif close_price < ema_200:
                # Calculate Pullback Fibs from Low to High
                fib_0618 = swing_low + (diff * 0.618)
                fib_0680 = swing_low + (diff * 0.680)
                
                # Did the price rally up into the Golden Pocket?
                if fib_0618 <= high_price <= fib_0680:
                    setup = {
                        "direction": "BEARISH (Sell the Rally)",
                        "color": 15548997, # Red
                        "zone": f"₹{fib_0618:,.2f} - ₹{fib_0680:,.2f}",
                        "sl": f"₹{fib_0680 + (close_price * 0.002):,.2f}", # SL slightly above 0.68
                        "spot": close_price,
                        "ema": ema_200
                    }

            return setup

        except Exception as e:
            print(f"Error processing {self.ticker}: {e}")
            return None

    def alert_discord(self, setup):
        """Dispatches the setup to the desk."""
        if not setup: return
        
        # Format for Crypto vs INR
        currency = "$" if "BTC" in self.ticker else "₹"
        
        embed = {
            "title": f"🎯 NERO'S FIBONACCI SCALP: {self.name}",
            "description": f"**15-Minute Golden Pocket Triggered**\n0.618 - 0.68 Zone + 200 EMA Confluence active.",
            "color": setup['color'],
            "fields": [
                {"name": "Action", "value": f"**{setup['direction']}**", "inline": True},
                {"name": "Current Spot", "value": f"{currency}{setup['spot']:,.2f}", "inline": True},
                {"name": "Trend Filter", "value": f"200 EMA @ {currency}{setup['ema']:,.2f}", "inline": True},
                {"name": "Golden Pocket Zone", "value": setup['zone'].replace("₹", currency), "inline": False},
                {"name": "🛡️ Strict Stop Loss", "value": setup['sl'].replace("₹", currency), "inline": True},
                {"name": "Mathematical Logic", "value": "Price has pulled back directly into the 61.8% - 68.0% value area while remaining aligned with the broader 200 EMA institutional trend.", "inline": False}
            ],
            "footer": {"text": "Bade Sahab Tech Scanner • Custom Indicator Logic"}
        }

        try:
            requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]})
            print(f"Fibonacci Alert fired for {self.name}!")
        except Exception as e:
            print(f"Failed to send Discord alert: {e}")

def main():
    print("Initiating Golden Pocket Radar...")
    
    # Tracking Nifty, BankNifty, and BTC (since he sent a crypto screenshot!)
    watchlist = {
        "Nifty 50 Index": "^NSEI",
        "Bank Nifty Index": "^NSEBANK",
        "Bitcoin (USD)": "BTC-USD"
    }

    for name, ticker in watchlist.items():
        sniper = GoldenPocketSniper(ticker, name)
        setup = sniper.fetch_and_calculate()
        
        if setup:
            sniper.alert_discord(setup)
        else:
            print(f"No Golden Pocket entry for {name} currently.")

if __name__ == "__main__":
    main()