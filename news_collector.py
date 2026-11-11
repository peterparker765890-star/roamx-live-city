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
from urllib.parse import (
    urlsplit, urlunsplit, parse_qsl, urlencode
)
from xml.etree import ElementTree as ET


# ============================================================
# ROAMX WORLD WATCH 4.0
# REAL RSS NEWS COLLECTION SYSTEM
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "news.json")

USER_AGENT = "ROAMX-WorldWatch/4.0"
REQUEST_TIMEOUT = 25
MAX_RETRIES = 2

DEFAULT_CATEGORIES = [
    "World",
    "India",
    "Technology",
    "Environment",
    "Disaster"
]

TRACKING_PARAMETERS = {
    "utm_source", "utm_medium", "utm_campaign",
    "utm_term", "utm_content", "utm_id",
    "gclid", "fbclid", "mc_cid", "mc_eid"
}

CATEGORY_KEYWORDS = {
    "World": [
        "international", "global", "united nations",
        "world summit", "diplomacy", "foreign policy",
        "international relations", "global development",
        "world leaders", "conflict", "peace agreement",
        "international security", "humanitarian crisis",
        "world economy", "election", "government",
        "sustainable development", "smart cities",
        "urban development", "public infrastructure",
        "public transportation", "housing",
        "community development"
    ],
    "India": [
        "india", "indian", "new delhi", "tamil nadu",
        "mumbai", "chennai", "bengaluru", "kolkata",
        "hyderabad", "infrastructure", "indian government",
        "indian cities", "public transport",
        "urban development", "public safety"
    ],
    "Technology": [
        "technology", "artificial intelligence", "AI",
        "smart city", "innovation", "scientific research",
        "technology research", "intelligent transportation",
        "environmental monitoring", "disaster technology",
        "robotics", "digital infrastructure",
        "cybersecurity", "engineering"
    ],
    "Environment": [
        "environment", "climate change", "air pollution",
        "water pollution", "waste management",
        "deforestation", "extreme weather",
        "water scarcity", "environmental protection",
        "sustainability", "heatwave", "global warming",
        "carbon emissions", "renewable energy",
        "biodiversity", "conservation"
    ],
    "Disaster": [
        "flood", "cyclone", "earthquake", "landslide",
        "wildfire", "tsunami", "natural disaster",
        "emergency warning", "disaster response",
        "disaster preparedness", "storm warning",
        "heavy rainfall", "extreme weather",
        "heatwave", "evacuation", "rescue operation"
    ]
}


