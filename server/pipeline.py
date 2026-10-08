"""Diarize-then-transcribe pipeline: pyannote community-1 -> per-turn language routing -> ASR, Whisper as reference.

Each chunk's language is detected (Whisper large-v3, restricted to LANGS); Russian goes to GigaAM-v3,
English and Chinese to Whisper large-v3 (GigaAM is Russian-only and turns English into gibberish).

Stereo input (Meetily fork "separate channels": L = microphone, R = system audio) is diarized on the
downmix; every chunk keeps its diarization label and is tagged with the dominant channel. Mic-dominant
chunks are transcribed from the mic channel, system-dominant ones from the system channel (clean audio
during crosstalk). Per-speaker channel totals let the client pick the owner's cluster: the mic also picks
up people sitting next to the owner, so "mic-dominant" alone does not mean "owner".

Usage: python pipeline.py <audio> <out.json>
"""
import json
import os
import re
import zlib
import pathlib
import subprocess
import sys
import time

import numpy as np
import torch

os.add_dll_directory(str(pathlib.Path(torch.__file__).parent / "lib"))

SR = 16000
DEVICE = "cuda"
MAX_CHUNK_S = 20.0      # GigaAM works best on <25 s inputs
MERGE_GAP_S = 0.8       # join same-speaker turns separated by short pauses
MIN_TURN_S = 0.3
PAD_S = 0.1
LANGS = ("ru", "en", "zh")   # languages expected in our meetings; detection is restricted to these
LID_MIN_S = 1.5              # shorter chunks inherit the language of the same speaker's nearest chunk
LID_NEIGHBOR_S = 60.0
ZH_PROMPT = "以下是普通话会议的转录。"  # keeps Whisper in simplified Chinese
LID_SURE = 0.8               # below this, decode with the top-2 languages and keep the more confident one
# Whisper's well-known hallucinations on noise/short clips (YouTube outros, subtitle credits)
HALLUCINATIONS = re.compile(
    r"点赞|订阅|转发|打赏|明镜|字幕|感谢观看|谢谢观看|请不吝|"
    r"субтитр|DimaTorzok|продолжение следует|спасибо за просмотр|подписывайтесь|"
    r"thank(s| you) for watching|subscribe|like and share|amara\.org", re.I)
CHANNEL_DOMINANCE = 2.0  # RMS ratio (~6 dB) for a chunk to count as mic-only / system-only

_models = {}


def log(msg):
    print(f"[pipeline] {msg}", flush=True)


def channel_count(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels",
                          "-of", "csv=p=0", str(path)], check=True, capture_output=True, text=True).stdout
    return int(out.strip() or 1)


def load_audio(path, channels=1):
    """float32 audio at 16 kHz: shape (n,) for mono, (n, 2) for stereo."""
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-f", "s16le", "-ac", str(channels),
         "-ar", str(SR), "-"],
        check=True, capture_output=True).stdout
    audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    return audio.reshape(-1, channels) if channels > 1 else audio


def diarizer():
    if "diar" not in _models:
        from pyannote.audio import Pipeline
        from pyannote.audio.core.task import Problem, Resolution, Specifications
        # torch>=2.6 loads checkpoints with weights_only=True; allowlist pyannote's metadata classes.
        torch.serialization.add_safe_globals([Specifications, Problem, Resolution, torch.torch_version.TorchVersion])
        p =Pipeline.from_pretrained("pyannote/speaker-diarization-community-1")
        _models["diar"] = p.to(torch.device(DEVICE))
    return _models["diar"]


def gigaam():
    if "gigaam" not in _models:
        from transformers import AutoModel
        m = AutoModel.from_pretrained("ai-sage/GigaAM-v3", revision="e2e_rnnt", trust_remote_code=True).model
        _models["gigaam"] = m.to(DEVICE).eval()
    return _models["gigaam"]


def whisper():
    if "whisper" not in _models:
        from faster_whisper import BatchedInferencePipeline, WhisperModel
        _models["whisper"] = BatchedInferencePipeline(WhisperModel("large-v3", device=DEVICE, compute_type="float16"))
    return _models["whisper"]


def diarize(audio, num_speakers=None, min_speakers=None, max_speakers=None):
    wave = torch.from_numpy(audio).unsqueeze(0)
    kwargs = {k: v for k, v in dict(num_speakers=num_speakers, min_speakers=min_speakers,
                                    max_speakers=max_speakers).items() if v}
    out = diarizer()({"waveform": wave, "sample_rate": SR}, **kwargs)
    ann = out.exclusive_speaker_diarization
    turns = [{"start": float(s.start), "end": float(s.end), "speaker": spk}
             for s, _, spk in ann.itertracks(yield_label=True) if s.duration >= MIN_TURN_S]
    turns.sort(key=lambda t: t["start"])
    labels = sorted(out.speaker_diarization.labels())
    emb = out.speaker_embeddings
    speakers = {}
    for i, lab in enumerate(labels):
        total = sum(t["end"] - t["start"] for t in turns if t["speaker"] == lab)
        speakers[lab] = {"speech_s": round(total, 1),
                         "embedding": emb[i].tolist() if emb is not None and i < len(emb) else None}
    return turns, speakers


