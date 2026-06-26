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

# 🛑 NOTE: is_market_open() shield has been REMOVED. Bot now runs 24/7/365.

def init_database(retries=3, delay=5):
    """Initializes the DB and handles Neon's 'Cold Start' sleep mode."""
    for attempt in range(retries):
        try:
            conn = psycopg2.connect(DB_URL)
            cursor = conn.cursor()
            # Create the table if it doesn't exist
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
            
            # --- ADD NEW COLUMNS IF THEY DONT EXIST ---
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
                    DO $$ 
                    BEGIN 
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                       WHERE table_name='events' AND column_name='{column}') THEN 
                            ALTER TABLE events ADD COLUMN {column} {col_type}; 
                        END IF; 
                    END $$;
                """)
            conn.commit()
            return conn
            
        except psycopg2.OperationalError as e:
            if "Control plane request failed" in str(e) or attempt < retries - 1:
                print(f"Database is waking up... Retrying in {delay} seconds (Attempt {attempt + 1}/{retries})")
                time.sleep(delay)
            else:
                print("Database connection totally failed after retries.")
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
    """PRE-API CHECK: Saves API tokens by blocking identical or highly similar headline strings."""
    cursor.execute("SELECT headline FROM events WHERE timestamp > %s", 
                   (datetime.now() - timedelta(hours=24),))
    recent_headlines = [row[0] for row in cursor.fetchall() if row[0]]
    
    for old_headline in recent_headlines:
        # If the headline is 80% identical to a previous one, consider it a duplicate!
        similarity = difflib.SequenceMatcher(None, headline.lower(), old_headline.lower()).ratio()
        if similarity > 0.80:
            return True
            
    return False

def is_event_duplicate(cursor, event_name):
    """POST-API CHECK: Blocks different headlines reporting the exact same event."""
    cursor.execute("SELECT timestamp FROM events WHERE event = %s AND timestamp > %s LIMIT 1", 
                   (event_name, datetime.now() - timedelta(hours=COOLDOWN_HOURS)))
    return cursor.fetchone() is not None

def is_worth_analyzing(headline):
    """🛡️ THE ZERO-TOKEN BOUNCER: Filters out corporate noise locally to save Gemini API tokens."""
    headline_lower = headline.lower()
    
    # 1. VIP MACRO OVERRIDE (If any of these are present, ALWAYS analyze it)
    vip_keywords = [
        "rbi", "fed", "war", "missile", "oil", "crude", "inflation", "cpi", 
        "gdp", "rate cut", "rate hike", "geopolitical", "govt", "government", 
        "us ", "china", "sebi"
    ]
    if any(vip in headline_lower for vip in vip_keywords):
        return True # VIP Pass: Send to Gemini

    # 2. THE TRASH FILTER (If no VIP words, check for corporate garbage)
    trash_keywords = [
        "dividend", "q1", "q2", "q3", "q4", "stake", "acquires", "ebitda", 
        "net profit", "board meeting", "appoints", "resigns", "fundraising",
        "yoy", "pat ", "standalone"
    ]
    if any(trash in headline_lower for trash in trash_keywords):
        return False # Blocked: Do not waste a token

    # 3. DEFAULT (If it's neither VIP nor obvious trash, let Gemini decide)
    return True

def cleanup_database(conn, cursor):
    """🧹 Auto-Cleaner: Keeps the free PostgreSQL database lightweight and fast."""
    try:
        # 1. Delete Corporate Noise (IGNORE / Low Impact) older than 48 hours
        cursor.execute('''
            DELETE FROM events 
            WHERE (event_type = 'IGNORE' OR impact_score < 40) 
            AND timestamp < NOW() - INTERVAL '48 hours'
        ''')
        
        # 2. Delete ALL alerts older than 14 days
        cursor.execute('''
            DELETE FROM events 
            WHERE timestamp < NOW() - INTERVAL '14 days'
        ''')
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Database cleanup failed: {e}")

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
    nifty_dir = data.get('nifty_direction', 'NEUTRAL').upper()
    prob       = data.get('direction_probability', 'N/A')
    region     = data.get('event_region', 'INDIAN').upper()
    event_type = data.get('event_type', 'OTHER')
    event_name = data.get('event', 'Unknown Event')
    strategy   = data.get('suggested_strategy', 'N/A')
    hedging    = data.get('strategy_hedging', 'N/A')
    reasoning  = data.get('reasoning', 'N/A')
    vix_impact = data.get('vix_impact', 'STABLE')

    # ── Direction badge ────────────────────────────────────────────────────────
    dir_icon  = "🟢" if nifty_dir == "BULLISH" else "🔴" if nifty_dir == "BEARISH" else "⚪"
    color     = 5763719 if nifty_dir == "BULLISH" else 15548997 if nifty_dir == "BEARISH" else 8421504

    # ── Spot price strings ─────────────────────────────────────────────────────
    n_spot = f"₹{nifty_spot:,.2f}" if nifty_spot > 0 else "—"
    b_spot = f"₹{banknifty_spot:,.2f}" if banknifty_spot > 0 else "—"
    v_lvl  = f"{vix_level:.2f}" if vix_level > 0 else "—"

    # ── VIX mood label ─────────────────────────────────────────────────────────
    vix_mood = {"SPIKE": "⚠️ Spiking — volatility expanding",
                "CRUSH": "✅ Crushing — ideal for credit spreads",
                "STABLE": "➡️ Stable"}.get(vix_impact.upper(), vix_impact)

    # ── Region tag ─────────────────────────────────────────────────────────────
    region_tag = {"GLOBAL": "🌐 Global", "HEAVYWEIGHT": "🏋️ Heavyweight",
                  "INDIAN": "🇮🇳 Indian"}.get(region, region)

    # ══════════════════════════════════════════════════════════════════════════
    # LINE 1: THE SIGNAL  (most important — visible without scrolling)
    # Format: 🟢 BULLISH · 78% probability · Bull Put Spread
    # ══════════════════════════════════════════════════════════════════════════
    signal_line = f"{dir_icon} **{nifty_dir}** · {prob} probability · `{strategy}`"

    embed = {
        "title": f"{event_name}",
        "description": (
            f"{signal_line}\n"
            f">>> {headline}"
        ),
        "color": color,
        "fields": []
    }

    # ── BLOCK A: Market snapshot (always shown, compact 3-col) ─────────────────
    embed["fields"].extend([
        {"name": "Nifty",     "value": n_spot,  "inline": True},
        {"name": "BankNifty", "value": b_spot,  "inline": True},
        {"name": "VIX",       "value": v_lvl,   "inline": True},
    ])

    # ── BLOCK B: VIX signal + region tag (1 row) ──────────────────────────────
    embed["fields"].extend([
        {"name": "VIX signal", "value": vix_mood,   "inline": True},
        {"name": "Region",     "value": region_tag,  "inline": True},
        {"name": "Type",       "value": f"`{event_type}`", "inline": True},
    ])

    # ── BLOCK C: Macro data — only when present and meaningful ────────────────
    actual   = data.get('macro_actual_data', 'N/A')
    forecast = data.get('macro_forecast_data', 'N/A')
    rate_imp = data.get('macro_rate_impact', 'N/A')

    has_macro = any(v not in ('N/A', 'NEUTRAL', '') for v in [actual, forecast, rate_imp])
    if has_macro:
        macro_val = ""
        if actual   != 'N/A': macro_val += f"**Actual:** {actual}\n"
        if forecast != 'N/A': macro_val += f"**Forecast:** {forecast}\n"
        if rate_imp not in ('N/A', 'NEUTRAL'): macro_val += f"**CB Impact:** {rate_imp}"
        embed["fields"].append({
            "name": "📊 Macro Data", "value": macro_val.strip(), "inline": False
        })

    # ── BLOCK D: Risk note — single clean line ────────────────────────────────
    if hedging and hedging != 'N/A':
        embed["fields"].append({
            "name": "🛡️ Risk", "value": hedging, "inline": False
        })

    # ── BLOCK E: Micro target — only when a real ticker exists ───────────────
    stock  = data.get('affected_stock', 'None')
    ticker = data.get('target_ticker', 'NONE')
    micro  = data.get('micro_strategy', 'N/A')

    if ticker not in ('NONE', 'N/A', '', None):
        s_spot = f"₹{target_spot:,.2f}" if target_spot > 0 else "—"
        embed["fields"].append({
            "name": f"🎯 {stock} ({ticker})",
            "value": f"Spot: **{s_spot}** · {micro}",
            "inline": False
        })

    # ── BLOCK F: Context — collapsed to 1 field, trimmed to 300 chars ─────────
    if reasoning and reasoning != 'N/A':
        short_reason = reasoning[:297] + "…" if len(reasoning) > 300 else reasoning
        embed["fields"].append({
            "name": "📌 Context", "value": short_reason, "inline": False
        })

    # ── Footer ────────────────────────────────────────────────────────────────
    embed["footer"] = {
        "text": f"Bade Sahab • {region_tag} • {datetime.now().strftime('%d %b %Y, %I:%M %p IST')}"
    }

    # ── Chart attachment ───────────────────────────────────────────────────────
    chart_ticker = ticker if ticker not in ('NONE', 'N/A', '', None) else "^NSEI"
    chart_spot   = target_spot if ticker not in ('NONE', 'N/A', '', None) else nifty_spot
    chart_path   = None

    if chart_spot > 0:
        chart_path = chart_generator.create_entry_chart(chart_ticker, nifty_dir, chart_spot)

    # ── Smart webhook routing ──────────────────────────────────────────────────
    target_webhook = {
        "GLOBAL":      WEBHOOK_GLOBAL,
        "HEAVYWEIGHT": WEBHOOK_HEAVYWEIGHT,
    }.get(region, WEBHOOK_INDIAN) or WEBHOOK_INDIAN

    # ── Send ───────────────────────────────────────────────────────────────────
    try:
        if chart_path and os.path.exists(chart_path):
            embed["image"] = {"url": f"attachment://{os.path.basename(chart_path)}"}
            with open(chart_path, "rb") as f:
                requests.post(
                    target_webhook,
                    data={"payload_json": json.dumps({"embeds": [embed]})},
                    files={"file": (os.path.basename(chart_path), f, "image/png")}
                )
            os.remove(chart_path)
        else:
            requests.post(target_webhook, json={"embeds": [embed]})
    except Exception as e:
        print(f"Failed to send alert: {e}")

def main():
    conn = init_database()
    cursor = conn.cursor()
    
    # 🧹 Run the Janitor before anything else to clear old data
    cleanup_database(conn, cursor)
    
    nifty_spot, banknifty_spot, vix_level = get_live_market_prices()

    try:
        # 1. Collect and filter headlines locally (The Zero-Token Bouncer)
        headlines_to_analyze = []
        for headline in news_fetcher.fetch_top_headlines():
            if is_headline_duplicate(cursor, headline):
                continue 
                
            if not is_worth_analyzing(headline):
                print(f"Skipped Corporate Noise (Local Bouncer): {headline}")
                # Still save it as IGNORE so it doesn't get processed again next 10 mins
                save_to_database(conn, cursor, headline, {"event_type": "IGNORE", "impact_score": 0}, nifty_spot, banknifty_spot, vix_level, 0)
                continue
                
            headlines_to_analyze.append(headline)

        # 2. Batch process the surviving headlines in ONE API call!
        if headlines_to_analyze:
            try:
                batch_response = analyzer.analyze_headlines_batch(headlines_to_analyze)
                parsed_batch = json.loads(batch_response).get("analyses", [])
                
                # 3. Handle the returned data normally
                for data in parsed_batch:
                    headline = data.get("headline_analyzed")
                    if not headline: 
                        continue
                        
                    target_spot = get_target_price(data.get("target_ticker", "NONE"))
                    
                    # Check for duplicate events BEFORE saving to the DB
                    is_duplicate = is_event_duplicate(cursor, data.get("event", "Unknown"))
                    
                    # 🛑 ALWAYS save to database so the PRE-API check remembers this exact string for next time!
                    save_to_database(conn, cursor, headline, data, nifty_spot, banknifty_spot, vix_level, target_spot)
                    
                    # ANTI-NOISE FILTER: Skip Discord alert for IGNORE or low impact
                    if data.get("event_type", "OTHER") == "IGNORE" or int(data.get("impact_score", 0)) < 40:
                        print(f"Skipped Corporate Noise (Saved to Blocklist): {headline}")
                        continue

                    # POST-API CHECK: Skip Discord alert if event was already reported
                    if is_duplicate: 
                        print(f"Skipped duplicate event (Saved to Blocklist): {headline}")
                        continue
                        
                    send_discord_alert(headline, data, nifty_spot, banknifty_spot, vix_level, target_spot)
                    
                    # Add a 2-second delay between Discord posts to avoid rate-limiting if there are multiple hits
                    time.sleep(2)
            except Exception as e:
                print(f"Error processing batch API response: {e}")
                conn.rollback()

    except Exception as e:
        print(e)
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    main()