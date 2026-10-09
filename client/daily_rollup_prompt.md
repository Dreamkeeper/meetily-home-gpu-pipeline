# Daily stand-up roll-up (short mode)

You update the shared files in the transcripts folder (`output_dir` in `client/config.json`) with ONE daily
stand-up whose folder is given in the request. Meetings are rolled up oldest first, so everything already
in the shared files comes from earlier meetings. The point of a daily roll-up is keeping `tasks.md`
current; touch other files only as described below. Do not edit `glossary.md` or `people.md`.

Read: the meeting's `notes.md` (source of truth), then `tasks.md` and `INDEX.md`. Read `transcript.md`
only to check a specific fact. Never read `data.json`.

Idempotent: if this meeting is already in a file (its folder link is there), update those entries in
place instead of adding duplicates.

Write in Russian, UTF-8. Links are Obsidian wiki links relative to the transcripts root, e.g.
`[[2026-10-09 10-00 Дейли — команда разработки/notes]]`. Dates are full ISO dates.

## 1. tasks.md
- «Задачи» rows with an ID: update that task's «Статус» and «Обновлена» (= meeting date). On explicit
  completion or cancellation, move the row to «Закрытые» with «Итог» and «Закрыта» = meeting link.
  «перенесено на <дата>» updates «Срок».
- Rows without an ID: if it is an existing open task restated (same owner, same work), treat it as an
  update of that task. Otherwise add it to «Открытые» with ID `T-<YYYYMMDD>-<NN>` (meeting date; continue
  after the highest NN already used for that date), canonical owner name (from `people.md` if unsure),
  full-date deadline, project if obvious, «Поставлена» = meeting link, «Обновлена» = meeting date.
- Close or change a task only on explicit evidence; tasks not mentioned stay unchanged.
- Columns or links added by the Bitrix24 step (e.g. a «Bitrix» column with `[#12345](https://…)` links)
  belong to the user's task sync: keep them and their values exactly; add an empty cell for new rows.
- Then fill the ID column of the meeting's `notes.md` «Задачи» table with the IDs used (only that column).

## 2. projects/<slug>.md — only for explicit decisions
- If the daily records an explicit decision about a project, append it to that project's «Решения»
  (`- <date> — <decision> (кто решил) — [[<folder>/notes]]`).
- Update the «Задачи» mirror lines of tasks you changed in step 1.
- Do not add «История встреч» entries, do not rewrite «Текущий статус», and do not create new projects
  for routine status updates.

## 3. INDEX.md
Insert one row at the top of the table (newest first), or update it if present:
`| <YYYY-MM-DD HH:MM> | <title from notes.md> | <participants, canonical short names> | <canonical projects as [[projects/<slug>]], or empty> | <one-line summary> | [[<folder>/notes]] |`

Finish by printing a short summary: tasks added/updated/closed, projects touched.
