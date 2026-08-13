# How to Set Up Cookies for Full-Text Fetching

The newsletter can fetch full article text from Postmedia and Globe and Mail
using your session cookies. This gives Claude the complete article instead of
just the RSS snippet, and improves MEI mention detection.

## Step 1 — Log in to the outlets in Chrome

- **Postmedia** (covers National Post, Financial Post, Ottawa Citizen, Calgary
  Herald, Edmonton Journal — one login for all):
  Go to https://nationalpost.com and sign in with your IEDM account.

- **Globe and Mail**:
  Go to https://www.theglobeandmail.com and sign in.

## Step 2 — Extract your session cookie

In Chrome, open any article on the site, then:

1. Press **F12** (or right-click → Inspect) to open DevTools
2. Go to the **Network** tab
3. Reload the page (Cmd+R)
4. Click on the first request in the list (the page URL)
5. In the right panel, click **Headers**
6. Scroll down to **Request Headers**
7. Find the line that starts with **Cookie:**
8. Click on it and copy the entire value (it's a long string of key=value pairs)

## Step 3 — Paste into cookies.json on the server

SSH into the server and open the file:

```bash
ssh root@142.93.145.200
nano /root/Canadian-Media-Monitor/cookies.json
```

Paste your cookies into the appropriate field:

```json
{
  "postmedia": "paste_your_nationalpost_cookie_string_here",
  "globe": "paste_your_globe_cookie_string_here"
}
```

Save with Ctrl+X → Y → Enter.

## Step 4 — Test

```bash
cd /root/Canadian-Media-Monitor
python3 -c "
from article_fetcher import fetch_full_text
text = fetch_full_text('https://nationalpost.com/news/canada/mark-carney-canada-strong-fund-likely-to-follow-path-of-failing-uk-fund-report')
print(f'Got {len(text)} chars' if text else 'FAILED — cookie may be wrong or expired')
"
```

## Cookie expiry

Postmedia and Globe sessions typically last 30–90 days. When they expire,
full-text fetching silently falls back to the RSS snippet (nothing breaks).
You'll see "cookie may be expired" warnings in the logs. Repeat steps 1–3 to refresh.
