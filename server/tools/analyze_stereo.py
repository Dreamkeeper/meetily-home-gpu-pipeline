"""How much does the stereo (L = mic, R = system) recording improve speaker attribution?

With a headset the mic channel carries only the owner, so channel energy is near ground truth for
"owner vs everyone else". We diarize the downmix (what a mono recording gets) and score how well
pyannote alone finds the owner; then diarize the system channel alone to see whether removing the
owner's voice changes how the remote speakers are separated.

Usage: python analyze_stereo.py <stereo audio> [...]
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))  # noqa: E401
import json
import sys
from collections import defaultdict

import numpy as np

import pipeline as P


def turns_with_channel(stereo, turns):
    out = []
    for t in P.merge_turns(turns):
        for c in P.split_long(stereo.mean(axis=1), t):
            a, b = int(c["start"] * P.SR), int(c["end"] * P.SR)
            if b - a < int(0.3 * P.SR):
                continue
            out.append({**c, "dur": c["end"] - c["start"], "channel": P.attribute_channel(stereo, a, b)})
    return out


def analyze(path):
    stereo = P.load_audio(path, 2)
    mono = stereo.mean(axis=1)
    turns, speakers = P.diarize(mono)
    chunks = turns_with_channel(stereo, turns)

    table = defaultdict(lambda: defaultdict(float))  # diar label -> channel -> seconds
    for c in chunks:
        table[c["speaker"]][c["channel"]] += c["dur"]
    mic_total = sum(v["mic"] for v in table.values())
    sys_total = sum(v["system"] for v in table.values())
    mixed_total = sum(v["mixed"] for v in table.values())
    owner = max(table, key=lambda k: table[k]["mic"])
    owner_total = sum(table[owner].values())

    # owner's speech in the mic channel that mono diarization put into other clusters
    mic_elsewhere = mic_total - table[owner]["mic"]
    # remote speech (system channel) that mono diarization put into the owner's cluster
    sys_in_owner = table[owner]["system"]

    # system channel alone: remote speakers without the owner's voice
    sys_turns, sys_speakers = P.diarize(stereo[:, 1].copy())
    remote_mono = [k for k in table if k != owner and table[k]["system"] > 30]
    remote_sys = [k for k, v in sys_speakers.items() if v["speech_s"] > 30]

    return {
        "file": str(path), "minutes": round(len(mono) / P.SR / 60, 1),
        "seconds_by_channel": {"mic(owner)": round(mic_total), "system(remote)": round(sys_total),
                                "mixed(overlap/unclear)": round(mixed_total)},
        "mono_diarization_table_s": {k: {ch: round(s) for ch, s in v.items()} for k, v in sorted(table.items())},
        "owner_cluster": owner,
        "owner_recall": round(table[owner]["mic"] / mic_total, 3) if mic_total else None,
        "owner_precision": round(table[owner]["mic"] / owner_total, 3) if owner_total else None,
        "owner_speech_in_other_clusters_s": round(mic_elsewhere),
        "remote_speech_in_owner_cluster_s": round(sys_in_owner),
        "remote_speakers_>30s": {"mono_downmix": len(remote_mono), "system_channel_only": len(remote_sys)},
        "system_only_speakers_s": {k: v["speech_s"] for k, v in sorted(sys_speakers.items())},
    }


if __name__ == "__main__":
    for f in sys.argv[1:]:
        print(json.dumps(analyze(f), ensure_ascii=False, indent=1), flush=True)
