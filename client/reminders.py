"""Recording reminders from the Bitrix24 calendar, with a one-click "Записать" button.

  python reminders.py register          # register the meetily-record: link handler (current user, no admin)
  python reminders.py test ["Название"] # show a reminder now
  python reminders.py upcoming          # list today's meetings and whether each would get a reminder
  python reminders.py start "<meetily-record:...>"   # used by the link handler

The watcher calls check(state) every poll. A reminder is shown about a minute before an accepted
Bitrix24 meeting starts, unless its name matches reminder_rules.json or Meetily is already recording.
"""
import datetime as dt
import json
import pathlib
import subprocess
import sys
import threading
import time
import urllib.parse

import requests

import bitrix

HERE = pathlib.Path(__file__).resolve().parent
_rules_file = HERE / "reminder_rules.json"
RULES = json.loads(_rules_file.read_text(encoding="utf-8")) if _rules_file.exists() else {}  # no file: remind for all
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
MEETILY = pathlib.Path.home() / "AppData" / "Local" / "meetily" / "meetily.exe"
PROTOCOL = "meetily-record"
CACHE_S = 600
_cache = {"at": 0.0, "events": []}


def todays_meetings():
    """Accepted Bitrix24 meetings of today as (id, name, start_local, end_local), cached for 10 min."""
    if time.time() - _cache["at"] < CACHE_S:
        return _cache["events"]
    base = bitrix._webhook()
    if not base:
        return []
    me = bitrix._call(base, "profile")
    today = dt.date.today()
    events = []
    for ev in bitrix._call(base, "calendar.event.get",
                           {"type": "user", "ownerId": me["ID"], "from": str(today), "to": str(today)}):
        if not ev.get("IS_MEETING") or ev.get("MEETING_STATUS") == "N" or ev.get("DT_SKIP_TIME") == "Y":
            continue
        start, end = (t.astimezone() for t in bitrix._event_times(ev))
        if start.date() == today:
            events.append((str(ev["ID"]), ev["NAME"], start, end))
    _cache.update(at=time.time(), events=events)
    return events


def skipped(name):
    low = name.lower()
    return any(p.lower() in low for p in RULES.get("skip_name_contains", []))


def meetily_recording():
    """True while Meetily writes a recording (its newest folder's metadata says "recording")."""
    root = pathlib.Path(CONFIG["recordings_dir"])
    folders = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime, reverse=True)
    for d in folders[:2]:
        try:
            meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("status") == "recording" and time.time() - d.stat().st_mtime < 6 * 3600:
            return True
    return False


def show(name, start):
    from winotify import Notification
    link = f"{PROTOCOL}:{urllib.parse.quote(name)}"
    toast = Notification(app_id="Meetily", title=f"Встреча начинается в {start:%H:%M}",
                         msg=f"«{name}» — записать в Meetily?", duration="long")
    toast.add_actions(label="Записать", launch=link)
    toast.show()


def prewarm():
    """Load Whisper on the GPU box ahead of the meeting (it is unloaded when idle so the GPU stays
    free for other use); a cold load takes about a minute, the reminder comes a minute early."""
    def run():
        try:
            requests.post(f"http://127.0.0.1:{CONFIG['local_port']}/v1/live/warmup", timeout=180)
        except requests.RequestException:
            pass  # GPU box offline: Meetily falls back to local Parakeet
    threading.Thread(target=run, name="prewarm", daemon=True).start()


def check(state):
    """Called from the watcher loop; state is a dict persisted by the watcher."""
    sent = state.setdefault("reminded", {})
    now = dt.datetime.now().astimezone()
    lead = dt.timedelta(minutes=RULES.get("minutes_before", 1))
    for ev_id, name, start, end in todays_meetings():
        key = f"{ev_id}@{start:%Y-%m-%dT%H:%M}"
        if key in sent or skipped(name):
            continue
        if start - lead - dt.timedelta(seconds=30) <= now <= start + dt.timedelta(minutes=3):
            sent[key] = now.isoformat()
            if not meetily_recording():
                prewarm()
                show(name, start)
                return name
    # forget yesterday's keys
    for key in [k for k, v in sent.items() if v[:10] < str(dt.date.today())]:
        del sent[key]
    return None


def start_from_link(link):
    """Handle meetily-record:<title>: start Meetily if needed, then ask it to record."""
    title = urllib.parse.unquote(link.split(":", 1)[1]).strip() if ":" in link else ""
    running = subprocess.run(["tasklist", "/fi", "imagename eq meetily.exe"], capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    if "meetily.exe" not in running.lower():
        subprocess.Popen([str(MEETILY)])
        time.sleep(10)  # let the window and frontend come up before the start request
    args = [str(MEETILY), "--start-recording"] + ([f"--meeting-name={title}"] if title else [])
    subprocess.Popen(args)


def register():
    """meetily-record: links -> this script (HKCU, current user only)."""
    import winreg
    pyw = pathlib.Path(sys.executable).with_name("pythonw.exe")
    command = f'"{pyw}" "{pathlib.Path(__file__).resolve()}" start "%1"'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROTOCOL}") as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, "URL:Meetily record")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROTOCOL}\shell\open\command") as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, command)
    print("registered:", command)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "register":
        register()
    elif cmd == "test":
        show(sys.argv[2] if len(sys.argv) > 2 else "Тестовая встреча", dt.datetime.now())
    elif cmd == "upcoming":
        for ev_id, name, start, end in todays_meetings():
            print(f"{start:%H:%M}-{end:%H:%M}  {'skip  ' if skipped(name) else 'remind'}  {name}")
    elif cmd == "start":
        start_from_link(sys.argv[2])