# ============================================================
# DATE AND TEXT HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def iso_time(value=None):
    value = value or utc_now()

    return value.astimezone(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def clean_text(value):
    if value is None:
        return ""

    value = html.unescape(str(value))
    value = re.sub(r"<[^>]*>", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def normalize_title(value):
    value = clean_text(value).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)

    return " ".join(value.split())


def normalize_url(url):
    if not url:
        return ""

    try:
        parts = urlsplit(html.unescape(str(url)).strip())

        if parts.scheme.lower() not in ("http", "https"):
            return ""

        if not parts.hostname:
            return ""

        if parts.username or parts.password:
            return ""

        hostname = parts.hostname.lower()

        if ":" in hostname and not hostname.startswith("["):
            hostname = "[" + hostname + "]"

        port = parts.port

        if port and not (
            (parts.scheme.lower() == "http" and port == 80)
            or
            (parts.scheme.lower() == "https" and port == 443)
        ):
            hostname += ":" + str(port)

        query = [
            (key, value)
            for key, value in parse_qsl(
                parts.query,
                keep_blank_values=True
            )
            if key.lower() not in TRACKING_PARAMETERS
            and not key.lower().startswith("utm_")
        ]

        return urlunsplit((
            parts.scheme.lower(),
            hostname,
            parts.path.rstrip("/") or "/",
            urlencode(query),
            ""
        ))

    except (ValueError, TypeError):
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

    except (ValueError, TypeError):
        pass

    try:
        parsed = parsedate_to_datetime(value)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    except (ValueError, TypeError, OverflowError):
        return None


# ============================================================
# XML HELPERS - RSS AND ATOM
# ============================================================

def local_name(tag):
    return str(tag).split("}")[-1].lower()


def child_text(element, names):
    for child in element:
        if local_name(child.tag) in names:
            if child.text:
                return child.text.strip()

            # Handles nested content elements.
            value = "".join(child.itertext()).strip()

            if value:
                return value

    return ""


def get_feed_link(element):
    for child in element:
        if local_name(child.tag) != "link":
            continue

        href = child.attrib.get("href")

        if href:
            rel = child.attrib.get("rel", "alternate")

            if rel in ("alternate", ""):
                return href.strip()

        if child.text:
            return child.text.strip()

    return ""


def get_feed_items(root):
    items = []

    for element in root.iter():
        if local_name(element.tag) in ("item", "entry"):
            items.append(element)

    return items


# ============================================================
# CONFIGURATION
# ============================================================

def load_config():
    if not os.path.isfile(CONFIG_FILE):
        raise FileNotFoundError(
            "news.json was not found in the repository root."
        )

    with open(CONFIG_FILE, "r", encoding="utf-8") as file:
        config = json.load(file)

    if not isinstance(config, dict):
        raise ValueError("Invalid news.json configuration.")

    return config


def get_sources(config):
    sources = config.get("sources", [])

    if isinstance(sources, dict):
        result = []

        for source_id, data in sources.items():
            if isinstance(data, dict):
                source = dict(data)
                source.setdefault("id", source_id)
                result.append(source)

        return result

    if isinstance(sources, list):
        return [
            source for source in sources
            if isinstance(source, dict)
        ]

    return []


def get_source_id(source):
    return str(
        source.get("id")
        or source.get("name")
        or "unknown_source"
    )


def get_source_url(source):
    # IMPORTANT: Your news.json uses the "rss" property.
    return (
        source.get("rss")
        or source.get("url")
        or source.get("feedUrl")
        or source.get("rssUrl")
        or source.get("feed")
        or ""
    )


def get_source_category(source):
    category = str(
        source.get("category", "")
    ).strip()

    if category in DEFAULT_CATEGORIES:
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

    return "World"


def get_category_configuration(config):
    result = {}

    for category in config.get("categories", []):
        if isinstance(category, dict):
            result[str(category.get("name", ""))] = category

    return result


# ============================================================
# SDG 11 RELEVANCE FILTER
# ============================================================

def is_relevant(article, config):
    relevance = config.get("relevance", {})

    if relevance.get("enabled") is False:
        return True

    category = article.get("category", "World")

    content = normalize_title(
        article.get("title", "") + " " +
        article.get("description", "")
    )

    keywords = list(CATEGORY_KEYWORDS.get(category, []))

    category_config = get_category_configuration(config).get(
        category, {}
    )

    keywords.extend(category_config.get("topics", []))
    keywords.extend(relevance.get("priorityTopics", []))

    for keyword in keywords:
        normalized = normalize_title(keyword)

        if normalized and normalized in content:
            return True

    # Preserve important international reporting.
    if (
        category == "World"
        and relevance.get("includeImportantInternationalEvents", True)
    ):
        return any(
            word in content.split()
            for word in (
                "war", "peace", "election", "summit",
                "government", "president", "minister",
                "crisis", "international", "global",
                "united", "nations", "conflict"
            )
        )

    return False


# ============================================================
# RSS DOWNLOAD
# ============================================================

def fetch_feed(source):
    feed_url = get_source_url(source)

    if not feed_url:
        raise ValueError(
            "RSS URL missing. Expected source['rss']."
        )

    feed_url = normalize_url(feed_url)

    if not feed_url:
        raise ValueError("Invalid RSS feed URL.")

    request = urllib.request.Request(
        feed_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "application/rss+xml, application/atom+xml, "
                "application/xml, text/xml, */*"
            )
        }
    )

    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(
                request,
                timeout=REQUEST_TIMEOUT
            ) as response:
                content = response.read()

            if not content:
                raise ValueError("RSS feed returned empty content.")

            root = ET.fromstring(content)

            items = get_feed_items(root)

            if not items:
                raise ValueError(
                    "Feed downloaded, but no RSS/Atom articles were found."
                )

            return items

        except Exception as error:
            last_error = error

            if attempt < MAX_RETRIES:
                time.sleep(2 * (attempt + 1))

    raise RuntimeError(str(last_error))


# ============================================================
# ARTICLE EXTRACTION
# ============================================================

