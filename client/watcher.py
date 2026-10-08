"""Laptop side: watch Meetily recordings, send new ones to the GPU box, render results into the transcripts folder.

Usage:
  python watcher.py            # poll forever (Task Scheduler entry)
  python watcher.py --once     # one pass, then exit
  python watcher.py --folder "Meeting 2026-09-30_11-01-39_2026-09-30_08-01"   # force one meeting
"""
import argparse
import datetime as dt
import json
import logging
import os
import re
import pathlib
import shutil
import socket
import subprocess
import sys
import threading
import time

import requests

import bitrix
import folders
import notify
import reminders
import render

HERE = pathlib.Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
STATE_FILE = HERE / "state" / "state.json"
CACHE = HERE / "state" / "audio"
TOKEN = (HERE / "secrets" / "service_token.txt").read_text(encoding="ascii").strip()
BASE = f"http://127.0.0.1:{CONFIG['local_port']}"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

STALE_RUN_S = 4 * 3600   # a "running" entry older than this is a crashed run

log = logging.getLogger("watcher")

# Telegram failure alerts only once retries suggest a real problem (home PC off, login expired, ...)
ALERT_AT = {"transcription": (3, 6), "notes": (2, 4), "roll-up": (2, 4)}


def alert_failure(name, stage, error, attempt):
    if attempt in ALERT_AT[stage]:
        try:
            notify.failure(name, stage, error, attempt)
        except Exception:
            log.exception("%s: Telegram failure alert not sent", name)


LIMIT_RE = re.compile(r"hit your [\w -]{0,20}limit|usage limit|limit reached|out of (extra )?usage", re.I)
RESET_RE = re.compile(r"(?:resets|reset at|resets at)\s+(?:(?P<mon>[A-Z][a-z]{2})\w*\s+(?P<day>\d{1,2}),?\s+(?:at\s+)?)?"
                      r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>[ap]m)", re.I)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def is_usage_limit(text):
    return bool(LIMIT_RE.search(str(text)))


def usage_limit_reset(exc):
    """If Claude Code stopped on a usage limit, return the earliest reset time (local) it mentions.

    Handles session limits ("resets 2:10pm") and dated weekly ones ("resets Oct 13, 9am"); a limit
    without a readable time is retried in an hour.
    """
    text = str(exc)
    if not is_usage_limit(text):
        return None
    now = dt.datetime.now()
    resets = []
    for m in RESET_RE.finditer(text):
        hour = int(m.group("h")) % 12 + (12 if m.group("ap").lower() == "pm" else 0)
        when = now.replace(hour=hour, minute=int(m.group("m") or 0), second=0, microsecond=0)
        if m.group("mon") and m.group("mon").lower()[:3] in MONTHS:
            when = when.replace(month=MONTHS[m.group("mon").lower()[:3]], day=int(m.group("day")))
            if when < now - dt.timedelta(days=1):
                when = when.replace(year=when.year + 1)
        elif when <= now:
            when += dt.timedelta(days=1)
        resets.append(when)
    return min(resets) if resets else now + dt.timedelta(hours=1)


def handle_usage_limit(name, st, exc, stage, next_try_key):
    """Wait for the limit to reset instead of counting a failure; tell the user once per reset."""
    reset = usage_limit_reset(exc)
    if not reset:
        return False
    st[next_try_key] = reset.timestamp() + 120
    log.warning("%s: %s waits for Claude usage limit reset at %s", name, stage, f"{reset:%H:%M}")
    if st.get("limit_notified") != reset.isoformat():
        try:
            notify.send(f"⏸ Лимит Claude исчерпан: {stage} для встречи {name} будут после {reset:%H:%M}.\n"
                        "Транскрипт уже готов; повтор автоматически.")
            st["limit_notified"] = reset.isoformat()
        except Exception:
            log.exception("%s: Telegram limit message not sent", name)
    return True


