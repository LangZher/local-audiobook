# Changelog

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
