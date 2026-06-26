import json
import os
import time
import psycopg2
import requests
import yfinance as yf
import difflib
from datetime import datetime, timedelta
from dotenv import load_dotenv
import news_fetcher
import analyzer
import chart_generator

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
WEBHOOK_INDIAN = os.getenv("DISCORD_WEBHOOK_FORADAR")
WEBHOOK_HEAVYWEIGHT = os.getenv("DISCORD_WEBHOOK_SECTOR")
WEBHOOK_GLOBAL = os.getenv("DISCORD_WEBHOOK_GLOBAL")
COOLDOWN_HOURS = 6

def init_database(retries=3, delay=5):
    """Initializes the DB and handles Neon's 'Cold Start' sleep mode."""
    for attempt in range(retries):
        try:
            conn = psycopg2.connect(DB_URL)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS events (
                    id SERIAL PRIMARY KEY,
                    headline TEXT,
                    event TEXT,
                    event_type TEXT,
                    impact_score INTEGER,
                    confidence INTEGER,
                    timestamp TIMESTAMP,
                    reasoning TEXT
                )
            ''')
            columns_to_add = {
                "nifty_spot": "NUMERIC", "banknifty_spot": "NUMERIC", "vix_level": "NUMERIC",
                "suggested_strategy": "TEXT", "verdict_issued": "BOOLEAN DEFAULT FALSE", "pnl_inr": "NUMERIC",
                "affected_sector": "TEXT", "affected_stock": "TEXT", "target_ticker": "TEXT",
                "micro_strategy": "TEXT", "target_spot": "NUMERIC", "trap_checked": "BOOLEAN DEFAULT FALSE",
                "direction_probability": "TEXT", "event_region": "TEXT", "nifty_direction": "TEXT",
                "macro_actual_data": "TEXT", "macro_forecast_data": "TEXT", "macro_rate_impact": "TEXT"
            }
            for column, col_type in columns_to_add.items():
                cursor.execute(f"""
                    DO $$ BEGIN 
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                       WHERE table_name='events' AND column_name='{column}') THEN 
                            ALTER TABLE events ADD COLUMN {column} {col_type}; 
                        END IF; 
                    END $$;
                """)
            conn.commit()
            return conn
        except psycopg2.OperationalError as e:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                raise e

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

def is_headline_duplicate(cursor, headline):
    cursor.execute("SELECT headline FROM events WHERE timestamp > %s", (datetime.now() - timedelta(hours=24),))
    recent_headlines = [row[0] for row in cursor.fetchall() if row[0]]
    for old_headline in recent_headlines:
        if difflib.SequenceMatcher(None, headline.lower(), old_headline.lower()).ratio() > 0.80:
            return True
    return False

def is_event_duplicate(cursor, event_name):
    cursor.execute("SELECT timestamp FROM events WHERE event = %s AND timestamp > %s LIMIT 1", 
                   (event_name, datetime.now() - timedelta(hours=COOLDOWN_HOURS)))
    return cursor.fetchone() is not None

def is_worth_analyzing(headline):
    headline_lower = headline.lower()
    vip_keywords = ["rbi", "fed", "war", "missile", "oil", "crude", "inflation", "cpi", "gdp", "rate cut", "rate hike", "geopolitical", "govt", "government", "us ", "china", "sebi"]
    if any(vip in headline_lower for vip in vip_keywords): return True
    trash_keywords = ["dividend", "q1", "q2", "q3", "q4", "stake", "acquires", "ebitda", "net profit", "board meeting", "appoints", "resigns", "fundraising", "yoy", "pat ", "standalone"]
    if any(trash in headline_lower for trash in trash_keywords): return False
    return True

def cleanup_database(conn, cursor):
    try:
        cursor.execute("DELETE FROM events WHERE (event_type = 'IGNORE' OR impact_score < 40) AND timestamp < NOW() - INTERVAL '48 hours'")
        cursor.execute("DELETE FROM events WHERE timestamp < NOW() - INTERVAL '14 days'")
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Cleanup failed: {e}")

def save_to_database(conn, cursor, headline, data, nifty_spot, banknifty_spot, vix_level, target_spot):
    cursor.execute('''
        INSERT INTO events (headline, event, event_type, impact_score, confidence, timestamp, reasoning, 
        nifty_spot, banknifty_spot, vix_level, suggested_strategy, affected_sector, affected_stock, target_ticker, micro_strategy, target_spot, direction_probability, event_region, nifty_direction, macro_actual_data, macro_forecast_data, macro_rate_impact)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (
        headline, data.get("event", "Unknown"), data.get("event_type", "OTHER"), data.get("impact_score", 0), data.get("confidence", 0),
        datetime.now(), data.get("reasoning", ""), nifty_spot if nifty_spot > 0 else None, banknifty_spot if banknifty_spot > 0 else None,
        vix_level if vix_level > 0 else None, data.get("suggested_strategy", "N/A"), data.get("affected_sector", "Broader Market"),
        data.get("affected_stock", "None"), data.get("target_ticker", "NONE"), data.get("micro_strategy", "N/A"), target_spot if target_spot > 0 else None,
        data.get("direction_probability", "N/A"), data.get("event_region", "INDIAN"), data.get("nifty_direction", "NEUTRAL"),
        data.get("macro_actual_data", "N/A"), data.get("macro_forecast_data", "N/A"), data.get("macro_rate_impact", "N/A")
    ))
    conn.commit()

