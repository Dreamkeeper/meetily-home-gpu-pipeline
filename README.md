# Meetily + home GPU meeting pipeline

Record meetings on a laptop with a [Meetily](https://github.com/Zackriya-Solutions/meetily) fork, transcribe them live and after the call on a GPU machine at home, and turn them into cleaned transcripts, notes, tasks and cross-meeting context in a Markdown folder (works well as an Obsidian vault).

Speech recognition and speaker separation run on your own hardware. Only the notes step uses Claude (Claude Code CLI, headless).

Built for mostly-Russian meetings with English and Chinese parts (e.g. calls with suppliers through an interpreter), but the language set is configurable.

## What it does

- **Live transcription on a remote GPU:** Meetily's "Remote Whisper" engine sends each speech segment through an SSH tunnel to a desktop GPU (Whisper large-v3). The laptop's GPU stays idle; local Parakeet is the fallback when the GPU box is offline. Translate mode translates only Chinese into English and keeps Russian and English as spoken.
- **Post-call transcript:** pyannote community-1 diarization, then per-chunk language detection (ru/en/zh) and routing: Russian → GigaAM-v3, English/Chinese → Whisper large-v3, with a hallucination filter.
- **Who is who:**
  - Meetily saves a stereo file (L = your mic, R = call audio), which identifies the owner together with a voice-profile match.
  - Voice profiles of colleagues name the other speakers.
  - An optional Bitrix24 calendar lookup supplies the invited participants.
- **Notes and memory:** Claude writes `cleaned.md` and `notes.md` per meeting, then rolls each meeting into `INDEX.md`, `tasks.md` (open/closed tasks with stable IDs), `glossary.md`, `people.md` and `projects/*.md`. Glossary terms are fed back as Whisper hotwords.
- **Daily stand-ups** (matched by calendar or meeting name, `daily_name_contains`) get a short mode: per-person status, blockers and task changes on a cheaper model (`daily_model`, default Sonnet), and a roll-up that only updates `tasks.md`, `INDEX.md` and explicit project decisions.
- **Automation:**
  - A watcher on the laptop picks up finished recordings and renames each meeting folder after its topic.
  - It sends Telegram notifications when a meeting is ready and when one fails, and handles Claude usage limits (waits for the reset or switches to a second account).
  - When a call starts (Zoom, or any app listed in `call_apps`, starts using the microphone) and Meetily isn't recording, a Windows toast offers a one-click "Записать" (start recording). The calendar event running at that moment, if any, names the recording; ad-hoc calls get the reminder too.

```
LAPTOP                                              GPU BOX (e.g. desktop with an NVIDIA card)
──────                                              ──────────────────────────────────────────
Meetily fork ── live segments ── SSH tunnel ──────► server/service.py  /v1/live/transcribe
  audio.mp4 + audio_stereo.mp4                         127.0.0.1:8765 (localhost only)
client/watcher.py ── finished recording ──────────► jobs: diarization + language routing + ASR
  render → Claude notes → folder rename → roll-up
  → Telegram; call-start reminders; tunnel keeper
```

## Requirements

- **GPU box:**
  - Windows with an NVIDIA GPU, about 8 GB of free VRAM or more (tested on an RTX 4060 Ti 16 GB);
  - `uv`, `ffmpeg`, `git`, and an OpenSSH server;
  - reachable from the laptop, e.g. over Tailscale.
- **Laptop:**
  - Windows, Python 3.11, `ffmpeg`, and an SSH client with a key for the GPU box;
  - Claude Code CLI;
  - optional: a Telegram bot and a Bitrix24 incoming webhook.
- **A Hugging Face account** with the conditions of `pyannote/speaker-diarization-community-1` accepted (first model download only).

## Setup

### GPU box (normal, non-admin PowerShell)
1. Clone this repo; the Meetily submodule isn't needed here.
2. In `server\`: `uv sync --python 3.12`.
3. Download the diarization model once: `.venv\Scripts\hf.exe auth login`. The service then runs offline with `HF_HUB_OFFLINE=1`.
4. Put a random token into `server\service_token.txt`, for example from `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
5. `server\install_autostart.ps1` installs a watchdog task and a Startup entry. Logs: `server\service.out.log`, `server\service.err.log`.
6. Updates: `git pull`, or `server\update.ps1`, which pulls, re-syncs packages and restarts only when no job is running. A read-only GitHub deploy key works well for pulling a private fork.

### Laptop (normal, non-admin PowerShell)
1. Install the Python packages from a normal terminal: `pip install requests numpy winotify`. (Not from the Claude desktop app's terminal: it redirects `AppData`, so the scheduled watcher wouldn't see packages installed with `--user`.)
2. Add an SSH host alias for the GPU box, e.g. `gpu-box`, using key authentication.
3. Install the Claude Code CLI and log in.
   - Optional second account for usage-limit fallback: `$env:CLAUDE_CONFIG_DIR="$env:USERPROFILE\.claude-fallback"; claude`, then `/login`.
4. Copy `client\config.example.json` to `client\config.json` and edit it: paths, `ssh_host`, `owner_name`, Claude accounts.
5. Copy `client\reminder_rules.example.json` to `client\reminder_rules.json`. Optional: add call apps (`Telegram.exe`, `Weixin.exe`, …) and meeting series that shouldn't trigger reminders.
6. Secrets: see `client\secrets\README.md` for the service token, and optionally the Bitrix24 webhook and the Telegram bot.
7. Run `client\install_autostart.ps1`. It installs the watcher task and the `meetily-record:` link handler used by reminders. Log: `client\state\watcher.log`.

### Meetily fork (laptop)
1. Run `meetily-build\install_toolchain.ps1`. It installs Rust, CMake, LLVM, VS Build Tools (C++), the Vulkan SDK and pnpm. Machine-wide installers show UAC prompts.
2. Run `git submodule update --init`, then `meetily-build\build_fork.ps1`.
3. Install `meetily\target\release\bundle\nsis\meetily_*_x64-setup.exe`.
4. In Meetily, turn on Settings → Recording → **Save Separate Channels**.
5. Select Settings → Transcription → **Remote Whisper**, URL `http://127.0.0.1:18765` (the watcher keeps the tunnel open), then click **Use**.
6. For translation, use the 🌐 language button: "Auto Detect (Translate to English)".

The fork ([Dreamkeeper/meetily](https://github.com/Dreamkeeper/meetily), branch `dreamkeeper-build`) adds the following. Some parts were proposed upstream: [#823](https://github.com/Zackriya-Solutions/meetily/pull/823) (separate channels), [#832](https://github.com/Zackriya-Solutions/meetily/pull/832) (duration).
- separate mic/system channels;
- a real `duration_seconds`;
- system audio that follows output-device changes, plus the mic returning to the headset after a Bluetooth drop-out (Windows);
- `--start-recording --meeting-name=…`;
- the Remote Whisper engine.

## Customizing

- **Languages:** `LANGS` in `server/pipeline.py` and `LIVE_TRANSLATE_LANGS` in `server/service.py`.
- **Notes language, structure and conventions:** `client/notes_prompt.md` and `client/rollup_prompt.md`. They are written for Russian-language notes.
- **Owner detection threshold, voice-matching thresholds:** `client/render.py` and `client/voices.py`.

## Gotchas

- **whisper-rs-sys + LLVM 23:** bindgen 0.69 produces an empty `whisper_full_params`. `build_fork.ps1` uses libclang 18 from the PyPI `libclang` wheel.
- **winget downloads can stall** on Delivery Optimization. Set winget's downloader to `wininet`.
- **Over SSH to a Windows GPU box,** the shell is cmd and isn't elevated. Call `uv.exe` by its full path, because the WinGet link fails there.
- **`git pull` over SSH hangs inside an SSH session** when git uses Windows' OpenSSH `ssh.exe`. Clone with Git's bundled ssh: `git clone -c core.sshCommand="'C:/Program Files/Git/usr/bin/ssh.exe' -o BatchMode=yes" …`.
- **torchcodec doesn't work on Windows,** so audio goes to pyannote as in-memory waveforms.
- **whisper large-v3-turbo can't translate:** it produces gibberish. Live translation uses full large-v3.
- **The GPU box frees Whisper after 10 idle minutes.** A cold load takes up to about a minute; Meetily queues live segments meanwhile, and the call reminder pre-warms the model.
- **Call detection** reads Windows' microphone privacy records (`CapabilityAccessManager\ConsentStore\microphone`): an app holds the mic while its `LastUsedTimeStop` is 0. A call joined without computer audio isn't detected.
- **Stereo owner detection** needs a voice match, because people sitting next to you are heard through your mic too.
- **Upstream Meetily's `duration_seconds`** is the end of the last live-transcript segment, not the real length. The fork fixes it, and the watcher measures the file anyway.

## License

MIT for the code in this repository. Meetily is MIT-licensed (see the submodule). Model licenses apply separately: pyannote community-1, GigaAM-v3 (MIT), Whisper (MIT).
