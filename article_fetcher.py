"""
Full-text article fetcher for paywalled outlets.

Supports:
  - Postmedia: National Post, Financial Post, Ottawa Citizen,
                Calgary Herald, Edmonton Journal  (one shared login)
  - Globe and Mail
  - Le Devoir
  - Toronto Star
  - Les Affaires
  - L'actualité
  - iPolitics

Cookies are stored in cookies.json next to this file.
See COOKIE_SETUP.md for how to extract them from your browser.

Usage:
    from article_fetcher import enrich_with_full_text
    enrich_with_full_text(articles)   # sets article.full_text in-place
"""
from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_COOKIES_PATH = os.path.join(os.path.dirname(__file__), "cookies.json")

# Maps URL substring → cookies.json key
_DOMAIN_TO_KEY = {
    "nationalpost.com":   "postmedia",
    "financialpost.com":  "postmedia",
    "ottawacitizen.com":  "postmedia",
    "calgaryherald.com":  "postmedia",
    "edmontonjournal.com":"postmedia",
    "theglobeandmail.com":"globe",
    "ledevoir.com":       "ledevoir",
    "thestar.com":        "thestar",
    "lesaffaires.com":    "lesaffaires",
    "lactualite.com":     "lactualite",
    "ipolitics.ca":       "ipolitics",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9,fr;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# ---------------------------------------------------------------------------
# Cookie loading
# ---------------------------------------------------------------------------

def _load_cookies() -> dict:
    if not os.path.exists(_COOKIES_PATH):
        log.debug("cookies.json not found — full-text fetching disabled.")
        return {}
    try:
        with open(_COOKIES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.warning("Could not read cookies.json: %s", exc)
        return {}


def _cookie_key(url: str) -> Optional[str]:
    for domain, key in _DOMAIN_TO_KEY.items():
        if domain in url:
            return key
    return None


# ---------------------------------------------------------------------------
# Paywall detection
# ---------------------------------------------------------------------------

_PAYWALL_PHRASES = [
    "subscribe to continue",
    "subscription required",
    "subscribers only",
    "create a free account to read",
    "sign in to read",
    "this article is for subscribers",
    "already a subscriber",
    "unlimited articles",
    "abonnez-vous",
    "réservé aux abonnés",
    "accès réservé",
]

def _is_paywalled(text: str) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in _PAYWALL_PHRASES)


# ---------------------------------------------------------------------------
# HTML → plain text extraction
# ---------------------------------------------------------------------------

# Per-outlet selectors, tried in order; first match with >300 chars wins
_SELECTORS = {
    "postmedia": [
        "div.article-content__content-group",
        "div[class*='article-content']",
        "div[class*='articleBody']",
        "div[itemprop='articleBody']",
        "div.story-text",
        "article",
    ],
    "globe": [
        "div.c-article-body",
        "div[class*='article__body']",
        "div[class*='articleBody']",
        "div[itemprop='articleBody']",
        "article",
    ],
    "ledevoir": [
        "div.article-body",
        "div[class*='article-body']",
        "div[itemprop='articleBody']",
        "div.field--type-text-long",
        "article",
    ],
    "thestar": [
        "div.article-body__content",
        "div[class*='article-body']",
        "div[itemprop='articleBody']",
        "article",
    ],
    "lesaffaires": [
        "div.article-body",
        "div[class*='article-body']",
        "div[class*='entry-content']",
        "article",
    ],
    "lactualite": [
        "div.entry-content",
        "div[class*='article-body']",
        "div[class*='entry-content']",
        "article",
    ],
    "ipolitics": [
        "div.entry-content",
        "div[class*='article-content']",
        "div[class*='post-content']",
        "article",
    ],
}
_DEFAULT_SELECTORS = ["div[itemprop='articleBody']", "article", "main"]


def _extract_text(html: str, key: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # Strip noise
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "aside", "figure", "figcaption", "noscript"]):
        tag.decompose()

    selectors = _SELECTORS.get(key, _DEFAULT_SELECTORS)

    for selector in selectors:
        elem = soup.select_one(selector)
        if elem:
            text = elem.get_text(separator=" ", strip=True)
            if len(text) > 300:
                return re.sub(r"\s+", " ", text).strip()

    # Fallback: collect all substantial paragraphs
    paragraphs = [
        p.get_text(strip=True)
        for p in soup.find_all("p")
        if len(p.get_text(strip=True)) > 60
    ]
    return re.sub(r"\s+", " ", " ".join(paragraphs)).strip()


# ---------------------------------------------------------------------------
# Single-article fetch
# ---------------------------------------------------------------------------

def fetch_full_text(url: str, cookies_cfg: dict | None = None) -> Optional[str]:
    """
    Fetch full article text for a supported paywalled URL.
    Returns extracted text (up to ~5000 chars) or None if unavailable.
    """
    key = _cookie_key(url)
    if not key:
        return None

    if cookies_cfg is None:
        cookies_cfg = _load_cookies()

    cookie_str = cookies_cfg.get(key, "").strip()
    if not cookie_str:
        log.debug("No cookie for '%s' — skipping %s", key, url)
        return None

    headers = {**_HEADERS, "Cookie": cookie_str}

    try:
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        if resp.status_code != 200:
            log.debug("HTTP %d for %s", resp.status_code, url)
            return None

        text = _extract_text(resp.text, key)

        if len(text) < 200:
            log.debug("Text too short (%d chars) — likely still paywalled: %s", len(text), url)
            return None

        if _is_paywalled(text[:1000]):
            log.debug("Paywall detected for %s — cookie may be expired.", url)
            return None

        log.debug("Full text fetched: %d chars from %s", len(text), url)
        return text[:5000]

    except Exception as exc:
        log.warning("Full-text fetch failed for %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Bulk enrichment (parallel)
# ---------------------------------------------------------------------------

def enrich_with_full_text(articles, max_workers: int = 5) -> None:
    """
    Fetch full article text for all supported paywalled outlets in parallel.
    Sets article.full_text in-place. Skips articles already enriched.
    Called after fetch_all() completes, before synthesize().
    """
    targets = [a for a in articles if _cookie_key(a.url) and not a.full_text]
    if not targets:
        log.info("No paywalled articles to enrich.")
        return

    log.info("Fetching full text for %d paywalled article(s)...", len(targets))
    cookies_cfg = _load_cookies()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_full_text, a.url, cookies_cfg): a
            for a in targets
        }
        success = 0
        for future in as_completed(futures):
            article = futures[future]
            try:
                text = future.result()
                if text:
                    article.full_text = text
                    success += 1
            except Exception as exc:
                log.warning("Enrichment error for %s: %s", article.url, exc)

    log.info("Full-text enrichment: %d/%d succeeded.", success, len(targets))
