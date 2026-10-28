# Voice note clean-up

You get ONE voice-message note (path in the request) inside the transcripts folder's «Голосовые» subfolder.
It holds YAML front matter, a title, an audio embed and «## Расшифровка (сырая)»: an automatic transcript
(GigaAM for Russian, Whisper for English/Chinese, lines tagged `(en)`/`(zh)`; timestamps `[mm:ss]`;
`**Спикер NN**` labels only when several voices were detected).

Rewrite that file in place. Do not edit any other file. Make the change directly with the Read/Write tools;
you cannot run scripts or shell commands in this session.

Look things up only as needed: `..\glossary.md` for product, brand, company and technology spellings (use Grep
for the specific word; its «Как искажает ASR» column lists known distortions, e.g. «Маэс» = MOES) and
`..\people.md` for names and nicknames.

## Result (Russian)

```
---
<keep every existing front-matter field>
short_summary: "<one line, at most 100 characters>"
---
# <Short title: who and what about, 3–8 words, e.g. «Саша — оплата этапов MOES и сроки»>

![[<audio file, as in the existing embed>]]

## Кратко
- 2–4 bullets: the main points, numbers, dates, requests. Omit the whole section for messages under ~60 s.

## Текст
<the cleaned transcript>
```

Rules for «Текст»:
- Fix ASR errors, punctuation and capitalization; restore brand names and English terms in Latin.
- Remove fillers («э-э», repeated words, false starts); merge broken sentences; split into readable paragraphs.
  Keep the speaker's wording and meaning, do not summarize here and never invent facts, numbers or names.
- Keep English as English. For Chinese, keep the original line and add «RU: …» with a translation.
- Unclear fragments: `[неразборчиво]`.
- Several speakers: keep their labels (replace with a name only if the text makes it certain).
- Drop the timestamps except one at the start of each paragraph if the message is longer than 3 minutes.
- Remove the «## Расшифровка (сырая)» section once «Текст» is written.

Write the file as UTF-8. At the end, print the title.
