"""Job service for the GPU box. Upload audio -> queued -> pipeline.process -> JSON result.

Run: .venv\\Scripts\\python service.py   (localhost only; clients use an SSH tunnel)
Auth: header "Authorization: Bearer <token>", token in service_token.txt next to this file.
Live endpoints (/v1/live/*) serve Meetily's "Remote Whisper" engine during a recording. They need no
token: the service listens on localhost only and is reachable solely through the SSH tunnel, and
they only turn posted audio into text (no access to jobs or results).
"""
import hashlib
import json
import pathlib
import queue
import threading
import time
import traceback

import numpy as np
import torch
import uvicorn
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

import pipeline

ROOT = pathlib.Path(__file__).resolve().parent
JOBS = ROOT / "jobs"
TOKEN = (ROOT / "service_token.txt").read_text(encoding="ascii").strip()
HOST, PORT = "127.0.0.1", 8765   # reached from the laptop through an SSH tunnel
IDLE_UNLOAD_S = 600   # free VRAM for other apps when idle
RESIDENT_MODELS = set()   # nothing stays loaded: the GPU is also used for gaming. Live clients wait for
                          # /v1/live/status "ready" after a warm-up instead (a cold Whisper load takes ~60 s)

JOBS.mkdir(exist_ok=True)
work_q: "queue.Queue[str]" = queue.Queue()
state_lock = threading.Lock()
last_activity = time.time()
app = FastAPI()


def auth(authorization: str = Header(default="")):
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "bad token")


def job_dir(job_id):
    return JOBS / job_id


