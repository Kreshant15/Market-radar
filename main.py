import json
import os
import time
import psycopg2
import requests
import yfinance as yf
import difflib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import news_fetcher
import analyzer

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
WEBHOOK_INDIAN = os.getenv("DISCORD_WEBHOOK_FORADAR")
WEBHOOK_HEAVYWEIGHT = os.getenv("DISCORD_WEBHOOK_SECTOR")
WEBHOOK_GLOBAL = os.getenv("DISCORD_WEBHOOK_GLOBAL")
COOLDOWN_HOURS = 6

def init_database(retries=3, delay=5):
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
            if attempt < retries - 1:
                print(f"Database is waking up... Retrying in {delay}s (Attempt {attempt + 1}/{retries})")
                time.sleep(delay)
            else:
                print("Database connection totally failed after retries.")
                raise e

def get_live_market_prices():
    try:
        nifty    = yf.Ticker("^NSEI")
        banknifty = yf.Ticker("^NSEBANK")
        vix      = yf.Ticker("^INDIAVIX")
        n = float(round(nifty.history(period="1d")['Close'].iloc[-1].item(), 2)) if not nifty.history(period="1d").empty else 0.0
        b = float(round(banknifty.history(period="1d")['Close'].iloc[-1].item(), 2)) if not banknifty.history(period="1d").empty else 0.0
        v = float(round(vix.history(period="1d")['Close'].iloc[-1].item(), 2)) if not vix.history(period="1d").empty else 0.0
        return n, b, v
    except Exception:
        return 0.0, 0.0, 0.0

def get_target_price(ticker):
    if not ticker or ticker == 'NONE':
        return 0.0
    try:
        history = yf.Ticker(ticker).history(period="1d")
        return float(round(history['Close'].iloc[-1].item(), 2)) if not history.empty else 0.0
    except Exception:
        return 0.0

def is_headline_duplicate(cursor, headline):
    cursor.execute(
        "SELECT headline FROM events WHERE timestamp > %s",
        (datetime.now() - timedelta(hours=24),)
    )
    recent_headlines = [row[0] for row in cursor.fetchall() if row[0]]
    for old_headline in recent_headlines:
        similarity = difflib.SequenceMatcher(None, headline.lower(), old_headline.lower()).ratio()
        if similarity > 0.80:
            return True
    return False

def is_event_duplicate(cursor, event_name):
    cursor.execute(
        "SELECT timestamp FROM events WHERE event = %s AND timestamp > %s LIMIT 1",
        (event_name, datetime.now() - timedelta(hours=COOLDOWN_HOURS))
    )
    return cursor.fetchone() is not None

def is_worth_analyzing(headline):
    headline_lower = headline.lower()
    vip_keywords = [
        "rbi", "fed", "war", "missile", "oil", "crude", "inflation", "cpi",
        "gdp", "rate cut", "rate hike", "geopolitical", "govt", "government",
        "us ", "china", "sebi", "iran", "israel", "russia", "pakistan",
        "airstrike", "attack", "bombing", "sanctions", "nuclear"
    ]
    if any(vip in headline_lower for vip in vip_keywords):
        return True
    trash_keywords = [
        "dividend", "q1", "q2", "q3", "q4", "stake", "acquires", "ebitda",
        "net profit", "board meeting", "appoints", "resigns", "fundraising",
        "yoy", "pat ", "standalone"
    ]
    if any(trash in headline_lower for trash in trash_keywords):
        return False
    return True

