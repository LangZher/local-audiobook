# Local Audiobook

A lightweight reader for your EPUBs that reads any page aloud with a **local** TTS model on Apple Silicon, and
keeps a log of how fast the model is, so every speed-up of the model shows on real books.

- **Library**: *Find EPUBs on this Mac* (Spotlight, Apple Books, iCloud Drive, Downloads, Documents, Desktop), tick
  the ones you want, *Import*: copies go into one folder, `~/Documents/Audiobook/books`.
- **Reader**: paginated two-column layout like Apple Books. ▶ reads the page, a faint highlight follows the
  sentence being read, pages turn on their own. Double-click a sentence to start there. ←/→ turn pages, space plays.
- **Make audio ahead** for a page, the next 10 pages, a chapter or the whole book in the background (local TTS is slow).
- **Voices**: pick the model and its settings, e.g. Ming-3.0-tts IP voices (灵小萌, 灵婉…) or a voice-design
  description; macOS `say` as an instant baseline.
- **Speed**: RTF and time split per model setup, wall time per job, and a fixed benchmark passage to compare
  versions by ear.
- **Storage**: size per book × voice, compress to AAC, delete.

Design, model protocol, chunking and preload rules: [DESIGN.md](DESIGN.md). Changes: [CHANGELOG.md](CHANGELOG.md).

## Run

```bash
python3 server.py            # opens http://127.0.0.1:8765/
```

or build the Mac app once and open it from Spotlight ("Local Audiobook"):

```bash
app/build.sh                 # → ~/Applications/Local Audiobook.app (needs Xcode command line tools)
```

The server is Python 3 stdlib only. `say` works out of the box.

## Ming-3.0-tts

Needs a Python env with Ming's requirements (torch 2.6, transformers 4.46), the
[Ming-3.0-tts](https://github.com/inclusionAI/Ming-3.0-tts) source and the 0.5B weights. Point the app at them in
`config.local.json` (not in git; it overrides `config.json`):

```json
{"backends": {"ming_torch": {
  "python": "/path/to/envs/mlx-speech/bin/python",
  "opts": {"src": "/path/to/Ming-3.0-tts", "model": "/path/to/Ming-3.0-tts-0.5b", "device": "mps", "dtype": "bf16"}}}}
```

On an M4 (PyTorch, MPS, bf16) it runs at RTF ≈ 2.1 with an IP voice and ≈ 6 with voice design, i.e. slower than real time: preprocess ahead, or help make it
faster. Adding a model is one file in `backends/` (see DESIGN.md § Model protocol).

## License

MIT. Ming-3.0-tts and its weights have their own license.