def announce(name, st):
    """'Meeting ready' message, once per meeting."""
    if st.get("notified"):
        return
    try:
        primary = claude_accounts()[0]["name"] if claude_accounts() else None
        fallback = st.get("notes_account") if st.get("notes_account") not in (None, primary) else None
        if notify.meeting_ready(st["out_dir"], rollup_ok=bool(st.get("rolled_up")), fallback_account=fallback):
            st["notified"] = True
    except Exception:
        log.exception("%s: Telegram notification not sent", name)


def load_state():
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_FILE)


def candidates(state):
    cutoff = dt.datetime.fromisoformat(CONFIG["process_after"])
    for d in sorted(pathlib.Path(CONFIG["recordings_dir"]).iterdir()):
        meta_path = d / "metadata.json"
        if not d.is_dir() or not meta_path.exists() or not (d / "audio.mp4").exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue  # being written
        if meta.get("status") != "completed" or recording_seconds(d, meta) < CONFIG["min_duration_s"]:
            continue
        if render.meeting_start(meta) < cutoff:
            continue
        st = state.get(d.name, {})
        if st.get("status") in ("done", "notes_pending", "notes_error") or st.get("next_try", 0) > time.time():
            continue
        # another run (e.g. a manual --folder run) owns it; only take over if that run is clearly dead
        if st.get("status") == "running" and time.time() - st.get("started", 0) < STALE_RUN_S:
            continue
        yield d, meta


def recording_seconds(folder, meta):
    """Recording length. Meetily's duration_seconds is unreliable: it is cleared before saving and
    falls back to the last live-transcript timestamp, so it is empty when there was no live transcript."""
    if meta.get("duration_seconds"):
        return meta["duration_seconds"]
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              str(folder / "audio.mp4")], capture_output=True, text=True, timeout=60,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        return float(out)
    except (ValueError, OSError, subprocess.SubprocessError):
        return 0.0


def notes_retries(state):
    """Meetings whose transcript exists but whose notes step has not succeeded yet."""
    for name, st in state.items():
        if st.get("status") in ("notes_pending", "notes_error") and st.get("next_try", 0) <= time.time():
            yield name, st


class Tunnel:
    """ssh -L to the service; the service listens on the GPU box's localhost only."""

    def __enter__(self):
        if self._port_open():
            self.proc = None  # reuse an existing tunnel
            return self
        self.proc = subprocess.Popen(
            ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30",
             "-L", f"{CONFIG['local_port']}:127.0.0.1:8765", CONFIG["ssh_host"]],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(60):
            if self._port_open():
                return self
            if self.proc.poll() is not None:
                break
            time.sleep(1)
        self.__exit__()
        raise ConnectionError("SSH tunnel to GPU box failed (offline?)")

    def __exit__(self, *exc):
        if getattr(self, "proc", None):
            self.proc.terminate()

    @staticmethod
    def _port_open():
        with socket.socket() as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", CONFIG["local_port"])) == 0


def source_audio(folder, meta):
    """Prefer the Meetily fork's unmixed stereo file (L = mic, R = system) when present."""
    stereo = meta.get("audio_file_stereo")
    if stereo and (folder / stereo).exists():
        return folder / stereo, 2
    return folder / "audio.mp4", 1


def tunnel_keeper():
    """Keep the SSH tunnel to the GPU box open permanently: Meetily's Remote Whisper engine streams
    live segments through it during recordings (jobs reuse the same tunnel)."""
    proc = None
    while True:
        try:
            if not Tunnel._port_open():
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                proc = subprocess.Popen(
                    ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                     "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                     "-L", f"{CONFIG['local_port']}:127.0.0.1:8765", CONFIG["ssh_host"]],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                log.info("tunnel keeper: (re)opened SSH tunnel on port %s", CONFIG["local_port"])
        except Exception:
            log.exception("tunnel keeper failed")
        time.sleep(10)


def encode(src, dst, channels=1):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        bitrate = CONFIG["opus_bitrate"] if channels == 1 else CONFIG.get("opus_bitrate_stereo", "48k")
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-vn",
                        "-ac", str(channels), "-ar", "16000", "-c:a", "libopus", "-b:a", bitrate, str(dst)],
                       check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return dst


