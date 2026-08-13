"""Email delivery of the morning brief via SendGrid."""
from __future__ import annotations
import logging
import os
from datetime import datetime
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

log = logging.getLogger(__name__)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8">
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    max-width: 720px; margin: 24px auto; padding: 0 16px;
    color: #1a1a1a; line-height: 1.55;
  }}
  h1 {{ font-size: 22px; border-bottom: 2px solid #1a1a1a; padding-bottom: 8px; margin-bottom: 4px; }}
  h2 {{ font-size: 17px; margin-top: 28px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  h3 {{ font-size: 14.5px; margin-top: 18px; color: #444; }}
  p, li {{ font-size: 14px; }}
  a {{ color: #0a58ca; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  ul {{ padding-left: 22px; }}
  .meta {{ color: #888; font-size: 12px; margin-bottom: 20px; }}
  .footer {{ color: #888; font-size: 11px; margin-top: 32px; border-top: 1px solid #eee; padding-top: 10px; }}
</style>
</head>
<body>
  <h1>Morning Media Brief</h1>
  <p class="meta">{date} · {n_articles} articles · {n_outlets} outlets monitored</p>
  {body}
  <p class="footer">Generated automatically each weekday morning.
  Reply to this email to flag misses, noise, or to request keyword changes.</p>
</body>
</html>"""


def send_brief(html_body: str, config: dict, n_articles: int, n_outlets: int) -> None:
    cfg = config["email"]

    full_html = HTML_TEMPLATE.format(
        date=datetime.now().strftime("%A, %B %d, %Y"),
        n_articles=n_articles,
        n_outlets=n_outlets,
        body=html_body,
    )

    subject = cfg["subject_template"].format(date=datetime.now().strftime("%b %d"))
    from_address = cfg["from_address"]
    recipients = cfg["recipients"]

    api_key = os.environ["SENDGRID_API_KEY"]
    sg = SendGridAPIClient(api_key=api_key)

    log.info("Sending to %d recipient(s) via SendGrid", len(recipients))

    for recipient in recipients:
        message = Mail(
            from_email=from_address,
            to_emails=recipient,
            subject=subject,
            html_content=full_html,
        )
        sg.send(message)

    log.info("Sent.")
