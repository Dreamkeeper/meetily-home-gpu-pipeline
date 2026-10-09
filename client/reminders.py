"""Recording reminders when a call starts, with a one-click "Записать" button.

  python reminders.py register          # register the meetily-record: link handler (current user, no admin)
  python reminders.py test ["Название"] # show a reminder now
  python reminders.py call              # is a call app using the mic right now, and which calendar event is on
  python reminders.py upcoming          # list today's Bitrix24 meetings and whether each would get a reminder
  python reminders.py start "<meetily-record:...>"   # used by the link handler

The watcher polls active_call() every few seconds. When a call app from reminder_rules.json
(`call_apps`, default Zoom) starts using the microphone and Meetily isn't recording, on_call_start()
shows a reminder. This also covers ad-hoc calls that aren't in the calendar. A Bitrix24 meeting running
at that moment names the recording; its name is checked against `skip_name_contains`.
"""
import datetime as dt
import json
import pathlib
import subprocess
import sys
import threading
import time
import urllib.parse
import winreg

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
CALL_APPS = [a.lower() for a in RULES.get("call_apps", ["Zoom.exe"])]
APP_LABELS = {"zoom.exe": "Zoom", "telegram.exe": "Telegram", "weixin.exe": "WeChat", "ms-teams.exe": "Teams"}
# Windows' privacy bookkeeping: per desktop app, LastUsedTimeStop == 0 while it holds the microphone
MIC_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged"
EVENT_SLACK = dt.timedelta(minutes=15)   # calls often start a bit before the calendar slot


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


def apps_using_mic():
    """Lower-case exe names of desktop apps that hold the microphone right now."""
    apps = set()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, MIC_KEY) as root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            sub = winreg.EnumKey(root, i)   # e.g. "C:#Program Files#Zoom#bin#Zoom.exe"
            try:
                with winreg.OpenKey(root, sub) as k:
                    start = winreg.QueryValueEx(k, "LastUsedTimeStart")[0]
                    stop = winreg.QueryValueEx(k, "LastUsedTimeStop")[0]
            except OSError:
                continue
            if start and stop == 0:
                apps.add(sub.rsplit("#", 1)[-1].lower())
    return apps


def _running(exe):
    out = subprocess.run(["tasklist", "/fi", f"imagename eq {exe}", "/nh"], capture_output=True, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    return exe.lower() in out.lower()


def active_call():
    """Call app (exe name) currently using the microphone, or None. The process check guards against
    a stale registry entry left by an app that crashed mid-call."""
    using = apps_using_mic()
    for app in CALL_APPS:
        if app in using and _running(app):
            return app
    return None


def current_event():
    """Name of the accepted Bitrix24 meeting running now (or starting within EVENT_SLACK), else None."""
    now = dt.datetime.now().astimezone()
    for _, name, start, end in todays_meetings():
        if start - EVENT_SLACK <= now <= end:
            return name
    return None


def on_call_start(app):
    """Reminder for a call that just started; returns what was shown, or None."""
    if meetily_recording():
        return None
    try:
        name = current_event()
    except Exception:   # calendar unavailable: still remind, just without a name
        name = None
    if name and skipped(name):
        return None
    prewarm()
    show(name, APP_LABELS.get(app, app))
    return name or APP_LABELS.get(app, app)


def show(name, app="Zoom"):
    from winotify import Notification
    link = f"{PROTOCOL}:{urllib.parse.quote(name or '')}"
    toast = Notification(app_id="Meetily", title=f"Звонок в {app} начался",
                         msg=f"«{name}» — записать в Meetily?" if name else "Записать в Meetily?",
                         duration="long")
    toast.add_actions(label="Записать", launch=link)
    toast.show()


def prewarm():
    """Start loading Whisper on the GPU box (it is unloaded when idle so the GPU stays free for other
    use); a cold load takes up to a minute, and Meetily queues live segments meanwhile."""
    def run():
        try:
            requests.post(f"http://127.0.0.1:{CONFIG['local_port']}/v1/live/warmup", timeout=180)
        except requests.RequestException:
            pass  # GPU box offline: Meetily falls back to local Parakeet
    threading.Thread(target=run, name="prewarm", daemon=True).start()


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
        show(sys.argv[2] if len(sys.argv) > 2 else "Тестовая встреча")
    elif cmd == "call":
        print("call app using the mic:", active_call(), "| calendar event now:", current_event())
    elif cmd == "upcoming":
        for ev_id, name, start, end in todays_meetings():
            print(f"{start:%H:%M}-{end:%H:%M}  {'skip  ' if skipped(name) else 'remind'}  {name}")
    elif cmd == "start":
        start_from_link(sys.argv[2])
