# Design

Local Audiobook is a small reader for EPUBs that reads any page aloud with a local TTS model. It also
**measures the model**: every chunk it generates is logged with its timing, so when the model gets faster (an MLX
port, fewer diffusion steps, quantization...) the speed-up shows on the Speed page, on real books.

```
 web/ (reader UI, WKWebView or browser)
   │  HTTP JSON
 server.py  (python3, stdlib only)
   ├─ epub.py        EPUB → chapters → paragraphs → chunks
   ├─ Queue          priority queue of chunks to generate (one at a time)
   ├─ storage        ~/Documents/Audiobook/audio/<book>/<voice>/<hash>.wav|m4a
   └─ Worker  ── JSON lines over stdin/stdout ──►  worker.py  (the backend's own python, e.g. a conda env)
                                                     └─ backends/<name>.py  (Ming, macOS say, next: MLX)
```

Why two processes: the reader stays stdlib and starts instantly; the model lives in its own environment
(torch, MLX, ...), loads once, and can crash or be swapped without taking the UI down.

## Model protocol

A backend is one module `backends/<name>.py`:

```python
OPTIONS = {"label": "...", "fields": [            # what the Voices page shows; stdlib only at import time
    {"key": "task", "type": "select", "choices": [...], "default": "IP"},
    {"key": "ip", "type": "select", "when": "IP", ...},  # "when": shown only for that task
]}

class Backend:
    label: str          # names the speed setup: "ming-0.5b torch mps bf16", later "ming-0.5b mlx 4bit"
    sample_rate: int
    def __init__(self, **opts): ...                      # load the model once
    def synthesize(self, text, voice, out_path) -> dict:  # write a wav
        return {"audio_s": 7.7, "parts_s": {"lm": 4.3, "flow": 11.3, "proj": 1.2, "vae_decode": 1.0}}
```

and an entry in `config.json` → `"backends"`: which python runs it and its `opts`. `worker.py` wraps it:

```
→ {"text": "…", "voice": {"task": "IP", "ip": "灵小萌", "seed": 1895}, "out": "/…/x.wav"}
← {"ok": true, "total_s": 18.5, "audio_s": 7.7, "parts_s": {...}}
```

`label` is the key the Speed page groups by, so give each optimization its own label. `parts_s` is optional; the
Speed page shows the split ("flow 61% · lm 23% …"), which is where the next optimization should go.

### Ming-3.0-tts (`ming_torch`)

