# Roll-up instructions (cross-meeting context)

You update the shared files in the transcripts folder (`output_dir` in `client/config.json`) with ONE meeting
whose folder is given in the request. Meetings are rolled up oldest first, so everything already in the
shared files comes from earlier meetings.

Read: the meeting's `notes.md` (source of truth), its `participants.json` if present, then the current
`INDEX.md`, `tasks.md`, `glossary.md`, `people.md` and the relevant `projects/*.md`. Read `cleaned.md`
only to check a specific fact. Never read `data.json`.

Idempotent: if this meeting is already in a file (its folder link is there), update those entries in
place instead of adding duplicates.

Write everything in Russian, UTF-8. Links are Obsidian wiki links relative to the transcripts root, e.g.
`[[2026-10-01 15-52/notes]]`. Dates are full ISO dates; resolve partial dates ("27–30", "до пятницы")
from the meeting date and say so only if it stays ambiguous.

## 1. glossary.md — projects first
- Map each item of the meeting's `projects:` to a canonical project in «Проекты». Reuse an existing
  project when it is the same topic under another name (e.g. "Widget Pro" and "Widget" → one project if the
  meeting treats them as one product line; "Выставка ExpoName / ExpoName 2026" variants → one).
  Add new variants to «Варианты / синонимы». Create a new canonical project only for a genuinely new
  topic; slug = short lowercase latin with hyphens (`acme-partnership`, `expo-2026`).
- Add new product, brand, technology and company terms to «Термины» with ASR distortions you can see by
  comparing `notes.md`/`cleaned.md` with the meeting's `transcript.md` wording. Mark spellings confirmed
  by Bitrix24 data or several meetings with ✓, guesses with (?).
- Add confirmed surname spellings that ASR distorts to «Имена».
- In the meeting's `notes.md` front matter, replace `projects:` with the canonical names (only that
  field; do not otherwise edit notes.md).

## 2. projects/<slug>.md — one file per canonical project touched by the meeting
Create if missing with this structure; otherwise update:

```
---
type: project
project: <canonical name>
aliases: [...]
status: active | paused | done
people: [canonical names]
updated: <meeting date>
---
# <canonical name>

## Текущий статус
<3–8 lines, rewritten each time to reflect the latest state across all meetings>

## Решения
- <date> — <decision> (кто решил) — [[<folder>/notes]]

## Открытые вопросы
- <question> — с <date> — [[<folder>/notes]]   (remove when a later meeting answers it; note the answer under Решения)

## Задачи
- <task ID> <owner>: <task> — <status>   (mirror of tasks.md rows for this project)

## История встреч
- <date> [[<folder>/notes]] — <2–3 lines: what changed for this project>
```

Append to «Решения» and «История встреч» (chronological). Rewrite «Текущий статус». Keep it factual:
only what the notes support.

## 3. tasks.md
- Add every task from the meeting's «Задачи» table to «Открытые» with ID `T-<YYYYMMDD>-<NN>`
  (meeting date, sequence within the meeting), canonical owner name, full-date deadline, canonical
  project, «Поставлена» = meeting link, «Обновлена» = meeting date.
- For existing open tasks: if this meeting reports progress, update «Статус»/«Обновлена»; if it
  confirms completion or cancellation, move the row to «Закрытые» with «Итог» and «Закрыта» = meeting
  link. Close only on explicit evidence; a task not mentioned stays open unchanged.
- The same task restated in a later meeting is the same task (update, don't duplicate).

- Columns or links added by the Bitrix24 step (e.g. a «Bitrix» column with `[#12345](https://…)` links, or
  a «Bitrix ID» column in people.md) belong to the user's task sync: keep them and their values exactly;
  add an empty cell for new rows.

## 4. people.md
- Ensure every participant is present with canonical name, Latin spelling (Bitrix24) and aliases /
  nicknames in «Как называют» (e.g. Ivan Petrov = Иван Петров = Ваня).
- Append the meeting link to «Встречи». Extend the role/context column with new facts.

## 5. INDEX.md
Insert one row at the top of the table (newest first), or update it if present:
`| <YYYY-MM-DD HH:MM> | <title from notes.md> | <participants, canonical short names> | <canonical projects as [[projects/<slug>]]> | <one-line summary> | [[<folder>/notes]] |`

Finish by printing a short summary: projects touched/created, tasks added/updated/closed, glossary
entries added.
