"""
manage_memory.py — CLI for inspecting and teaching the secretary's memory.

Usage:
  python manage_memory.py contacts                        # list all contacts
  python manage_memory.py priority sarah@acme.com high    # mark as high priority
  python manage_memory.py ignore newsletters@company.com  # add to ignore list
  python manage_memory.py overdue                         # show overdue follow-ups
  python manage_memory.py set followup_after_days 7       # change a preference
  python manage_memory.py thread sarah@acme.com "Waiting on contract signature"
"""

import sys
import memory as mem
from datetime import datetime


def cmd_contacts():
    from memory import get_db
    with get_db() as conn:
        rows = conn.execute(
            "SELECT email, name, priority, last_email_from, last_email_to, open_thread "
            "FROM contacts ORDER BY priority DESC, last_email_from DESC"
        ).fetchall()
    if not rows:
        print("No contacts yet.")
        return
    print(f"\n{'EMAIL':<35} {'NAME':<20} {'PRIORITY':<10} {'LAST FROM':<12} {'OPEN THREAD'}")
    print("─" * 100)
    for r in rows:
        last = r["last_email_from"][:10] if r["last_email_from"] else "—"
        thread = (r["open_thread"] or "")[:40]
        print(f"{r['email']:<35} {(r['name'] or ''):<20} {r['priority']:<10} {last:<12} {thread}")


def cmd_priority(email: str, level: str, notes: str = None):
    if level not in ("high", "normal", "low"):
        print("Priority must be: high | normal | low")
        sys.exit(1)
    mem.set_contact_priority(email, level, notes)
    print(f"✅  {email} → priority: {level}")


def cmd_ignore(pattern: str):
    current = mem.get_preference("ignored_senders") or []
    if pattern not in current:
        current.append(pattern)
        mem.set_preference("ignored_senders", current)
    print(f"✅  Added '{pattern}' to ignored senders. Current list: {current}")


def cmd_overdue():
    days    = mem.get_preference("followup_after_days") or 5
    overdue = mem.get_overdue_contacts(days=days)
    if not overdue:
        print(f"No overdue contacts (threshold: {days} days).")
        return
    print(f"\nOverdue follow-ups (>{days} days):\n")
    for c in overdue:
        name  = c["name"] or c["email"]
        since = c["last_email_from"][:10] if c["last_email_from"] else "unknown"
        days_ago = (datetime.utcnow() - datetime.fromisoformat(c["last_email_from"])).days \
                   if c["last_email_from"] else "?"
        thread = f"\n   Thread: {c['open_thread']}" if c["open_thread"] else ""
        tag    = " ⭐" if c["priority"] == "high" else ""
        print(f"  • {name}{tag} — last heard {since} ({days_ago} days ago){thread}")


def cmd_set(key: str, value: str):
    # Try to parse as int/float/list first
    try:
        parsed = int(value)
    except ValueError:
        try:
            parsed = float(value)
        except ValueError:
            parsed = value
    mem.set_preference(key, parsed)
    print(f"✅  Set {key} = {parsed}")


def cmd_thread(email: str, note: str):
    mem.upsert_contact(email, open_thread=note)
    print(f"✅  Open thread for {email}: '{note}'")


def cmd_briefings():
    recent = mem.get_recent_briefings(days=7)
    if not recent:
        print("No briefings stored yet.")
        return
    for b in recent:
        print(f"\n{'─'*60}\n📅  {b['date']}\n{'─'*60}")
        print(b["content"])


HELP = """
Commands:
  contacts                          List all known contacts
  priority <email> <high|normal|low> [notes]   Set contact priority
  ignore <email-or-pattern>         Add to ignored senders
  overdue                           Show overdue follow-ups
  thread <email> "<note>"           Set an open thread note for a contact
  set <key> <value>                 Update a preference
  briefings                         Show recent briefings

Preferences you can set:
  followup_after_days   (int)   Days before flagging as overdue. Default: 5
  briefing_tone         (str)   warm | concise | detailed
  max_emails_in_briefing (int)  Default: 5
"""

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(HELP)
    elif args[0] == "contacts":
        cmd_contacts()
    elif args[0] == "priority" and len(args) >= 3:
        cmd_priority(args[1], args[2], args[3] if len(args) > 3 else None)
    elif args[0] == "ignore" and len(args) >= 2:
        cmd_ignore(args[1])
    elif args[0] == "overdue":
        cmd_overdue()
    elif args[0] == "set" and len(args) >= 3:
        cmd_set(args[1], args[2])
    elif args[0] == "thread" and len(args) >= 3:
        cmd_thread(args[1], " ".join(args[2:]))
    elif args[0] == "briefings":
        cmd_briefings()
    else:
        print(HELP)
