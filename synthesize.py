"""
Sends filtered articles to the Claude API and returns an HTML clipping brief
in the IEDM/MEI "Veille médiatique" style.

Structure:
  1. Featured articles (3-5, with "Angle" editorial paragraph)
  2. MEI in medias  (only when articles mention MEI/IEDM)
  3. FYIs           (remaining articles — title + topic tag + link only)
  4. AB PRESS REVIEW (Alberta-region articles — same brief format)

Language rule: French articles → French "Angle". English → English.
Editorial lens: classical-liberal / IEDM-style.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List

from anthropic import Anthropic

from fetch import Article

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are producing a daily media clipping brief ("Veille médiatique") for the communications team of the Montreal Economic Institute (IEDM/MEI), a classical-liberal Canadian think tank.

Your editorial lens, applied consistently:
- Pro–capital markets and free enterprise
- In favour of decentralization (provinces > federal where possible)
- Skeptical of expanded state intervention and new government programs
- Attentive to opportunity cost, fiscal trade-offs, and unintended consequences
- Sympathetic to private-sector alternatives, choice, and competition
- Defends individual economic liberty and consumer choice

HARD RULES — non-negotiable:
1. Never distort, invert, or omit a fact to fit the lens. Credibility requires honesty about what the article actually reports.
2. The lens shows up in WHAT YOU EMPHASIZE and WHAT CONTEXT YOU ADD — not in fabricated framing.
3. For genuinely neutral or empirical stories, summarize plainly without forcing a lens.
4. LANGUAGE RULE: Write the "Angle" commentary in the SAME LANGUAGE as the article. French article → French commentary. English article → English commentary. Do not translate.
5. Keep "Angle" commentaries to 2-3 tight, analytical sentences. No rhetoric.
6. Include key figures (dollar amounts, percentages, dates) when the article contains them.
7. Every article that has a URL must appear exactly once in the output — no omissions, no duplicates.
8. Do not output any reasoning, thinking, or explanation. Output ONLY the final HTML."""


USER_PROMPT_TEMPLATE = """Today is {date}.

Below are {n} articles published in the last 24 hours that matched at least one monitored topic. Each entry includes outlet, region, language, title, URL, matched topics, and a content excerpt.

ARTICLES:
{articles_json}

Produce ONLY the inner HTML for an email body (no <html>, <head>, <body> tags, no markdown fences, no preamble).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT STRUCTURE (follow exactly)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SECTION 1 — FEATURED ARTICLES
Pick 3 to 5 articles that are most strategically relevant to IEDM's mission. Give each a full "Angle" editorial paragraph. Use this HTML block for each:

<div style="margin-bottom:20px;font-family:Aptos,Calibri,Helvetica,sans-serif;font-size:12pt;">
  <p style="margin:0 0 4px 0;"><b>[Article Headline] - [Outlet Name]</b></p>
  <p style="margin:0 0 6px 0;">Angle : [2-3 sentence editorial commentary written in the article's original language — French for French articles, English for English articles]</p>
  <p style="margin:0;"><a href="[URL]" style="color:#467886;">[URL]</a></p>
</div>

SECTION 2 — MEI IN MEDIAS (optional)
Include this section ONLY if one or more articles explicitly mention "MEI", "IEDM", or "Montreal Economic Institute" in their content field. If none do, skip this section entirely. Format:

<p style="margin:20px 0 8px 0;font-family:Aptos,Calibri,Helvetica,sans-serif;font-size:12pt;"><b><u>MEI in medias</u></b></p>
<div style="margin-bottom:12px;font-family:Aptos,Calibri,Helvetica,sans-serif;font-size:12pt;">
  <p style="margin:0 0 2px 0;"><b>[Article Headline] - [Outlet Name]</b></p>
  <p style="margin:0;"><a href="[URL]" style="color:#467886;">[URL]</a></p>
</div>

SECTION 3 — FYIs
All remaining articles (excluding Alberta-region articles and any placed in sections 1 or 2) go here. Brief format only — no Angle paragraph. Just headline, a short topic tag (1–3 words in the article's language), and link:

<p style="margin:20px 0 8px 0;font-family:Aptos,Calibri,Helvetica,sans-serif;font-size:12pt;"><b><u>FYIs</u></b></p>
<div style="margin-bottom:12px;font-family:Aptos,Calibri,Helvetica,sans-serif;font-size:12pt;">
  <p style="margin:0 0 2px 0;"><b>[Article Headline] - [Outlet Name]</b></p>
  <p style="margin:0 0 2px 0;">[Short topic tag]</p>
  <p style="margin:0;"><a href="[URL]" style="color:#467886;">[URL]</a></p>
</div>

SECTION 4 — AB PRESS REVIEW
Articles where region == "Alberta" go here (unless already featured in Section 1). Same brief format as FYIs:

<p style="margin:20px 0 8px 0;font-family:Aptos,Calibri,Helvetica,sans-serif;font-size:12pt;"><b><u>AB PRESS REVIEW</u></b></p>
<div style="margin-bottom:12px;font-family:Aptos,Calibri,Helvetica,sans-serif;font-size:12pt;">
  <p style="margin:0 0 2px 0;"><b>[Article Headline] - [Outlet Name]</b></p>
  <p style="margin:0 0 2px 0;">[Short topic tag]</p>
  <p style="margin:0;"><a href="[URL]" style="color:#467886;">[URL]</a></p>
</div>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PLACEMENT RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- EVERY article with a URL must appear exactly once across all four sections. Do not omit any.
- An Alberta article that is among the most important may appear in Section 1 instead of Section 4 — but never in both.
- MEI in medias articles may also be featured in Section 1 if they are among the top picks — but list them in Section 2 only if they were NOT featured in Section 1.
- Omit Section 2 entirely if no articles mention MEI/IEDM.
- Omit Section 4 entirely if there are no Alberta articles.
- Output nothing else — no intro, no closing note, no source list."""


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

    user_msg = USER_PROMPT_TEMPLATE.format(
        date=datetime.now().strftime("%A, %B %d, %Y"),
        n=len(articles),
        articles_json=json.dumps(payload, ensure_ascii=False, indent=2),
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
