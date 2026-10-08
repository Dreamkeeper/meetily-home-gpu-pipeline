"""Bitrix24: find the calendar event a recording belongs to and save its participants.

Needs an incoming webhook (Bitrix24 -> Разработчикам -> Другое -> Входящий вебхук,
permissions: calendar, user). Put its URL (https://<portal>/rest/<id>/<key>/) into
secrets/bitrix_webhook.txt. Without that file this module does nothing.

  python bitrix.py <meeting_out_dir> <meetily_meeting_dir>   # manual run for one meeting
"""
import datetime as dt
import json
import pathlib
import re
import sys

import requests

import render

HERE = pathlib.Path(__file__).resolve().parent
WEBHOOK_FILE = HERE / "secrets" / "bitrix_webhook.txt"
MATCH_SLACK = dt.timedelta(minutes=20)   # recordings often start a bit before/after the slot


def _webhook():
    if not WEBHOOK_FILE.exists():
        return None
    url = WEBHOOK_FILE.read_text(encoding="utf-8-sig").strip()
    # Bitrix24 shows the webhook with an example method appended (".../profile.json"); keep the base
    url = re.sub(r"/[a-z0-9_.]+\.json$", "", url, flags=re.I)
    return url if url.endswith("/") else url + "/"


def _call(base, method, params=None):
    r = requests.post(f"{base}{method}.json", json=params or {}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Bitrix24 {method}: {data.get('error_description') or data['error']}")
    return data["result"]


def _event_times(ev):
    """UTC start/end of this occurrence.

    Recurring events come back expanded, one entry per occurrence: DATE_FROM/DATE_TO are the
    occurrence's local times in TZ_FROM, while *_TS_UTC describe the whole series (e.g. up to 2038),
    so they must not be used for matching.
    """
    fmt = "%d.%m.%Y %H:%M:%S"
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(ev.get("TZ_FROM") or "Europe/Moscow")
    except Exception:  # no tzdata on this Python: fall back to the laptop's zone
        tz = dt.datetime.now().astimezone().tzinfo
    start = dt.datetime.strptime(ev["DATE_FROM"], fmt).replace(tzinfo=tz)
    end = dt.datetime.strptime(ev["DATE_TO"], fmt).replace(tzinfo=tz)
    return start.astimezone(dt.timezone.utc), end.astimezone(dt.timezone.utc)


def find_participants(meta):
    """Return participants dict for the event overlapping the recording, or None."""
    base = _webhook()
    if not base:
        return None
    rec_start = render.meeting_start(meta).astimezone(dt.timezone.utc)
    rec_end = rec_start + dt.timedelta(seconds=meta.get("duration_seconds") or 0)
    me = _call(base, "profile")
    day = rec_start.astimezone()
    events = _call(base, "calendar.event.get", {
        "type": "user", "ownerId": me["ID"],
        "from": (day - dt.timedelta(days=1)).strftime("%Y-%m-%d"),
        "to": (day + dt.timedelta(days=1)).strftime("%Y-%m-%d"),
    })
    best, best_overlap = None, dt.timedelta(0)
    for ev in events:
        # only real meetings the owner hasn't declined ("Обед" etc. are personal blocks)
        if not ev.get("IS_MEETING") or ev.get("MEETING_STATUS") == "N" or ev.get("DT_SKIP_TIME") == "Y":
            continue
        start, end = _event_times(ev)
        overlap = min(end, rec_end) - max(start - MATCH_SLACK, rec_start)
        if overlap > best_overlap:
            best, best_overlap = ev, overlap
    if not best:
        return None
    attendees = best.get("ATTENDEE_LIST") or []
    ids = [a["id"] for a in attendees] or [best.get("OWNER_ID")]
    users = {u["ID"]: u for u in _call(base, "user.get", {"ID": ids})} if ids else {}
    start, end = _event_times(best)
    return {
        "source": "bitrix24",
        "event": best.get("NAME"),
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "participants": [
            {"name": f"{users.get(str(a['id']), {}).get('NAME', '')} {users.get(str(a['id']), {}).get('LAST_NAME', '')}".strip(),
             "position": users.get(str(a["id"]), {}).get("WORK_POSITION") or None,
             "status": a.get("status")}  # Y = accepted, N = declined, Q = no answer
            for a in attendees
        ],
    }


def save_participants(out_dir, meta):
    info = find_participants(meta)
    if info:
        (pathlib.Path(out_dir) / "participants.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return info


if __name__ == "__main__":
    out, meeting = map(pathlib.Path, sys.argv[1:3])
    meta = json.loads((meeting / "metadata.json").read_text(encoding="utf-8"))
    print(json.dumps(save_participants(out, meta), ensure_ascii=False, indent=1))
