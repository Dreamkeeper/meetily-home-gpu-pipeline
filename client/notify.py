"""Telegram notifications for the meeting pipeline.

  python notify.py setup   # after sending /start to the bot: saves your chat id and sends a test message
  python notify.py test <meeting_out_dir>   # send the "meeting ready" message for an existing meeting

The bot token goes into secrets/telegram_token.txt (from @BotFather). Messages carry a summary only
(title, participants, key points, the owner's tasks); full notes stay local.
"""
import html
import json
import pathlib
import re
import sys
import time

import requests

HERE = pathlib.Path(__file__).resolve().parent
TOKEN_FILE = HERE / "secrets" / "telegram_token.txt"
CHAT_FILE = HERE / "state" / "telegram_chat.json"
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
FALLBACK_PROXY = CONFIG.get("telegram_fallback_proxy", "http://127.0.0.1:7897")
MAX_LEN = 4000  # Telegram limit is 4096


def _token():
    return TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else None


def _api(method, **params):
    """Call the Bot API directly; on network failure retry through the local VPN proxy."""
    url = f"https://api.telegram.org/bot{_token()}/{method}"
    last = None
    for proxies in (None, {"https": FALLBACK_PROXY}):
        for _ in range(2):
            try:
                r = requests.post(url, json=params, timeout=30, proxies=proxies)
                data = r.json()
                if not data.get("ok"):
                    raise RuntimeError(f"Telegram {method}: {data.get('description')}")
                return data["result"]
            except requests.RequestException as exc:
                last = exc
                time.sleep(3)
    raise last


def configured():
    return bool(_token() and CHAT_FILE.exists())


def send(text):
    if not configured():
        return False
    chat = json.loads(CHAT_FILE.read_text(encoding="utf-8"))["chat_id"]
    if len(text) > MAX_LEN:
        text = text[:MAX_LEN].rsplit("\n", 1)[0] + "\n…"
    _api("sendMessage", chat_id=chat, text=text, parse_mode="HTML", disable_web_page_preview=True)
    return True


def _section(md, title):
    m = re.search(rf"^## {re.escape(title)}[^\n]*\n(.*?)(?=^## |\Z)", md, re.S | re.M)
    return m.group(1).strip() if m else ""


def _owner_tasks(md, owner_first):
    tasks = []
    for line in _section(md, "Задачи").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and owner_first in cells[0] and not set(cells[0]) <= set("-: "):
            due = cells[2] if cells[2] and cells[2] != "не указано" else ""
            if due and not due.lower().startswith("до"):
                due = f"до {due}"
            tasks.append(cells[1] + (f" ({due})" if due else ""))
    return tasks


def _md_bold(text):
    return re.sub(r"\*\*(.+?)\*\*", lambda m: f"<b>{m.group(1)}</b>", text)


def meeting_ready(out, rollup_ok=True, fallback_account=None):
    """Compose the 'meeting processed' message from notes.md."""
    out = pathlib.Path(out)
    md = (out / "notes.md").read_text(encoding="utf-8")
    front = md.split("---")[1] if md.startswith("---") else ""
    title = (re.search(r"^# (.+)$", md, re.M) or [None, out.name])[1]
    duration = (re.search(r"duration_min:\s*(\d+)", front) or [None, "?"])[1]
    people = re.search(r"participants:\s*\[(.*)\]", front)
    people = ", ".join(p.strip().strip('"') for p in people.group(1).split(",")) if people else ""
    brief = [l.lstrip("-* ").strip() for l in _section(md, "Кратко").splitlines() if l.strip().startswith(("-", "*"))]
    decisions = [l for l in _section(md, "Ключевые решения").splitlines() if l.strip().startswith(("-", "*", "1", "2"))]
    all_tasks = [l for l in _section(md, "Задачи").splitlines()
                 if l.startswith("|") and not re.match(r"^\|\s*[-:]+", l) and "Ответственный" not in l]
    owner_first = CONFIG.get("owner_name", "").split()[0] if CONFIG.get("owner_name") else ""
    mine = _owner_tasks(md, owner_first) if owner_first else []

    e = html.escape
    lines = [f"✅ <b>{e(title)}</b>", f"{e(out.name)} · {duration} мин"]
    if people:
        lines.append(f"👥 {e(people)}")
    if brief:
        lines += ["", *[f"• {_md_bold(e(b))}" for b in brief[:6]]]
    lines += ["", f"Решений: {len(decisions)} · задач: {len(all_tasks)}"]
    if mine:
        lines += ["", "<b>Ваши задачи:</b>", *[f"☐ {e(t)}" for t in mine[:10]]]
    if fallback_account:
        lines += ["", f"🔁 Заметки сделаны на запасном аккаунте Claude ({e(fallback_account)}): лимит основного исчерпан."]
    if not rollup_ok:
        lines += ["", "⚠️ Сводка (INDEX/tasks/projects) не обновилась, будет повтор."]
    lines += ["", f"<code>{e(str(out / 'notes.md'))}</code>"]
    return send("\n".join(lines))


def failure(meeting, stage, error, attempt):
    e = html.escape
    return send(f"❌ <b>Встреча не обработана</b>\n{e(meeting)}\nЭтап: {e(stage)} (попытка {attempt})\n"
                f"<code>{e(str(error)[:600])}</code>\nПовтор будет автоматически.")


def setup():
    if not _token():
        sys.exit(f"Put the bot token from @BotFather into {TOKEN_FILE}")
    me = _api("getMe")
    updates = _api("getUpdates")
    chats = [u["message"]["chat"] for u in updates if u.get("message", {}).get("chat", {}).get("type") == "private"]
    if not chats:
        sys.exit(f"No messages yet: open t.me/{me['username']}, press Start, then rerun setup")
    chat = chats[-1]
    CHAT_FILE.parent.mkdir(exist_ok=True)
    CHAT_FILE.write_text(json.dumps({"chat_id": chat["id"], "name": chat.get("first_name")}), encoding="utf-8")
    send(f"🔔 Уведомления о встречах подключены (бот @{html.escape(me['username'])}).")
    print(f"ok: bot @{me['username']} -> chat {chat['id']} ({chat.get('first_name')})")


if __name__ == "__main__":
    if sys.argv[1] == "setup":
        setup()
    elif sys.argv[1] == "test":
        print("sent" if meeting_ready(sys.argv[2]) else "not configured")