def extract_article(element, source):
    title = clean_text(
        child_text(element, {"title"})
    )

    link = normalize_url(get_feed_link(element))

    if not link:
        link = normalize_url(
            child_text(element, {"link"})
        )

    description = clean_text(
        child_text(
            element,
            {
                "description",
                "summary",
                "content",
                "encoded"
            }
        )
    )

    published_value = child_text(
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

    if not title or not link or not published_date:
        return None

    publisher = clean_text(
        child_text(element, {"source"})
        or source.get("publisher")
        or source.get("name")
        or get_source_id(source)
    )

    category = get_source_category(source)

    article = {
        "id": make_article_id(link, title),
        "title": title,
        "description": description[:1200],
        "url": link,
        "link": link,
        "source": publisher,
        "category": category,
        "publishedAt": iso_time(published_date)
    }

    return article


# ============================================================
# ARTICLE VALIDATION AND NORMALIZATION
# ============================================================

def normalize_article(article):
    if not isinstance(article, dict):
        return None

    title = clean_text(article.get("title", ""))

    url = normalize_url(
        article.get("url")
        or article.get("link")
        or ""
    )

    published = parse_date(
        article.get("publishedAt")
        or article.get("published_at")
        or article.get("pubDate")
        or article.get("date")
    )

    if not title or not url or not published:
        return None

    category = article.get("category", "World")

    if category not in DEFAULT_CATEGORIES:
        category = "World"

    return {
        **article,
        "id": make_article_id(url, title),
        "title": title,
        "description": clean_text(
            article.get("description", "")
        )[:1200],
        "url": url,
        "link": url,
        "source": clean_text(
            article.get("source", "Unknown source")
        ),
        "category": category,
        "publishedAt": iso_time(published)
    }


# ============================================================
# DUPLICATE DETECTION
# ============================================================

def deduplicate_articles(articles):
    unique_urls = {}
    unique_titles = {}

    valid_count = 0

    for original in articles:
        article = normalize_article(original)

        if not article:
            continue

        valid_count += 1

        url_key = normalize_url(article["url"])
        title_key = normalize_title(article["title"])

        existing = (
            unique_urls.get(url_key)
            or unique_titles.get(title_key)
        )

        if existing:
            old_date = parse_date(existing["publishedAt"])
            new_date = parse_date(article["publishedAt"])

            if new_date > old_date:
                # Keep the newest duplicate.
                old_url = normalize_url(existing["url"])
                old_title = normalize_title(existing["title"])

                unique_urls.pop(old_url, None)
                unique_titles.pop(old_title, None)

                unique_urls[url_key] = article
                unique_titles[title_key] = article

        else:
            unique_urls[url_key] = article
            unique_titles[title_key] = article

    return list(unique_urls.values()), (
        valid_count - len(unique_urls)
    )


# ============================================================
# SAFE JSON SAVE
# ============================================================

def save_config(config):
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

        os.replace(temporary_file, CONFIG_FILE)

    except Exception:
        if os.path.exists(temporary_file):
            os.remove(temporary_file)

        raise


# ============================================================
# MAIN COLLECTION PROCESS
# ============================================================

def main():
    print("=" * 55)
    print("          ROAMX WORLD WATCH 4.0")
    print("          AUTOMATIC NEWS COLLECTOR")
    print("=" * 55)

    config = load_config()

    collection = config.get("collection", {})

    if collection.get("enabled") is False:
        print("Collection is disabled.")
        return

    now = utc_now()

    max_articles = max(
        1, int(collection.get("maxArticles", 300))
    )

    max_age_days = max(
        1, int(collection.get("maximumArticleAgeDays",
                              collection.get("maxArticleAgeDays", 7)))
    )

    max_per_source = max(
        1, int(collection.get("maxArticlesPerSource", 50))
    )

    minimum_date = now - timedelta(days=max_age_days)
    maximum_date = now + timedelta(days=1)

    existing_articles = config.get("articles", [])

    if not isinstance(existing_articles, list):
        existing_articles = []

    existing_articles = [
        normalized
        for item in existing_articles
        if (normalized := normalize_article(item))
    ]

    existing_ids = {
        article["id"] for article in existing_articles
    }

    print("Existing articles:", len(existing_articles))

    sources = get_sources(config)

    print("Configured RSS sources:", len(sources))

    collected_articles = []
    failed_sources = []
    successful_sources = 0

    # --------------------------------------------------------
    # COLLECT FROM EVERY ENABLED SOURCE
    # --------------------------------------------------------

    for source in sources:
        if source.get("enabled", True) is False:
            print("Skipping disabled source:", get_source_id(source))
            continue

        source_id = get_source_id(source)

        print("\nChecking source:", source_id)

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

                if not published:
                    continue

                if published < minimum_date:
                    continue

                if published > maximum_date:
                    continue

                if not is_relevant(article, config):
                    continue

                source_articles.append(article)

            source_articles.sort(
                key=lambda item: parse_date(
                    item["publishedAt"]
                ),
                reverse=True
            )

            source_articles = source_articles[:max_per_source]

            collected_articles.extend(source_articles)

            successful_sources += 1

            print(
                "Source successful:",
                source_id,
                "| Articles:",
                len(source_articles)
            )

        except Exception as error:
            message = str(error)[:250]

            print("Source failed:", source_id, "|", message)

            failed_sources.append({
                "source": source_id,
                "error": message
            })

    # --------------------------------------------------------
    # PRESERVE PREVIOUS ARTICLES
    # --------------------------------------------------------

    if collection.get("preserveExistingArticles", True):
        preserved_articles = [
            article
            for article in existing_articles
            if (
                parse_date(article["publishedAt"])
                and parse_date(article["publishedAt"]) >= minimum_date
                and parse_date(article["publishedAt"]) <= maximum_date
            )
        ]
    else:
        preserved_articles = []

    # --------------------------------------------------------
    # MERGE AND DEDUPLICATE
    # --------------------------------------------------------

    combined_articles = (
        preserved_articles + collected_articles
    )

    merged_articles, duplicates_removed = deduplicate_articles(
        combined_articles
    )

    merged_articles.sort(
        key=lambda item: parse_date(
            item["publishedAt"]
        ),
        reverse=True
    )

    final_articles = merged_articles[:max_articles]

    final_ids = {
        article["id"] for article in final_articles
    }

    new_article_ids = [
        article["id"]
        for article in final_articles
        if article["id"] not in existing_ids
    ]

    old_by_id = {
        article["id"]: article
        for article in existing_articles
    }

    updated_article_ids = []

    for article in final_articles:
        old = old_by_id.get(article["id"])

        if old:
            if (
                article.get("title") != old.get("title")
                or article.get("description") != old.get("description")
                or article.get("category") != old.get("category")
            ):
                updated_article_ids.append(article["id"])

    removed_ids = existing_ids - final_ids

    # --------------------------------------------------------
    # CATEGORY STATISTICS
    # --------------------------------------------------------

    category_counts = {
        category: 0
        for category in DEFAULT_CATEGORIES
    }

    for article in final_articles:
        category = article.get("category", "World")

        if category not in category_counts:
            category_counts[category] = 0

        category_counts[category] += 1

    # --------------------------------------------------------
    # UPDATE DATABASE
    # --------------------------------------------------------

    config["articles"] = final_articles

    config["lastUpdated"] = iso_time(now)
    config["updatedAt"] = iso_time(now)

    config["statistics"] = {
        "totalArticles": len(final_articles),
        "categories": category_counts,
        "successfulSources": successful_sources,
        "failedSources": len(failed_sources)
    }

    change_log = config.get("changeLog", {})

    if not isinstance(change_log, dict):
        change_log = {}

    previous_success = change_log.get("lastSuccessfulRun")

    if successful_sources > 0:
        previous_success = iso_time(now)

    change_log.update({
        "lastRun": iso_time(now),
        "lastSuccessfulRun": previous_success,
        "lastUpdated": iso_time(now),
        "articlesAdded": len(new_article_ids),
        "articlesUpdated": len(updated_article_ids),
        "duplicatesRemoved": duplicates_removed,
        "articlesRemoved": len(removed_ids),
        "successfulSources": successful_sources,
        "failedSources": failed_sources,
        "newArticleIds": new_article_ids,
        "updatedArticleIds": updated_article_ids
    })

    config["changeLog"] = change_log

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_config(config)

    # --------------------------------------------------------
    # FINAL REPORT
    # --------------------------------------------------------

    print("\n" + "=" * 55)
    print("          COLLECTION REPORT")
    print("=" * 55)

    print("Successful sources :", successful_sources)
    print("Failed sources     :", len(failed_sources))
    print("Total articles     :", len(final_articles))
    print("New articles       :", len(new_article_ids))
    print("Updated articles   :", len(updated_article_ids))
    print("Duplicates removed :", duplicates_removed)
    print("Articles removed   :", len(removed_ids))

    print("\nCategory statistics:")

    for category, count in category_counts.items():
        print(category, ":", count)

    print("\nLast run:", iso_time(now))

    if successful_sources == 0:
        print("\nWARNING: No RSS source was successfully collected.")

    print("\nROAMX database saved successfully.")
    print("=" * 55)


# ============================================================
# EXECUTION
# ============================================================

if __name__ == "__main__":
    main()