def merge_turns(turns):
    merged = []
    for t in turns:
        last = merged[-1] if merged else None
        if (last and last["speaker"] == t["speaker"] and t["start"] - last["end"] <= MERGE_GAP_S
                and t["end"] - last["start"] <= MAX_CHUNK_S):
            last["end"] = t["end"]
        else:
            merged.append(dict(t))
    return merged


def split_long(audio, turn):
    """Split a turn longer than MAX_CHUNK_S at pauses found by Silero VAD."""
    if turn["end"] - turn["start"] <= MAX_CHUNK_S + 5:
        return [turn]
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    s0 = int(turn["start"] * SR)
    region = audio[s0:int(turn["end"] * SR)]
    parts = get_speech_timestamps(region, VadOptions(max_speech_duration_s=MAX_CHUNK_S,
                                                     min_silence_duration_ms=250, speech_pad_ms=100))
    if not parts:
        return [turn]
    return [{"start": turn["start"] + p["start"] / SR, "end": turn["start"] + p["end"] / SR,
             "speaker": turn["speaker"]} for p in parts]


def attribute_channel(stereo, a, b):
    """'mic' / 'system' / 'mixed' for the stereo slice [a, b) by RMS dominance."""
    rms = np.sqrt(np.mean(np.square(stereo[a:b]), axis=0) + 1e-10)
    ratio = rms[0] / rms[1]
    if ratio >= CHANNEL_DOMINANCE:
        return "mic"
    if ratio <= 1 / CHANNEL_DOMINANCE:
        return "system"
    return "mixed"


def detect_language(wm, source):
    """(lang, prob, ranked candidates) restricted to LANGS, renormalized."""
    _, _, all_probs = wm.detect_language(audio=source)
    probs = {lang: p for lang, p in all_probs if lang in LANGS}
    total = sum(probs.values()) or 1.0
    ranked = sorted(probs, key=probs.get, reverse=True) or ["ru"]
    return ranked[0], probs.get(ranked[0], 0.0) / total, ranked


def whisper_decode(wm, source, lang, hotwords=None, beam_size=5):
    """(text, avg_logprob, no_speech_prob) for one chunk in a fixed language."""
    segs, _ = wm.transcribe(source, language=lang, beam_size=beam_size, vad_filter=False,
                            condition_on_previous_text=False, without_timestamps=True,
                            initial_prompt=ZH_PROMPT if lang == "zh" else None,
                            hotwords=hotwords if lang == "en" else None)
    segs = list(segs)
    if not segs:
        return "", -10.0, 1.0
    text = " ".join(seg.text.strip() for seg in segs).strip()
    weight = [max(1, len(seg.text)) for seg in segs]
    logprob = sum(seg.avg_logprob * w for seg, w in zip(segs, weight)) / sum(weight)
    return text, logprob, max(seg.no_speech_prob for seg in segs)


def is_hallucination(text, logprob=None, no_speech=None):
    if not text or HALLUCINATIONS.search(text):
        return True
    raw = text.encode("utf-8")
    if len(raw) > 40 and len(raw) / len(zlib.compress(raw)) > 2.6:  # "we can do a lot... we can do a lot..."
        return True
    return no_speech is not None and logprob is not None and no_speech > 0.6 and logprob < -1.0


def fill_short_languages(chunks):
    """Short chunks: language of the same speaker's nearest detected chunk, else the speaker's majority."""
    by_speaker = {}
    for c in chunks:
        if c.get("lang"):
            by_speaker.setdefault(c["speaker"], []).append(c)
    for c in chunks:
        if c.get("lang"):
            continue
        same = by_speaker.get(c["speaker"], [])
        near = min(same, key=lambda o: abs(o["start"] - c["start"]), default=None)
        if near and abs(near["start"] - c["start"]) <= LID_NEIGHBOR_S:
            c["lang"], c["lang_p"] = near["lang"], None
        elif same:
            totals = {}
            for o in same:
                totals[o["lang"]] = totals.get(o["lang"], 0) + o["end"] - o["start"]
            c["lang"], c["lang_p"] = max(totals, key=totals.get), None
        else:
            c["lang"], c["lang_p"] = "ru", None


