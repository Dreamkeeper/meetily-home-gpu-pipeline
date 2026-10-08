"""Meeting folder names: "<YYYY-MM-DD HH-MM> <short title>", with links in the archive kept in sync.

  python folders.py plan      # show the renames for existing folders
  python folders.py apply     # rename existing folders and rewrite links (stop the watcher first)
"""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
ROOT = pathlib.Path(CONFIG["output_dir"])
STATE = HERE / "state" / "state.json"
PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}-\d{2}")
MAX_TITLE = 60


def prefix(name):
    m = PREFIX_RE.match(name)
    return m.group(0) if m else None


def sanitize(title):
    title = re.sub(r'[:/\\?*<>|"«»]', " ", title)
    title = re.sub(r"\s+", " ", title).strip(" .-—")
    if len(title) > MAX_TITLE:
        title = title[:MAX_TITLE].rsplit(" ", 1)[0].rstrip(" ,.-—")
    return title


def short_title(folder):
    """short_title from notes.md front matter; else the notes heading up to its first colon."""
    notes = folder / "notes.md"
    if not notes.exists():
        return None
    text = notes.read_text(encoding="utf-8")
    front = text.split("---")[1] if text.startswith("---") else ""
    m = re.search(r'^short_title:\s*"?(.+?)"?\s*$', front, re.M)
    if m:
        return sanitize(m.group(1))
    h = re.search(r"^# (.+)$", text, re.M)
    if not h:
        return None
    title = sanitize(re.split(r"[:;]\s", h.group(1))[0])
    return re.sub(r"(\s+\S{1,2})+$", "", title)  # don't end on a dangling preposition after truncation


def target_name(folder):
    p, title = prefix(folder.name), short_title(folder)
    return f"{p} {title}" if p and title else folder.name


def find_existing(stamp):
    """Folder for a meeting time stamp "YYYY-MM-DD HH-MM", renamed or not."""
    matches = sorted(ROOT.glob(f"{stamp}*"))
    return matches[0] if matches else ROOT / stamp


def relink(old, new):
    """Rewrite references to a folder name in every .md file of the archive and in the watcher state."""
    pattern = re.compile(re.escape(old) + r"(?=[/\\\]|`\"'),.;]|$)", re.M)
    changed = 0
    for md in ROOT.rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        updated = pattern.sub(new, text)
        if updated != text:
            md.write_text(updated, encoding="utf-8")
            changed += 1
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8"))
        for st in state.values():
            if st.get("out_dir") and pathlib.Path(st["out_dir"]).name == old:
                st["out_dir"] = str(ROOT / new)
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return changed


def apply_title(folder):
    """Rename one meeting folder to its titled name; returns the (possibly new) path."""
    folder = pathlib.Path(folder)
    new_name = target_name(folder)
    if new_name == folder.name or (ROOT / new_name).exists():
        return folder
    folder.rename(ROOT / new_name)
    relink(folder.name, new_name)
    return ROOT / new_name


if __name__ == "__main__":
    folders = sorted(d for d in ROOT.iterdir() if d.is_dir() and prefix(d.name))
    for d in folders:
        new = target_name(d)
        if new == d.name:
            continue
        if sys.argv[1] == "apply":
            apply_title(d)
        print(f"{d.name}  ->  {new}")
