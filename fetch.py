"""
Fetches articles from configured RSS feeds, filters by recency and topic keywords.
Many Canadian news sites block the default Python user agent, so we ask
feedparser to identify itself as a normal browser.
"""
from __future__ import annotations
import logging
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from typing import List
import feedparser
import yaml
from nltk.stem import SnowballStemmer

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

log = logging.getLogger(__name__)

# Pretend to be a real browser so publishers don't block us.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
feedparser.USER_AGENT = USER_AGENT

# Stemmers for English and French — handle singular/plural automatically.
_stemmers = {
    "en": SnowballStemmer("english"),
    "fr": SnowballStemmer("french"),
}


@dataclass
class Article:
    outlet: str
    region: str
    language: str
    title: str
    url: str
    published: str           # ISO-8601 UTC
    summary: str
    matched_topics: List[str] = field(default_factory=list)
    full_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _parse_published(entry) -> datetime | None:
    """Return UTC datetime from an RSS entry, or None if unparseable."""
    for field_name in ("published_parsed", "updated_parsed"):
        t = getattr(entry, field_name, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _stem_phrase(phrase: str, language: str) -> str:
    """Stem every token in a word or phrase so singular/plural both match.
    Tokens containing numbers or hyphens (e.g. C-48) are kept as-is.
    """
    stemmer = _stemmers.get(language, _stemmers["en"])
    tokens = re.findall(r"[a-zA-ZÀ-ÿ0-9][a-zA-ZÀ-ÿ0-9-]*", phrase.lower())
    stemmed = []
    for t in tokens:
        if re.match(r"^[a-zA-ZÀ-ÿ]+$", t):
            stemmed.append(stemmer.stem(t))
        else:
            stemmed.append(t)  # keep codes like c-48, c-69 intact
    return " ".join(stemmed)


def _stem_text(text: str, language: str) -> str:
    """Stem all tokens in an article text block for matching."""
    stemmer = _stemmers.get(language, _stemmers["en"])
    tokens = re.findall(r"[a-zA-ZÀ-ÿ0-9][a-zA-ZÀ-ÿ0-9-]*", text.lower())
    stemmed = []
    for t in tokens:
        if re.match(r"^[a-zA-ZÀ-ÿ]+$", t):
            stemmed.append(stemmer.stem(t))
        else:
            stemmed.append(t)
    return " ".join(stemmed)


def _is_excluded(text: str, exclude_keywords: list) -> bool:
    """Return True if the article matches any exclusion keyword and should be dropped."""
    text_lower = text.lower()
    for kw in exclude_keywords:
        kw_lower = kw.lower()
        pattern = r"(?<!\S)" + re.escape(kw_lower) + r"(?!\S)"
        if re.search(pattern, text_lower):
            return True
    return False


def _match_topics(text: str, topics_cfg: dict, language: str) -> List[str]:
    """Return list of topic names whose keywords appear in text.
    Uses stemming so singular and plural forms both match automatically.
    """
    stemmed_text = _stem_text(text, language)
    primary = f"keywords_{language}"
    matched: List[str] = []

    for topic_name, kw_cfg in topics_cfg.items():
        keywords = kw_cfg.get(primary, []) or kw_cfg.get("keywords_en", [])
        for kw in keywords:
            stemmed_kw = _stem_phrase(kw, language)
            if not stemmed_kw:
                continue
            pattern = r"(?<!\S)" + re.escape(stemmed_kw) + r"(?!\S)"
            if re.search(pattern, stemmed_text):
                matched.append(topic_name)
                break

    return matched


def _fetch_full_text(url: str, timeout: int = 12) -> str:
    """Best-effort article-body extraction. Returns '' on failure (paywalls etc.)."""
    if not HAS_TRAFILATURA or not url:
        return ""
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ""
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            favor_recall=False,
        )
        return text or ""
    except Exception as e:
        log.debug("Full-text fetch failed for %s: %s", url, e)
        return ""


def fetch_all(config: dict) -> List[Article]:
    """Fetch every RSS feed in config, return Articles matching at least one topic."""
    lookback = timedelta(hours=config["filter"]["lookback_hours"])
    cutoff = datetime.now(timezone.utc) - lookback
    topics_cfg = config["topics"]
    fetch_full = config["filter"].get("fetch_full_text", True)
    exclude_kws = config.get("exclude_keywords", [])

    all_outlets = []
    for group in config["outlets"].values():
        all_outlets.extend(group)

    articles: List[Article] = []
    for outlet in all_outlets:
        log.info("Fetching %s", outlet["name"])
        try:
            feed = feedparser.parse(outlet["rss"], agent=USER_AGENT)
        except Exception as e:
            log.warning("  failed to parse %s: %s", outlet["name"], e)
            continue

        entry_count = len(feed.entries)
        if entry_count == 0:
            log.warning("  no entries from %s (feed may have moved or be blocked)",
                        outlet["name"])
            continue

        log.info("  %d total entries in feed", entry_count)
        outlet_hits = 0

        for entry in feed.entries:
            pub = _parse_published(entry)
            if not pub or pub < cutoff:
                continue

            title = _strip_html(getattr(entry, "title", ""))
            summary = _strip_html(getattr(entry, "summary", ""))
            combined = f"{title}\n{summary}"

            if exclude_kws and _is_excluded(combined, exclude_kws):
                continue

            matched = _match_topics(combined, topics_cfg, outlet["language"])
            if not matched:
                continue

            articles.append(Article(
                outlet=outlet["name"],
                region=outlet["region"],
                language=outlet["language"],
                title=title,
                url=getattr(entry, "link", ""),
                published=pub.isoformat(),
                summary=summary[:1500],
                matched_topics=matched,
            ))
            outlet_hits += 1

        log.info("  %d relevant from %s", outlet_hits, outlet["name"])

    log.info("Total relevant articles: %d", len(articles))

    if fetch_full and HAS_TRAFILATURA and articles:
        log.info("Fetching full text (this takes a minute)...")
        for i, art in enumerate(articles, 1):
            txt = _fetch_full_text(art.url)
            if txt:
                art.full_text = txt[:5000]
            if i % 10 == 0:
                log.info("  %d / %d", i, len(articles))
            time.sleep(0.3)

    return articles