Follows `MingTTS.synthesize` from [inclusionAI/Ming-3.0-tts](https://github.com/inclusionAI/Ming-3.0-tts), run on
MPS in bf16 (Ming's own autocast is CUDA-only, so the backend wraps the call in an MPS autocast).

| Task | Voice fields | Call |
|---|---|---|
| `IP` | `ip` (灵小萌, 灵婉, 灵楷, US_Emma…) | `synthesize(task="IP", controls={"ip": …}, use_zero_spk_emb=True)` |
| `VOICE_DESIGN` | `style` (a description) | first call makes an **anchor** clip from the description; every chunk then uses it as `prompt_wav_path` + `prompt_text` |
| `FREEFORM` | `instruction` | same anchoring as voice design |
| `TTS` | `prompt_wav`, `prompt_text` | zero-shot clone of that recording |

Common: `raw_text` (send the chunk as one piece instead of Ming's ~24-char fragments), `max_length`, `cfg`, `seed`.
The anchor exists because voice design without a prompt invents a new voice on every call: chunk 2 would not sound
like chunk 1. IP voices are stable by name, so `IP` is the default.

## Chunks: the unit of audio

Before chunking, `epub.unwrap` joins paragraphs that are really one paragraph hard-wrapped into lines: some EPUBs
(无人生还) put every printed line in its own `<p>`, cutting sentences mid-word ("…倚角而" | "坐，…"). Every chunk then
ended on half a word, and Ming garbled exactly that end, which sounded like the audio getting worse toward the end
of each sentence. It applies only to chapters where many long, punctuated lines end without a sentence end, so normal
books and contents pages stay as they are; `epub.PARSER` makes cached books parse again when this changes.

`epub.split_chunks(text, max_chars=30)`:

1. Never cross a paragraph (a paragraph break is a natural pause, and the text layout stays intact).
2. Split at sentence ends (`。！？!?；;…`, `.` before a space), keeping closing quotes with their sentence.
3. Merge sentences greedily while the chunk stays ≤ `max_chars` (config `chunk_chars`).
4. A sentence longer than that is split at commas (`，,、：:`); a clause still longer is cut where a word surely ends
   (`soft_split`: after 的/了/着, before 在/把/被/和…, at quotes and dashes), nearest its middle. Never mid-word: like
   Ming's own splitter, a clause with no such place stays whole (up to twice `max_chars`).

Joined, the chunks give back the paragraph exactly; each is one `<span>` in the reader. **Why 30**: Ming 0.5B keeps a
voice natural for about 30 characters per generation; past that it turns mechanical and the speaker's character goes
(heard on 灵小柔 with raw text, 2026-10-30: "…现在正在头等车厢的吸烟室里，" fine, then "倚角而坐，一边喷着雪茄烟，"
robotic). Whisper still transcribes such audio correctly, so this is checked by ear, not by ASR. 30 chars ≈ 7 s of
speech, which also makes the first sound after Play come sooner. Ming splits further into ~24-char fragments itself
unless `raw_text` is on. Was 60 until v0.3.1; changing it re-chunks every book, so audio is made again.

Audio is stored by **hash of the chunk text** under a **hash of the voice settings**, so re-chunking a book or
editing a voice never plays the wrong audio: it simply finds nothing and generates again. Fields left at their
default do not count in the voice hash, so saving the voice list (which fills every field in) or adding another
voice keeps each voice's id and its audio; on start and on save, audio under an older id of a voice is moved into
its current folder.

## Queue and preload

One model process, one chunk at a time, by priority:

| Priority | Who asks | What |
|---|---|---|
| 0 play | Play / double-click a sentence | the sentence to play now + `lookahead` (4) after it; replaces older play jobs of the book |
| 1 read-ahead | the player, at each new sentence | the next `lookahead` sentences |
| 0 sample | the Voices page | a short line in Chinese and in English per voice (made on save for a new voice) |
| 2 preprocess | the Preprocess menu | this page, next 10 pages, this chapter, the whole book |

**Pause** (the bar at the top while anything is made ahead) holds priority 2 only: the chunk in progress finishes,
then nothing is made in the background, while ▶ Play and read-ahead still make what they read. Each job shows its
time left: its chunks still to make × the voice's measured generation seconds per character (recent runs).

Playback goes sentence by sentence: if the audio is there it plays; if not, the player waits (shows "generating…")
and resumes when the poller sees it. Pages turn on their own as the highlighted sentence moves on (sliding); a sentence
that runs onto the next page turns it when the voice gets there (the share of its text on the first page, against the
audio's progress), not when the sentence ends. The next chapter follows the last one. With RTF > 1 (today's PyTorch baseline: about 2 for IP voices, 6 for voice design) playback waits between
sentences, so for listening without pauses preprocess first; when the model reaches RTF < 1, read-ahead alone is
enough. That moment is the goal the Speed page tracks.

A "page" is what the reader shows at the current window size (two columns like Apple Books when wide). Pages are
counted per chapter, so "next 10 pages" means the sentences that start on those pages now.

## What gets measured

- `runs.jsonl`: one line per generated chunk: model `label`, voice, chars, `total_s`, `audio_s`, `rtf`, `parts_s`.
- `batches.jsonl`: one line per finished job (a page, 10 pages, a chapter, the benchmark passage): chunks
  generated / already cached, audio length, **wall time**, RTF, and the first chunk (▶ to listen).
- **Benchmark passage** (`config.json` → `bench_text`): the same text every time, per voice, so speed and quality
  can be compared across model versions by ear and by number.

## Storage

`~/Documents/Audiobook/` (config `data_dir`): `books/` holds the gathered EPUBs (copies; Apple Books' folder-EPUBs
are zipped into normal files), `audio/` the generated speech. The Storage page lists size per book × voice, converts
WAV to AAC (`afconvert`, 48 kbps) or deletes it. Ming writes 32-bit float WAV, so this matters: one page
(93 s of audio) went from 19.5 MB to 0.74 MB.

## First numbers (M4 24GB, Ming 0.5B, PyTorch MPS bf16, 2026-10-28)

| What | Audio | Generation | RTF | Time split |
|---|---|---|---|---|
| IP 灵小萌, one page of 霍乱时期的爱情 (14 chunks) | 92.9 s | 201 s | 2.17 | flow 67% · lm 21% · proj 7% · vae 3% |
| IP 灵小萌, benchmark passage | 18.0 s | 37 s | 2.06 | |
| VOICE_DESIGN + anchor, benchmark passage | 20.5 s | 127 s | 6.17 | flow 54% · lm 16% (+26 s once for the anchor) |

Voice design is ~3× slower than IP: the anchor prompt makes every step longer. The flow-matching head is the
biggest target either way.

## Next

- `backends/ming_mlx.py`: the MLX port (same protocol, new `label`), then `mx.compile`, fewer flow steps, 4-bit LM.
- Stream inside a chunk (play the first fragment while the rest generates).
- Batch two chunks per call (CFG pairs, read-ahead).
