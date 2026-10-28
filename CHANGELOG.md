# Changelog

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
