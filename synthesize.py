"""
Sends filtered articles to the Claude API and returns an HTML clipping brief.
Format: articles grouped by topic. For each article:
- Headline (clickable link)
- Outlet · By Author · published time · region
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
- Pro-capital markets and free enterprise
- In favour of decentralization (provinces > federal where possible)
- Skeptical of expanded state intervention and new government programs
- Attentive to opportunity cost, fiscal trade-offs, and unintended consequences
- Sympathetic to private-sector alternatives, choice, and competition
- Defends individual economic liberty and consumer choice
HARD RULES - these are non-negotiable:
1. Never distort, invert, or omit a fact to fit the lens. If a program reduced wait times by 12%, say so. Credibility comes from honesty about what the article reports.
2. The lens shows up in WHAT YOU EMPHASIZE and WHAT CONTEXT YOU ADD - not in fabricated framing. Examples:
   - Add opportunity-cost framing: "The $X billion commitment comes as the federal deficit reaches $Y."
   - Note market alternatives: "The article focuses on public investment; private-sector capacity in this area is not addressed."
   - Flag decentralization angles: "Provincial governments have historically led in this file."
   - Surface trade-offs: "Affordability gains for X may come at the cost of Y for Z."
3. For genuinely empirical or neutral stories (e.g., Stats Canada data), summarize plainly. Do not force a lens onto facts that don't invite one.
4. Include 1-2 key figures or statistics per summary when the article contains them - dollar amounts, percentages, headcounts, dates.
5. LANGUAGE RULE: Write each summary in the SAME language as the article. French article -> French summary. English article -> English summary. You may include one short phrase in the other language if its exact wording is revealing (e.g., a minister's direct quote).
6. Keep every summary to EXACTLY 3 sentences. No more, no less. Tight, scannable, factual.
7. INCLUDE EVERY ARTICLE. You must produce a summary block for every single article that has a URL. Do not skip, drop, or omit any article for any reason. If an article seems off-topic or weak, include it anyway - the filtering was done upstream.
8. Do not output any reasoning, thinking, or explanation. Output ONLY the final HTML, nothing else.
9. CANADA ANGLE - MANDATORY: Every summary you write must make the Canadian connection explicit. If the article covers an international development (tariffs, energy markets, geopolitical events, supply chains, etc.), your summary must state clearly how it affects Canada, Canadian industries, Canadian consumers, or specific Canadian provinces. Do not summarize the international event in isolation - always land on the Canadian implication. If the Canadian angle is not stated in the article, note that explicitly (e.g., "The article does not address the direct impact on Canadian exporters.").
10. TALKING POINTS FIRST: The brief is used by IEDM staff to prepare talking points about the Canadian economy. Write every summary so that a reader can immediately identify: (a) what is happening, (b) what it means for Canada or a Canadian sector, and (c) what the fiscal or market trade-off is. Summaries that do not connect to a Canadian economic reality are not useful.
11. BYLINE: In the metadata line under each headline, include the author as "By [Author Name]" when the article's "author" field is non-empty. If the author field is empty or blank, omit the byline entirely — do not write "By Unknown" or leave a placeholder."""
USER_PROMPT_TEMPLATE = """Today is {date}.
Below are {n} articles published in the last 24 hours that matched at least one monitored topic. Each article includes outlet, region, language, author, headline, URL, the matched topic(s), and the article's content excerpt.
ARTICLES:
{articles_json}
TOPIC LABELS (use these exact strings as section headers):
{topic_labels_json}
Produce ONLY the inner HTML for an email body (no <html>, <head>, <body>, no markdown fences, no preamble). Structure:
<h2 style="color:#1a1a2e;border-bottom:2px solid #e0e0e0;padding-bottom:6px;margin-top:28px;">[Topic Label]</h2>
<!-- Repeat the block below for each article under this topic. Order articles within a topic by recency, most recent first. -->
<div style="margin-bottom:18px;">
  <p style="margin:0 0 4px 0;"><a href="ARTICLE_URL" style="color:#0a58ca;text-decoration:none;font-weight:600;">Article Headline Here</a></p>
  <p style="margin:0 0 6px 0;color:#888;font-size:12px;">Outlet Name · By Author Name · Published time (e.g., "today 8:30 AM ET" or "yesterday 6:15 PM ET") · Region</p>
  <p style="margin:0;">Exactly three sentences here, applying the lens where natural, plain where not. In the article's language. Include key figures when present. Make the Canadian economic angle explicit.</p>
</div>
RULES FOR ASSEMBLY:
- Show topics in this exact order, skipping any with zero articles:
  Public Spending & Taxation, Energy, Housing, Healthcare, AI & Regulation, Affordability & Cost of Living, Trade.
- If an article matches multiple topics, place it under the topic where it fits best - do not duplicate it.
- For the "Published time" line, render the article's timestamp as a friendly relative time in Eastern Time (e.g., "today 8:30 AM ET", "yesterday 6:15 PM ET").
- For the author: include "By [Author Name]" between the outlet and the published time when the author field is non-empty. Omit entirely if the author field is blank.
- Capitalize region as "Quebec", "Alberta", or "National".
- EVERY article with a URL must appear in the output. Zero omissions.
- Output nothing else - no intro paragraph, no closing note, no list of sources at the end."""


