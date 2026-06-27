import os
import json
import psycopg2
import requests
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
DB_URL                    = os.getenv("DATABASE_URL")
DISCORD_WEBHOOK_TEARSHEET = os.getenv("DISCORD_WEBHOOK_TEARSHEET")

INITIAL_CAPITAL = 1_000_000.0  # ₹10,00,000

def init_database(conn, cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id              SERIAL PRIMARY KEY,
            current_balance NUMERIC NOT NULL,
            updated_at      TIMESTAMP NOT NULL
        )
    """)
    conn.commit()
    cursor.execute("SELECT COUNT(*) FROM portfolio")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO portfolio (id, current_balance, updated_at) VALUES (1, %s, NOW())",
            (INITIAL_CAPITAL,)
        )
        conn.commit()
        print("Portfolio initialized at ₹10,00,000")

    # Ensure pnl_inr column exists
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_name='events' AND column_name='pnl_inr'
    """)
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE events ADD COLUMN pnl_inr NUMERIC")
        conn.commit()

def fetch_weekly_trades(cursor):
    cursor.execute("""
        SELECT event, pnl_inr, timestamp
        FROM events
        WHERE verdict_issued = TRUE
        AND timestamp >= NOW() - INTERVAL '7 days'
        ORDER BY timestamp ASC
    """)
    return cursor.fetchall()

def fetch_alltime_stats(cursor):
    cursor.execute("""
        SELECT
            COUNT(*)                                    AS total,
            COUNT(*) FILTER (WHERE pnl_inr > 0)        AS wins,
            COUNT(*) FILTER (WHERE pnl_inr <= 0)       AS losses,
            COALESCE(SUM(pnl_inr), 0)                  AS total_pnl,
            COALESCE(MAX(pnl_inr), 0)                  AS best_trade,
            COALESCE(MIN(pnl_inr), 0)                  AS worst_trade
        FROM events
        WHERE verdict_issued = TRUE AND pnl_inr IS NOT NULL
    """)
    row = cursor.fetchone()
    return {
        "total":       int(row[0] or 0),
        "wins":        int(row[1] or 0),
        "losses":      int(row[2] or 0),
        "total_pnl":   float(row[3] or 0),
        "best_trade":  float(row[4] or 0),
        "worst_trade": float(row[5] or 0),
    }

def fetch_alltime_equity_curve(cursor):
    """Pull all settled trades in chronological order for the full equity curve."""
    cursor.execute("""
        SELECT pnl_inr, timestamp
        FROM events
        WHERE verdict_issued = TRUE AND pnl_inr IS NOT NULL
        ORDER BY timestamp ASC
    """)
    return cursor.fetchall()

def generate_chart(trades_alltime, current_balance, weekly_pnl):
    plt.style.use("dark_background")
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11, 7),
        facecolor="#0D1117",
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.35}
    )

    # ── Equity curve (all time) ────────────────────────────────────────────────
    if trades_alltime:
        running = INITIAL_CAPITAL
        dates, balances = [], []
        for pnl, ts in trades_alltime:
            running += float(pnl)
            dates.append(ts)
            balances.append(running)

        curve_color = "#00E676" if current_balance >= INITIAL_CAPITAL else "#FF1744"
        ax1.plot(dates, balances, color=curve_color, linewidth=2, zorder=3)
        ax1.fill_between(dates, balances, INITIAL_CAPITAL,
                         where=[b >= INITIAL_CAPITAL for b in balances],
                         alpha=0.15, color="#00E676")
        ax1.fill_between(dates, balances, INITIAL_CAPITAL,
                         where=[b < INITIAL_CAPITAL for b in balances],
                         alpha=0.15, color="#FF1744")
        ax1.axhline(INITIAL_CAPITAL, color="#888888", linewidth=0.8,
                    linestyle="--", label="Starting Capital")

        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
        ax1.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
        plt.setp(ax1.get_xticklabels(), rotation=30, ha="right",
                 color="#888888", fontsize=7)
    else:
        ax1.text(0.5, 0.5, "No trade history yet",
                 ha="center", va="center", color="#888888",
                 transform=ax1.transAxes)

    ax1.set_title("Bade Sahab · All-Time Fund Performance",
                  color="white", fontsize=13, fontweight="bold", pad=12)
    ax1.set_ylabel("Virtual AUM (₹)", color="#888888", fontsize=8)
    ax1.tick_params(colors="#888888", labelsize=7)
    ax1.grid(True, linestyle="--", alpha=0.15)
    ax1.set_facecolor("#0D1117")
    ax1.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda x, _: f"₹{x/1e5:.1f}L")
    )

    # ── Weekly P&L bar chart ───────────────────────────────────────────────────
    if trades_alltime:
        # Last 7 days only for the bar chart
        week_ago  = datetime.now(tz=trades_alltime[0][1].tzinfo) - timedelta(days=7)
        week_data = [(float(p), t) for p, t in trades_alltime if t >= week_ago]

        if week_data:
            bar_colors = ["#00E676" if p > 0 else "#FF1744" for p, _ in week_data]
            ax2.bar(
                [t for _, t in week_data],
                [p for p, _ in week_data],
                color=bar_colors, width=timedelta(hours=8), alpha=0.8
            )
            ax2.axhline(0, color="#888888", linewidth=0.6)
            ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
            plt.setp(ax2.get_xticklabels(), rotation=30, ha="right",
                     color="#888888", fontsize=6)
        else:
            ax2.text(0.5, 0.5, "No trades this week",
                     ha="center", va="center", color="#888888",
                     transform=ax2.transAxes, fontsize=8)

    ax2.set_title("This Week's Trade P&L", color="#AAAAAA", fontsize=9, pad=6)
    ax2.set_ylabel("P&L (₹)", color="#888888", fontsize=7)
    ax2.tick_params(colors="#888888", labelsize=6)
    ax2.grid(True, linestyle="--", alpha=0.1)
    ax2.set_facecolor("#0D1117")

    chart_path = "weekly_tearsheet.png"
    plt.savefig(chart_path, dpi=130, facecolor=fig.get_facecolor(),
                bbox_inches="tight")
    plt.close()
    return chart_path

