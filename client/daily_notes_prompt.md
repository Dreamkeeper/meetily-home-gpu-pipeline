# Daily stand-up notes (short mode)

You process one daily stand-up folder inside the transcripts folder (`output_dir` in `client/config.json`).
Dailies are short and repetitive: write compact notes about task status, blockers and decisions, not a
full record of the meeting. Do not write `cleaned.md`, and do not edit `people.md` or `glossary.md`.

Read:

- `transcript.md` — transcript with speaker labels. Front matter `voice_matches`: `auto: true` names are already applied and reliable, others are hints. `local_speakers`: `role: owner` is the recording owner; `role: local (in the room)` is someone sitting next to the owner, NOT the owner.
- `speakers.json` (optional) — confirmed `SPEAKER_XX -> name`; overrides any inference.
- `participants.json` (optional) — invited participants and the calendar event name (`event`).
- `..\people.md` — canonical spelling of names and nicknames (read only).
- The «Открытые» section of `..\tasks.md` — so you can recognise existing tasks people report on.
- `..\glossary.md` only to check a specific product/term spelling (use Grep for the word instead of reading it all); the `projects:` field uses names from its «Проекты» table.
- `whisper-reference.md` only when an English term in the transcript is unrecognisable.

Names: in stand-ups people usually take turns and address each other by name; use that. Mark inferred
names as `Имя (?)`; if not inferable, keep `Спикер NN`. Resolve partial dates from the meeting date and
write full dates. Never invent facts, tasks, numbers or deadlines.

## Output: `notes.md` (Russian)

```
---
date, time, duration_min, type: daily, short_title: "...", participants: [...], projects: [...]
---
# <Название дейли и главная новость, коротко>
Источник: [[transcript]]

## Кратко             — 2–4 bullets: main news, blockers, decisions; "без существенных изменений" is fine
## По участникам      — one bullet per person who spoke: «сделано …; дальше …; блокеры …» (one or two lines)
## Ключевые решения   — only explicit decisions, who decided; omit the section if none
## Задачи             — table: Ответственный | Задача | Срок | Статус | ID
## Риски и блокеры    — only real blockers or risks; omit the section if none
```

«Задачи» rows:
- a new task assigned or promised in this meeting: ID empty, Статус «новая»;
- an existing open task from `tasks.md` that someone explicitly reports on: its ID, and the new status (в работе / сделано / отменено / заблокировано: причина / перенесено на <дата>);
- do not list tasks nobody mentioned, and do not turn routine "today I'll keep working on X" into a new task unless it has a clear deliverable.
Use "не указано" for unknown cells.

`short_title` names the meeting folder: «Дейли — <team>», with the team taken from the calendar event name
(e.g. «Дейли — команда разработки»). No date, no quotes, none of the characters `: / \ ? * < > |`, at most
60 characters.

## Rules

- Do not overwrite an existing `notes.md`; write `notes-2.md` instead.
- Write files as UTF-8.
- At the end, print a short summary: title, speaker mapping with confidence, number of new and updated tasks.
