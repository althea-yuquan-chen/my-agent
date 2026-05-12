# AI Secretary Agent — Morning Briefing

Runs every morning. Reads your Gmail + Google Calendar, asks Claude to write
a personal briefing, and posts it to Slack and/or Google Chat.

---

## What you get every morning

```
Good morning Alex! Happy Tuesday.

📅 Today's schedule
• 10:00 — Weekly sync with the team
• 14:00 — Customer call: Acme Corp
• 16:30 — 1:1 with mentor

📧 Emails needing your attention
1. [URGENT] Sarah (sarah@acme.com) — contract renewal deadline Friday
2. John re: "Q3 proposal" — waiting on your feedback since yesterday
3. Investor update request from Mark — you haven't replied in 5 days

🎯 Suggested focus today
Block 1h before the Acme call to review the contract. The rest of the
afternoon is clear — good time to clear the proposal backlog.

🔁 Follow-ups
• Reply to Mark's investor update (5 days overdue)
• Send Sarah the signed contract before Friday
```

---

## Setup (30 minutes first time)

### 1. Install dependencies

```bash
cd secretary-agent
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Get a Google OAuth credentials file

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or use existing)
3. Enable these APIs:
   - **Gmail API** — search "Gmail API" → Enable
   - **Google Calendar API** — search "Google Calendar API" → Enable
4. Go to **APIs & Services → Credentials**
5. Click **Create Credentials → OAuth 2.0 Client ID**
6. Application type: **Desktop app**
7. Download the JSON file → save it as `credentials.json` in this folder

> First run will open a browser asking you to log in with your Google account.
> After that, `token.json` is saved and it runs silently forever.

### 3. Set up environment variables

```bash
cp .env.example .env
# Edit .env with your actual values
```

You need at minimum:
- `ANTHROPIC_API_KEY` — from [console.anthropic.com](https://console.anthropic.com)
- `YOUR_NAME` — your first name for the greeting
- At least one of `SLACK_WEBHOOK_URL` or `GOOGLE_CHAT_WEBHOOK`

**Getting a Slack webhook:**
1. Go to [api.slack.com/apps](https://api.slack.com/apps) → Create New App → From scratch
2. Features → Incoming Webhooks → Turn on → Add New Webhook to Workspace
3. Pick a channel (e.g. a private `#my-secretary` channel just for you)
4. Copy the webhook URL into `.env`

**Getting a Google Chat webhook:**
1. Open Google Chat → open a Space (or create one called "Secretary")
2. Click the space name → Apps & integrations → Webhooks → Add webhook
3. Name it "Secretary", copy the URL into `.env`

### 4. Test it manually

```bash
source venv/bin/activate
python secretary.py
```

The first run opens a browser for Google login. After that it's fully automatic.
Briefings are also saved locally in the `briefings/` folder.

### 5. Schedule it to run every morning

**macOS — launchd (recommended):**

```bash
# Edit the plist below to match your actual paths, then:
cp com.secretary.agent.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.secretary.agent.plist
```

`com.secretary.agent.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.secretary.agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/FULL/PATH/TO/secretary-agent/venv/bin/python</string>
    <string>/FULL/PATH/TO/secretary-agent/secretary.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>8</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>EnvironmentVariables</key>
  <dict>
    <key>ANTHROPIC_API_KEY</key><string>YOUR_KEY_HERE</string>
    <key>YOUR_NAME</key><string>YOUR_NAME_HERE</string>
    <key>SLACK_WEBHOOK_URL</key><string>YOUR_SLACK_WEBHOOK</string>
    <key>GOOGLE_CHAT_WEBHOOK</key><string>YOUR_CHAT_WEBHOOK</string>
  </dict>
  <key>StandardOutPath</key>
  <string>/tmp/secretary-agent.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/secretary-agent-err.log</string>
</dict>
</plist>
```

**Linux — cron:**
```bash
crontab -e
# Add this line (runs at 8am daily):
0 8 * * * cd /path/to/secretary-agent && venv/bin/python secretary.py >> /tmp/secretary.log 2>&1
```

**Windows — Task Scheduler:**
1. Open Task Scheduler → Create Basic Task
2. Trigger: Daily at 8:00am
3. Action: Start a program → `C:\path\to\venv\Scripts\python.exe`
4. Arguments: `C:\path\to\secretary.py`

---

## Costs

- **Anthropic API**: ~$0.002 per briefing (less than half a cent/day)
- **Google APIs**: Free within quota (Gmail and Calendar are both free for personal use)
- **Slack/Google Chat webhooks**: Free

Monthly cost: roughly **$0.06**. 

---

## What to build next (for your manager)

Once this works for yourself, the next additions are:

1. **Customer follow-up tracker** — a small SQLite table tracking last-contact per person,
   so the agent can say "You haven't replied to Alice in 8 days."
2. **Draft reply suggestions** — for emails flagged as needing action, have Claude
   pre-write a draft and save it to Gmail Drafts via the Gmail API.
3. **Google Chat integration** — make the agent a 2-way Chat bot so your manager
   can reply and the agent responds.

Each is a 1-2 day addition on top of this foundation.
