import yfinance as yf
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.gridspec as gridspec
import os
from datetime import datetime
from zoneinfo import ZoneInfo

TICKER_NAMES = {
    "^NSEI":    "Nifty 50",
    "^NSEBANK": "Bank Nifty",
    "^INDIAVIX":"India VIX",
    "BTC-USD":  "Bitcoin (USD)",
    "BZ=F":     "Brent Crude",
    "INR=X":    "USD/INR",
}

def create_entry_chart(ticker="^NSEI", direction="BULLISH", spot_price=0.0):
    try:
        data = yf.Ticker(ticker).history(period="5d", interval="15m")
        if data.empty or spot_price == 0.0:
            return None

        # ── Colors ────────────────────────────────────────────────────────────
        dir_upper = direction.upper()
        entry_color = (
            "#00E676" if dir_upper == "BULLISH" else
            "#FF1744" if dir_upper == "BEARISH" else
            "#FFEA00"
        )

        # ── Layout: price + volume ─────────────────────────────────────────────
        plt.style.use("dark_background")
        fig = plt.figure(figsize=(11, 6), facecolor="#0D1117")
        gs  = gridspec.GridSpec(2, 1, height_ratios=[3, 1], hspace=0.08)
        ax1 = fig.add_subplot(gs[0])
        ax2 = fig.add_subplot(gs[1], sharex=ax1)

        # ── Price line ─────────────────────────────────────────────────────────
        ax1.plot(data.index, data["Close"], color="#00E5FF", linewidth=1.5, zorder=3)
        ax1.fill_between(data.index, data["Close"], data["Close"].min(),
                         alpha=0.08, color="#00E5FF")

        # ── Entry dot + label ──────────────────────────────────────────────────
        last_time  = data.index[-1]
        price_range = data["Close"].max() - data["Close"].min()
        label_offset = price_range * 0.06

        ax1.scatter(last_time, spot_price,
                    color=entry_color, s=160, zorder=5,
                    edgecolors="white", linewidths=1.5)

        # Smart label placement — above if price in lower half, below if upper
        mid = (data["Close"].max() + data["Close"].min()) / 2
        y_offset = label_offset if spot_price < mid else -label_offset * 2.5

        ax1.annotate(
            f"ENTRY\n₹{spot_price:,.2f}",
            xy=(last_time, spot_price),
            xytext=(0, y_offset),
            textcoords="offset points",
            color="white",
            fontsize=8,
            fontweight="bold",
            ha="center",
            bbox=dict(boxstyle="round,pad=0.4", fc=entry_color, ec="white", alpha=0.4)
        )

        # ── Volume bars ────────────────────────────────────────────────────────
        if "Volume" in data.columns and data["Volume"].sum() > 0:
            vol_colors = [
                "#00E676" if c >= o else "#FF1744"
                for c, o in zip(data["Close"], data["Open"])
            ]
            ax2.bar(data.index, data["Volume"], color=vol_colors, alpha=0.6, width=0.006)
            ax2.set_ylabel("Volume", color="#888888", fontsize=7)
            ax2.tick_params(axis="y", labelcolor="#888888", labelsize=6)
        else:
            ax2.set_visible(False)

        # ── Formatting ─────────────────────────────────────────────────────────
        ticker_name = TICKER_NAMES.get(ticker, ticker.replace(".NS", "").replace("^", ""))
        now_ist     = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")

        ax1.set_title(
            f"{ticker_name}  ·  5D / 15m  ·  {now_ist}",
            color="white", pad=12, fontsize=12, fontweight="bold"
        )
        ax1.set_ylabel("Price (₹)", color="#888888", fontsize=8)
        ax1.tick_params(colors="#888888", labelsize=7)
        ax1.grid(True, linestyle="--", alpha=0.15)
        ax1.set_facecolor("#0D1117")

        ax2.set_facecolor("#0D1117")
        ax2.grid(True, linestyle="--", alpha=0.1)
        ax2.tick_params(colors="#888888", labelsize=6)

        plt.setp(ax1.get_xticklabels(), visible=False)
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d\n%H:%M"))
        plt.xticks(rotation=0, color="#888888", fontsize=6)

        # ── Unique filename to avoid race conditions ───────────────────────────
        safe_ticker = ticker.replace("^", "").replace(".", "_").replace("=", "")
        filepath    = f"chart_{safe_ticker}_{datetime.now().strftime('%H%M%S')}.png"

        plt.savefig(filepath, dpi=120, facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close()
        return filepath

    except Exception as e:
        print(f"Chart error ({ticker}): {e}")
        return None