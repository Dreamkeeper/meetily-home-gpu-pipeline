"""Voice messages and audio files -> transcript note in the vault + Telegram reply.

Two ways in, one processing path:
- Forward a voice message or audio file to the notifications bot. poll_telegram() long-polls the bot (only the
  owner's private chat is accepted), downloads the file (Bot API limit: 20 MB) into the inbox folder with a
  sidecar .json (sender, time, the message to reply to) and acknowledges it.
- Drop any audio file into <output_dir>\\_voice-inbox\\ (Telegram Desktop "Save as", WeChat/WhatsApp exports,
  files over 20 MB).

process_inbox() handles both: GPU box job (diarization + ru/en/zh routing) -> the raw transcript is written to
<output_dir>\\Голосовые\\<YYYY-MM-DD HH-MM> <sender>.md at once (with the audio next to it) -> headless Claude
(voice_prompt.md, sonnet) fixes names/terms from the glossary and adds a short summary -> Telegram reply.
A failed item stays in the inbox and is retried. Voice notes are not rolled up into tasks.md/INDEX.md
(Dmitry, 2026-10-28).

  python voice.py <audio file> [sender]   # process one file by hand (result also sent to Telegram)
"""
import datetime as dt
import html
import json
import pathlib
import re
import shutil
import sys
import time

import requests

import folders
import notify

HERE = pathlib.Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
ROOT = pathlib.Path(CONFIG["output_dir"])
INBOX = pathlib.Path(CONFIG.get("voice_inbox_dir") or ROOT / "_voice-inbox")
NOTES = pathlib.Path(CONFIG.get("voice_dir") or ROOT / "Голосовые")
STATE = HERE / "state" / "voice_telegram.json"
BASE = f"http://127.0.0.1:{CONFIG['local_port']}"
AUDIO_EXT = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".mp4", ".webm", ".aac", ".flac", ".amr", ".wma"}
MAX_BOT_FILE = 20 * 1024 * 1024
RETRY_S = (120, 600, 1800, 3600)


def _headers():
    return {"Authorization": "Bearer " + (HERE / "secrets" / "service_token.txt").read_text(encoding="ascii").strip()}


def _owner_chat():
    return json.loads(notify.CHAT_FILE.read_text(encoding="utf-8"))["chat_id"] if notify.CHAT_FILE.exists() else None


def _fmt_dur(seconds):
    s = int(round(seconds or 0))
    return f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------- Telegram -> inbox
def _known_name(*keys):
    """Bots see the sender's own profile name, not the owner's contact name for them, so map it via
    `telegram_names` in config.json: Telegram user id, "@username" or profile name -> canonical name."""
    names = CONFIG.get("telegram_names") or {}
    return next((names[k] for k in keys if k and k in names), None)


def _sender_and_time(msg):
    origin = msg.get("forward_origin") or {}
    kind = origin.get("type")
    if kind == "user":
        u = origin.get("sender_user", {})
        name = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or u.get("username") or "Telegram"
        name = _known_name(str(u.get("id")), u.get("username") and "@" + u["username"], name) or name
    elif kind == "hidden_user":
        name = origin.get("sender_user_name") or "Telegram"
        name = _known_name(name) or name
    elif kind in ("chat", "channel"):
        chat = origin.get("sender_chat") or origin.get("chat") or {}
        name = chat.get("title") or origin.get("author_signature") or "Telegram"
    else:
        name = CONFIG.get("owner_name") or "Я"
    return name, dt.datetime.fromtimestamp(origin.get("date") or msg["date"]).astimezone()


def _attachment(msg):
    for key in ("voice", "audio", "video_note"):
        if msg.get(key):
            return msg[key], key
    doc = msg.get("document") or {}
    if (doc.get("mime_type") or "").startswith("audio/") or pathlib.Path(doc.get("file_name") or "").suffix.lower() in AUDIO_EXT:
        return doc, "document"
    return None, None


