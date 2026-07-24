"""
RSS fetching, keyword matching, and article de-duplication.
Pipeline:
  1. Parse each outlet's RSS feed
  2. EXCLUDE articles matching exclude_keywords (police, sports, etc.)
  3. CANADA FILTER — drop articles with no Canadian angle
  4. Match remaining articles against topic keywords (with stemming for singular/plural)
  5. Return de-duplicated Article list
"""
from __future__ import annotations
import hashlib
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
import feedparser
import yaml

# No RSS feed should take longer than 10 seconds to respond
socket.setdefaulttimeout(10)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NLTK stemming setup
# ---------------------------------------------------------------------------
try:
    from nltk.stem import SnowballStemmer
    import nltk
    for _pkg in ("punkt", "punkt_tab"):
        try:
            nltk.data.find(f"tokenizers/{_pkg}")
        except LookupError:
            nltk.download(_pkg, quiet=True)
    _stemmers = {
        "english": SnowballStemmer("english"),
        "french": SnowballStemmer("french"),
    }
    _STEMMING_AVAILABLE = True
except ImportError:
    _STEMMING_AVAILABLE = False
    _stemmers = {}
    log.warning("NLTK not available — stemming disabled, keywords must match exactly.")

# Tokenizer: keeps alphanumeric + hyphens so "C-48" stays "c-48"
_TOKEN_RE = re.compile(r"[a-zA-ZÀ-ÿ0-9][a-zA-ZÀ-ÿ0-9-]*")

# Pure-alpha test: only stem tokens with no digits or hyphens
_ALPHA_RE = re.compile(r"^[a-zA-ZÀ-ÿ]+$")

# ---------------------------------------------------------------------------
# Article dataclass
# ---------------------------------------------------------------------------
@dataclass
class Article:
    title: str
    url: str
    outlet: str
    region: str
    language: str
    published: str
    summary: str
    matched_topics: List[str] = field(default_factory=list)
    full_text: Optional[str] = None

# ---------------------------------------------------------------------------
# Stemming helpers
# ---------------------------------------------------------------------------
def _stem_phrase(phrase: str, lang: str) -> list[str]:
    """Tokenize and stem a keyword phrase."""
    if not _STEMMING_AVAILABLE:
        return phrase.lower().split()
    stemmer = _stemmers.get(lang, _stemmers["english"])
    tokens = _TOKEN_RE.findall(phrase.lower())
    result = []
    for tok in tokens:
        if _ALPHA_RE.match(tok):
            result.append(stemmer.stem(tok))
        else:
            result.append(tok)
    return result

def _stem_text(text: str, lang: str) -> list[str]:
    """Tokenize and stem a full block of text."""
    return _stem_phrase(text, lang)

def _text_contains_phrase(stemmed_text: list[str], stemmed_phrase: list[str]) -> bool:
    """Check if stemmed_phrase appears as a contiguous run in stemmed_text."""
    if not stemmed_phrase:
        return False
    n = len(stemmed_phrase)
    for i in range(len(stemmed_text) - n + 1):
        if stemmed_text[i : i + n] == stemmed_phrase:
            return True
    return False

# ---------------------------------------------------------------------------
# Exclusion filter  ← blocks police/crime/sports articles
# ---------------------------------------------------------------------------
def _is_excluded(text: str, exclude_keywords: list) -> bool:
    """
    Return True if `text` contains any keyword from exclude_keywords.
    Word-boundary aware. Case-insensitive.
    """
    text_lower = text.lower()
    for kw in exclude_keywords:
        kw_lower = kw.lower()
        pattern = r"(?<!\S)" + re.escape(kw_lower) + r"(?!\S)"
        if re.search(pattern, text_lower):
            return True
    return False

# ---------------------------------------------------------------------------
# Canada relevance filter  ← NEW: blocks articles with no Canadian angle
# ---------------------------------------------------------------------------
_CANADA_KEYWORDS = [
    # Country
    "canada", "canadian", "canadien", "canadienne",
    # Federal institutions
    "federal", "fédéral", "parliament", "parlement", "ottawa",
    "bank of canada", "banque du canada", "cra", "rcmp", "grc",
    "house of commons", "chambre des communes", "senate", "sénat",
    "premier", "prime minister", "ministre",
    # Provinces & territories
    "ontario", "quebec", "québec", "british columbia",
    "alberta", "manitoba", "saskatchewan",
    "nova scotia", "nouvelle-écosse",
    "new brunswick", "nouveau-brunswick",
    "newfoundland", "labrador",
    "prince edward island", "île-du-prince-édouard",
    "northwest territories", "territoires du nord-ouest",
    "nunavut", "yukon",
    # Major cities
    "toronto", "montreal", "montréal", "vancouver", "calgary",
    "edmonton", "winnipeg", "halifax", "saskatoon", "regina",
    "victoria", "gatineau", "longueuil", "surrey", "brampton",
    "mississauga", "markham", "kitchener",
    # Short forms used in Canadian press
    " b.c.", " b.c ", " ont.", " ont ", " alta.", " alta ",
    " sask.", " man.", " n.s.", " n.b.", " p.e.i.",
]

