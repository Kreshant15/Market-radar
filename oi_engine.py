import os
import json
import time
import requests
import psycopg2
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
DB_URL          = os.getenv("DATABASE_URL")
WEBHOOK_PREMARKET = os.getenv("DISCORD_WEBHOOK_PREMARKET")

# ── NSE SESSION (bypasses bot detection) ──────────────────────────────────────
def get_nse_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.nseindia.com/",
        "Connection": "keep-alive",
    })
    # Warm up the session — NSE requires cookies from the main page first
    try:
        session.get("https://www.nseindia.com", timeout=10)
        time.sleep(2)
        session.get("https://www.nseindia.com/market-data/equity-derivatives-watch", timeout=10)
        time.sleep(1)
    except Exception as e:
        print(f"NSE session warmup warning: {e}")
    return session

def fetch_option_chain(session, symbol="NIFTY"):
    """Fetches raw options chain from NSE for NIFTY, BANKNIFTY, or SENSEX."""
    # BSE handles SENSEX options chain differently
    if symbol == "SENSEX":
        url = "https://api.bseindia.com/BseIndiaAPI/api/OptionChain/w?scripcode=1&expirydate="
        try:
            r = session.get(url, timeout=15)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            print(f"BSE fetch error for SENSEX: {e}")
        return None

    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    for attempt in range(3):
        try:
            r = session.get(url, timeout=15)
            if r.status_code == 200:
                return r.json()
            print(f"NSE returned {r.status_code} for {symbol}, retrying...")
            time.sleep(3)
        except Exception as e:
            print(f"Fetch error ({symbol}, attempt {attempt+1}): {e}")
            time.sleep(3)
    return None

def parse_option_chain(data, symbol="NIFTY"):
    """
    Parses raw NSE options chain into:
    - PCR (Put-Call Ratio)
    - Max Pain strike
    - Top CE/PE OI buildup strikes
    - Spot price
    - Nearest expiry date
    """
    if not data:
        return None

    try:
        records   = data.get("records", {})
        filtered  = data.get("filtered", {})
        spot      = float(records.get("underlyingValue", 0))
        expiries  = records.get("expiryDates", [])
        nearest_expiry = expiries[0] if expiries else "N/A"

        # Filter to nearest expiry only
        chain = [
            row for row in records.get("data", [])
            if row.get("expiryDate") == nearest_expiry
        ]

        total_ce_oi, total_pe_oi = 0, 0
        strike_pain   = {}   # max pain calc
        ce_oi_map     = {}   # strike → CE OI
        pe_oi_map     = {}   # strike → PE OI
        ce_buildup    = {}   # strike → CE OI change
        pe_buildup    = {}   # strike → PE OI change

        for row in chain:
            strike = row.get("strikePrice", 0)
            ce = row.get("CE", {})
            pe = row.get("PE", {})

            ce_oi   = ce.get("openInterest", 0) or 0
            pe_oi   = pe.get("openInterest", 0) or 0
            ce_chg  = ce.get("changeinOpenInterest", 0) or 0
            pe_chg  = pe.get("changeinOpenInterest", 0) or 0

            total_ce_oi += ce_oi
            total_pe_oi += pe_oi
            ce_oi_map[strike]  = ce_oi
            pe_oi_map[strike]  = pe_oi
            ce_buildup[strike] = ce_chg
            pe_buildup[strike] = pe_chg

            # Max pain: sum of all ITM losses at each strike
            strike_pain[strike] = strike_pain.get(strike, 0)

        # Max pain calculation
        strikes = sorted(strike_pain.keys())
        pain_values = {}
        for s in strikes:
            total_loss = 0
            for k in strikes:
                ce_loss = max(0, k - s) * ce_oi_map.get(k, 0)
                pe_loss = max(0, s - k) * pe_oi_map.get(k, 0)
                total_loss += ce_loss + pe_loss
            pain_values[s] = total_loss
        max_pain = min(pain_values, key=pain_values.get) if pain_values else 0

        # PCR
        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0

        # Top 3 CE walls (resistance) and PE walls (support)
        top_ce = sorted(ce_oi_map.items(), key=lambda x: x[1], reverse=True)[:3]
        top_pe = sorted(pe_oi_map.items(), key=lambda x: x[1], reverse=True)[:3]

        # Top buildup (aggressive positioning)
        top_ce_build = sorted(ce_buildup.items(), key=lambda x: x[1], reverse=True)[:2]
        top_pe_build = sorted(pe_buildup.items(), key=lambda x: x[1], reverse=True)[:2]

        # OI interpretation
        if pcr > 1.3:
            oi_bias = "BULLISH"
            oi_read = "Heavy PE writing — market makers expect upside"
        elif pcr < 0.7:
            oi_bias = "BEARISH"
            oi_read = "Heavy CE writing — market makers expect downside"
        else:
            oi_bias = "NEUTRAL"
            oi_read = "Balanced OI — range-bound session likely"

        return {
            "symbol":         symbol,
            "spot":           spot,
            "expiry":         nearest_expiry,
            "pcr":            pcr,
            "max_pain":       max_pain,
            "total_ce_oi":    total_ce_oi,
            "total_pe_oi":    total_pe_oi,
            "top_ce_walls":   top_ce,
            "top_pe_walls":   top_pe,
            "ce_buildup":     top_ce_build,
            "pe_buildup":     top_pe_build,
            "oi_bias":        oi_bias,
            "oi_read":        oi_read,
        }

    except Exception as e:
        print(f"Parse error for {symbol}: {e}")
        return None

