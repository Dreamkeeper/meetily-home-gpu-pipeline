"""Render the server's pipeline JSON into Markdown files inside a meeting folder.

Usage: python render.py <result.json> <meetily_meeting_dir> <out_dir>
Writes: transcript.md (GigaAM, speaker-attributed), whisper-reference.md, data.json
"""
import datetime as dt
import json
import pathlib
import shutil
import sys

import voices

OWNER = json.loads((pathlib.Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8")).get(
    "owner_name", "Я")


def ts(s):
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


OWNER_MIC_SHARE = 0.6    # a cluster counts as "local" when most of its speech is louder on the mic
OWNER_VOICE_MIN = 0.6    # ...and as the owner only when it also matches the owner's voice profile


def owner_clusters(result):
    """Diarization clusters that are the recording owner, using the stereo channel totals.

    The mic channel carries the owner but also anyone sitting next to them (measured: a colleague's
    7 minutes in a room meeting came through the mic), so a mic-heavy cluster is the owner only if it
    also matches the owner's voice profile. Without a profile, fall back to the most mic-heavy cluster.
    Returns (labels, details) where details goes into the transcript front matter.
    """
    local = {}
    for label, spk in result["speakers"].items():
        ch = spk.get("channel_s")
        if not ch:
            continue
        total = sum(ch.values()) or 1.0
        if ch["mic"] >= 30 and ch["mic"] / total >= OWNER_MIC_SHARE:
            local[label] = spk
    if not local:
        return [], {}
    profile = voices.load().get(OWNER, {}).get("embedding")
    details = {}
    for label, spk in local.items():
        score = voices.similarity(profile, spk.get("embedding")) if profile else None
        details[label] = {"mic_s": spk["channel_s"]["mic"], "voice": None if score is None else round(score, 2)}
    if profile:
        owners = [k for k, d in details.items() if d["voice"] is not None and d["voice"] >= OWNER_VOICE_MIN]
    else:
        owners = [max(local, key=lambda k: local[k]["channel_s"]["mic"])]
    for k, d in details.items():
        d["role"] = "owner" if k in owners else "local (in the room)"
    return owners, details


def speaker_langs(spk):
    """' · en 3.2 мин, zh 1.1 мин' for speakers who spoke anything other than Russian."""
    langs = {k: v for k, v in (spk.get("lang_s") or {}).items() if v >= 5}
    if not langs or set(langs) == {"ru"}:
        return ""
    return " · " + ", ".join(f"{k} {v / 60:.1f} мин" for k, v in sorted(langs.items(), key=lambda kv: -kv[1]))


def speaker_label(raw, names):
    return names.get(raw, raw.replace("SPEAKER_", "Спикер "))


def meeting_start(meta):
    """Meetily stores UTC; convert to local time."""
    created = dt.datetime.fromisoformat(meta["created_at"][:26] + "+00:00")
    return created.astimezone()


def render(result, meta, names=None, matches=None, local=None):
    names = names or {}
    matches = matches or {}
    local = local or {}
    start = meeting_start(meta)
    speakers = sorted(result["speakers"].items(), key=lambda kv: -kv[1]["speech_s"])
    front = [
        "---",
        f"date: {start:%Y-%m-%d}",
        f"time: \"{start:%H:%M}\"",
        f"duration_min: {round(result['duration_s'] / 60)}",
        "type: meeting-transcript",
        f"source: \"{meta.get('meeting_name', '')}\"",
        "asr: " + (", ".join(f"{lang}={model}" for lang, model in result["models"]["asr"].items())
                   if isinstance(result["models"]["asr"], dict) else result["models"]["asr"]),
        f"diarization: {result['models']['diarization']}",
        "speakers:",
        *[f"  - \"{speaker_label(k, names)}\"  # {v['speech_s'] / 60:.1f} мин речи{speaker_langs(v)}" for k, v in speakers],
        "voice_matches:  # cosine vs saved voice profiles; auto=true means the name above came from the voice",
        *[f"  {k}: {{name: \"{m['name']}\", score: {m['score']}, runner_up: {m['runner_up']}, auto: {str(m['auto']).lower()}}}"
          for k, m in sorted(matches.items())],
        "local_speakers:  # stereo: clusters mostly on the owner's mic; role=owner needs a voice match too",
        *[f"  {k}: {{role: \"{d['role']}\", mic_s: {d['mic_s']}, voice_vs_owner: {d['voice']}}}"
          for k, d in sorted(local.items())],
        "---",
        "",
        f"# Транскрипт {start:%Y-%m-%d %H:%M}",
        "",
    ]
    body = []
    prev = None
    for seg in result["segments"]:
        who = speaker_label(seg["speaker"], names)
        lang = seg.get("lang", "ru")
        if (who, lang) != prev:  # a language switch starts a new line, so translations stay aligned
            tag = "" if lang == "ru" else f" ({lang})"
            body.append(f"\n**[{ts(seg['start'])}] {who}{tag}:** {seg['text']}")
            prev = (who, lang)
        else:
            body[-1] += " " + seg["text"]
    return "\n".join(front) + "\n" + "\n".join(body).lstrip("\n") + "\n"


def render_reference(result):
    lines = ["# Whisper large-v3 (справочный вариант, без спикеров)", "",
             "Используется только для сверки английских терминов и имён.", ""]
    lines += [f"[{ts(s['start'])}] {s['text']}" for s in result["whisper_reference"]]
    return "\n".join(lines) + "\n"


def main(result_path, meeting_dir, out_dir):
    result = json.loads(pathlib.Path(result_path).read_text(encoding="utf-8"))
    meta = json.loads((pathlib.Path(meeting_dir) / "metadata.json").read_text(encoding="utf-8"))
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    names_file = out / "speakers.json"
    confirmed = json.loads(names_file.read_text(encoding="utf-8")) if names_file.exists() else {}
    matches = voices.match(result)
    names = {k: m["name"] for k, m in matches.items() if m["auto"]}
    names.update({k: v for k, v in confirmed.items() if v})
    names.setdefault("SELF", OWNER)  # data.json v1 (before 2026-10-05) labelled all mic-dominant speech SELF
    owners, local = owner_clusters(result)
    for label in owners:
        if label not in confirmed:
            names[label] = OWNER
    (out / "transcript.md").write_text(render(result, meta, names, matches, local), encoding="utf-8")
    if result.get("whisper_reference"):
        (out / "whisper-reference.md").write_text(render_reference(result), encoding="utf-8")
    if pathlib.Path(result_path).resolve() != (out / "data.json").resolve():
        shutil.copyfile(result_path, out / "data.json")
    print(out)


if __name__ == "__main__":
    main(*sys.argv[1:4])
