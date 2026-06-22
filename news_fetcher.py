import feedparser
import json

def fetch_top_headlines():
    # 🎯 TARGETED MACRO SEARCH STRINGS based on elite desk feedback
    rss_urls = [
        # 1. Indian Macro & Institutional Flow (RBI, CPI, GDP, FII/DII)
        "https://news.google.com/rss/search?q=Nifty+OR+BankNifty+OR+RBI+Policy+OR+India+CPI+Inflation+OR+India+GDP+OR+FII+DII+buying+selling+when:1h&hl=en-IN&gl=IN&ceid=IN:en",
        
        # 2. Global Macro & US Triggers (Fed, US CPI, NFP, Crude, War)
        "https://news.google.com/rss/search?q=US+Fed+Interest+Rate+OR+US+CPI+Inflation+OR+Non-Farm+Payrolls+NFP+OR+Crude+Oil+OR+Geopolitical+War+when:1h&hl=en-US&gl=US&ceid=US:en",
        
        # 3. Livemint Markets RSS (Broad Indian market pulse)
        "https://www.livemint.com/rss/markets",
        
        # 4. Yahoo Finance Global Top Stories
        "https://finance.yahoo.com/news/rssindex",
        
        # 5. CoinDesk RSS (Crypto macro pulse)
        "https://www.coindesk.com/arc/outboundfeeds/rss/"
    ]
    
    headlines = []
    
    for url in rss_urls:
        feed = feedparser.parse(url)
        # Grab top 3 from each strict macro feed
        for entry in feed.entries[:3]:
            if entry.title not in headlines: 
                headlines.append(entry.title)
                
    return headlines

if __name__ == "__main__":
    top_news = fetch_top_headlines()
    print(json.dumps(top_news, indent=1))