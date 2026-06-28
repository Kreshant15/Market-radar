import urllib.parse
import json
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo

TICKER_NAMES = {
    "^NSEI":    "Nifty 50",
    "^NSEBANK": "Bank Nifty",
    "^INDIAVIX":"India VIX",
    "BTC-USD":  "Bitcoin",
    "BZ=F":     "Brent Crude",
}

def create_entry_chart(ticker="^NSEI", direction="BULLISH", spot_price=0.0):
    """
    Returns a QuickChart URL instead of a local file.
    Discord embeds this as an image — no attachment, notification shows correctly.
    """
    try:
        data = yf.Ticker(ticker).history(period="5d", interval="15m")
        if data.empty or spot_price == 0.0:
            return None

        # Sample down to ~40 points so URL stays short
        step    = max(1, len(data) // 40)
        sampled = data.iloc[::step].tail(40)

        labels = [
            ts.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %H:%M")
            for ts in sampled.index
        ]
        prices = [round(float(p), 2) for p in sampled["Close"]]

        line_color = (
            "#00E676" if direction.upper() == "BULLISH" else
            "#FF1744" if direction.upper() == "BEARISH" else
            "#FFEA00"
        )

        ticker_name = TICKER_NAMES.get(ticker, ticker.replace(".NS","").replace("^",""))
        now_ist     = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b, %I:%M %p IST")

        chart_config = {
            "type": "line",
            "data": {
                "labels": labels,
                "datasets": [{
                    "label": f"{ticker_name} · {now_ist}",
                    "data": prices,
                    "borderColor": line_color,
                    "backgroundColor": line_color + "18",
                    "borderWidth": 2,
                    "pointRadius": 0,
                    "fill": True,
                    "tension": 0.3,
                }]
            },
            "options": {
                "plugins": {
                    "legend": {"labels": {"color": "#CCCCCC", "font": {"size": 11}}},
                    "annotation": {
                        "annotations": [{
                            "type":        "line",
                            "yMin":        spot_price,
                            "yMax":        spot_price,
                            "borderColor": "#FFFFFF",
                            "borderWidth": 1,
                            "borderDash":  [5, 5],
                            "label": {
                                "content": f"Entry ₹{spot_price:,.2f}",
                                "enabled": True,
                                "color":   "#FFFFFF",
                                "font":    {"size": 10}
                            }
                        }]
                    }
                },
                "scales": {
                    "x": {
                        "ticks": {
                            "color":    "#888888",
                            "maxTicksLimit": 6,
                            "font":     {"size": 9}
                        },
                        "grid": {"color": "#333333"}
                    },
                    "y": {
                        "ticks": {"color": "#888888", "font": {"size": 9}},
                        "grid":  {"color": "#333333"}
                    }
                },
                "backgroundColor": "#0D1117"
            }
        }

        config_str   = json.dumps(chart_config, separators=(",", ":"))
        encoded      = urllib.parse.quote(config_str)
        chart_url    = f"https://quickchart.io/chart?c={encoded}&w=800&h=400&bkg=%230D1117"

        # QuickChart has a URL length limit (~16KB). If too long, return None gracefully.
        if len(chart_url) > 15000:
            print(f"Chart URL too long for {ticker}, skipping chart.")
            return None

        return chart_url

    except Exception as e:
        print(f"Chart error ({ticker}): {e}")
        return None