def send_tearsheet(weekly_trades, alltime, current_balance, chart_path):
    now_ist    = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y")
    weekly_pnl = sum(float(t[1]) for t in weekly_trades)
    w_wins     = sum(1 for t in weekly_trades if float(t[1]) > 0)
    w_total    = len(weekly_trades)
    w_winrate  = round((w_wins / w_total) * 100, 1) if w_total > 0 else 0.0

    alltime_wr = round((alltime["wins"] / alltime["total"]) * 100, 1) \
                 if alltime["total"] > 0 else 0.0
    pnl_from_start = current_balance - INITIAL_CAPITAL
    roi            = round((pnl_from_start / INITIAL_CAPITAL) * 100, 2)

    color = 5763719 if weekly_pnl >= 0 else 15548997
    pnl_icon = "📈" if weekly_pnl >= 0 else "📉"

    embed = {
        "title":       f"📊 Weekly Tear Sheet · {now_ist}",
        "description": "Bade Sahab · Virtual Hedge Fund · Weekly Performance Report",
        "color":       color,
        "fields": [
            # Weekly block
            {
                "name":  "📅 This Week",
                "value": (
                    f"{pnl_icon} P&L: **₹{weekly_pnl:+,.0f}**\n"
                    f"Win Rate: **{w_winrate}%** ({w_wins}W / {w_total - w_wins}L of {w_total} calls)"
                ),
                "inline": False
            },
            # All-time block
            {
                "name":  "🏆 All-Time Record",
                "value": (
                    f"Win Rate: **{alltime_wr}%** "
                    f"({alltime['wins']}W / {alltime['losses']}L of {alltime['total']} calls)\n"
                    f"Total P&L: **₹{alltime['total_pnl']:+,.0f}**"
                ),
                "inline": False
            },
            # Best/Worst
            {"name": "🏅 Best Trade",  "value": f"₹{alltime['best_trade']:+,.0f}",  "inline": True},
            {"name": "💀 Worst Trade", "value": f"₹{alltime['worst_trade']:+,.0f}", "inline": True},
            # Fund AUM
            {
                "name":  "🏦 Virtual AUM",
                "value": f"**₹{current_balance:,.0f}** ({roi:+.2f}% ROI from ₹10L)",
                "inline": False
            },
        ],
        "image":  {"url": "attachment://weekly_tearsheet.png"},
        "footer": {"text": f"Bade Sahab · Quantitative Fund · {now_ist}"}
    }

    try:
        with open(chart_path, "rb") as f:
            r = requests.post(
                DISCORD_WEBHOOK_TEARSHEET,
                data={"payload_json": json.dumps({"embeds": [embed]})},
                files={"file": ("weekly_tearsheet.png", f, "image/png")},
                timeout=15
            )
        os.remove(chart_path)
        if r.status_code in [200, 204]:
            print("Tear sheet sent successfully.")
        else:
            print(f"Discord error: {r.status_code}")
    except Exception as e:
        print(f"Tear sheet send failed: {e}")

def send_empty_report(current_balance):
    """Clean message when no trades settled this week."""
    roi   = round(((current_balance - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100, 2)
    embed = {
        "title":       "📊 Weekly Tear Sheet · No Trades This Week",
        "color":       8421504,
        "description": "No virtual trades were settled this week. Fund remains in cash.",
        "fields": [
            {"name": "🏦 Virtual AUM", "value": f"**₹{current_balance:,.0f}** ({roi:+.2f}% ROI)", "inline": False}
        ],
        "footer": {"text": "Bade Sahab · Quantitative Fund"}
    }
    try:
        requests.post(DISCORD_WEBHOOK_TEARSHEET, json={"embeds": [embed]}, timeout=10)
    except Exception as e:
        print(f"Empty report send failed: {e}")

def main():
    print("Generating Friday Tear Sheet...")
    conn   = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    init_database(conn, cursor)

    cursor.execute("SELECT current_balance FROM portfolio WHERE id = 1")
    current_balance = float(cursor.fetchone()[0])

    weekly_trades   = fetch_weekly_trades(cursor)
    alltime         = fetch_alltime_stats(cursor)
    alltime_curve   = fetch_alltime_equity_curve(cursor)

    if not weekly_trades and alltime["total"] == 0:
        print("No trade history at all — sending empty report.")
        send_empty_report(current_balance)
        cursor.close(); conn.close()
        return

    weekly_pnl  = sum(float(t[1]) for t in weekly_trades)
    chart_path  = generate_chart(alltime_curve, current_balance, weekly_pnl)
    send_tearsheet(weekly_trades, alltime, current_balance, chart_path)

    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()