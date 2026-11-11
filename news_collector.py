import json
import hashlib
import re
import html
import os
import tempfile
import time
import urllib.request
import urllib.error

from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from xml.etree import ElementTree as ET


# ============================================================
# ROAMX WORLD WATCH - AUTOMATIC NEWS COLLECTOR
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "news.json")

USER_AGENT = "ROAMX-WorldWatch/3.0 (RSS News Collector)"
REQUEST_TIMEOUT = 25

DEFAULT_CATEGORIES = [
    "World",
    "India",
    "Technology",
    "Environment",
    "Disaster"
]


# ============================================================
# GENERAL HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def iso_time(value=None):
    value = value or utc_now()
    return value.astimezone(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def clean_text(value):
    if not value:
        return ""

    value = html.unescape(str(value))
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def normalize_title(title):
    title = clean_text(title).lower()
    title = re.sub(r"[^a-z0-9]+", " ", title)
    return " ".join(title.split())


def normalize_url(url):
    if not url:
        return ""

    url = html.unescape(str(url)).strip()

    try:
        parts = urlsplit(url)

        if parts.scheme not in ("http", "https") or not parts.netloc:
            return ""

        ignored_parameters = {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "utm_id",
            "gclid",
            "fbclid"
        }

        query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in ignored_parameters
        ]

        return urlunsplit((
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            urlencode(query),
            ""
        ))

    except Exception:
        return ""


def make_article_id(url, title):
    identity = normalize_url(url) or normalize_title(title)

    return hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()[:20]


def parse_date(value):
    if not value:
        return None

    value = str(value).strip()

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    except Exception:
        pass

    try:
        parsed = parsedate_to_datetime(value)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    except Exception:
        return None


def xml_child_text(element, names):
    for child in element.iter():
        tag = child.tag.split("}")[-1].lower()

        if tag in names and child.text:
            return child.text.strip()

    return ""


def get_feed_link(element):
    for child in element:
        tag = child.tag.split("}")[-1].lower()

        if tag == "link":
            href = child.attrib.get("href")

            if href:
                return href.strip()

            if child.text:
                return child.text.strip()

    return ""


# ============================================================
# CONFIGURATION
# ============================================================

def load_config():
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(
            "news.json was not found in the repository root."
        )

    with open(CONFIG_FILE, "r", encoding="utf-8") as file:
        config = json.load(file)

    if not isinstance(config, dict):
        raise ValueError("news.json must contain a JSON object.")

    return config


def get_sources(config):
    sources = config.get("sources", [])

    if isinstance(sources, dict):
        result = []

        for source_id, source_data in sources.items():
            if isinstance(source_data, dict):
                source = dict(source_data)
                source.setdefault("id", source_id)
                result.append(source)

        return result

    if isinstance(sources, list):
        return sources

    return []


def get_source_url(source):
    return (
        source.get("url")
        or source.get("feedUrl")
        or source.get("rssUrl")
        or source.get("feed")
        or ""
    )


def get_source_id(source):
    return (
        source.get("id")
        or source.get("name")
        or "unknown_source"
    )


def get_source_category(source):
    category = str(source.get("category", "")).strip()

    if category:
        return category

    source_id = get_source_id(source).lower()

    if "india" in source_id:
        return "India"

    if "disaster" in source_id or "weather" in source_id:
        return "Disaster"

    if "environment" in source_id or "climate" in source_id:
        return "Environment"

    if "technology" in source_id or "tech" in source_id:
        return "Technology"

    if "world" in source_id or "global" in source_id:
        return "World"

    return "World"


# ============================================================
# RSS COLLECTION
# ============================================================

def fetch_feed(source):
    feed_url = get_source_url(source)

    if not feed_url:
        raise ValueError("RSS source has no feed URL.")

    request = urllib.request.Request(
        feed_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=REQUEST_TIMEOUT
    ) as response:

        content = response.read()

    root = ET.fromstring(content)

    items = []

    for element in root.iter():
        tag = element.tag.split("}")[-1].lower()

        if tag in ("item", "entry"):
            items.append(element)

    return items


