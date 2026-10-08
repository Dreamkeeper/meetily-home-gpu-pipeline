"""Voice profiles: match diarized speakers to known people by pyannote speaker embeddings.

  python voices.py enroll <meeting_out_dir>   # learn from that folder's speakers.json (confirmed names)
  python voices.py match <meeting_out_dir>    # print matches for a processed meeting
  python voices.py list

Profiles live in state/voices.json (running mean of per-meeting speaker centroids).
Within one meeting, different people scored 0.12-0.62 (two female voices 0.62), so a name is only
applied automatically when the best score is high AND clearly ahead of the runner-up.
"""
import json
import pathlib
import sys
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
PROFILES = HERE / "state" / "voices.json"
AUTO_MIN = 0.70      # tune after a few meetings (see `match` output)
AUTO_MARGIN = 0.10
HINT_MIN = 0.45


def _unit(v):
    v = np.asarray(v, dtype=float)
    return v / (np.linalg.norm(v) or 1.0)


def similarity(a, b):
    if a is None or b is None:
        return None
    return float(_unit(a) @ _unit(b))


def load():
    return json.loads(PROFILES.read_text(encoding="utf-8")) if PROFILES.exists() else {}


def save(profiles):
    PROFILES.parent.mkdir(parents=True, exist_ok=True)
    PROFILES.write_text(json.dumps(profiles, ensure_ascii=False), encoding="utf-8")


def enroll(result, names, meeting_id):
    """names: {"SPEAKER_00": "Имя Фамилия"}; skips unknown/merged speakers."""
    profiles = load()
    for label, name in names.items():
        spk = result["speakers"].get(label)
        if not name or not spk or not spk.get("embedding") or spk["speech_s"] < 30:
            continue
        p = profiles.setdefault(name, {"embedding": None, "n": 0, "meetings": []})
        if meeting_id in p["meetings"]:
            continue
        new = _unit(spk["embedding"])
        mean = new if p["embedding"] is None else _unit(np.array(p["embedding"]) * p["n"] + new)
        p.update(embedding=mean.tolist(), n=p["n"] + 1, updated=time.time())
        p["meetings"].append(meeting_id)
    save(profiles)
    return profiles


def match(result):
    """Returns {label: {"name", "score", "runner_up", "auto"}} for every speaker with an embedding."""
    profiles = {k: v for k, v in load().items() if v.get("embedding")}
    if not profiles:
        return {}
    names = list(profiles)
    P = np.array([_unit(profiles[n]["embedding"]) for n in names])
    out, taken = {}, set()
    rows = []
    for label, spk in result["speakers"].items():
        if spk.get("embedding"):
            scores = P @ _unit(spk["embedding"])
            rows.append((label, scores))
    # greedy one-to-one assignment, strongest pairs first
    pairs = sorted(((s[i], label, i) for label, s in rows for i in range(len(names))), reverse=True)
    best = {}
    for score, label, i in pairs:
        if label in best or i in taken:
            continue
        best[label] = i
        taken.add(i)
    for label, scores in rows:
        if label not in best:
            continue
        i = best[label]
        others = np.delete(scores, i)
        runner = float(others.max()) if len(others) else 0.0
        score = float(scores[i])
        if score < HINT_MIN:
            continue
        out[label] = {"name": names[i], "score": round(score, 2), "runner_up": round(runner, 2),
                      "auto": score >= AUTO_MIN and score - runner >= AUTO_MARGIN}
    return out


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "list":
        for name, p in load().items():
            print(f"{name}: {p['n']} meeting(s)")
        sys.exit()
    folder = pathlib.Path(sys.argv[2])
    result = json.loads((folder / "data.json").read_text(encoding="utf-8"))
    if cmd == "enroll":
        names = json.loads((folder / "speakers.json").read_text(encoding="utf-8"))
        enroll(result, names, folder.name)
        print(f"enrolled from {folder.name}: {sorted(n for n in names.values() if n)}")
    elif cmd == "match":
        print(json.dumps(match(result), ensure_ascii=False, indent=1))