def read_state(job_id):
    p = job_dir(job_id) / "state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def write_state(job_id, **updates):
    with state_lock:
        st = read_state(job_id) or {}
        st.update(updates, updated=time.time())
        (job_dir(job_id) / "state.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        return st


def worker():
    global last_activity
    while True:
        try:
            job_id = work_q.get(timeout=30)
        except queue.Empty:
            idle = [k for k in pipeline._models if k not in RESIDENT_MODELS]
            if idle and time.time() - last_activity > IDLE_UNLOAD_S:
                for k in idle:
                    del pipeline._models[k]
                torch.cuda.empty_cache()
                print(f"[service] unloaded idle models {idle}; kept {sorted(RESIDENT_MODELS)}", flush=True)
            continue
        st = read_state(job_id)
        d = job_dir(job_id)
        try:
            write_state(job_id, status="running", started=time.time())
            result = pipeline.process(d / st["filename"], num_speakers=st.get("num_speakers"),
                                      min_speakers=st.get("min_speakers"), max_speakers=st.get("max_speakers"),
                                      hotwords=st.get("hotwords"))
            (d / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            write_state(job_id, status="done", timings=result["timings_s"])
        except Exception as exc:  # keep the worker alive; client decides whether to retry
            traceback.print_exc()
            write_state(job_id, status="error", error=f"{type(exc).__name__}: {exc}")
        finally:
            last_activity = time.time()


# In Meetily's "translate to English" mode only these languages are translated; English and Russian
# segments are transcribed as spoken (the owner reads both; Whisper's Russian->English is not wanted).
LIVE_TRANSLATE_LANGS = {"zh"}

live_lock = threading.Lock()
live_state = {"last_lang": None}


def live_decode(wm, audio, lang, translate):
    task = "translate" if translate and lang != "en" else "transcribe"
    segs, _ = wm.transcribe(audio, language=lang, task=task, beam_size=5, vad_filter=False,
                            condition_on_previous_text=False, without_timestamps=True,
                            initial_prompt=pipeline.ZH_PROMPT if lang == "zh" and task == "transcribe" else None)
    segs = list(segs)
    if not segs:
        return "", -10.0, 1.0
    text = " ".join(seg.text.strip() for seg in segs).strip()
    weight = [max(1, len(seg.text)) for seg in segs]
    logprob = sum(seg.avg_logprob * w for seg, w in zip(segs, weight)) / sum(weight)
    return text, logprob, max(seg.no_speech_prob for seg in segs)


def live_transcribe_sync(audio, language):
    """One VAD segment from Meetily: language detection (ru/en/zh), transcription (or, in translate
    mode, translation to English for LIVE_TRANSLATE_LANGS only), and the batch pipeline's
    hallucination filter."""
    global last_activity
    last_activity = time.time()
    if len(audio) < pipeline.SR * 0.3:
        return {"text": "", "language": None}
    translate = language == "auto-translate"
    fixed = None if language in (None, "", "auto", "auto-translate") else language
    with live_lock:
        wm = pipeline.whisper().model
        lang, prob = fixed, None
        if lang is None:
            if len(audio) >= pipeline.SR * pipeline.LID_MIN_S:
                lang, prob, ranked = pipeline.detect_language(wm, audio)
                if prob < pipeline.LID_SURE and len(ranked) > 1:
                    scores = {c: pipeline.whisper_decode(wm, audio, c, beam_size=1)[1] for c in ranked[:2]}
                    lang = max(scores, key=scores.get)
            else:
                lang = live_state["last_lang"] or "ru"
        do_translate = translate and lang in LIVE_TRANSLATE_LANGS
        text, logprob, no_speech = live_decode(wm, audio, lang, do_translate)
        if pipeline.is_hallucination(text, logprob, no_speech):
            text = ""
        live_state["last_lang"] = lang
    last_activity = time.time()
    return {"text": text, "language": lang, "lang_p": None if prob is None else round(prob, 2),
            "translated": do_translate}


@app.post("/v1/live/transcribe")
async def live_transcribe(request: Request, language: str = "auto"):
    """Body: raw little-endian float32 samples, 16 kHz mono."""
    audio = np.frombuffer(await request.body(), dtype="<f4").copy()
    return await run_in_threadpool(live_transcribe_sync, audio, language)


warm_lock = threading.Lock()


def load_whisper():
    global last_activity
    with warm_lock:  # concurrent warm-ups must not load the model twice
        last_activity = time.time()
        pipeline.whisper()
        last_activity = time.time()


@app.post("/v1/live/warmup")
async def live_warmup():
    """Load Whisper for live transcription (sent when a recording starts or a meeting reminder fires)."""
    t = time.time()
    await run_in_threadpool(load_whisper)
    return {"ok": True, "load_s": round(time.time() - t, 1)}


@app.get("/v1/live/status")
def live_status():
    """ready=false while Whisper is (being) loaded; clients queue their segments until it is true."""
    return {"ready": "whisper" in pipeline._models, "loading": warm_lock.locked()}


@app.get("/health")
def health():
    ok = torch.cuda.is_available()
    free, total = torch.cuda.mem_get_info() if ok else (0, 0)
    return {"gpu": torch.cuda.get_device_name(0) if ok else None, "vram_free_gb": round(free / 2**30, 1),
            "queued": work_q.qsize(), "models_loaded": sorted(pipeline._models)}


@app.post("/jobs", dependencies=[Depends(auth)])
async def create_job(file: UploadFile = File(...), num_speakers: int | None = Form(None),
                     min_speakers: int | None = Form(None), max_speakers: int | None = Form(None),
                     hotwords: str | None = Form(None)):
    if not torch.cuda.is_available():
        raise HTTPException(503, "GPU not available (eGPU disconnected?)")
    data = await file.read()
    job_id = hashlib.sha256(data).hexdigest()[:16]
    st = read_state(job_id)
    if st and st["status"] in ("queued", "running", "done"):
        return {"id": job_id, **st}
    d = job_dir(job_id)
    d.mkdir(exist_ok=True)
    name = "audio" + pathlib.Path(file.filename or "a.opus").suffix
    (d / name).write_bytes(data)
    st = write_state(job_id, status="queued", filename=name, created=time.time(), error=None,
                     num_speakers=num_speakers, min_speakers=min_speakers, max_speakers=max_speakers,
                     hotwords=hotwords)
    work_q.put(job_id)
    return {"id": job_id, **st}


@app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
def get_job(job_id: str):
    st = read_state(job_id)
    if not st:
        raise HTTPException(404, "unknown job")
    return {"id": job_id, **st}


@app.get("/jobs/{job_id}/result", dependencies=[Depends(auth)])
def get_result(job_id: str):
    p = job_dir(job_id) / "result.json"
    if not p.exists():
        raise HTTPException(404, "not ready")
    return FileResponse(p, media_type="application/json")


def requeue_unfinished():
    for d in sorted(JOBS.iterdir(), key=lambda p: p.stat().st_mtime):
        st = read_state(d.name)
        if st and st["status"] in ("queued", "running"):
            write_state(d.name, status="queued")
            work_q.put(d.name)


if __name__ == "__main__":
    requeue_unfinished()
    threading.Thread(target=worker, daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