def save_oi_snapshot(parsed):
    """Save OI snapshot to DB for F&O rec engine and morning briefing to read."""
    if not parsed:
        return
    try:
        conn   = psycopg2.connect(DB_URL)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS oi_snapshots (
                id SERIAL PRIMARY KEY,
                symbol TEXT,
                spot NUMERIC,
                expiry TEXT,
                pcr NUMERIC,
                max_pain NUMERIC,
                total_ce_oi BIGINT,
                total_pe_oi BIGINT,
                top_ce_walls JSONB,
                top_pe_walls JSONB,
                ce_buildup JSONB,
                pe_buildup JSONB,
                oi_bias TEXT,
                oi_read TEXT,
                timestamp TIMESTAMP DEFAULT NOW()
            )
        """)
        cursor.execute("""
            INSERT INTO oi_snapshots
            (symbol, spot, expiry, pcr, max_pain, total_ce_oi, total_pe_oi,
             top_ce_walls, top_pe_walls, ce_buildup, pe_buildup, oi_bias, oi_read)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            parsed["symbol"], parsed["spot"], parsed["expiry"],
            parsed["pcr"], parsed["max_pain"],
            parsed["total_ce_oi"], parsed["total_pe_oi"],
            json.dumps(parsed["top_ce_walls"]),
            json.dumps(parsed["top_pe_walls"]),
            json.dumps(parsed["ce_buildup"]),
            json.dumps(parsed["pe_buildup"]),
            parsed["oi_bias"], parsed["oi_read"]
        ))
        # Keep only last 20 snapshots per symbol
        cursor.execute("""
            DELETE FROM oi_snapshots WHERE id IN (
                SELECT id FROM oi_snapshots WHERE symbol = %s
                ORDER BY timestamp DESC OFFSET 20
            )
        """, (parsed["symbol"],))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"OI snapshot save error: {e}")