def _reply(chat, msg_id, text, parse_mode=None):
    params = dict(chat_id=chat, text=text[:notify.MAX_LEN], disable_web_page_preview=True)
    if parse_mode:
        params["parse_mode"] = parse_mode
    if msg_id:
        params["reply_parameters"] = {"message_id": msg_id, "allow_sending_without_reply": True}
    return notify._api("sendMessage", **params)


def poll_telegram(log, timeout=25):
    """One long-poll round: forwarded voice/audio from the owner -> inbox file + sidecar, acknowledged."""
    chat = _owner_chat()
    if not chat:
        time.sleep(timeout)
        return
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"offset": 0}
    updates = notify._api("getUpdates", offset=state["offset"], timeout=timeout, allowed_updates=["message"])
    for u in updates:
        state["offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        if msg.get("chat", {}).get("id") != chat:
            continue   # only the owner's private chat
        att, kind = _attachment(msg)
        if not att:
            if not (msg.get("text") or "").startswith("/"):
                _reply(chat, msg["message_id"], "Перешлите сюда голосовое или аудиофайл — расшифрую и сохраню в «Голосовые».")
            continue
        if (att.get("file_size") or 0) > MAX_BOT_FILE:
            _reply(chat, msg["message_id"], f"Файл больше 20 МБ — боту его не скачать. Положите его в {INBOX}.")
            continue
        sender, sent_at = _sender_and_time(msg)
        ext = pathlib.Path(att.get("file_name") or "").suffix.lower() or (".mp4" if kind == "video_note" else ".ogg")
        INBOX.mkdir(parents=True, exist_ok=True)
        dst = INBOX / f"tg-{msg['message_id']}{ext}"
        info = notify._api("getFile", file_id=att["file_id"])
        url = f"https://api.telegram.org/file/bot{notify._token()}/{info['file_path']}"
        data = None
        for _, proxies in notify.routes():
            try:
                r = requests.get(url, timeout=120, proxies=proxies)
                r.raise_for_status()
                data = r.content
                break
            except requests.RequestException:
                continue
        if data is None:
            _reply(chat, msg["message_id"], "Не удалось скачать файл из Telegram, перешлите ещё раз.")
            continue
        dst.with_suffix(dst.suffix + ".part").write_bytes(data)
        dst.with_suffix(".json").write_text(json.dumps({
            "sender": sender, "sent_at": sent_at.isoformat(), "chat_id": chat, "reply_to": msg["message_id"],
            "duration": att.get("duration")}, ensure_ascii=False), encoding="utf-8")
        dst.with_suffix(dst.suffix + ".part").replace(dst)
        _reply(chat, msg["message_id"], f"⏳ Расшифровываю ({_fmt_dur(att.get('duration'))})…")
        u = (msg.get("forward_origin") or {}).get("sender_user") or {}
        log.info("voice: queued Telegram message %s from %s (tg id %s, @%s)", msg["message_id"], sender,
                 u.get("id"), u.get("username"))
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state), encoding="utf-8")


# ---------------------------------------------------------------- inbox -> note + reply
def transcribe(path):
    """GPU box job for one file; returns the result dict (same service as meetings, polled every 3 s)."""
    with path.open("rb") as f:
        r = requests.post(f"{BASE}/jobs", headers=_headers(), files={"file": (path.name, f, "audio/ogg")}, timeout=(30, 600))
    r.raise_for_status()
    job = r.json()
    deadline = time.time() + 3600
    while job["status"] in ("queued", "running"):
        if time.time() > deadline:
            raise TimeoutError(f"job {job['id']} still {job['status']}")
        time.sleep(3)
        job = requests.get(f"{BASE}/jobs/{job['id']}", headers=_headers(), timeout=30).json()
    if job["status"] != "done":
        raise RuntimeError(f"job {job['id']} failed: {job.get('error')}")
    r = requests.get(f"{BASE}/jobs/{job['id']}/result", headers=_headers(), timeout=(30, 300))
    r.raise_for_status()
    return r.json()