def glossary_hotwords(limit=400):
    """Canonical terms from glossary.md «Термины» (first column) for Whisper's hotwords prompt."""
    path = pathlib.Path(CONFIG["output_dir"]) / "glossary.md"
    if not path.exists():
        return None
    terms, section = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        elif section == "Термины" and line.startswith("|") and not line.startswith(("|---", "| Термин")):
            cell = line.split("|")[1].replace("✓", "").replace("(?)", "").strip()
            terms += [t.strip() for t in cell.split("/") if t.strip()]
    text = ", ".join(dict.fromkeys(terms))
    return text[:limit].rsplit(",", 1)[0] if len(text) > limit else (text or None)


def run_job(audio):
    health = requests.get(f"{BASE}/health", timeout=30).json()
    if not health.get("gpu"):
        raise RuntimeError(f"GPU box has no GPU available: {health}")
    with audio.open("rb") as f:
        hotwords = glossary_hotwords()
        r = requests.post(f"{BASE}/jobs", headers=HEADERS, files={"file": (audio.name, f, "audio/ogg")},
                          data={"hotwords": hotwords} if hotwords else None, timeout=(30, 1800))
    r.raise_for_status()
    job = r.json()
    log.info("job %s: %s", job["id"], job["status"])
    deadline = time.time() + 3 * 3600
    while job["status"] in ("queued", "running"):
        if time.time() > deadline:
            raise TimeoutError(f"job {job['id']} still {job['status']}")
        time.sleep(15)
        job = requests.get(f"{BASE}/jobs/{job['id']}", headers=HEADERS, timeout=30).json()
    if job["status"] != "done":
        raise RuntimeError(f"job {job['id']} failed: {job.get('error')}")
    r = requests.get(f"{BASE}/jobs/{job['id']}/result", headers=HEADERS, timeout=(30, 600))
    r.raise_for_status()
    return job, r.content


def out_dir_for(meta):
    """Existing folder for this meeting (possibly already renamed with its title), else a new dated one."""
    return folders.find_existing(f"{render.meeting_start(meta):%Y-%m-%d %H-%M}")


def _claude_available():
    cmd = CONFIG.get("notes_cmd")
    return bool(cmd and shutil.which(cmd[0]))


def claude_accounts():
    """Accounts in failover order; one with its own config dir counts only once it has been logged in."""
    usable = []
    for acct in CONFIG.get("claude_accounts") or [{"name": "default", "config_dir": None}]:
        cdir = acct.get("config_dir")
        if cdir and not (pathlib.Path(cdir) / ".claude.json").exists():
            continue
        usable.append(acct)
    return usable


LAST_ACCOUNT = None   # account that produced the most recent successful run


