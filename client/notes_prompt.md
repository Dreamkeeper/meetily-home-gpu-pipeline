# Meeting notes instructions

You process one meeting folder inside the transcripts folder (`output_dir` in `client/config.json`).
The folder contains:

- `transcript.md` — primary transcript (GigaAM-v3 ASR, speaker labels from pyannote diarization). Source of truth for content and speaker attribution.
- `whisper-reference.md` — second ASR pass (Whisper large-v3), no speakers. Use ONLY to recover English terms, product/brand names and words GigaAM garbled (GigaAM transliterates English: "BTUBI" = "B2B", "BTC" = "B2C"). Never take speaker attribution from it.
- `speakers.json` (optional) — confirmed mapping `SPEAKER_XX -> name`. Confirmed names override any inference.
- `participants.json` (optional) — invited participants of this meeting (e.g. from Bitrix24). The speakers are most likely among them, but not everyone invited necessarily spoke or attended.
- `transcript.md` front matter `local_speakers` (stereo recordings) — clusters heard mostly through the owner's own microphone. `role: owner` (mic + voice match) is the recording owner and is already named in the transcript; `role: local (in the room)` is someone sitting next to the owner (in-person participant), NOT the owner — identify them from context and the participants list.
- `transcript.md` front matter `voice_matches` — similarity of each diarized speaker to saved voice profiles. `auto: true` names are already applied in the transcript and are reliable; others are hints (score ≥ 0.45) to confirm or reject from context.

Also read `..\people.md` (the people index next to the meeting folders): use its canonical spelling of names and surnames; nicknames map to people there (Маша → Мария Петрова). After writing the notes, update `people.md`: add people who appeared in this meeting and are missing (mark unconfirmed ones `(?)`), extend the role/context column when the meeting reveals something new, and add this meeting's link `[[<folder>/notes]]` to the "Встречи" column of each participant.

Also read `..\glossary.md`: use its canonical spellings for products, brands, technologies and surnames when fixing ASR errors (its «Как искажает ASR» column lists known distortions), and use only names from its «Проекты» table in the `projects:` front matter field when the topic matches one (a later roll-up step maps new ones).

Resolve partial dates from the meeting date: "27–30" said in an October meeting is 27–30 октября (or the next month if those days have passed); write full dates in tasks and deadlines.

The recording owner is named at the end of the request (`owner_name` in `client/config.json`). Recordings are mostly Russian IT/product meetings.

## Languages (factory calls)

Lines in `transcript.md` tagged `(en)` or `(zh)` were recognized as English or Chinese and transcribed by Whisper; untagged lines are Russian (GigaAM). Typical factory call: the owner speaks English; the factory speaks English, or Chinese interpreted by the owner's colleague; the factory side also talks among themselves in Chinese.

- In `cleaned.md`, keep English as English (fix ASR errors only). Show every Chinese line as the original followed by a Russian translation on the next line: `> 原文…` then `RU: …`. Mark unclear Chinese as `[неразборчиво]` instead of guessing; Whisper sometimes turns noise into short Chinese phrases, so drop isolated one-word Chinese fragments that carry no meaning.
- Identify the interpreter (a colleague of the owner alternating between Chinese and English/Russian) and note «переводчик» next to their name. When the interpreter's rendering differs in substance from what the Chinese side said, keep both and point out the discrepancy in the notes.
- Internal Chinese chatter on the factory side is often the most candid part (doubts, prices, constraints). Translate it and summarize it in `notes.md` under a separate «Внутренние обсуждения фабрики (по-китайски)» subsection of «Ход встречи», with timestamps. Do not overstate it: it is what they said among themselves, not a commitment.
- `notes.md` is in Russian as usual; quote key English or Chinese phrases in the original only where exact wording matters (prices, terms, commitments).

## Output 1: `cleaned.md`

- YAML front matter copied from `transcript.md`, plus `speaker_map:` with your mapping and confidence.
- Preserve chronological order, speaker attribution, and a timestamp per speaker turn.
- Replace `Спикер NN` with names: confirmed ones from `speakers.json`; otherwise infer from context (direct address answered by that speaker, self-introductions, "Серёж, можешь…" followed by the answer). Mark inferred names as `Имя (?)`. If not inferable, keep `Спикер NN`.
- Fix ASR errors, punctuation, capitalization, agreement, broken phrases. Restore English IT terms and product names in Latin (deploy, merge, API, B2B, Zigbee, DALI…), using the Whisper reference to confirm.
- Remove fillers, false starts, repetitions and small talk that carries no substance; merge fragmented turns of the same speaker. Diarization sometimes splits one speaker's sentence or assigns a short interjection to the wrong person — fix only when context makes it obvious.
- Never invent facts, decisions, names, numbers, dates or commitments. Mark unclear fragments as `[неразборчиво]`.

## Output 2: `notes.md`

Russian. Structure:

```
---
date, time, duration_min, type: meeting-notes, short_title: "...", participants: [...], projects: [...], tags: [...]
---
# <Короткое содержательное название встречи>
Источник: [[transcript]] · [[cleaned]]

## Кратко            — 3–6 bullet points: what the meeting was about and its outcome
## Участники         — name/label, role or area if evident, confidence of identification
## Контекст
## Ключевые решения  — only explicit decisions; who decided
## Задачи            — table: Ответственный | Задача | Срок | Статус  (use "не указано")
## Открытые вопросы
## Риски и блокеры
## Ход встречи       — detailed chronological notes by topic with timestamps [HH:MM:SS], enough to reconstruct the discussion
## Термины и сущности — products, systems, companies, people mentioned (for the knowledge base)
```

`short_title` names the meeting folder, so it must make the meeting easy to find in a file list: 3–6 words in Russian, counterpart or team first, then the topic (e.g. «Поставщик — сроки образцов», «Партнёр — участие в выставке», «Онбординг по Scrum»). No date, no quotes, none of the characters `: / \ ? * < > |`, at most 60 characters.

## Rules

- Do not overwrite an existing `cleaned.md`/`notes.md`; write `cleaned-2.md`/`notes-2.md` instead.
- Write files as UTF-8.
- At the end, print a short summary: title, speaker mapping with confidence, number of decisions and tasks.
