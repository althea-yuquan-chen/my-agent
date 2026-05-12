"""
AI Secretary Agent — Morning Briefing (with long-term memory + multi-mailbox)

Supports:
  - Multiple Gmail accounts (separate OAuth token per account)
  - Hotmail / Outlook via IMAP
  - Any IMAP mailbox (Yahoo, custom domain, etc.)
"""

import os
import imaplib
import email
import datetime
import anthropic
import requests
from email.header import decode_header
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from pathlib import Path
from dotenv import load_dotenv

import memory as mem

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "")
YOUR_NAME            = os.getenv("YOUR_NAME", "there")
SLACK_WEBHOOK_URL    = os.getenv("SLACK_WEBHOOK_URL", "")
GOOGLE_CHAT_WEBHOOK  = os.getenv("GOOGLE_CHAT_WEBHOOK", "")
EMAIL_LOOKBACK_HOURS = 18
MAX_EMAILS_PER_BOX   = 15

BASE = Path(__file__).parent

# ── Mailbox definitions ─────────────────────────────────────────────────────────
# Edit this list to match your accounts.
# Gmail:   needs credentials.json (OAuth app) + a unique token file per account
# IMAP:    needs server address + username + password from .env

MAILBOXES = [
    {
        "type":       "gmail",
        "label":      "Gmail (personal)",
        "creds_file": BASE / "credentials.json",
        "token_file": BASE / "token_personal.json",
        "calendar":   True,               # fetch Google Calendar from this account
    },
    {
        "type":       "gmail",
        "label":      "Gmail (work)",
        "creds_file": BASE / "credentials.json",  # same OAuth app, different token
        "token_file": BASE / "token_work.json",
        "calendar":   False,
    },
    {
        "type":     "imap",
        "label":    "Hotmail",
        "server":   "outlook.office365.com",
        "port":     993,
        "username": os.getenv("HOTMAIL_ADDRESS", ""),
        "password": os.getenv("HOTMAIL_PASSWORD", ""),
    },
]


def active_mailboxes() -> list[dict]:
    """Return only mailboxes that are actually configured."""
    result = []
    for box in MAILBOXES:
        if box["type"] == "gmail":
            if box["creds_file"].exists():
                result.append(box)
            else:
                print(f"⚠️   Skipping {box['label']} — credentials.json not found")
        elif box["type"] == "imap":
            if box.get("username") and box.get("password"):
                result.append(box)
            else:
                print(f"⚠️   Skipping {box['label']} — username/password not set in .env")
    return result


# ── Google Auth (one token file per Gmail account) ──────────────────────────────
def get_google_creds(creds_file: Path, token_file: Path) -> Credentials:
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            print(f"\n🔐  Opening browser to authorize: {token_file.stem}")
            flow  = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json())
    return creds


# ── Gmail fetcher ───────────────────────────────────────────────────────────────
def fetch_gmail(box: dict) -> list[dict]:
    creds   = get_google_creds(box["creds_file"], box["token_file"])
    service = build("gmail", "v1", credentials=creds)
    cutoff  = datetime.datetime.utcnow() - datetime.timedelta(hours=EMAIL_LOOKBACK_HOURS)
    query   = f"after:{int(cutoff.timestamp())} -category:promotions -category:social"

    results  = service.users().messages().list(
        userId="me", q=query, maxResults=MAX_EMAILS_PER_BOX
    ).execute()
    messages = results.get("messages", [])
    ignored  = mem.get_preference("ignored_senders") or []
    emails   = []

    for msg in messages:
        uid = f"{box['label']}:{msg['id']}"   # namespace prevents cross-mailbox collisions
        if mem.already_seen_email(uid):
            continue

        full = service.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]
        ).execute()
        headers = {h["name"]: h["value"] for h in full["payload"]["headers"]}
        sender  = headers.get("From", "")
        subject = headers.get("Subject", "(no subject)")
        snippet = full.get("snippet", "")[:200]

        if any(p.lower() in sender.lower() for p in ignored):
            continue

        email_addr, name_part = parse_sender(sender)
        mem.upsert_contact(email_addr, name=name_part, direction="from_them")
        mem.log_event("email_seen", f"[{box['label']}] From {sender}: {subject}",
                      source_id=uid,
                      metadata={"from": sender, "subject": subject, "mailbox": box["label"]})

        contact_info = mem.get_contact(email_addr)
        emails.append({
            "mailbox":  box["label"],
            "from":     sender,
            "email":    email_addr,
            "subject":  subject,
            "snippet":  snippet,
            "priority": contact_info["priority"] if contact_info else "normal",
        })

    return emails