def build_oi_embed(parsed):
    """Builds the Discord embed for one index."""
    if not parsed:
        return None

    symbol   = parsed["symbol"]
    spot     = parsed["spot"]
    pcr      = parsed["pcr"]
    max_pain = parsed["max_pain"]
    bias     = parsed["oi_bias"]
    oi_read  = parsed["oi_read"]

    icon  = "🟢" if bias == "BULLISH" else "🔴" if bias == "BEARISH" else "⚪"
    color = 5763719 if bias == "BULLISH" else 15548997 if bias == "BEARISH" else 8421504

    # CE walls (resistance levels)
    ce_wall_str = "\n".join(
        [f"**{int(s):,}** — {int(oi):,} contracts" for s, oi in parsed["top_ce_walls"]]
    ) or "N/A"

    # PE walls (support levels)
    pe_wall_str = "\n".join(
        [f"**{int(s):,}** — {int(oi):,} contracts" for s, oi in parsed["top_pe_walls"]]
    ) or "N/A"

    # Buildup
    ce_build_str = "\n".join(
        [f"**{int(s):,}** CE +{int(c):,}" for s, c in parsed["ce_buildup"] if c > 0]
    ) or "No significant buildup"

    pe_build_str = "\n".join(
        [f"**{int(s):,}** PE +{int(c):,}" for s, c in parsed["pe_buildup"] if c > 0]
    ) or "No significant buildup"

    pain_vs_spot = max_pain - spot
    pain_str = (
        f"**{int(max_pain):,}** "
        f"({'▲' if pain_vs_spot > 0 else '▼'} {abs(pain_vs_spot):.0f} pts from spot)"
    )

    now_ist = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")

    return {
        "title": f"{icon} {symbol} OI Snapshot · Expiry: {parsed['expiry']}",
        "description": (
            f"**Spot:** ₹{spot:,.2f}  ·  **PCR:** `{pcr}`  ·  **OI Bias:** {bias}\n"
            f"> {oi_read}"
        ),
        "color": color,
        "fields": [
            {"name": "🔴 CE Walls (Resistance)", "value": ce_wall_str,    "inline": True},
            {"name": "🟢 PE Walls (Support)",    "value": pe_wall_str,    "inline": True},
            {"name": "\u200b",                    "value": "\u200b",       "inline": False},
            {"name": "📈 CE Buildup",             "value": ce_build_str,  "inline": True},
            {"name": "📉 PE Buildup",             "value": pe_build_str,  "inline": True},
            {"name": "🎯 Max Pain",               "value": pain_str,      "inline": False},
        ],
        "footer": {"text": f"Bade Sahab · OI Engine · {now_ist}"}
    }

def send_oi_report(embeds):
    """Send all index OI embeds in one Discord message (max 10 embeds per message)."""
    if not embeds:
        print("No OI data to send.")
        return
    try:
        r = requests.post(
            WEBHOOK_PREMARKET,
            json={"embeds": embeds},
            timeout=10
        )
        if r.status_code in [200, 204]:
            print(f"OI report sent — {len(embeds)} indices.")
        else:
            print(f"Discord error: {r.status_code} — {r.text}")
    except Exception as e:
        print(f"OI send failed: {e}")

def main():
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)

    # Only run on weekdays
    if now.weekday() >= 5:
        print("Weekend — OI engine skipped.")
        return

    print(f"OI Engine starting — {now.strftime('%H:%M IST')}")
    session = get_nse_session()

    symbols = ["NIFTY", "BANKNIFTY", "SENSEX"]
    embeds  = []

    for symbol in symbols:
        print(f"Fetching {symbol} options chain...")
        raw    = fetch_option_chain(session, symbol)
        parsed = parse_option_chain(raw, symbol)

        if parsed:
            save_oi_snapshot(parsed)
            embed = build_oi_embed(parsed)
            if embed:
                embeds.append(embed)
            print(f"{symbol}: PCR={parsed['pcr']} | Max Pain={parsed['max_pain']} | Bias={parsed['oi_bias']}")
        else:
            print(f"{symbol}: Failed to parse data.")

        time.sleep(2)  # Be polite to NSE between requests

    send_oi_report(embeds)
    print("OI Engine complete.")

if __name__ == "__main__":
    main()