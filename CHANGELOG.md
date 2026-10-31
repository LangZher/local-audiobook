# Changelog

## 0.4.0 — 2026-10-30
- Parallel workers: several copies of the model make audio side by side. On an M4 24GB, 3 workers make audio 2×
  as fast as 1 (throughput RTF 1.01 vs 1.96, measured in the app): faster than listening, so a chapter that took
  ~40 min takes ~20. Each worker needs ~5 GB; extra workers quit after 90 s without work.
- GPU tab: GPU busy % with the last 2 minutes, memory bar (model workers / other apps / free, and the GPU limit),
  how many workers fit, a picker 1…N with the speed and memory of each (★ recommended), how long the open chapter
  takes, and the measured speed per worker count.
- The job bar and "time left" count all workers. The voice-design anchor is written atomically (two workers may make
  it at once).

## 0.3.1 — 2026-10-30
- Audio no longer turns robotic after ~30 characters: chunks are now at most 30 characters (was 60). Ming 0.5B
  keeps a voice natural for about that long per generation; past it the voice went mechanical (heard on 灵小柔:
  "倚角而坐，一边喷着雪茄烟，"). Every book is re-chunked, so its audio is made again.
- A clause longer than 30 characters with no comma is cut where a word ends (after 的/了, before 在/把, at quotes
  and dashes), never mid-word; with no such place it stays whole, as in Ming's own splitter.
- Hard-wrapped books: all 18 chapters of 无人生还 are now rejoined (two were just under the detection threshold), and
  lines that end in "……" or "——" are rejoined too.

## 0.3.0 — 2026-10-30
- Fix: audio getting worse toward the end of a sentence. In books that put every printed line in its own paragraph
  (无人生还), chunks were cut mid-word ("…倚角而" | "坐，…") and Ming garbled the dangling end (Whisper heard "倚角而"
  as "一腳兒"). Such chapters are now joined back into real paragraphs before chunking; other books are unchanged.
  The book is parsed again, so its audio is made again. The "Raw text" option now says to leave it off: sending a
  whole chunk in one piece also makes its ending a little worse.
- Reader: highlight-strength slider (stronger by default, with an underline); a sentence that crosses a page turns
  the page when the voice gets there instead of when it ends; page turns slide.
- Making audio ahead: a bar at the top with what is being made, progress and time left (from the voice's measured
  speed), a panel per job with Cancel and an "all done around 23:40", and Pause / Resume (playback still works while
  paused). The wait for a sentence shows "12 s of ~20 s".
- Voices: each voice says a short sample in Chinese and in English (▶ 中文 / ▶ English); a new voice makes both on
  Save and plays the Chinese one.

## 0.2.1 — 2026-10-28
- Fix: adding a voice and pressing Save turned every existing voice into "(old voice settings)" and its audio
  stopped playing (a whole book of 灵小萌 had to be made again). Save fills in each voice's default fields, and the
  voice id hashed them, so 灵小萌 went from `1eedf8c2f3` to `9137f67e61`. The id now ignores fields at their
  default, and on start and on save audio under any older id of the same voice is moved into its current folder
  (duplicates dropped), so the existing audio is found again.

## 0.2.0 — 2026-10-28
Importing and processing explain themselves (feedback: after a scan it was unclear how to get books in, or how
they get audio).
- Library: a 1 Find → 2 Import → 3 Listen strip that checks off steps and highlights the next one; hidden once
  every step was done.
- Scan results: one row per book (the Apple Books copy when the same book is in several places, the rest listed as
  "also in"), books already in the library left out and counted, the Import button on top with the selected count,
  select all, and a closing message with "Open <book>".
- Book cards: NEW badge after import, and "no audio yet · about 17 h to listen" or "🔊 audio for 3% of the book".
- Reader: a one-time hint on how audio is made, with the voice's measured speed; "Preprocess…" is now
  "Make audio ahead…" and says how many sentences and roughly how long, and turns on the dotted done-marks.
- `AUDIOBOOK_DATA` env var: use another library folder (tests).

## 0.1.1 — 2026-10-28
- Fix: EPUBs whose OPF misspells `xmlns` (e.g. epubBuilder's `mlns=`) opened with 0 chapters; bare OPF tags are now read
  as OPF (无人生还 now has 19 chapters).

## 0.1.0 — 2026-10-28
First version.
- Library: scan the Mac for EPUBs (Spotlight + iCloud Books, Downloads, Documents, Desktop) and copy them into
  `~/Documents/Audiobook/books`; Apple Books' folder-EPUBs are zipped into normal files. Covers, reading progress.
- Reader: two-column pages like Apple Books, ▶ reads from the page (or a double-clicked sentence), faint highlight
  on the sentence being read, automatic page and chapter turns, optional dotted marks for sentences with audio.
- Chunking: paragraph → sentences → chunks of ≤ 60 chars; audio stored by text hash × voice hash.
- Queue: play (0) > read-ahead (1, next 4 sentences) > preprocess (2: page, 10 pages, chapter, book).
- Backends behind one protocol (`backends/`, `worker.py`): Ming-3.0-tts 0.5B in PyTorch on MPS (IP, voice design
  with an anchor clip, freeform, zero-shot clone) and macOS `say`.
- Speed page: RTF and time split per model setup, wall time per job, benchmark passage.
- Storage page: size per book × voice, compress to AAC, delete.
- Mac app (`app/build.sh`): WKWebView window that starts and stops the server; open it from Spotlight.