# ── IMAP fetcher (Hotmail / Outlook / Yahoo / any IMAP) ─────────────────────────
def fetch_imap(box: dict) -> list[dict]:
    ignored = mem.get_preference("ignored_senders") or []
    emails  = []

    try:
        mail = imaplib.IMAP4_SSL(box["server"], box["port"])
        mail.login(box["username"], box["password"])
        mail.select("INBOX")

        cutoff   = datetime.datetime.utcnow() - datetime.timedelta(hours=EMAIL_LOOKBACK_HOURS)
        date_str = cutoff.strftime("%d-%b-%Y")
        _, data  = mail.search(None, f'(SINCE "{date_str}")')
        msg_ids  = data[0].split()[-MAX_EMAILS_PER_BOX:]

        for msg_id in reversed(msg_ids):
            uid = f"{box['label']}:{msg_id.decode()}"
            if mem.already_seen_email(uid):
                continue

            _, msg_data = mail.fetch(
                msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
            )
            msg_obj = email.message_from_bytes(msg_data[0][1])
            sender  = decode_mime_str(msg_obj.get("From", ""))
            subject = decode_mime_str(msg_obj.get("Subject", "(no subject)"))

            if any(p.lower() in sender.lower() for p in ignored):
                continue

            # Fetch a short body snippet
            try:
                _, body_data = mail.fetch(msg_id, "(BODY.PEEK[TEXT]<0.400>)")
                snippet = body_data[0][1].decode("utf-8", errors="ignore")[:200].replace("\r\n", " ")
            except Exception:
                snippet = ""

            email_addr, name_part = parse_sender(sender)
            mem.upsert_contact(email_addr, name=name_part, direction="from_them")
            mem.log_event("email_seen", f"[{box['label']}] From {sender}: {subject}",
                          source_id=uid,
                          metadata={"from": sender, "subject": subject, "mailbox": box["label"]})

            contact_info = mem.get_contact(email_addr)
            emails.append({
                "mailbox":  box["label"],
                "from":     sender,
                "email":    email_addr,
                "subject":  subject,
                "snippet":  snippet,
                "priority": contact_info["priority"] if contact_info else "normal",
            })

        mail.logout()

    except imaplib.IMAP4.error as e:
        print(f"⚠️   IMAP login failed for {box['label']}: {e}")
        print(f"     → Check username/password. For Hotmail with 2FA, use an App Password:")
        print(f"       account.microsoft.com → Security → Advanced security → App passwords")
    except Exception as e:
        print(f"⚠️   Failed to fetch {box['label']}: {e}")

    return emails


# ── Google Calendar ─────────────────────────────────────────────────────────────
def fetch_todays_events(box: dict) -> list[dict]:
    creds   = get_google_creds(box["creds_file"], box["token_file"])
    service = build("calendar", "v3", credentials=creds)
    now     = datetime.datetime.utcnow()
    start   = datetime.datetime(now.year, now.month, now.day, 0, 0, 0).isoformat() + "Z"
    end     = datetime.datetime(now.year, now.month, now.day, 23, 59, 59).isoformat() + "Z"

    result = service.events().list(
        calendarId="primary", timeMin=start, timeMax=end,
        singleEvents=True, orderBy="startTime"
    ).execute()

    events = []
    for e in result.get("items", []):
        start_time = e["start"].get("dateTime", e["start"].get("date", ""))
        if "T" in start_time:
            start_time = start_time[11:16]
        events.append({
            "time":     start_time,
            "title":    e.get("summary", "(untitled)"),
            "location": e.get("location", ""),
        })
        mem.log_event("calendar_event", f"{start_time} — {e.get('summary', '')}")
    return events


# ── Helpers ─────────────────────────────────────────────────────────────────────
def parse_sender(sender: str) -> tuple:
    if "<" in sender:
        email_addr = sender.split("<")[1].rstrip(">").strip()
        name_part  = sender.split("<")[0].strip().strip('"') or None
    else:
        email_addr = sender.strip()
        name_part  = None
    return email_addr, name_part


def decode_mime_str(value: str) -> str:
    parts  = decode_header(value)
    result = []
    for part, encoding in parts:
        if isinstance(part, bytes):
            result.append(part.decode(encoding or "utf-8", errors="ignore"))
        else:
            result.append(part)
    return "".join(result)