def raw_text(data):
    """Plain transcript: paragraphs per speaker turn; speaker labels only when there is more than one voice."""
    segs = data.get("segments") or []
    many = len({s["speaker"] for s in segs}) > 1
    out, last = [], None
    for s in segs:
        tag = f" ({s['lang']})" if s.get("lang") in ("en", "zh") else ""
        stamp = f"[{int(s['start']) // 60:02d}:{int(s['start']) % 60:02d}]"
        if s["speaker"] != last or not out:
            label = f"**Спикер {s['speaker'][-2:]}** " if many else ""
            out.append(f"{label}{stamp}{tag} {s['text'].strip()}")
        else:
            out[-1] += f" {s['text'].strip()}"
        last = s["speaker"]
    return "\n\n".join(out)


def _section(md, title):
    m = re.search(rf"^## {re.escape(title)}[^\n]*\n(.*?)(?=^## |\Z)", md, re.S | re.M)
    return m.group(1).strip() if m else ""


def _html(md_text):
    """Escape for Telegram HTML and keep the note's **bold** (speaker labels)."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(md_text, quote=False))


def _chunks(text, size=3300):
    """Split the transcript at paragraph boundaries into pieces that fit one message after HTML escaping."""
    out, cur = [], ""
    for para in text.split("\n\n"):
        while len(para) > size:            # a single huge paragraph: cut at a sentence end if possible
            cut = para.rfind(". ", 0, size) + 1 or size
            out.append((cur + "\n\n" + para[:cut]).strip() if cur else para[:cut].strip())
            cur, para = "", para[cut:].strip()
        if cur and len(cur) + len(para) + 2 > size:
            out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


def _send_result(meta, note, duration):
    """Reply in Telegram (HTML): bold title and meta line, «Кратко» as bullets, then the transcript in a
    collapsible quote, so the summary and the text are clearly apart; long transcripts continue in more
    messages. Files dropped into the inbox get a plain message instead of a reply."""
    if not notify.configured():
        return
    md = note.read_text(encoding="utf-8")
    title = (re.search(r"^# (.+)$", md, re.M) or [None, note.stem])[1]
    bullets = [l.lstrip("-* ").strip() for l in _section(md, "Кратко").splitlines() if l.strip().startswith(("-", "*"))]
    text = _section(md, "Текст") or _section(md, "Расшифровка (сырая)")
    text = re.sub(r"^!\[\[.*?\]\]\s*$", "", text, flags=re.M).strip()
    when = dt.datetime.fromisoformat(meta["sent_at"])
    head = (f"🎙 <b>{_html(title)}</b>\n"
            f"<i>{html.escape(meta['sender'])} · {when:%d.%m %H:%M} · {_fmt_dur(duration)}</i>")
    summary = ("<b>📝 Кратко</b>\n" + "\n".join(f"• {_html(b)}" for b in bullets)) if bullets else ""
    parts = _chunks(text) if text else []
    n = len(parts)
    blocks = [f"<b>📄 Расшифровка{f' ({i}/{n})' if n > 1 else ''}</b>\n<blockquote expandable>{_html(t)}</blockquote>"
              for i, t in enumerate(parts, 1)]
    first = "\n\n".join(x for x in (head, summary) if x)
    if blocks and len(first) + len(blocks[0]) + 2 <= notify.MAX_LEN:
        first = f"{first}\n\n{blocks.pop(0)}"
    messages = [first] + blocks
    chat = meta.get("chat_id") or _owner_chat()
    for i, m in enumerate(messages):
        _reply(chat, meta.get("reply_to") if i == 0 else None, m, parse_mode="HTML")


def _unique(base):
    name, n = base, 2
    while (NOTES / f"{name}.md").exists():
        name, n = f"{base} ({n})", n + 1
    return name


def process_file(audio, meta, run_claude, log):
    """One audio file: transcribe, write the note (raw first), clean up with Claude, reply. Raises on failure."""
    data = transcribe(audio)
    duration = data.get("duration_s") or meta.get("duration") or 0
    sent_at = dt.datetime.fromisoformat(meta["sent_at"])
    NOTES.mkdir(parents=True, exist_ok=True)
    (NOTES / "_logs").mkdir(exist_ok=True)
    base = _unique(f"{sent_at:%Y-%m-%d %H-%M} {folders.sanitize(meta['sender'])[:40]}")
    audio_dst = NOTES / f"{base}{audio.suffix.lower()}"
    shutil.copyfile(audio, audio_dst)
    note = NOTES / f"{base}.md"
    note.write_text(
        "---\n"
        f"type: voice-note\ndate: {sent_at:%Y-%m-%d}\ntime: \"{sent_at:%H:%M}\"\n"
        f"sender: \"{meta['sender']}\"\nduration_s: {round(duration)}\nsource: {meta.get('source', 'telegram')}\n"
        f"audio: \"{audio_dst.name}\"\n---\n"
        f"# Голосовое: {meta['sender']}, {sent_at:%d.%m.%Y %H:%M}\n\n![[{audio_dst.name}]]\n\n"
        f"## Расшифровка (сырая)\n\n{raw_text(data)}\n", encoding="utf-8")
    try:
        run_claude(f"Read {HERE / 'voice_prompt.md'} and follow it for the voice note \"{note}\". "
                   f"Rewrite that file in place. Recording owner: {CONFIG.get('owner_name', 'unknown')}.",
                   NOTES, f"_logs/{base}.log", model=CONFIG.get("voice_model", "sonnet"))
    except Exception:
        log.exception("voice: clean-up failed for %s; keeping the raw transcript", note.name)
    _send_result(meta, note, duration)
    log.info("voice: %s -> %s", audio.name, note.name)
    return note


def process_inbox(run_claude, log):
    """Every settled audio file in the inbox (Telegram downloads and dropped files), oldest first."""
    if not INBOX.exists():
        INBOX.mkdir(parents=True, exist_ok=True)
        return
    now = time.time()
    for audio in sorted(INBOX.iterdir(), key=lambda p: p.stat().st_mtime):
        if audio.suffix.lower() not in AUDIO_EXT or now - audio.stat().st_mtime < 5:
            continue
        side = audio.with_suffix(".json")
        meta = json.loads(side.read_text(encoding="utf-8")) if side.exists() else {
            "sender": audio.stem, "source": "folder",
            "sent_at": dt.datetime.fromtimestamp(audio.stat().st_mtime).astimezone().isoformat()}
        if meta.get("next_try", 0) > now:
            continue
        try:
            process_file(audio, meta, run_claude, log)
        except Exception as exc:
            meta["attempts"] = meta.get("attempts", 0) + 1
            meta["next_try"] = now + RETRY_S[min(meta["attempts"], len(RETRY_S)) - 1]
            side.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            log.warning("voice: %s failed (attempt %d), retry later: %s", audio.name, meta["attempts"], exc)
            if meta["attempts"] == 1 and meta.get("chat_id"):
                _reply(meta["chat_id"], meta.get("reply_to"),
                       "⚠️ Не получилось расшифровать (GPU-сервер недоступен?). Повторю автоматически.")
            continue
        audio.unlink()
        side.unlink(missing_ok=True)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    sys.path.insert(0, str(HERE))
    import watcher
    src = pathlib.Path(sys.argv[1])
    meta = {"sender": sys.argv[2] if len(sys.argv) > 2 else src.stem, "source": "manual",
            "sent_at": dt.datetime.fromtimestamp(src.stat().st_mtime).astimezone().isoformat()}
    print(process_file(src, meta, watcher._run_claude, logging.getLogger("voice")))
