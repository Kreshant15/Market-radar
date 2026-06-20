import yfinance as yf
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import os

def create_entry_chart(ticker="^NSEI", direction="BULLISH", spot_price=0.0):
    """Generates a dark-themed 5-day 15m chart and marks the entry point."""
    try:
        # 1. Fetch 15-minute interval data for the last 5 days
        stock = yf.Ticker(ticker)
        data = stock.history(period="5d", interval="15m")
        if data.empty or spot_price == 0.0:
            return None

        # 2. Setup Professional Dark Theme
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(10, 5))
        
        # Plot the closing prices line
        ax.plot(data.index, data['Close'], color='#00E5FF', linewidth=1.5) # Cyan trendline
        
        # 3. Mark the Entry Point dynamically based on sentiment
        color = '#00E676' if direction.upper() == 'BULLISH' else '#FF1744' # Green or Red
        if direction.upper() == 'NEUTRAL':
            color = '#FFEA00' # Yellow
            
        last_time = data.index[-1]
        
        # Draw the glowing entry dot
        ax.scatter(last_time, spot_price, color=color, s=150, zorder=5, edgecolors='white', linewidths=1.5)
        
        # Add a floating label pointing to the dot
        ax.annotate(
            f'🚨 BADE SAHAB ENTRY\n₹{spot_price:,.2f}', 
            xy=(last_time, spot_price),
            xytext=(-10, 20),
            textcoords='offset points',
            color='white',
            fontweight='bold',
            ha='right',
            bbox=dict(boxstyle="round,pad=0.4", fc=color, ec="white", alpha=0.3)
        )

        # 4. Chart Formatting
        ticker_name = "Nifty 50" if ticker == "^NSEI" else "Bank Nifty"
        ax.set_title(f"{ticker_name} - 5 Day Trend (15m Intervals)", color='white', pad=15, fontsize=14, fontweight='bold')
        ax.grid(True, linestyle='--', alpha=0.2)
        
        # Format X-axis dates cleanly
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d\n%H:%M'))
        plt.xticks(rotation=0)
        plt.tight_layout()

        # 5. Save to file
        filepath = "chart.png"
        plt.savefig(filepath, dpi=120, facecolor=fig.get_facecolor(), bbox_inches='tight')
        plt.close()
        
        return filepath
        
    except Exception as e:
        print(f"Chart Generation Error: {e}")
        return None