def cleanup_database(conn, cursor):
    try:
        cursor.execute('''
            DELETE FROM events
            WHERE (event_type = 'IGNORE' OR impact_score < 40)
            AND timestamp < NOW() - INTERVAL '48 hours'
        ''')
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
        INSERT INTO events (
            headline, event, event_type, impact_score, confidence, timestamp, reasoning,
            nifty_spot, banknifty_spot, vix_level, suggested_strategy, affected_sector,
            affected_stock, target_ticker, micro_strategy, target_spot, direction_probability,
            event_region, nifty_direction, macro_actual_data, macro_forecast_data, macro_rate_impact
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ''', (
        headline,
        data.get("event", "Unknown"),
        data.get("event_type", "OTHER"),
        data.get("impact_score", 0),
        data.get("confidence", 0),
        datetime.now(),
        data.get("reasoning", ""),
        nifty_spot if nifty_spot > 0 else None,
        banknifty_spot if banknifty_spot > 0 else None,
        vix_level if vix_level > 0 else None,
        data.get("suggested_strategy", "N/A"),
        data.get("affected_sector", "Broader Market"),
        data.get("affected_stock", "None"),
        data.get("target_ticker", "NONE"),
        data.get("micro_strategy", "N/A"),
        target_spot if target_spot > 0 else None,
        data.get("direction_probability", "N/A"),
        data.get("event_region", "INDIAN"),
        data.get("nifty_direction", "NEUTRAL"),
        data.get("macro_actual_data", "N/A"),
        data.get("macro_forecast_data", "N/A"),
        data.get("macro_rate_impact", "N/A")
    ))
    conn.commit()

def send_discord_alert(headline, data, nifty_spot, banknifty_spot, vix_level, target_spot):
    nifty_dir  = data.get('nifty_direction', 'NEUTRAL').upper()
    prob       = data.get('direction_probability', 'N/A')
    region     = data.get('event_region', 'INDIAN').upper()
    event_type = data.get('event_type', 'OTHER')
    event_name = data.get('event', 'Unknown Event')
    strategy   = data.get('suggested_strategy', 'N/A')
    hedging    = data.get('strategy_hedging', 'N/A')
    reasoning  = data.get('reasoning', 'N/A')
    vix_impact = data.get('vix_impact', 'STABLE')

    dir_icon = "🟢" if nifty_dir == "BULLISH" else "🔴" if nifty_dir == "BEARISH" else "⚪"
    color    = 5763719 if nifty_dir == "BULLISH" else 15548997 if nifty_dir == "BEARISH" else 8421504

    n_spot = f"₹{nifty_spot:,.2f}"    if nifty_spot    > 0 else "—"
    b_spot = f"₹{banknifty_spot:,.2f}" if banknifty_spot > 0 else "—"
    v_lvl  = f"{vix_level:.2f}"        if vix_level     > 0 else "—"

    vix_mood = {
        "SPIKE":  "⚠️ Spiking — volatility expanding",
        "CRUSH":  "✅ Crushing — ideal for credit spreads",
        "STABLE": "➡️ Stable"
    }.get(vix_impact.upper(), vix_impact)

    region_tag = {
        "GLOBAL":      "🌐 Global",
        "HEAVYWEIGHT": "🏋️ Heavyweight",
        "INDIAN":      "🇮🇳 Indian"
    }.get(region, region)

    signal_line = f"{dir_icon} **{nifty_dir}** · {prob} probability · `{strategy}`"

    embed = {
        "title":       event_name,
        "description": f"{signal_line}\n>>> {headline}",
        "color":       color,
        "fields":      []
    }

    # BLOCK A: Market snapshot
    embed["fields"].extend([
        {"name": "Nifty",     "value": n_spot, "inline": True},
        {"name": "BankNifty", "value": b_spot, "inline": True},
        {"name": "VIX",       "value": v_lvl,  "inline": True},
    ])

    # BLOCK B: VIX signal + region + type
    embed["fields"].extend([
        {"name": "VIX Signal", "value": vix_mood,              "inline": True},
        {"name": "Region",     "value": region_tag,             "inline": True},
        {"name": "Type",       "value": f"`{event_type}`",      "inline": True},
    ])

    # BLOCK C: Macro data — only when meaningful
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

    # BLOCK D: Risk note
    if hedging and hedging != 'N/A':
        embed["fields"].append({
            "name": "🛡️ Risk", "value": hedging, "inline": False
        })

    # BLOCK E: Micro target — only when real ticker exists
    stock  = data.get('affected_stock', 'None')
    ticker = data.get('target_ticker', 'NONE')
    micro  = data.get('micro_strategy', 'N/A')
    if ticker not in ('NONE', 'N/A', '', None):
        s_spot = f"₹{target_spot:,.2f}" if target_spot > 0 else "—"
        embed["fields"].append({
            "name":  f"🎯 {stock} ({ticker})",
            "value": f"Spot: **{s_spot}** · {micro}",
            "inline": False
        })

    # BLOCK F: Context trimmed to 300 chars
    if reasoning and reasoning != 'N/A':
        short_reason = reasoning[:297] + "…" if len(reasoning) > 300 else reasoning
        embed["fields"].append({
            "name": "📌 Context", "value": short_reason, "inline": False
        })

    # Footer
    embed["footer"] = {
        "text": f"Bade Sahab • {region_tag} • {datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%d %b %Y, %I:%M %p IST')}"
    }

    # Chart — one call, URL-based, no file attachment

    # Webhook routing
    target_webhook = {
        "GLOBAL":      WEBHOOK_GLOBAL,
        "HEAVYWEIGHT": WEBHOOK_HEAVYWEIGHT,
    }.get(region, WEBHOOK_INDIAN) or WEBHOOK_INDIAN

    # Discord requires all field values to be non-empty
    for field in embed["fields"]:
        if not field.get("value") or str(field["value"]).strip() == "":
            field["value"] = "—"

    # Check total embed length
    total_len = len(embed.get("title","")) + len(embed.get("description",""))
    for field in embed["fields"]:
        total_len += len(field.get("name","")) + len(field.get("value",""))
    if total_len > 5900:
        # Trim context field if too long
        for field in embed["fields"]:
            if field["name"] == "📌 Context":
                field["value"] = field["value"][:200] + "…"
                break

    # Send
    try:
        r = requests.post(target_webhook, json={"embeds": [embed]}, timeout=10)
        print(f"Alert sent: {event_name} → {region} | HTTP {r.status_code}")
        if r.status_code not in (200, 204):
            print(f"Discord error body: {r.text}")  # ← this will show exact reason
    except Exception as exc:
        print(f"Failed to send alert: {exc}")


def main():
    conn   = init_database()
    cursor = conn.cursor()

    cleanup_database(conn, cursor)

    nifty_spot, banknifty_spot, vix_level = get_live_market_prices()
    print(f"Market prices — Nifty: {nifty_spot} | BankNifty: {banknifty_spot} | VIX: {vix_level}")

    try:
        headlines_to_analyze = []
        for headline in news_fetcher.fetch_top_headlines():
            if is_headline_duplicate(cursor, headline):
                print(f"Duplicate headline skipped: {headline[:60]}")
                continue
            if not is_worth_analyzing(headline):
                print(f"Noise filtered: {headline[:60]}")
                save_to_database(conn, cursor, headline, {"event_type": "IGNORE", "impact_score": 0},
                                 nifty_spot, banknifty_spot, vix_level, 0)
                continue
            headlines_to_analyze.append(headline)

        print(f"{len(headlines_to_analyze)} headlines passed to Gemini.")

        if headlines_to_analyze:
            try:
                batch_response = analyzer.analyze_headlines_batch(headlines_to_analyze)
                parsed_batch   = json.loads(batch_response).get("analyses", [])
                print(f"Gemini returned {len(parsed_batch)} analyses.")
            except Exception as e:
                print(f"Gemini batch call failed: {e}")
                parsed_batch = []

            for item in parsed_batch:
                headline = item.get("headline_analyzed")
                if not headline:
                    continue

                target_spot  = get_target_price(item.get("target_ticker", "NONE"))
                is_duplicate = is_event_duplicate(cursor, item.get("event", "Unknown"))

                # Save to DB — isolated try/except so one failure doesn't kill the batch
                try:
                    save_to_database(conn, cursor, headline, item,
                                     nifty_spot, banknifty_spot, vix_level, target_spot)
                except Exception as db_err:
                    print(f"DB save failed for '{headline[:60]}': {db_err}")
                    conn.rollback()
                    continue

                # Noise filter
                if item.get("event_type", "OTHER") == "IGNORE" or int(item.get("impact_score", 0)) < 40:
                    print(f"Low impact, skipping alert: {headline[:60]}")
                    continue

                # Duplicate event filter
                if is_duplicate:
                    print(f"Duplicate event, skipping alert: {headline[:60]}")
                    continue

                print(f"Sending alert for: {headline[:60]}...")
                send_discord_alert(headline, item, nifty_spot, banknifty_spot, vix_level, target_spot)
                time.sleep(2)

    except Exception as e:
        print(f"Main loop error: {e}")
    finally:
        cursor.close()
        conn.close()
        print("Main run complete.")

if __name__ == "__main__":
    main()