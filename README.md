# Canadian Media Morning Brief

A scheduled agent that pulls articles from Canadian media outlets each weekday morning, filters them against your topic list, synthesizes them with the Claude API, and emails the result to your communications team.

## What it monitors

- **Anglophone (national):** National Post, Financial Post, Globe and Mail, The Hub, CBC News
- **Francophone (Quebec-anchored):** La Presse, Journal de Montréal, Le Soleil, Le Devoir, Radio-Canada, Le Quotidien
- **Alberta:** Calgary Herald, Edmonton Journal, Western Standard, CBC Calgary, CBC Edmonton

Topics: public spending, energy, housing, healthcare, immigration, AI regulation.
Priority regions: Alberta, Quebec.

## How it works

1. `fetch.py` parses every RSS feed, keeps articles from the last 24h whose title or summary contains at least one topic keyword (in EN or FR), and optionally pulls article body text via `trafilatura` for richer synthesis.
2. `synthesize.py` sends the batch to Claude with a structured prompt. The model produces an HTML brief organized by top stories → topic → region → framing watch → on-the-radar → sources.
3. `email_sender.py` wraps that HTML in a styled email template and sends it via SMTP.
4. `.github/workflows/morning_brief.yml` runs the whole thing on a weekday cron (10:00 UTC ≈ 6 AM ET).

## Setup

```bash
git clone <this-repo>
cd canadian-media-monitor
pip install -r requirements.txt
cp .env.example .env   # then edit .env with real values
```

Edit `config.yaml`:

- `email.recipients`: your comms team's addresses
- `email.from_address`: the bot's sending address
- Keyword lists per topic — these are the main lever for noise vs. coverage

### Secrets

- `ANTHROPIC_API_KEY` — from [console.anthropic.com](https://console.anthropic.com)
- `SMTP_USER` / `SMTP_PASSWORD` — for Gmail, generate an [App Password](https://myaccount.google.com/apppasswords) (regular passwords won't work with 2FA). For other SMTP providers, adjust `smtp_host` and `smtp_port` in `config.yaml`.

### Running

Manually: `python main.py`

Scheduled: push to a GitHub repo, add the three secrets above under Settings → Secrets and variables → Actions, and the workflow runs Mon–Fri at 10:00 UTC. Trigger ad-hoc runs from the Actions tab via "Run workflow".

## Verify the RSS feeds before going live

RSS URLs change. Some of the URLs in `config.yaml` (especially Postmedia properties and Capitales Médias / CN2i titles like Le Soleil, Le Quotidien) have shifted formats over the years. Run this once and replace any that come back empty:

```bash
python -c "
import yaml, feedparser
cfg = yaml.safe_load(open('config.yaml'))
for group in cfg['outlets'].values():
    for o in group:
        n = len(feedparser.parse(o['rss']).entries)
        status = 'OK' if n else 'EMPTY — check URL'
        print(f'{n:3d}  {status:25s}  {o[\"name\"]}')
"
```

## Tuning the brief

- **Too noisy** → tighten keywords. Replace bare nouns like `oil` with multi-word phrases like `oil sands` or `oil and gas`.
- **Missing stories** → widen keywords, or raise `filter.lookback_hours` from 24 to 36.
- **Brief feels generic** → edit the prompt in `synthesize.py`. The system prompt and the structure template are where the brief's voice lives. Worth iterating on the first few mornings.
- **Brief too long** → lower `claude.max_output_tokens` and the word cap in the prompt.
- **Wrong model size** → swap `claude.model` in `config.yaml`. Sonnet is the default for cost/quality balance; Opus gives more nuanced framing analysis at higher cost; Haiku is cheaper if you're running on a tight budget.

## Caveats

- **Paywalls.** Globe and Mail, La Presse Premium, and Postmedia titles often serve only RSS summaries to non-subscribers. The brief works on summaries but is meaningfully richer when full text is available. If your org has subscriptions, you could log the scraper in or feed it the print edition PDFs separately.
- **Outlet skew is real.** Western Standard tilts right-populist; Le Devoir tilts centre-left sovereigntist; CBC and Radio-Canada are centrist with progressive social coverage. The brief surfaces this divergence rather than averaging it out — that's the comms-team value.
- **Time zone in cron.** GitHub Actions cron is UTC. `10:00 UTC` is 6 AM EDT (summer) and 5 AM EST (winter). If a year-round 6 AM ET is important, run a small wrapper that delays an hour during EST.
- **Bilingual fidelity.** The synthesis is in English but quotes French outlets in French. If your team prefers fully French briefs or a bilingual side-by-side, adjust the prompt in `synthesize.py`.

## File layout

```
canadian-media-monitor/
├── config.yaml           # outlets, keywords, recipients, model
├── main.py               # orchestration
├── fetch.py              # RSS + filter + full-text
├── synthesize.py         # Claude API call + prompt
├── email_sender.py       # SMTP + HTML wrapper
├── requirements.txt
├── .env.example
├── .github/workflows/
│   └── morning_brief.yml
└── README.md
```
