import os
import psycopg2
import requests
import matplotlib.pyplot as plt
import json
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def init_database_if_needed(conn, cursor):
    """Gracefully ensures all tables and portfolio rows exist before running the report."""
    # 1. Create portfolio table if it doesn't exist
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS portfolio (
            id SERIAL PRIMARY KEY,
            current_balance NUMERIC NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    ''')
    conn.commit()
    
    # 2. Seed portfolio if it has no rows
    cursor.execute('SELECT COUNT(*) FROM portfolio')
    if cursor.fetchone()[0] == 0:
        print("Migrating: Initializing Virtual Hedge Fund with ₹10,00,000...")
        cursor.execute('INSERT INTO portfolio (id, current_balance, updated_at) VALUES (1, 1000000.0, NOW())')
        conn.commit()

    # 3. Ensure the events table has the necessary P&L column
    cursor.execute("""
        SELECT COUNT(*) 
        FROM information_schema.columns 
        WHERE table_name='events' AND column_name='pnl_inr'
    """)
    if cursor.fetchone()[0] == 0:
        print("Migrating: Adding missing column 'pnl_inr' to events table...")
        cursor.execute("ALTER TABLE events ADD COLUMN pnl_inr NUMERIC;")
        conn.commit()

def generate_weekly_report():
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    
    # Self-repair: Ensure everything is set up in the database first
    init_database_if_needed(conn, cursor)
    
    # Fetch portfolio balance
    cursor.execute('SELECT current_balance FROM portfolio WHERE id = 1')
    current_balance = float(cursor.fetchone()[0])
    
    # Fetch trades from the last 7 days
    cursor.execute('''
        SELECT event, pnl_inr, timestamp 
        FROM events 
        WHERE verdict_issued = TRUE 
        AND timestamp >= NOW() - INTERVAL '7 days'
        ORDER BY timestamp ASC
    ''')
    trades = cursor.fetchall()
    
    if not trades:
        print("No trades closed this week. Fund balance is stable.")
        # Send a clean weekend status message instead of crashing
        embed = {
            "title": "📊 Weekly Fund Tear Sheet",
            "color": 8421504,
            "description": "No virtual trades were settled this week. The fund remains in cash.",
            "fields": [
                {"name": "🏦 Total Virtual AUM", "value": f"**₹{current_balance:,.2f}**", "inline": False}
            ],
            "footer": {"text": "Bade Sahab Quantitative Fund"}
        }
        try:
            requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
        except Exception as e:
            print(f"Error sending empty status to Discord: {e}")
        
        cursor.close()
        conn.close()
        return
        
    total_trades = len(trades)
    winning_trades = sum(1 for t in trades if float(t[1]) > 0)
    win_rate = (winning_trades / total_trades) * 100
    weekly_pnl = sum(float(t[1]) for t in trades)
    
    # Generate Equity Curve Chart
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 5))
    
    balances = [current_balance - weekly_pnl] # Start of week balance
    dates = ["Start"]
    
    running_bal = balances[0]
    for t in trades:
        running_bal += float(t[1])
        balances.append(running_bal)
        dates.append(t[0][:10] + "..") # Shortened event name
        
    ax.plot(dates, balances, marker='o', color='#00E676' if weekly_pnl >= 0 else '#FF1744', linewidth=2)
    ax.set_title("Bade Sahab: Weekly Fund Performance", color='white', pad=15, fontsize=14, fontweight='bold')
    ax.grid(True, linestyle='--', alpha=0.2)
    plt.xticks(rotation=45, ha='right')
    plt.tight_layout()
    
    chart_path = "weekly_equity.png"
    plt.savefig(chart_path, dpi=120, facecolor=fig.get_facecolor(), bbox_inches='tight')
    plt.close()
    
    # Send to Discord
    color = 5763719 if weekly_pnl >= 0 else 15548997
    embed = {
        "title": "📊 Weekly Fund Tear Sheet",
        "color": color,
        "fields": [
            {"name": "📈 Win Rate", "value": f"{win_rate:.1f}% ({winning_trades}/{total_trades})", "inline": True},
            {"name": "💸 Weekly Net P&L", "value": f"₹{weekly_pnl:,.2f}", "inline": True},
            {"name": "🏦 Total Virtual AUM", "value": f"**₹{current_balance:,.2f}**", "inline": False}
        ],
        "image": {"url": "attachment://weekly_equity.png"},
        "footer": {"text": "Bade Sahab Quantitative Fund"}
    }
    
    try:
        with open(chart_path, "rb") as f:
            requests.post(
                DISCORD_WEBHOOK_URL,
                data={"payload_json": json.dumps({"embeds": [embed]})},
                files={"file": ("weekly_equity.png", f, "image/png")}
            )
        os.remove(chart_path)
        print("Tear sheet sent successfully.")
    except Exception as e:
        print(f"Error sending tear sheet: {e}")
        
    cursor.close()
    conn.close()

if __name__ == "__main__":
    generate_weekly_report()