# ---------------------------------------------------------------------------
# MEI/IEDM media mentions section  ← built in Python, no Claude tokens used
# ---------------------------------------------------------------------------
def _build_mei_section(mei_articles: List[Article], ytd_count: int) -> str:
    """
    Render the MEI/IEDM media mentions section as HTML.
    Appended after the Claude brief; no additional Claude call needed.
    """
    import pytz
    EASTERN = pytz.timezone("America/Toronto")

    lines = [
        '<h2 style="color:#1a1a2e;border-bottom:2px solid #e0e0e0;padding-bottom:6px;'
        'margin-top:36px;">MEI / IEDM — Mentions médias du jour</h2>',
    ]

    if mei_articles:
        for a in mei_articles:
            try:
                pub_dt = datetime.fromisoformat(a.published).astimezone(EASTERN)
                time_str = pub_dt.strftime("%-I:%M %p ET").lower().replace("am", "AM").replace("pm", "PM")
            except Exception:
                time_str = ""
            byline = f" · By {a.author}" if a.author else ""
            meta = f"{a.outlet}{byline}{' · ' + time_str if time_str else ''} · {a.region}".strip(" ·")
            lines.append(
                f'<div style="margin-bottom:14px;">'
                f'<p style="margin:0 0 3px 0;">'
                f'<a href="{a.url}" style="color:#0a58ca;text-decoration:none;font-weight:600;">'
                f'{a.title}</a></p>'
                f'<p style="margin:0;color:#888;font-size:12px;">{meta}</p>'
                f'</div>'
            )
    else:
        lines.append(
            '<p style="color:#888;font-size:13px;font-style:italic;">'
            'Aucune mention trouvée dans les fils RSS des dernières 24 heures.</p>'
        )

    lines.append(
        f'<p style="margin-top:16px;font-size:13px;border-top:1px solid #eee;'
        f'padding-top:10px;color:#555;">'
        f'<strong>Mentions MEI/IEDM cumulées en {datetime.now().year} : {ytd_count}</strong></p>'
    )

    return "\n".join(lines)


def synthesize(articles: List[Article], model: str, max_tokens: int = 8000,
               topic_labels: dict | None = None,
               mei_articles: List[Article] | None = None,
               mei_ytd_count: int = 0) -> str:
    """Call Claude with the article batch, return the HTML clipping brief.

    If mei_articles is provided, a MEI/IEDM media mentions section is
    appended after the Claude-generated content (built directly in Python,
    no additional API call).
    """
    client = Anthropic()
    payload = []
    for a in articles:
        body = a.full_text or a.summary
        payload.append({
            "outlet": a.outlet,
            "region": a.region,
            "language": a.language,
            "author": a.author,
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
    if brief.startswith("```"):
        brief = brief.split("```", 2)[1]
        if brief.startswith("html"):
            brief = brief[4:]
        brief = brief.rsplit("```", 1)[0].strip()

    # Append MEI section if caller passed MEI data
    if mei_articles is not None:
        brief += "\n\n" + _build_mei_section(mei_articles, mei_ytd_count)

    return brief
