"""One-off benchmark: Whisper large-v3 vs GigaAM-v3 on the same audio (no diarization)."""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))  # noqa: E401
import json
import os
import pathlib
import subprocess
import sys
import time

import numpy as np
import torch

os.add_dll_directory(str(pathlib.Path(torch.__file__).parent / "lib"))
SR = 16000


def load(path):
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
        check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def ts(s):
    return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"


def bench_whisper(audio, out):
    from faster_whisper import BatchedInferencePipeline, WhisperModel
    t0 = time.perf_counter()
    model = WhisperModel("large-v3", device="cuda", compute_type="float16")
    pipe = BatchedInferencePipeline(model)
    t1 = time.perf_counter()
    segs, _ = pipe.transcribe(audio, language="ru", batch_size=16, vad_filter=True,
                              condition_on_previous_text=False, beam_size=5)
    lines = [f"[{ts(s.start)}] {s.text.strip()}" for s in segs]
    t2 = time.perf_counter()
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    del pipe, model
    torch.cuda.empty_cache()
    return t1 - t0, t2 - t1


def bench_gigaam(audio, out):
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    from transformers import AutoModel
    t0 = time.perf_counter()
    m = AutoModel.from_pretrained("ai-sage/GigaAM-v3", revision="e2e_rnnt", trust_remote_code=True).model
    m = m.to("cuda").eval()
    t1 = time.perf_counter()
    chunks = get_speech_timestamps(audio, VadOptions(max_speech_duration_s=22, min_silence_duration_ms=300,
                                                     speech_pad_ms=200))
    lines = []
    with torch.inference_mode():
        for c in chunks:
            wav = torch.from_numpy(audio[c["start"]:c["end"]]).to("cuda").unsqueeze(0)
            length = torch.full([1], wav.shape[-1], device="cuda")
            enc, enc_len = m.forward(wav, length)
            text = m.decoding.decode(m.head, enc, enc_len)[0]
            if text.strip():
                lines.append(f"[{ts(c['start'] / SR)}] {text.strip()}")
    t2 = time.perf_counter()
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return t1 - t0, t2 - t1


if __name__ == "__main__":
    src = pathlib.Path(sys.argv[1])
    audio = load(str(src))
    dur = len(audio) / SR
    res = {"audio_min": round(dur / 60, 1)}
    for name, fn in (("gigaam_v3_e2e_rnnt", bench_gigaam), ("whisper_large_v3", bench_whisper)):
        load_s, run_s = fn(audio, src.with_name(f"{src.stem}.{name}.txt"))
        res[name] = {"load_s": round(load_s, 1), "transcribe_s": round(run_s, 1), "x_realtime": round(dur / run_s, 1)}
        print(name, res[name], flush=True)
    print(json.dumps(res))
