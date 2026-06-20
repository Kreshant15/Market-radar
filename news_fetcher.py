import feedparser
import json

def fetch_top_headlines():
    # Expanded list of RSS feeds for Global, Crypto, and Domestic live tracking
    rss_urls = [
        # 1. Domestic & Core Market News (India Region)
        "https://news.google.com/rss/search?q=Nifty+OR+BankNifty+OR+RBI+OR+Repo+Rate+OR+SEBI+when:1h&hl=en-IN&gl=IN&ceid=IN:en",
        
        # 2. Global Macro & US Fed (US Region for better Fed/NFP coverage)
        "https://news.google.com/rss/search?q=US+Fed+OR+FOMC+OR+Jerome+Powell+OR+Crude+Oil+OR+CPI+when:1h&hl=en-US&gl=US&ceid=US:en",
        
        # 3. Livemint Markets RSS
        "https://www.livemint.com/rss/markets",
        
        # 4. Yahoo Finance Global Top Stories
        "https://finance.yahoo.com/news/rssindex",
        
        # 5. CoinDesk RSS (Live Bitcoin / Crypto updates)
        "https://www.coindesk.com/arc/outboundfeeds/rss/"
    ]
    
    headlines = []
    
    # Loop through each URL and grab the top 3 headlines from each
    for url in rss_urls:
        feed = feedparser.parse(url)
        for entry in feed.entries[:3]:
            # Basic check to avoid exact identical titles in the same run
            if entry.title not in headlines: 
                headlines.append(entry.title)
                
    return headlines

if __name__ == "__main__":
    top_news = fetch_top_headlines()
    print(json.dumps(top_news, indent=1))