def extract_article(element, source):
    title = clean_text(
        xml_child_text(element, {"title"})
    )

    link = normalize_url(get_feed_link(element))

    if not link:
        link = normalize_url(
            xml_child_text(element, {"link"})
        )

    description = clean_text(
        xml_child_text(
            element,
            {"description", "summary", "content", "encoded"}
        )
    )

    published_value = xml_child_text(
        element,
        {
            "pubdate",
            "published",
            "updated",
            "date",
            "issued"
        }
    )

    published_date = parse_date(published_value)

    publisher = clean_text(
        xml_child_text(element, {"source"})
    )

    if not publisher:
        publisher = clean_text(
            source.get("publisher")
            or source.get("name")
            or get_source_id(source)
        )

    if not title or not link or not published_date:
        return None

    if not link.startswith(("http://", "https://")):
        return None

    return {
        "id": make_article_id(link, title),
        "title": title,
        "description": description[:1200],
        "url": link,
        "link": link,
        "source": publisher,
        "category": get_source_category(source),
        "publishedAt": iso_time(published_date)
    }


# ============================================================
# DUPLICATE DETECTION
# ============================================================

def article_identity(article):
    url = normalize_url(
        article.get("url") or article.get("link", "")
    )

    title = normalize_title(
        article.get("title", "")
    )

    return url, title


def deduplicate_articles(articles):
    unique = {}

    for article in articles:
        if not isinstance(article, dict):
            continue

        title = clean_text(article.get("title", ""))
        url = normalize_url(
            article.get("url") or article.get("link", "")
        )

        if not title or not url:
            continue

        published = parse_date(
            article.get("publishedAt")
            or article.get("published_at")
            or article.get("pubDate")
            or article.get("date")
        )

        if not published:
            continue

        article["title"] = title
        article["url"] = url
        article["link"] = url
        article["publishedAt"] = iso_time(published)

        article.setdefault(
            "id",
            make_article_id(url, title)
        )

        url_key, title_key = article_identity(article)

        if not url_key or not title_key:
            continue

        key = url_key

        if key not in unique:
            unique[key] = article

        else:
            existing_date = parse_date(
                unique[key].get("publishedAt")
            )

            if published > existing_date:
                unique[key] = article

    # Also remove same-title articles from different URLs.
    title_unique = {}

    for article in unique.values():
        title_key = normalize_title(article.get("title", ""))

        if title_key not in title_unique:
            title_unique[title_key] = article
            continue

        old_date = parse_date(
            title_unique[title_key].get("publishedAt")
        )

        new_date = parse_date(
            article.get("publishedAt")
        )

        if new_date > old_date:
            title_unique[title_key] = article

    return list(title_unique.values())


# ============================================================
# MAIN COLLECTOR
# ============================================================

