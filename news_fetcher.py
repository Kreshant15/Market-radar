import feedparser
import json

def fetch_top_headlines():
    # List of different RSS feeds to scan
    rss_urls = [
        # 1. Your expanded Google News Aggregator (changed from 'when:48h' to 'when:12h')
        "https://news.google.com/rss/search?q=Nifty+OR+BankNifty+OR+RBI+OR+SEBI+OR+Adani+OR+Reliance+OR+HDFC+when:12h&hl=en-IN&gl=IN&ceid=IN:en",
        
        # 2. Livemint Markets RSS
        "https://www.livemint.com/rss/markets",
        
        # 3. Economic Times Markets RSS
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"
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