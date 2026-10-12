"""One PDF per meeting for sharing (e.g. in a Bitrix24 chat), named after the meeting folder.

Contents: notes.md, then zh-chatter-summary.md (if any), then cleaned.md, without Obsidian-only syntax
(front matter, [[wiki links]], pointer lines to other vault files). The PDFs go to `share_dir`
(default: "Meeting shares" next to the transcripts folder), outside the vault, so Obsidian doesn't
index every meeting twice. Rendered by Microsoft Edge in headless mode (system fonts cover Cyrillic
and Chinese).

  python share.py "<meeting folder>"   # one meeting (name inside the transcripts folder, or a full path)
  python share.py --all                # every meeting whose PDF is missing or older than its sources

The watcher calls refresh_all() from its notes thread, so PDFs appear after the notes and follow later
edits (once a source file has been left alone for SETTLE_S).
"""
import html
import json
import pathlib
import re
import subprocess
import sys
import time

import markdown

HERE = pathlib.Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
ROOT = pathlib.Path(CONFIG["output_dir"])
SHARE_DIR = pathlib.Path(CONFIG.get("share_dir") or ROOT.parent / "Meeting shares")
EDGE = next((p for p in (pathlib.Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                         pathlib.Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")) if p.exists()), None)
WORK = HERE / "state" / "share"
SETTLE_S = 120   # don't re-render while a file is still being edited (Obsidian autosaves)
PARTS = [("notes.md", None),
         ("zh-chatter-summary.md", "Внутренние разговоры по-китайски — сводка"),
         ("cleaned.md", "Транскрипт (очищенный)")]
VAULT_POINTERS = re.compile(r"\[\[(?:[^\]]*/)?(?:transcript|cleaned|zh-chatter-[a-z]+|whisper-reference)(?:\|[^\]]*)?\]\]")
WIKI = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")

CSS = """
@page { size: A4; margin: 16mm 15mm 16mm 15mm; }
body { font-family: "Segoe UI", "Microsoft YaHei", sans-serif; font-size: 10.5pt; line-height: 1.45; color: #1a1a1a; }
.meta { color: #666; font-size: 9pt; margin-bottom: 1.2em; }
.part { break-before: page; }
.part-title { color: #777; font-size: 9pt; letter-spacing: .08em; text-transform: uppercase; margin: 0 0 .4em; }
h1 { font-size: 17pt; margin: 0 0 .5em; }
h2 { font-size: 13pt; border-bottom: 1px solid #ccc; padding-bottom: 2px; margin-top: 1.4em; }
h3 { font-size: 11.5pt; margin-top: 1.1em; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; margin: .6em 0; }
th, td { border: 1px solid #bbb; padding: 3px 6px; vertical-align: top; text-align: left; }
th { background: #f0f0f0; }
tr { break-inside: avoid; }
blockquote { margin: .3em 0 .3em .8em; padding-left: .6em; border-left: 3px solid #ddd; color: #333; }
code { font-family: Consolas, monospace; font-size: 9.5pt; }
"""


def split_front_matter(text):
    """(front matter dict of simple `key: value` lines, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    front = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
        if m:
            front[m.group(1)] = m.group(2).strip().strip('"')
    return front, text[end + 4:].lstrip("\n")


def clean(body):
    """Obsidian -> plain Markdown for a reader without the vault."""
    lines = [l for l in body.splitlines() if not VAULT_POINTERS.search(l)]   # «Источник: [[transcript]] · …»
    body = "\n".join(lines)
    body = WIKI.sub(lambda m: m.group(2) or m.group(1).removesuffix("/notes").rsplit("/", 1)[-1], body)
    return re.sub(r"\n{3,}", "\n\n", body)


def sources(folder):
    return [folder / name for name, _ in PARTS if (folder / name).exists()]


def pdf_path(folder):
    return SHARE_DIR / f"{folder.name}.pdf"


def build_html(folder):
    md = markdown.Markdown(extensions=["tables", "sane_lists"])
    front, _ = split_front_matter((folder / "notes.md").read_text(encoding="utf-8"))
    meta = " · ".join(x for x in (
        f"{front.get('date', '')} {front.get('time', '')}".strip(),
        f"{front['duration_min']} мин" if front.get("duration_min") else "",
        front.get("participants", "").strip("[]").replace('"', ""),
    ) if x)
    parts = []
    for i, (name, title) in enumerate(PARTS):
        path = folder / name
        if not path.exists():
            continue
        _, body = split_front_matter(path.read_text(encoding="utf-8"))
        md.reset()
        head = f'<p class="part-title">{html.escape(title)}</p>' if title else ""
        parts.append(f'<section class="{"part" if parts else "first"}">{head}{md.convert(clean(body))}</section>')
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>{html.escape(folder.name)}</title>'
            f'<style>{CSS}</style></head><body><div class="meta">{html.escape(meta)}</div>{"".join(parts)}</body></html>')


def make_pdf(folder):
    """Render the meeting's share PDF; returns its path."""
    if EDGE is None:
        raise RuntimeError("Microsoft Edge not found (needed to render the share PDF)")
    folder = pathlib.Path(folder)
    WORK.mkdir(parents=True, exist_ok=True)
    SHARE_DIR.mkdir(parents=True, exist_ok=True)
    page = WORK / "page.html"
    page.write_text(build_html(folder), encoding="utf-8")
    out, tmp = pdf_path(folder), WORK / "page.pdf"
    tmp.unlink(missing_ok=True)
    subprocess.run([str(EDGE), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--user-data-dir={WORK / 'edge-profile'}", f"--print-to-pdf={tmp}", page.as_uri()],
                   timeout=180, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if not tmp.exists() or tmp.stat().st_size == 0:
        raise RuntimeError(f"Edge produced no PDF for {folder.name}")
    tmp.replace(out)
    return out


def needs_update(folder):
    src = sources(folder)
    if not (folder / "notes.md").exists():
        return False
    newest = max(p.stat().st_mtime for p in src)
    if time.time() - newest < SETTLE_S:
        return False
    out = pdf_path(folder)
    return not out.exists() or out.stat().st_mtime < newest


def refresh_all(log=None):
    """(Re)render every meeting whose PDF is missing or stale; returns the rendered paths."""
    done = []
    for folder in sorted(d for d in ROOT.iterdir() if d.is_dir() and re.match(r"\d{4}-\d{2}-\d{2} ", d.name)):
        if needs_update(folder):
            try:
                done.append(make_pdf(folder))
                if log:
                    log.info("share PDF: %s", done[-1].name)
            except Exception:
                if log:
                    log.exception("share PDF failed for %s", folder.name)
                else:
                    raise
    return done


if __name__ == "__main__":
    if sys.argv[1:] == ["--all"]:
        for p in refresh_all():
            print(p)
    else:
        f = pathlib.Path(sys.argv[1])
        print(make_pdf(f if f.is_absolute() else ROOT / f))