def main():
    print("========================================")
    print("       ROAMX WORLD WATCH")
    print("       AUTOMATIC NEWS COLLECTOR")
    print("========================================")

    config = load_config()

    collection = config.get("collection", {})

    if collection.get("enabled") is False:
        print("News collection is disabled in news.json.")
        return

    max_articles = int(
        collection.get("maxArticles", 300)
    )

    max_age_days = int(
        collection.get("maxArticleAgeDays", 7)
    )

    max_per_source = int(
        collection.get("maxArticlesPerSource", 50)
    )

    now = utc_now()
    minimum_date = now - timedelta(days=max_age_days)

    existing_articles = config.get("articles", [])

    if not isinstance(existing_articles, list):
        existing_articles = []

    existing_articles = [
        article for article in existing_articles
        if isinstance(article, dict)
    ]

    existing_ids = {
        str(article.get("id"))
        for article in existing_articles
        if article.get("id")
    }

    previous_count = len(existing_articles)

    print("Existing articles:", previous_count)

    collected_articles = []
    failed_sources = []
    successful_sources = 0

    sources = get_sources(config)

    print("RSS sources configured:", len(sources))

    for source in sources:
        if not isinstance(source, dict):
            continue

        source_id = get_source_id(source)

        print("\nChecking:", source_id)

        try:
            feed_items = fetch_feed(source)

            source_articles = []

            for item in feed_items:
                article = extract_article(item, source)

                if not article:
                    continue

                published = parse_date(
                    article["publishedAt"]
                )

                if not published or published < minimum_date:
                    continue

                source_articles.append(article)

            source_articles.sort(
                key=lambda article: parse_date(
                    article["publishedAt"]
                ),
                reverse=True
            )

            source_articles = source_articles[:max_per_source]

            collected_articles.extend(source_articles)

            successful_sources += 1

            print(
                "Collected:",
                len(source_articles),
                "articles"
            )

        except Exception as error:
            print("Source failed:", source_id, "-", str(error))

            failed_sources.append({
                "source": source_id,
                "error": str(error)[:250]
            })

    # Preserve existing articles within the configured age limit.
    preserved_articles = []

    for article in existing_articles:
        published = parse_date(
            article.get("publishedAt")
            or article.get("published_at")
            or article.get("pubDate")
            or article.get("date")
        )

        if published and published >= minimum_date:
            preserved_articles.append(article)

    # Merge existing and newly collected articles.
    combined_articles = (
        preserved_articles + collected_articles
    )

    combined_articles = deduplicate_articles(
        combined_articles
    )

    combined_articles.sort(
        key=lambda article: parse_date(
            article["publishedAt"]
        ),
        reverse=True
    )

    final_articles = combined_articles[:max_articles]

    final_ids = {
        str(article.get("id"))
        for article in final_articles
    }

    new_article_ids = [
        article["id"]
        for article in final_articles
        if article.get("id")
        and article["id"] not in existing_ids
    ]

    old_by_id = {
        str(article.get("id")): article
        for article in existing_articles
        if article.get("id")
    }

    updated_article_ids = []

    for article in final_articles:
        article_id = str(article.get("id"))

        if article_id in old_by_id:
            if (
                article.get("title") != old_by_id[article_id].get("title")
                or article.get("description") != old_by_id[article_id].get("description")
            ):
                updated_article_ids.append(article_id)

    articles_added = len(new_article_ids)

    articles_removed = max(
        0,
        previous_count - len(
            [
                article for article in final_articles
                if str(article.get("id")) in existing_ids
            ]
        )
    )

    # Update category statistics.
    category_counts = {
        category: 0
        for category in DEFAULT_CATEGORIES
    }

    for article in final_articles:
        category = article.get("category", "World")

        if category not in category_counts:
            category_counts[category] = 0

        category_counts[category] += 1

    # Update configuration and collection history.
    config["articles"] = final_articles

    config["lastUpdated"] = iso_time(now)
    config["updatedAt"] = iso_time(now)

    config["statistics"] = {
        "totalArticles": len(final_articles),
        "categories": category_counts,
        "successfulSources": successful_sources,
        "failedSources": len(failed_sources)
    }

    old_change_log = config.get("changeLog", {})

    if not isinstance(old_change_log, dict):
        old_change_log = {}

    old_change_log.update({
        "lastRun": iso_time(now),
        "lastSuccessfulRun": (
            iso_time(now)
            if successful_sources > 0
            else old_change_log.get("lastSuccessfulRun")
        ),
        "lastUpdated": iso_time(now),
        "articlesAdded": articles_added,
        "articlesUpdated": len(updated_article_ids),
        "duplicatesRemoved": max(
            0,
            len(combined_articles) - len(
                deduplicate_articles(combined_articles)
            )
        ),
        "articlesRemoved": articles_removed,
        "failedSources": failed_sources,
        "successfulSources": successful_sources,
        "newArticleIds": new_article_ids,
        "updatedArticleIds": updated_article_ids
    })

    config["changeLog"] = old_change_log

    # Save JSON safely to avoid corrupting the database.
    fd, temporary_file = tempfile.mkstemp(
        prefix="roamx_news_",
        suffix=".json",
        dir=BASE_DIR
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                config,
                file,
                indent=2,
                ensure_ascii=False
            )

            file.write("\n")

        os.replace(
            temporary_file,
            CONFIG_FILE
        )

    except Exception:
        if os.path.exists(temporary_file):
            os.remove(temporary_file)

        raise

    print("\n========================================")
    print("       COLLECTION COMPLETED")
    print("========================================")
    print("Successful sources:", successful_sources)
    print("Failed sources:", len(failed_sources))
    print("Total articles:", len(final_articles))
    print("New articles:", articles_added)
    print("Updated articles:", len(updated_article_ids))
    print("========================================")


if __name__ == "__main__":
    main()