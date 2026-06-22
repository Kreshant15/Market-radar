import json
import os
import time
import psycopg2
import requests
import yfinance as yf
from datetime import datetime, timedelta
from dotenv import load_dotenv
import news_fetcher
import analyzer
import chart_generator

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
WEBHOOK_FORADAR = os.getenv("DISCORD_WEBHOOK_FORADAR")
WEBHOOK_SECTOR = os.getenv("DISCORD_WEBHOOK_SECTOR")
COOLDOWN_HOURS = 6

# 🛑 NOTE: is_market_open() shield has been REMOVED. Bot now runs 24/7/365.

def init_database():
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS events (id SERIAL PRIMARY KEY, headline TEXT NOT NULL, event TEXT NOT NULL, event_type TEXT NOT NULL, impact_score INTEGER NOT NULL, confidence INTEGER NOT NULL, timestamp TIMESTAMP NOT NULL, reasoning TEXT NOT NULL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS portfolio (id SERIAL PRIMARY KEY, current_balance NUMERIC NOT NULL, updated_at TIMESTAMP NOT NULL)''')
    
    cursor.execute('SELECT COUNT(*) FROM portfolio')
    if cursor.fetchone()[0] == 0:
        cursor.execute('INSERT INTO portfolio (id, current_balance, updated_at) VALUES (1, 1000000.0, NOW())')
        conn.commit()
    
    columns_to_add = {
        "nifty_spot": "NUMERIC", "banknifty_spot": "NUMERIC", "vix_level": "NUMERIC",
        "suggested_strategy": "TEXT", "verdict_issued": "BOOLEAN DEFAULT FALSE", "pnl_inr": "NUMERIC",
        "affected_sector": "TEXT", "affected_stock": "TEXT", "target_ticker": "TEXT",
        "micro_strategy": "TEXT", "target_spot": "NUMERIC", "trap_checked": "BOOLEAN DEFAULT FALSE",
        "direction_probability": "TEXT" # New Probability Column
    }
    
    for column, col_type in columns_to_add.items():
        cursor.execute(f"SELECT COUNT(*) FROM information_schema.columns WHERE table_name='events' AND column_name='{column}'")
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"ALTER TABLE events ADD COLUMN {column} {col_type};")
            conn.commit()
            
    return conn

def get_live_market_prices():
    try:
        nifty, banknifty, vix = yf.Ticker("^NSEI"), yf.Ticker("^NSEBANK"), yf.Ticker("^INDIAVIX")
        return (
            float(round(nifty.history(period="1d")['Close'].iloc[-1].item(), 2)) if not nifty.history(period="1d").empty else 0.0,
            float(round(banknifty.history(period="1d")['Close'].iloc[-1].item(), 2)) if not banknifty.history(period="1d").empty else 0.0,
            float(round(vix.history(period="1d")['Close'].iloc[-1].item(), 2)) if not vix.history(period="1d").empty else 0.0
        )
    except Exception: return 0.0, 0.0, 0.0

def get_target_price(ticker):
    if not ticker or ticker == 'NONE': return 0.0
    try:
        asset = yf.Ticker(ticker)
        history = asset.history(period="1d")
        return float(round(history['Close'].iloc[-1].item(), 2)) if not history.empty else 0.0
    except Exception: return 0.0

def is_duplicate_event(cursor, event_name, headline):
    # Strict deduplication checks BOTH the headline string and the AI's categorized event name
    cursor.execute("SELECT timestamp FROM events WHERE (event = %s OR headline = %s) AND timestamp > %s ORDER BY timestamp DESC LIMIT 1", 
                   (event_name, headline, datetime.now() - timedelta(hours=COOLDOWN_HOURS)))
    return cursor.fetchone() is not None

def save_to_database(conn, cursor, headline, data, nifty_spot, banknifty_spot, vix_level, target_spot):
    cursor.execute('''
        INSERT INTO events (headline, event, event_type, impact_score, confidence, timestamp, reasoning, 
        nifty_spot, banknifty_spot, vix_level, suggested_strategy, affected_sector, affected_stock, target_ticker, micro_strategy, target_spot, direction_probability)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (
        headline, data.get("event", "Unknown"), data.get("event_type", "OTHER"), data.get("impact_score", 0), data.get("confidence", 0),
        datetime.now(), data.get("reasoning", ""), nifty_spot if nifty_spot > 0 else None, banknifty_spot if banknifty_spot > 0 else None,
        vix_level if vix_level > 0 else None, data.get("suggested_strategy", "N/A"), data.get("affected_sector", "Broader Market"),
        data.get("affected_stock", "None"), data.get("target_ticker", "NONE"), data.get("micro_strategy", "N/A"), target_spot if target_spot > 0 else None,
        data.get("direction_probability", "N/A")
    ))
    conn.commit()