def send_discord_alert(headline, data, nifty_spot, banknifty_spot, vix_level, target_spot):
    # [Keeping your exact alert logic here]
    nifty_dir = data.get('nifty_direction', 'NEUTRAL').upper()
    prob = data.get('direction_probability', 'N/A')
    region = data.get('event_region', 'INDIAN').upper()
    event_type = data.get('event_type', 'OTHER')
    event_name = data.get('event', 'Unknown Event')
    strategy = data.get('suggested_strategy', 'N/A')
    hedging = data.get('strategy_hedging', 'N/A')
    reasoning = data.get('reasoning', 'N/A')
    vix_impact = data.get('vix_impact', 'STABLE')

    dir_icon = "🟢" if nifty_dir == "BULLISH" else "🔴" if nifty_dir == "BEARISH" else "⚪"
    color = 5763719 if nifty_dir == "BULLISH" else 15548997 if nifty_dir == "BEARISH" else 8421504
    n_spot, b_spot, v_lvl = f"₹{nifty_spot:,.2f}" if nifty_spot > 0 else "—", f"₹{banknifty_spot:,.2f}" if banknifty_spot > 0 else "—", f"{vix_level:.2f}" if vix_level > 0 else "—"
    vix_mood = {"SPIKE": "⚠️ Spiking", "CRUSH": "✅ Crushing", "STABLE": "➡️ Stable"}.get(vix_impact.upper(), vix_impact)
    region_tag = {"GLOBAL": "🌐 Global", "HEAVYWEIGHT": "🏋️ Heavyweight", "INDIAN": "🇮🇳 Indian"}.get(region, region)
    
    signal_line = f"{dir_icon} **{nifty_dir}** · {prob} probability · `{strategy}`"
    embed = {"title": f"{event_name}", "description": f"{signal_line}\n>>> {headline}", "color": color, "fields": []}
    embed["fields"].extend([{"name": "Nifty", "value": n_spot, "inline": True}, {"name": "BankNifty", "value": b_spot, "inline": True}, {"name": "VIX", "value": v_lvl, "inline": True}])
    embed["fields"].extend([{"name": "VIX signal", "value": vix_mood, "inline": True}, {"name": "Region", "value": region_tag, "inline": True}, {"name": "Type", "value": f"`{event_type}`", "inline": True}])

    actual, forecast, rate_imp = data.get('macro_actual_data', 'N/A'), data.get('macro_forecast_data', 'N/A'), data.get('macro_rate_impact', 'N/A')
    if any(v not in ('N/A', 'NEUTRAL', '') for v in [actual, forecast, rate_imp]):
        macro_val = ""
        if actual != 'N/A': macro_val += f"**Actual:** {actual}\n"
        if forecast != 'N/A': macro_val += f"**Forecast:** {forecast}\n"
        if rate_imp not in ('N/A', 'NEUTRAL'): macro_val += f"**CB Impact:** {rate_imp}"
        embed["fields"].append({"name": "📊 Macro Data", "value": macro_val.strip(), "inline": False})

    if hedging and hedging != 'N/A': embed["fields"].append({"name": "🛡️ Risk", "value": hedging, "inline": False})
    ticker = data.get('target_ticker', 'NONE')
    if ticker not in ('NONE', 'N/A', '', None):
        s_spot = f"₹{target_spot:,.2f}" if target_spot > 0 else "—"
        embed["fields"].append({"name": f"🎯 {data.get('affected_stock', 'Stock')} ({ticker})", "value": f"Spot: **{s_spot}** · {data.get('micro_strategy', 'N/A')}", "inline": False})
    if reasoning and reasoning != 'N/A':
        embed["fields"].append({"name": "📌 Context", "value": reasoning[:297] + "…", "inline": False})

    embed["footer"] = {"text": f"Bade Sahab • {region_tag} • {datetime.now().strftime('%d %b %Y, %I:%M %p IST')}"}
    chart_ticker = ticker if ticker not in ('NONE', 'N/A', '', None) else "^NSEI"
    chart_spot = target_spot if ticker not in ('NONE', 'N/A', '', None) else nifty_spot
    chart_path = chart_generator.create_entry_chart(chart_ticker, nifty_dir, chart_spot) if chart_spot > 0 else None

    target_webhook = {"GLOBAL": WEBHOOK_GLOBAL, "HEAVYWEIGHT": WEBHOOK_HEAVYWEIGHT}.get(region, WEBHOOK_INDIAN) or WEBHOOK_INDIAN
    try:
        if chart_path and os.path.exists(chart_path):
            embed["image"] = {"url": f"attachment://{os.path.basename(chart_path)}"}
            with open(chart_path, "rb") as f:
                requests.post(target_webhook, data={"payload_json": json.dumps({"embeds": [embed]})}, files={"file": (os.path.basename(chart_path), f, "image/png")})
            os.remove(chart_path)
        else:
            requests.post(target_webhook, json={"embeds": [embed]})
    except Exception as e: print(f"Failed to send alert: {e}")

def main():
    """Executes once per Cron trigger."""
    print(f"[{datetime.now()}] Cron trigger started...")
    conn = None
    try:
        conn = init_database()
        cursor = conn.cursor()
        
        headlines = news_fetcher.fetch_news()
        if not headlines:
            print("No new news found.")
            return

        for headline in headlines:
            if is_headline_duplicate(cursor, headline): continue
            if not is_worth_analyzing(headline): continue
            
            print(f"Analyzing: {headline[:50]}...")
            data = analyzer.analyze_headline(headline)
            
            if not data or data.get("event_type") == "IGNORE": continue
            if is_event_duplicate(cursor, data.get("event")): continue
            
            nifty, banknifty, vix = get_live_market_prices()
            target_spot = get_target_price(data.get("target_ticker", "NONE"))
            
            save_to_database(conn, cursor, headline, data, nifty, banknifty, vix, target_spot)
            send_discord_alert(headline, data, nifty, banknifty, vix, target_spot)
            print(f"✅ Alert sent: {data.get('event')}")

        cleanup_database(conn, cursor)
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        if conn:
            conn.close()
            print("Database connection closed.")
    print(f"[{datetime.now()}] Process complete. Exiting.")

if __name__ == "__main__":
    main()
