import feedparser
import json

def fetch_top_headlines():
    # Google News RSS search query targeting business or geopolitics
    rss_url = "https://news.google.com/rss/search?q=Nifty+OR+RBI+OR+adani+OR+reliance+OR+India+macroeconomics+when:1h&hl=en-IN&gl=IN&ceid=IN:en"
    
    # Parse the RSS feed data
    feed = feedparser.parse(rss_url)
    
    # Extract the title from the first 5 entries into a Python list
    headlines = [entry.title for entry in feed.entries[:5]]
    
    return headlines

if __name__ == "__main__":
    top_5_news = fetch_top_headlines()
    
    # Outputting the list formatted with indentation
    print(json.dumps(top_5_news, indent=1))