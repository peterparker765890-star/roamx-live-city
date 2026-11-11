import json
import re
import html
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


# ==========================================
# ROAMX AUTOMATIC NEWS COLLECTOR
# ==========================================

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_FILE = BASE_DIR / "news.json"

MAX_ARTICLES = 80
TIMEOUT = 20

HEADERS = {
    "User-Agent": "ROAMX-NewsCollector/1.0 (GitHub Actions)"
}


# English-language RSS sources.
# Each feed has a default ROAMX category.

FEEDS = [
    {
        "name": "BBC World",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "category": "world"
    },
    {
        "name": "BBC Asia",
        "url": "https://feeds.bbci.co.uk/news/world/asia/rss.xml",
        "category": "world"
    },
    {
        "name": "BBC Science and Environment",
        "url": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        "category": "environment"
    },
    {
        "name": "BBC Technology",
        "url": "https://feeds.bbci.co.uk/news/technology/rss.xml",
        "category": "technology"
    },
    {
        "name": "The Guardian World",
        "url": "https://www.theguardian.com/world/rss",
        "category": "world"
    },
    {
        "name": "The Guardian Environment",
        "url": "https://www.theguardian.com/environment/rss",
        "category": "environment"
    },
    {
        "name": "The Guardian Technology",
        "url": "https://www.theguardian.com/technology/rss",
        "category": "technology"
    },
    {
        "name": "Al Jazeera",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "category": "world"
    },
    {
        "name": "Google News India",
        "url": "https://news.google.com/rss/search?q=India+when%3A2d&hl=en-IN&gl=IN&ceid=IN%3Aen",
        "category": "india"
    },
    {
        "name": "Google News Disasters",
        "url": "https://news.google.com/rss/search?q=natural+disaster+OR+earthquake+OR+flood+OR+cyclone+when%3A2d&hl=en-US&gl=US&ceid=US%3Aen",
        "category": "disasters"
    },
    {
        "name": "Google News Environment",
        "url": "https://news.google.com/rss/search?q=environment+OR+climate+change+when%3A2d&hl=en-US&gl=US&ceid=US%3Aen",
        "category": "environment"
    },
    {
        "name": "Google News Technology",
        "url": "https://news.google.com/rss/search?q=technology+OR+cybersecurity+when%3A2d&hl=en-US&gl=US&ceid=US%3Aen",
        "category": "technology"
    }
]


def clean_text(value):
    """Remove HTML tags and clean RSS text."""

    if not value:
        return ""

    value = html.unescape(str(value))
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def get_element_text(element, names):
    """Find an XML element using its local tag name."""

    for child in element.iter():
        tag = child.tag.split("}")[-1].lower()

        if tag in names:
            if child.text:
                return clean_text(child.text)

    return ""


def get_article_link(item):
    """Support both RSS and Atom article links."""

    for child in item:
        tag = child.tag.split("}")[-1].lower()

        if tag == "link":
            href = child.attrib.get("href")

            if href:
                return href.strip()

            if child.text:
                return child.text.strip()

    return ""


def parse_date(value):
    """Convert RSS publication dates to UTC ISO format."""

    if not value:
        return None

    try:
        date = parsedate_to_datetime(value)

        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)

        return date.astimezone(timezone.utc).isoformat()

    except (TypeError, ValueError, OverflowError):
        try:
            date = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )

            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)

            return date.astimezone(timezone.utc).isoformat()

        except (TypeError, ValueError):
            return None


def infer_category(title, description, default):
    """Assign a category using headline keywords."""

    text = (title + " " + description).lower()

    if any(word in text for word in [
        "earthquake", "flood", "cyclone", "hurricane",
        "wildfire", "tsunami", "landslide", "disaster",
        "volcano", "emergency evacuation"
    ]):
        return "disasters"

    if any(word in text for word in [
        "climate change", "global warming", "pollution",
        "environment", "wildlife", "biodiversity",
        "deforestation", "carbon emissions"
    ]):
        return "environment"

    if any(word in text for word in [
        "artificial intelligence", "cybersecurity",
        "software", "technology", "smartphone",
        "computer", "robotics", "semiconductor"
    ]):
        return "technology"

    return default


def fetch_feed(feed):
    """Download and parse one RSS or Atom feed."""

    articles = []

    request = urllib.request.Request(
        feed["url"],
        headers=HEADERS
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=TIMEOUT
        ) as response:
            content = response.read()

        root = ET.fromstring(content)

        items = []

        for element in root.iter():
            tag = element.tag.split("}")[-1].lower()

            if tag in ("item", "entry"):
                items.append(element)

        for item in items:
            title = get_element_text(item, ["title"])
            description = get_element_text(
                item,
                ["description", "summary", "content"]
            )

            link = get_article_link(item)

            published = get_element_text(
                item,
                ["pubdate", "published", "updated", "date"]
            )

            published_at = parse_date(published)

            if not title or not link:
                continue

            if not link.startswith(("https://", "http://")):
                continue

            # Google News sometimes provides the publisher name
            # inside a separate source element.
            source = get_element_text(item, ["source"])
            if not source:
                source = feed["name"]

            category = infer_category(
                title,
                description,
                feed["category"]
            )

            articles.append({
                "title": title,
                "description": description or
                    "Read the full report for more information.",
                "url": link,
                "source": source,
                "published_at": published_at,
                "category": category
            })

        print(
            f"[SUCCESS] {feed['name']}: "
            f"{len(articles)} articles"
        )

    except Exception as error:
        print(
            f"[WARNING] {feed['name']} failed: {error}"
        )

    return articles


def article_timestamp(article):
    """Get a sortable timestamp."""

    value = article.get("published_at")

    if not value:
        return 0

    try:
        date = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        return date.timestamp()

    except (ValueError, TypeError):
        return 0


def collect_news():
    """Collect articles from every configured feed."""

    all_articles = []

    print("====================================")
    print("       ROAMX NEWS COLLECTOR")
    print("====================================")

    for feed in FEEDS:
        articles = fetch_feed(feed)
        all_articles.extend(articles)

    # Remove duplicate headlines.
    unique_articles = []
    seen_titles = set()
    seen_urls = set()

    for article in all_articles:
        title_key = re.sub(
            r"[^a-z0-9]",
            "",
            article["title"].lower()
        )

        url_key = article["url"].rstrip("/").lower()

        if title_key in seen_titles or url_key in seen_urls:
            continue

        seen_titles.add(title_key)
        seen_urls.add(url_key)

        unique_articles.append(article)

    # Put the newest published stories first.
    unique_articles.sort(
        key=article_timestamp,
        reverse=True
    )

    return unique_articles[:MAX_ARTICLES]


def save_news(articles):
    """Write the collected news to news.json."""

    data = {
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "articles": articles
    }

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temporary_file = OUTPUT_FILE.with_suffix(".tmp")

    with temporary_file.open(
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )

    temporary_file.replace(OUTPUT_FILE)

    print("------------------------------------")
    print(f"Total articles saved: {len(articles)}")
    print(f"Output: {OUTPUT_FILE}")
    print("ROAMX news collection completed.")
    print("------------------------------------")


def main():
    articles = collect_news()

    if not articles:
        raise RuntimeError(
            "No news feeds returned valid articles. "
            "Keeping the existing news.json unchanged."
        )

    save_news(articles)


if __name__ == "__main__":
    main()