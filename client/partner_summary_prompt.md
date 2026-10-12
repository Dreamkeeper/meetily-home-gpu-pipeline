# English call summary for the other side (WeChat message)

You write ONE plain-text message in English that the recording owner will send to the other side of the
call (a supplier, factory or partner) as a WeChat message, right after the call. The folder of the meeting
is given in the request. Write the message to `summary-en.txt` in that folder. Do not edit any other file.

Make the change directly with the Write tool. You cannot run scripts or shell commands in this session
(they are refused), so do not write helper scripts.

Read the meeting's `notes.md` (source of truth). Read `cleaned.md` only to check an exact figure, date or
wording. Never read `data.json`.

## Only what was said openly to both sides

`notes.md` is internal. The message must contain only what was discussed openly with the other side. Leave out:
- the other side's internal conversations (for example in Chinese) and anything learned only from them;
- discrepancies between an interpreter and the original speaker;
- our side's internal discussion, opinions, doubts, budgets, pressure and internal targets that were not told to the other side;
- disputes about who promised what; keep the message constructive.
If a request in the action items comes from an internal observation, phrase it neutrally
(e.g. "please check if there is a newer version and send it").

## Format (WeChat: plain text, no Markdown)

- No `**`, `#`, tables or links. Numbered lists `1.` and short lines only. No emoji.
- No email sign-off ("Best regards", name): it is a chat message.
- Structure:
  1. One line: greeting to the other side's main contact by first name if known ("Hi Susan, thank you for the call today…").
  2. Our goal, one line, if it was stated in the call.
  3. Priorities, if any were stated (numbered).
  4. «Status:» one numbered item per product/topic: what was agreed or reported, with dates.
  5. Our position, one or two lines, only if it was stated to the other side (e.g. timeline, advance payment).
  6. «Action items, <their company>:» numbered, with dates where given.
  7. «Action items, <our company>:» numbered.
  8. One closing line inviting them to message the owner if something is blocking.
- Dates as "Oct 16"; product names, part numbers, quantities and certifications exactly as in the notes.
- Simple, polite business English that a non-native reader understands; short sentences.
- Our company name: the side of the recording owner (from `notes.md`, «Участники»). Their company: the other side.
- Length: as short as the content allows; at most about 3000 characters.

At the end, print the message.