# ── Claude briefing ─────────────────────────────────────────────────────────────
def generate_briefing(all_emails: list[dict], events: list[dict]) -> str:
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    today   = datetime.date.today().strftime("%A, %B %d %Y")
    weekday = datetime.date.today().strftime("%A")

    memory_context = mem.build_memory_context()
    tone           = mem.get_preference("briefing_tone") or "warm"

    # Group emails by mailbox for a cleaner prompt
    by_mailbox: dict[str, list] = {}
    for e in all_emails:
        by_mailbox.setdefault(e["mailbox"], []).append(e)

    email_block = ""
    for mailbox, emails in by_mailbox.items():
        email_block += f"\n[{mailbox}]\n"
        for e in emails:
            star = "⭐ " if e["priority"] == "high" else ""
            email_block += (
                f"  - {star}From: {e['from']} | "
                f"Subject: {e['subject']} | Preview: {e['snippet']}\n"
            )
    if not email_block.strip():
        email_block = "No new emails across all mailboxes."

    event_block = "\n".join(
        f"- {e['time']} — {e['title']}" + (f" @ {e['location']}" if e["location"] else "")
        for e in events
    ) or "No events today."

    prompt = f"""
You are a smart personal secretary with long-term memory. Today is {today}.

── WHAT YOU ALREADY KNOW (from memory) ──────────────────
{memory_context if memory_context else "No prior memory yet — this may be the first run."}

── TODAY'S CALENDAR ─────────────────────────────────────
{event_block}

── RECENT EMAILS across all mailboxes (last {EMAIL_LOOKBACK_HOURS}h) ──
{email_block}

Write a morning briefing for {YOUR_NAME}. Tone: {tone}.

Structure:
1. One-line greeting for {weekday}
2. Today's schedule (brief)
3. Key emails needing attention (max 4 total, note which mailbox each is from)
4. Overdue follow-ups from memory — name the person and how many days
5. Suggested daily focus

Rules:
- Always note which mailbox an important email came from (e.g. "in your Hotmail")
- Always mention overdue follow-ups from memory even if no new email today
- ⭐ contacts always get flagged when they appear
- Under 350 words, specific names and threads, {tone} tone
"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ── Delivery ────────────────────────────────────────────────────────────────────
def post_to_slack(text: str):
    if not SLACK_WEBHOOK_URL:
        return
    requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10).raise_for_status()
    print("✅  Posted to Slack")


def post_to_google_chat(text: str):
    if not GOOGLE_CHAT_WEBHOOK:
        return
    requests.post(GOOGLE_CHAT_WEBHOOK, json={"text": text}, timeout=10).raise_for_status()
    print("✅  Posted to Google Chat")


def send_email_to_self(text: str):
    import smtplib
    from email.mime.text import MIMEText
    gmail_user = os.getenv("GMAIL_ADDRESS", "")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "")
    if not gmail_user or not gmail_pass:
        return
    today = datetime.date.today().strftime("%A, %b %d")
    msg   = MIMEText(text)
    msg["Subject"] = f"📋 Morning briefing — {today}"
    msg["From"]    = gmail_user
    msg["To"]      = gmail_user
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        server.send_message(msg)
    print("✅  Emailed to yourself")


def send_telegram(text: str):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id   = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10).raise_for_status()
    print("✅  Sent via Telegram")


def save_to_file(text: str):
    today = datetime.date.today().isoformat()
    out   = Path(__file__).parent / "briefings" / f"{today}.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text)
    print(f"✅  Saved to {out}")


# ── Main ────────────────────────────────────────────────────────────────────────
def run():
    print("🤖  Secretary agent starting...")
    mem.init_default_preferences()

    all_emails: list[dict] = []
    all_events: list[dict] = []

    for box in active_mailboxes():
        print(f"📬  Fetching {box['label']}...")
        if box["type"] == "gmail":
            all_emails += fetch_gmail(box)
            if box.get("calendar"):
                all_events += fetch_todays_events(box)
        elif box["type"] == "imap":
            all_emails += fetch_imap(box)

    all_emails.sort(key=lambda e: 0 if e["priority"] == "high" else 1)

    print(f"📧  {len(all_emails)} new emails total  |  📅  {len(all_events)} events")

    briefing = generate_briefing(all_emails, all_events)

    print("\n" + "─" * 60)
    print(briefing)
    print("─" * 60 + "\n")

    mem.save_briefing(briefing, len(all_emails), len(all_events))

    post_to_slack(briefing)
    post_to_google_chat(briefing)
    send_email_to_self(briefing)
    send_telegram(briefing)
    save_to_file(briefing)

    print("✅  Done.")


if __name__ == "__main__":
    run()