def _run_claude(prompt, out, log_name):
    """Headless Claude Code run with cwd = the transcripts root (it updates the shared files there).

    Tries the configured accounts in order and moves on only when one hits its usage limit;
    if all are limited, raises with every limit message so the earliest reset can be scheduled.
    """
    global LAST_ACCOUNT
    limits, logs = [], []
    for acct in claude_accounts():
        env = dict(os.environ)
        if acct.get("config_dir"):
            env["CLAUDE_CONFIG_DIR"] = acct["config_dir"]
        r = subprocess.run([*CONFIG["notes_cmd"], "--add-dir", str(HERE), "-p", prompt], cwd=out.parent,
                           timeout=3600, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        logs.append(f"=== account: {acct['name']} (exit {r.returncode})\n{r.stdout}\n--- stderr ---\n{r.stderr}")
        (out / log_name).write_text("\n".join(logs), encoding="utf-8")
        if r.returncode == 0:
            if acct is not claude_accounts()[0]:
                log.warning("%s: %s ran on fallback account '%s'", out.name, log_name, acct["name"])
            LAST_ACCOUNT = acct["name"]
            return acct["name"]
        output = (r.stdout + r.stderr)[-400:]
        if not is_usage_limit(output):
            raise RuntimeError(f"{log_name} step failed on account {acct['name']} (exit {r.returncode}): {output[-300:]}")
        limits.append(f"[{acct['name']}] {output.strip()}")
        log.warning("%s: account '%s' hit its usage limit; trying the next one", out.name, acct["name"])
    raise RuntimeError(f"{log_name}: all Claude accounts hit their usage limit: " + " | ".join(limits))


def run_notes(out):
    """Cleaned transcript + notes. Returns False if Claude Code is not configured."""
    if not _claude_available():
        return False
    _run_claude(f"Read {HERE / 'notes_prompt.md'} and follow it for the meeting folder \"{out}\". "
                "Write cleaned.md and notes.md there and update people.md. "
                f"Recording owner: {CONFIG.get('owner_name', 'unknown')}.", out, "notes.log")
    if not (out / "notes.md").exists():
        raise RuntimeError("notes step finished without writing notes.md")
    return True


def run_rollup(out):
    """Fold one meeting into INDEX.md, tasks.md, glossary.md, people.md and projects/*.md."""
    root = out.parent
    before = (root / "INDEX.md").read_text(encoding="utf-8") if (root / "INDEX.md").exists() else ""
    _run_claude(f"Read {HERE / 'rollup_prompt.md'} and follow it for the meeting folder \"{out}\".",
                out, "rollup.log")
    index = (root / "INDEX.md").read_text(encoding="utf-8") if (root / "INDEX.md").exists() else ""
    if f"[[{out.name}/notes]]" not in index:
        raise RuntimeError("roll-up finished but INDEX.md has no row for this meeting")
    return index != before


def rollup_step(name, st):
    try:
        run_rollup(pathlib.Path(st["out_dir"]))
        st["rolled_up"] = True
        st.pop("rollup_error", None)
        log.info("%s: rolled up into INDEX/tasks/projects", name)
    except Exception as exc:
        if handle_usage_limit(name, st, exc, "сводка", "rollup_next_try"):
            st["rollup_error"] = f"usage limit: {exc}"
            return
        st["rollup_attempts"] = st.get("rollup_attempts", 0) + 1
        delay = min(6 * 3600, 600 * 2 ** (st["rollup_attempts"] - 1))
        st.update(rollup_error=f"{type(exc).__name__}: {exc}", rollup_next_try=time.time() + delay)
        log.exception("%s: roll-up failed (attempt %d), retry in %ds", name, st["rollup_attempts"], delay)
        alert_failure(name, "roll-up", exc, st["rollup_attempts"])


def rollup_retries(state):
    """Done meetings not yet folded into the shared files, oldest first (later meetings close tasks)."""
    pending = [(name, st) for name, st in state.items()
               if st.get("status") == "done" and not st.get("rolled_up")
               and st.get("rollup_next_try", 0) <= time.time() and st.get("out_dir")]
    return sorted(pending, key=lambda item: pathlib.Path(item[1]["out_dir"]).name)


def notes_step(name, st):
    try:
        st["status"] = "done" if run_notes(pathlib.Path(st["out_dir"])) else "notes_pending"
        st["notes_account"] = LAST_ACCOUNT
        st.pop("notes_error", None)
        if st["status"] == "done":
            try:  # "<date time> <short title>" before the roll-up writes links to the folder
                st["out_dir"] = str(folders.apply_title(pathlib.Path(st["out_dir"])))
            except Exception:
                log.exception("%s: folder rename failed; keeping %s", name, st["out_dir"])
            st["rolled_up"] = False
            rollup_step(name, st)
            announce(name, st)
    except Exception as exc:
        if handle_usage_limit(name, st, exc, "заметки", "next_try"):
            st.update(status="notes_error", notes_error=f"usage limit: {exc}")
            return
        st["notes_attempts"] = st.get("notes_attempts", 0) + 1
        delay = min(6 * 3600, 600 * 2 ** (st["notes_attempts"] - 1))
        st.update(status="notes_error", notes_error=f"{type(exc).__name__}: {exc}", next_try=time.time() + delay)
        log.exception("%s: notes failed (attempt %d), retry in %ds", name, st["notes_attempts"], delay)
        alert_failure(name, "notes", exc, st["notes_attempts"])


def process(folder, meta, state, out=None):
    name = folder.name
    st = state.setdefault(name, {"attempts": 0})
    st.update(status="running", started=time.time())
    save_state(state)
    try:
        src, channels = source_audio(folder, meta)
        audio = encode(src, CACHE / f"{name}.opus", channels)
        with Tunnel():
            job, result = run_job(audio)
        out = pathlib.Path(out) if out else out_dir_for(meta)
        out.mkdir(parents=True, exist_ok=True)
        result_path = out / "data.json"
        result_path.write_bytes(result)
        render.main(result_path, folder, out)
        st.update(job_id=job["id"], out_dir=str(out), timings=job.get("timings"), error=None)
        log.info("%s -> %s", name, out)
        (CACHE / f"{name}.opus").unlink(missing_ok=True)
        try:
            info = bitrix.save_participants(out, meta)
            if info:
                log.info("%s: Bitrix24 event %r, %d participants", name, info["event"], len(info["participants"]))
        except Exception:  # participants are a hint for the notes step, never a blocker
            log.exception("%s: Bitrix24 lookup failed", name)
        notes_step(name, st)
    except Exception as exc:
        st["attempts"] += 1
        delay = min(3600, 120 * 2 ** (st["attempts"] - 1))
        st.update(status="error", error=f"{type(exc).__name__}: {exc}", next_try=time.time() + delay)
        log.exception("%s failed (attempt %d), retry in %ds", name, st["attempts"], delay)
        alert_failure(name, "transcription", exc, st["attempts"])
    finally:
        save_state(state)


def reminder_step():
    """Recording reminder for meetings starting now (Bitrix24 calendar); never blocks processing."""
    path = HERE / "state" / "reminders.json"
    try:
        rstate = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        name = reminders.check(rstate)
        path.write_text(json.dumps(rstate, ensure_ascii=False, indent=1), encoding="utf-8")
        if name:
            log.info("recording reminder shown: %s", name)
    except Exception:
        log.exception("recording reminder check failed")


def single_instance():
    """Hold a localhost port for the process lifetime; a second watcher fails to bind and exits."""
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", CONFIG["local_port"] - 1))
        return sock
    except OSError:
        sock.close()
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--folder")
    ap.add_argument("--out", help="with --folder: write here instead of the transcripts folder (testing)")
    args = ap.parse_args()
    (HERE / "state").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(HERE / "state" / "watcher.log", encoding="utf-8"),
                                  *([logging.StreamHandler(sys.stdout)] if sys.stdout else [])])
    guard = single_instance() if not args.folder else None
    if args.folder is None and guard is None:
        log.info("another watcher is already running; exiting")
        return
    state = load_state()
    if args.folder:
        folder = pathlib.Path(CONFIG["recordings_dir"]) / args.folder
        process(folder, json.loads((folder / "metadata.json").read_text(encoding="utf-8")), state, args.out)
        return
    threading.Thread(target=tunnel_keeper, name="tunnel-keeper", daemon=True).start()
    while True:
        reminder_step()
        for folder, meta in list(candidates(state)):
            process(folder, meta, state)
        for name, st in list(notes_retries(state)):
            notes_step(name, st)
            save_state(state)
        for name, st in rollup_retries(state):
            rollup_step(name, st)
            save_state(state)
        if args.once:
            return
        time.sleep(CONFIG["poll_s"])


if __name__ == "__main__":
    main()
