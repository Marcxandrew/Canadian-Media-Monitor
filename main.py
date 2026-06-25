"""Entry point. Orchestrates fetch -> synthesize -> email."""
from __future__ import annotations
import logging
import sys
from fetch import fetch_all, load_config
from synthesize import synthesize
from email_sender import send_brief
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)
def _prioritize(articles, priority_regions, cap):
    """If we have too many articles for one prompt, keep priority regions first."""
    if len(articles) <= cap:
        return articles
    priority_set = set(priority_regions)
    articles.sort(
        key=lambda a: (0 if a.region in priority_set else 1, a.published),
        reverse=False,
    )
    log.info("Capping %d articles to %d (priority: %s)",
             len(articles), cap, ", ".join(priority_regions))
    return articles[:cap]
def main() -> int:
    config = load_config()
    articles = fetch_all(config)
    min_required = config["filter"]["min_relevant_articles"]
    if len(articles) < min_required:
        log.warning("Only %d relevant articles (min %d). Skipping email.",
                    len(articles), min_required)
        return 0
    articles = _prioritize(
        articles,
        config["priority_regions"],
        config["claude"]["max_articles_per_call"],
    )
    topic_labels = {
        key: cfg.get("label", key.replace("_", " ").title())
        for key, cfg in config["topics"].items()
    }
    brief_html = synthesize(
        articles,
        model=config["claude"]["model"],
        max_tokens=config["claude"]["max_output_tokens"],
        topic_labels=topic_labels,
    )
    n_outlets_monitored = sum(len(v) for v in config["outlets"].values())
    send_brief(brief_html, config,
               n_articles=len(articles),
               n_outlets=n_outlets_monitored)
    return 0
if __name__ == "__main__":
    sys.exit(main())
