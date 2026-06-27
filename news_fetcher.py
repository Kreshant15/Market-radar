import feedparser
import json

def fetch_top_headlines():
    rss_urls = [
        # 1. Indian Macro & Institutional Flow
        "https://news.google.com/rss/search?q=Nifty+OR+BankNifty+OR+RBI+Policy+OR+India+CPI+Inflation+OR+India+GDP+OR+FII+DII+buying+selling+when:2h&hl=en-IN&gl=IN&ceid=IN:en",

        # 2. Global Macro & US Triggers
        "https://news.google.com/rss/search?q=US+Fed+Interest+Rate+OR+US+CPI+Inflation+OR+Non-Farm+Payrolls+NFP+OR+Crude+Oil+when:2h&hl=en-US&gl=US&ceid=US:en",

        # 3. ⚡ GEOPOLITICAL WIRE — expanded keywords, 6h window so nothing slips
        "https://news.google.com/rss/search?q=war+OR+airstrike+OR+missile+OR+attack+OR+bombing+OR+sanctions+OR+invasion+OR+conflict+OR+military+strike+OR+nuclear+when:6h&hl=en-US&gl=US&ceid=US:en",

        # 4. Iran/Middle East/Russia/China specific — the high-impact zones
        "https://news.google.com/rss/search?q=(Iran+OR+Israel+OR+Russia+OR+China+OR+Pakistan+OR+Taiwan)+AND+(attack+OR+strike+OR+war+OR+sanctions+OR+troops+OR+military)+when:6h&hl=en-US&gl=US&ceid=US:en",

        # 5. Market Heavyweights
        "https://news.google.com/rss/search?q=Reliance+Industries+OR+HDFC+Bank+news+when:2h&hl=en-IN&gl=IN&ceid=IN:en",

        # 6. Livemint Markets
        "https://www.livemint.com/rss/markets",

        # 7. Yahoo Finance Global
        "https://finance.yahoo.com/news/rssindex",

        # 8. Reuters World News — catches geopolitical events fastest
        "https://feeds.reuters.com/reuters/worldNews",

        # 9. BBC News World — backup geopolitical wire
        "http://feeds.bbci.co.uk/news/world/rss.xml",

        # 10. CoinDesk
        "https://www.coindesk.com/arc/outboundfeeds/rss/"
    ]

    headlines = []
    for url in rss_urls:
        try:
            feed = feedparser.parse(url)
            # Geo feeds (Reuters/BBC) get top 5, others get top 3
            limit = 5 if any(x in url for x in ["reuters", "bbc", "Iran", "Russia"]) else 3
            for entry in feed.entries[:limit]:
                if entry.title not in headlines:
                    headlines.append(entry.title)
        except Exception as e:
            print(f"Feed error ({url[:50]}): {e}")

    return headlines

if __name__ == "__main__":
    top_news = fetch_top_headlines()
    print(json.dumps(top_news, indent=1))