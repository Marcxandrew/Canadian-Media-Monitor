"""
Sends filtered articles to the Claude API and returns an HTML clipping brief.

Format: articles grouped by topic. For each article:
- Headline (clickable)
- Outlet · published time · region
- 3-4 sentence summary in English (even if source is French)

Editorial lens: classical-liberal / IEDM-style. Facts are facts; the lens shows
up in what context is added (opportunity cost, market alternatives, decentralization
implications) — never by distorting what the article actually says.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List

from anthropic import Anthropic

from fetch import Article

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are producing a daily media clipping brief for the communications team of the Montreal Economic Institute (IEDM/MEI), a classical-liberal Canadian think tank.

Your editorial lens, applied consistently to every summary:
- Pro–capital markets and free enterprise
- In favour of decentralization (provinces > federal where possible)
- Skeptical of expanded state intervention and new government programs
- Attentive to opportunity cost, fiscal trade-offs, and unintended consequences
- Sympathetic to private-sector alternatives, choice, and competition
- Defends individual economic liberty and consumer choice

HARD RULES — these are non-negotiable:
1. Never distort, invert, or omit a fact to fit the lens. If a program reduced wait times by 12%, say so. Credibility comes from honesty about what the article reports.
2. The lens shows up in WHAT YOU EMPHASIZE and WHAT CONTEXT YOU ADD — not in fabricated framing. Examples:
   - Add opportunity-cost framing: "The $X billion commitment comes as the federal deficit reaches $Y."
   - Note market alternatives the article doesn't mention: "The article focuses on public investment; private-sector capacity in this area is not addressed."
   - Flag decentralization angles: "Provincial governments have historically led in this file."
   - Surface trade-offs: "Affordability gains for X may come at the cost of Y for Z."
3. For genuinely empirical or neutral stories (e.g., Stats Canada data drops with no obvious policy angle), summarize plainly. Do not force a lens onto facts that don't invite one.
4. Include 1-2 key figures or statistics per summary when the article contains them — dollar amounts, percentages, headcounts, dates. Skip if the article is qualitative.
5. Summaries are always in English. If the article is in French, you still write the summary in English. You may include one short French phrase in quotes if its specific wording is revealing (e.g., a minister's exact words).
6. Keep summaries to 3-4 sentences. Tight, scannable, factual.
7. Do not editorialize beyond the lens described above. No partisan attacks, no rhetoric, no political punditry. Calm, analytical, professional."""


USER_PROMPT_TEMPLATE = """Today is {date}.

Below are {n} articles published in the last 24 hours that matched at least one monitored topic. Each article includes outlet, region, language, headline, URL, the matched topic(s), and the article's RSS summary.

ARTICLES:
{articles_json}

TOPIC LABELS (use these exact strings as section headers):
{topic_labels_json}

Produce ONLY the inner HTML for an email body (no <html>, <head>, <body>, no markdown fences, no preamble). Structure:

<h2>[Topic Label]</h2>
<!-- Repeat the block below for each article under this topic. Order articles within a topic by recency, most recent first. -->
<div style="margin-bottom:18px;">
  <p style="margin:0 0 4px 0;"><a href="ARTICLE_URL" style="color:#0a58ca;text-decoration:none;font-weight:600;">Article Headline Here</a></p>
  <p style="margin:0 0 6px 0;color:#888;font-size:12px;">Outlet Name · Published time (e.g., "today 8:30 AM" or "yesterday 6:15 PM") · Region</p>
  <p style="margin:0;">Three to four sentence summary here, applying the lens where natural, plain where not. Include key figures when present.</p>
</div>

RULES FOR ASSEMBLY:
- Show topics in this order, skipping any with zero articles:
  Public Spending & Taxation, Energy, Housing, Healthcare, Immigration, AI & Regulation, Affordability & Cost of Living.
- If an article matches multiple topics, place it under the topic where it fits best — do not duplicate it.
- For the "Published time" line, render the article's timestamp as a friendly relative time in Eastern Time (e.g., "today 8:30 AM ET", "yesterday 6:15 PM ET").
- Capitalize region as "Quebec", "Alberta", or "National".
- Never include articles whose URL is missing.
- Output nothing else — no intro paragraph, no closing note, no list of sources at the end. The clipping list IS the brief."""


def synthesize(articles: List[Article], model: str, max_tokens: int = 4000,
               topic_labels: dict | None = None) -> str:
    """Call Claude with the article batch, return the HTML clipping brief."""
    client = Anthropic()  # picks up ANTHROPIC_API_KEY from env

    payload = []
    for a in articles:
        # Prefer full body when we have it; fall back to RSS summary
        body = a.full_text or a.summary
        payload.append({
            "outlet": a.outlet,
            "region": a.region,
            "language": a.language,
            "title": a.title,
            "url": a.url,
            "published": a.published,
            "matched_topics": a.matched_topics,
            "content": body[:2000],
        })

    labels_payload = topic_labels or {}

    user_msg = USER_PROMPT_TEMPLATE.format(
        date=datetime.now().strftime("%A, %B %d, %Y"),
        n=len(articles),
        articles_json=json.dumps(payload, ensure_ascii=False, indent=2),
        topic_labels_json=json.dumps(labels_payload, ensure_ascii=False, indent=2),
    )

    log.info("Calling Claude (%s) with %d articles", model, len(articles))
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    text_parts = [block.text for block in response.content if block.type == "text"]
    brief = "\n".join(text_parts).strip()

    # Defensive: strip markdown fences if the model added them despite instructions
    if brief.startswith("```"):
        brief = brief.split("```", 2)[1]
        if brief.startswith("html"):
            brief = brief[4:]
        brief = brief.rsplit("```", 1)[0].strip()

    return brief