@torch.inference_mode()
def transcribe_turns(audio, turns, stereo=None, hotwords=None):
    m, wm = gigaam(), whisper().model
    chunks = []
    for t in turns:
        a = int(max(0.0, t["start"] - PAD_S) * SR)
        b = int(min(len(audio) / SR, t["end"] + PAD_S) * SR)
        c, source = dict(t), audio[a:b]
        if stereo is not None:
            c["channel"] = attribute_channel(stereo, a, b)
            if c["channel"] == "mic":
                source = stereo[a:b, 0]
            elif c["channel"] == "system":
                source = stereo[a:b, 1]
        c["_source"] = np.ascontiguousarray(source)
        if t["end"] - t["start"] >= LID_MIN_S:
            c["lang"], c["lang_p"], ranked = detect_language(wm, c["_source"])
            if c["lang_p"] < LID_SURE and len(ranked) > 1:
                # unsure: let the decoder decide between the two best candidates
                scores = {lang: whisper_decode(wm, c["_source"], lang, beam_size=1)[1] for lang in ranked[:2]}
                c["lang"], c["lid"] = max(scores, key=scores.get), "decode"
        chunks.append(c)
    fill_short_languages(chunks)

    out, dropped = [], []
    for c in chunks:
        source = c.pop("_source")
        if c["lang"] == "ru":
            wav = torch.from_numpy(source).to(DEVICE).unsqueeze(0)
            length = torch.full([1], wav.shape[-1], device=DEVICE)
            enc, enc_len = m.forward(wav, length)
            text, c["asr"] = m.decoding.decode(m.head, enc, enc_len)[0].strip(), "gigaam"
        else:
            text, logprob, no_speech = whisper_decode(wm, source, c["lang"], hotwords)
            c["asr"] = "whisper"
            if is_hallucination(text, logprob, no_speech):
                dropped.append({"start": round(c["start"], 2), "lang": c["lang"], "text": text})
                continue
        if text:
            if c.get("lang_p") is not None:
                c["lang_p"] = round(c["lang_p"], 2)
            out.append({**{k: round(v, 2) if isinstance(v, float) else v for k, v in c.items()}, "text": text})
    if dropped:
        log(f"dropped {len(dropped)} Whisper hallucinations, e.g. {[d['text'][:40] for d in dropped[:3]]}")
    return out


def whisper_reference(audio, hotwords=None):
    # hotwords: canonical terms from the client's glossary (brands, products) to bias spellings
    segs, _ = whisper().transcribe(audio, language="ru", batch_size=16, vad_filter=True,
                                   condition_on_previous_text=False, beam_size=5, hotwords=hotwords or None)
    return [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segs]


def process(audio_path, num_speakers=None, min_speakers=None, max_speakers=None, with_whisper=True,
            hotwords=None):
    timings = {}
    t = time.perf_counter()
    stereo = load_audio(audio_path, 2) if channel_count(audio_path) >= 2 else None
    audio = stereo.mean(axis=1) if stereo is not None else load_audio(audio_path)
    timings["load"] = time.perf_counter() - t
    duration = len(audio) / SR
    log(f"audio {duration / 60:.1f} min, {'stereo (L=mic, R=system)' if stereo is not None else 'mono'}")

    t = time.perf_counter()
    raw_turns, speakers = diarize(audio, num_speakers, min_speakers, max_speakers)
    timings["diarize"] = time.perf_counter() - t
    log(f"diarized: {len(speakers)} speakers, {len(raw_turns)} turns in {timings['diarize']:.0f}s")

    chunks = [c for turn in merge_turns(raw_turns) for c in split_long(audio, turn)]
    t = time.perf_counter()
    segments = transcribe_turns(audio, chunks, stereo, hotwords)
    for lab, info in speakers.items():
        info["lang_s"] = {lang: round(sum(s["end"] - s["start"] for s in segments
                                          if s["speaker"] == lab and s["lang"] == lang), 1) for lang in LANGS}
    if stereo is not None:
        for lab, info in speakers.items():
            info["channel_s"] = {ch: round(sum(s["end"] - s["start"] for s in segments
                                               if s["speaker"] == lab and s.get("channel") == ch), 1)
                                 for ch in ("mic", "system", "mixed")}
    timings["asr"] = time.perf_counter() - t
    langs = {lang: sum(1 for s in segments if s["lang"] == lang) for lang in LANGS}
    log(f"asr: {len(segments)} segments {langs} in {timings['asr']:.0f}s")

    reference = []
    if with_whisper:
        t = time.perf_counter()
        reference = whisper_reference(audio, hotwords)
        timings["whisper"] = time.perf_counter() - t
        log(f"whisper reference: {len(reference)} segments in {timings['whisper']:.0f}s")

    return {"version": 3, "duration_s": round(duration, 2), "stereo": stereo is not None,
            "models": {"diarization": "pyannote/speaker-diarization-community-1",
                       "asr": {"ru": "ai-sage/GigaAM-v3@e2e_rnnt", "en": "whisper-large-v3", "zh": "whisper-large-v3"},
                       "language_id": "whisper-large-v3 (restricted to ru/en/zh)",
                       "reference_asr": "whisper-large-v3" if with_whisper else None},
            "speakers": speakers, "segments": segments, "whisper_reference": reference,
            "timings_s": {k: round(v, 1) for k, v in timings.items()}}


if __name__ == "__main__":
    result = process(sys.argv[1])
    pathlib.Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"wrote {sys.argv[2]} timings={result['timings_s']}")
