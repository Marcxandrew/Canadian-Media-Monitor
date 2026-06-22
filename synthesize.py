"""
Sends filtered articles to the Claude API and returns an HTML clipping brief.

Format: articles grouped by topic. For each article:
- Headline (clickable link)
- Outlet · published time · region
- 3-sentence summary. French articles stay in French; English articles in English.

Editorial lens: classical-liberal / IEDM-style.
Note: exclusion filtering (police, sports, etc.) is handled upstream in fetch.py.
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
   - Note market alternatives: "The article focuses on public investment; private-sector capacity in this area is not addressed."
   - Flag decentralization angles: "Provincial governments have historically led in this file."
   - Surface trade-offs: "Affordability gains for X may come at the cost of Y for Z."
3. For genuinely empirical or neutral stories (e.g., Stats Canada data), summarize plainly. Do not force a lens onto facts that don't invite one.
4. Include 1-2 key figures or statistics per summary when the article contains them — dollar amounts, percentages, headcounts, dates.
5. LANGUAGE RULE: Write each summary in the SAME language as the article. French article → French summary. English article → English summary. You may include one short phrase in the other language if its exact wording is revealing (e.g., a minister's direct quote).
6. Keep every summary to EXACTLY 3 sentences. No more, no less. Tight, scannable, factual.
7. INCLUDE EVERY ARTICLE. You must produce a summary block for every single article that has a URL. Do not skip, drop, or omit any article for any reason. If an article seems off-topic or weak, include it anyway — the filtering was done upstream.
8. Do not output any reasoning, thinking, or explanation. Output ONLY the final HTML, nothing else."""


USER_PROMPT_TEMPLATE = """Today is {date}.

Below are {n} articles published in the last 24 hours that matched at least one monitored topic. Each article includes outlet, region, language, headline, URL, the matched topic(s), and the article's content excerpt.

ARTICLES:
{articles_json}

TOPIC LABELS (use these exact strings as section headers):
{topic_labels_json}

Produce ONLY the inner HTML for an email body (no <html>, <head>, <body>, no markdown fences, no preamble). Structure:

<h2 style="color:#1a1a2e;border-bottom:2px solid #e0e0e0;padding-bottom:6px;margin-top:28px;">[Topic Label]</h2>
<!-- Repeat the block below for each article under this topic. Order articles within a topic by recency, most recent first. -->
<div style="margin-bottom:18px;">
  <p style="margin:0 0 4px 0;"><a href="ARTICLE_URL" style="color:#0a58ca;text-decoration:none;font-weight:600;">Article Headline Here</a></p>
  <p style="margin:0 0 6px 0;color:#888;font-size:12px;">Outlet Name · Published time (e.g., "today 8:30 AM ET" or "yesterday 6:15 PM ET") · Region</p>
  <p style="margin:0;">Exactly three sentences here, applying the lens where natural, plain where not. In the article's language. Include key figures when present.</p>
</div>

RULES FOR ASSEMBLY:
- Show topics in this exact order, skipping any with zero articles:
  Public Spending & Taxation, Energy, Housing, Healthcare, AI & Regulation, Affordability & Cost of Living, Trade.
- If an article matches multiple topics, place it under the topic where it fits best — do not duplicate it.
- For the "Published time" line, render the article's timestamp as a friendly relative time in Eastern Time (e.g., "today 8:30 AM ET", "yesterday 6:15 PM ET").
- Capitalize region as "Quebec", "Alberta", or "National".
- EVERY article with a URL must appear in the output. Zero omissions.
- Output nothing else — no intro paragraph, no closing note, no list of sources at the end."""


def synthesize(articles: List[Article], model: str, max_tokens: int = 8000,
               topic_labels: dict | None = None) -> str:
    """Call Claude with the article batch, return the HTML clipping brief."""
    client = Anthropic()  # picks up ANTHROPIC_API_KEY from env

    payload = []
    for a in articles:
        body = a.full_text or a.summary
        payload.append({
            "outlet": a.outlet,
            "region": a.region,
            "language": a.language,
            "title": a.title,
            "url": a.url,
            "published": a.published,
            "matched_topics": a.matched_topics,
            "content": body[:800],
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