def _is_canada_relevant(text: str) -> bool:
    """
    Return True only if the article has a clear Canadian angle.
    Every article in the brief must relate to Canada, its provinces,
    or Canadian public policy — no pure international stories.
    """
    text_lower = text.lower()
    return any(kw in text_lower for kw in _CANADA_KEYWORDS)

# ---------------------------------------------------------------------------
# Topic matching
# ---------------------------------------------------------------------------
def _matches_topic(stemmed_en: list[str], stemmed_fr: list[str], keywords: list) -> bool:
    """Return True if any keyword phrase matches in the EN or FR stemmed text."""
    for kw in keywords:
        for lang, stemmed_text in [("english", stemmed_en), ("french", stemmed_fr)]:
            stemmed_kw = _stem_phrase(kw.lower(), lang)
            if _text_contains_phrase(stemmed_text, stemmed_kw):
                return True
    return False

# ---------------------------------------------------------------------------
# Config loader (imported by main.py)
# ---------------------------------------------------------------------------
def load_config(config_path: str = "config.yml") -> dict:
    path = Path(config_path)
    if not path.is_absolute():
        path = Path(__file__).parent / config_path
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------
def fetch_all(config_path="config.yml") -> List[Article]:
    if isinstance(config_path, dict):
        config = config_path
    else:
        config = load_config(config_path)

    outlets_cfg = config.get("outlets", {})
    topics_cfg = config.get("topics", {})
    exclude_kws = config.get("exclude_keywords", [])

    if not exclude_kws:
        log.warning("No exclude_keywords found in config.yml — all articles will pass through.")

    region_map = {
        "anglophone_national": "National",
        "francophone": "Quebec",
        "alberta": "Alberta",
    }

    seen_ids: set[str] = set()
    articles: List[Article] = []

    for region_key, outlet_list in outlets_cfg.items():
        region_label = region_map.get(region_key, region_key.replace("_", " ").title())

        for outlet_cfg in outlet_list:
            outlet_name = outlet_cfg.get("name", "Unknown")
            feed_url = outlet_cfg.get("feed", "")
            lang = outlet_cfg.get("language", "english")

            if not feed_url:
                log.warning("Outlet %s has no feed URL, skipping.", outlet_name)
                continue

            try:
                feed = feedparser.parse(feed_url, agent="Mozilla/5.0", request_headers={"Connection": "close"})
                if not feed.entries and feed.get("bozo"):
                    log.warning("Bad feed for %s, skipping.", outlet_name)
                    continue
            except Exception as exc:
                log.warning("Failed to parse feed for %s: %s", outlet_name, exc)
                continue

            for entry in feed.entries:
                url = entry.get("link", "").strip()
                if not url:
                    continue

                art_id = hashlib.md5(url.encode()).hexdigest()
                if art_id in seen_ids:
                    continue

                title = entry.get("title", "").strip()
                rss_summary = entry.get("summary", "").strip()

                published_struct = (
                    entry.get("published_parsed") or entry.get("updated_parsed")
                )
                if published_struct:
                    try:
                        published = datetime(
                            *published_struct[:6], tzinfo=timezone.utc
                        ).isoformat()
                    except Exception:
                        published = datetime.now(timezone.utc).isoformat()
                else:
                    published = datetime.now(timezone.utc).isoformat()

                combined = f"{title} {rss_summary}"

                # ── STEP 1: EXCLUSION FILTER ──────────────────────────────
                # Drop police/crime/sports articles before anything else.
                if exclude_kws and _is_excluded(combined, exclude_kws):
                    log.debug("EXCLUDED (%s): %s", outlet_name, title)
                    continue

                # ── STEP 2: CANADA RELEVANCE FILTER ──────────────────────
                # Drop any article with no clear Canadian angle.
                if not _is_canada_relevant(combined):
                    log.debug("NOT CANADA-RELEVANT (%s): %s", outlet_name, title)
                    continue

                # ── STEP 3: TOPIC MATCHING ────────────────────────────────
                stemmed_en = _stem_text(combined, "english")
                stemmed_fr = _stem_text(combined, "french")

                matched_topics = []
                for topic_key, topic_cfg in topics_cfg.items():
                    keywords = topic_cfg.get("keywords", [])
                    if _matches_topic(stemmed_en, stemmed_fr, keywords):
                        matched_topics.append(topic_key)

                if not matched_topics:
                    continue

                seen_ids.add(art_id)
                articles.append(
                    Article(
                        title=title,
                        url=url,
                        outlet=outlet_name,
                        region=region_label,
                        language=lang,
                        published=published,
                        summary=rss_summary[:800],
                        matched_topics=matched_topics,
                        full_text=None,
                    )
                )

    log.info(
        "Fetched %d articles after exclusion + Canada filter + topic matching.", len(articles)
    )
    return articles