def send_discord_alert(headline, data, nifty_spot, banknifty_spot, vix_level, target_spot):
    nifty_dir = data.get('nifty_direction', '').upper()
    prob = data.get('direction_probability', 'N/A')
    color = 5763719 if nifty_dir == 'BULLISH' else 15548997 if nifty_dir == 'BEARISH' else 8421504

    # Format spot strings, noting if market is closed (e.g. Sunday)
    n_spot = f"₹{nifty_spot:,}" if nifty_spot > 0 else "Market Closed"
    b_spot = f"₹{banknifty_spot:,}" if banknifty_spot > 0 else "Market Closed"

    embed = {
        "title": f"🚨 [{data.get('event_type')}] {data.get('event')}",
        "description": f"**Headline:** {headline}",
        "color": color,
        "fields": [
            {"name": "Historical Probability", "value": f"**{prob}** {nifty_dir}", "inline": True},
            {"name": "Nifty Spot", "value": n_spot, "inline": True},
            {"name": "BankNifty Spot", "value": b_spot, "inline": True},
            {"name": "VIX Impact", "value": data.get('vix_impact'), "inline": True},
            {"name": "📈 Strategy", "value": f"**{data.get('suggested_strategy', 'N/A')}**", "inline": False}
        ],
        "footer": {"text": "Bade Sahab Live Macro Desk • 24/7 Global Scanner"}
    }

    stock, sector, ticker = data.get('affected_stock', 'None'), data.get('affected_sector', 'Broader Market'), data.get('target_ticker', 'NONE')
    if stock != 'None' or sector != 'Broader Market':
        s_spot = f"₹{target_spot:,}" if target_spot > 0 else "Market Closed"
        embed["fields"].append({"name": f"🎯 Micro Target: {stock if stock != 'None' else sector} ({ticker})", "value": f"Spot: **{s_spot}**\nStrategy: **{data.get('micro_strategy', 'N/A')}**", "inline": False})

    embed["fields"].extend([{"name": "🛡️ Risk", "value": data.get('strategy_hedging', 'N/A'), "inline": False}, {"name": "Historical Context", "value": data.get('reasoning'), "inline": False}])
    
    chart_ticker = ticker if ticker != 'NONE' else "^NSEI"
    chart_spot = target_spot if ticker != 'NONE' else nifty_spot
    chart_path = None
    
    # Only generate chart if spot price is > 0 (meaning market isn't completely offline/weekend flat)
    if chart_spot > 0:
        chart_path = chart_generator.create_entry_chart(chart_ticker, nifty_dir, chart_spot)
        
    target_webhook = WEBHOOK_SECTOR if (stock != 'None' or sector != 'Broader Market') else WEBHOOK_FORADAR

    try:
        if chart_path and os.path.exists(chart_path):
            embed["image"] = {"url": f"attachment://{os.path.basename(chart_path)}"}
            with open(chart_path, "rb") as f:
                requests.post(target_webhook, data={"payload_json": json.dumps({"embeds": [embed]})}, files={"file": (os.path.basename(chart_path), f, "image/png")})
            os.remove(chart_path)
        else:
            requests.post(target_webhook, json={"embeds": [embed]})
    except Exception as e:
        print(f"Failed to send alert: {e}")

def main():
    conn = init_database()
    cursor = conn.cursor()
    nifty_spot, banknifty_spot, vix_level = get_live_market_prices()

    try:
        for headline in news_fetcher.fetch_top_headlines():
            try:
                data = json.loads(analyzer.analyze_headline(headline))
                
                # 🛑 ANTI-NOISE FILTER: Instantly skip anything tagged IGNORE or low impact
                if data.get("event_type", "OTHER") == "IGNORE" or int(data.get("impact_score", 0)) < 40:
                    print(f"Skipped Corporate Noise/Low Impact: {headline}")
                    continue

                if is_duplicate_event(cursor, data.get("event", "Unknown"), headline): 
                    print(f"Skipped duplicate: {headline}")
                    continue
                    
                target_spot = get_target_price(data.get("target_ticker", "NONE"))
                save_to_database(conn, cursor, headline, data, nifty_spot, banknifty_spot, vix_level, target_spot)
                send_discord_alert(headline, data, nifty_spot, banknifty_spot, vix_level, target_spot)
                time.sleep(5)
            except Exception as e:
                conn.rollback()
    except Exception as e:
        print(e)
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    main()