#!/usr/bin/env python3
"""Local Audiobook: library + reader + TTS job queue. Stdlib only; the model runs in worker.py (its own python).

  python3 server.py [--port 8765] [--no-open]

Data (config "data_dir", default ~/Documents/Audiobook): books/ (the EPUBs, gathered in one folder),
cache/ (parsed books, covers), audio/<book>/<preset>/<text hash>.wav|m4a, voices/ (voice-design anchors),
runs.jsonl (one line per generated chunk), batches.jsonl (one line per finished page/chapter job),
presets.json, progress.json, worker.log.
"""
import argparse
import hashlib
import heapq
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import epub
import backends

ROOT = Path(__file__).resolve().parent
VERSION = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else "dev"


def load_config():
    cfg = json.loads((ROOT / "config.json").read_text())
    local = ROOT / "config.local.json"   # your own paths; not in git
    if local.exists():
        over = json.loads(local.read_text())
        for k, v in over.items():
            if k == "backends":
                for b, bv in v.items():
                    cfg["backends"].setdefault(b, {}).update(bv)
            else:
                cfg[k] = v
    return cfg


CFG = load_config()
DATA = Path(os.environ.get("AUDIOBOOK_DATA") or CFG["data_dir"]).expanduser()   # env: a scratch library for tests
for sub in ("books", "cache", "audio", "voices"):
    (DATA / sub).mkdir(parents=True, exist_ok=True)
LOCK = threading.RLock()


def jload(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def jsave(path, obj):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    tmp.replace(path)


def append_jsonl(path, obj):
    with LOCK, open(path, "a") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path):
    try:
        return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
    except OSError:
        return []


# ---------- presets: a voice = backend + its settings; the id is a hash, so any change gets fresh audio ----------
DEFAULT_PRESETS = [
    {"name": "灵小萌 · Ming 0.5B", "backend": "ming_torch", "voice": {"task": "IP", "ip": "灵小萌", "seed": 1895}},
    {"name": "深夜电台男声 · voice design", "backend": "ming_torch",
     "voice": {"task": "VOICE_DESIGN", "seed": 1895,
               "style": "温暖沉稳的中年男声，语速适中，像在深夜电台里给人读小说，叙述自然，不夸张。"}},
    {"name": "Tingting · macOS say", "backend": "say", "voice": {"voice": "Tingting", "rate": 220}},
]


def _hash(backend, voice):
    return hashlib.sha1(json.dumps([backend, voice], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10]


def _defaults(backend):
    try:
        return {f["key"]: f.get("default") for f in backends.options(backend)["fields"]}
    except (ImportError, KeyError):
        return {}


def preset_id(p):
    # fields left at their default do not count, so the editor filling them in keeps the id (and its audio)
    d = _defaults(p["backend"])
    return _hash(p["backend"], {k: v for k, v in p["voice"].items() if k not in d or v != d[k]})


def legacy_ids(p):
    """Ids this voice had before v0.2.2: the hash of the voice with any subset of its default-valued fields."""
    d = _defaults(p["backend"])
    keep = {k: v for k, v in p["voice"].items() if k not in d or v != d[k]}
    # the browser saves 2.0 as 2, so try the JSON round-trip form of each default
    opt = {k: int(d[k]) if isinstance(d[k], float) and d[k].is_integer() else d[k] for k in d if k not in keep}
    out = set()
    for r in range(len(opt) + 1):
        for ks in itertools.combinations(sorted(opt), r):
            out.add(_hash(p["backend"], {**keep, **{k: opt[k] for k in ks}}))
    return out - {preset_id(p)}


def presets():
    ps = jload(DATA / "presets.json", None) or DEFAULT_PRESETS
    return [{**p, "id": preset_id(p)} for p in ps]


def merge_legacy_audio():
    """Move audio stored under an older id of a current voice into that voice's folder (same voice, same text)."""
    moved = 0
    for p in presets():
        for old in legacy_ids(p):
            for src in (DATA / "audio").glob(f"*/{old}"):
                dest = src.parent / p["id"]
                dest.mkdir(exist_ok=True)
                for f in src.iterdir():
                    if (dest / f.name).exists():
                        f.unlink()
                    else:
                        f.replace(dest / f.name)
                        moved += 1
                src.rmdir()
    return moved


def preset(pid):
    return next((p for p in presets() if p["id"] == pid), None)


# ---------- books ----------
BOOKS = {}   # id -> parsed book (memory cache)


def book_files():
    out = {}
    for f in sorted((DATA / "books").glob("*.epub")):
        try:
            out[epub.quick_info(f)["id"]] = f
        except Exception as e:
            print(f"skip {f.name}: {e}", file=sys.stderr)
    return out


FILES = {}


def refresh_files():
    FILES.clear()
    FILES.update(book_files())


def bench_book():
    chunks = [{"id": f"0-{i}", "t": t} for i, t in enumerate(epub.split_chunks(CFG["bench_text"], CFG["chunk_chars"]))]
    return {"id": "_bench", "title": "Benchmark passage", "author": "", "max_chars": CFG["chunk_chars"],
            "chapters": [{"title": "bench", "paras": [{"h": False, "chunks": chunks}]}]}


# a short sample per language, so a voice can be heard before reading with it (Ming's voices speak both)
SAMPLES = {"zh": "你好，这是我的声音。今天我们一起读一本好书，一页一页，慢慢地读。",
           "en": "Hello, this is how I sound. Let's read a good book together, one page at a time."}


def samples_book():
    chunks = [{"id": f"0-{i}", "t": t} for i, t in enumerate(SAMPLES.values())]
    return {"id": "_samples", "title": "Voice samples", "author": "", "max_chars": CFG["chunk_chars"],
            "chapters": [{"title": "samples", "paras": [{"h": False, "chunks": chunks}]}]}


SAMPLE_IDS = {lang: f"0-{i}" for i, lang in enumerate(SAMPLES)}
SPECIAL = {"_bench": bench_book, "_samples": samples_book}


def get_book(bid):
    with LOCK:
        if bid in BOOKS:
            return BOOKS[bid]
        cache = DATA / "cache" / f"{bid}.json"
        b = SPECIAL[bid]() if bid in SPECIAL else jload(cache, None)
        if not b or b.get("max_chars") != CFG["chunk_chars"] or (bid not in SPECIAL and b.get("parser") != epub.PARSER):
            if bid not in FILES:
                refresh_files()
            b = epub.parse(FILES[bid], CFG["chunk_chars"])
            jsave(cache, b)
        b["index"] = {c["id"]: c["t"] for ch in b["chapters"] for p in ch["paras"] for c in p["chunks"]}
        BOOKS[bid] = b
        return b


def library():
    prog = jload(DATA / "progress.json", {})
    out = []
    for bid, f in FILES.items():
        b = get_book(bid)
        chars = sum(len(t) for t in b["index"].values())
        audio = {}   # voice id -> chunks with audio (counted from files, so it is cheap and always true)
        for d in (DATA / "audio" / bid).glob("*"):
            n = sum(1 for x in d.iterdir() if x.suffix in (".wav", ".m4a"))
            if n:
                audio[d.name] = n
        out.append({"id": bid, "title": b["title"], "author": b["author"], "chapters": len(b["chapters"]),
                    "chars": chars, "chunks": len(b["index"]), "audio": audio, "file": f.name,
                    "progress": prog.get(bid), "added": f.stat().st_mtime})
    out.sort(key=lambda x: -max((x["progress"] or {}).get("ts", 0), x["added"]))
    return out


def cover(bid):
    p = DATA / "cache" / f"{bid}.cover"
    if not p.exists():
        c = epub.Epub(FILES[bid]).cover()
        p.write_bytes(c[0] if c else b"")
    return p.read_bytes()


def scan():
    """EPUBs anywhere Spotlight knows plus the configured folders (files, and Apple Books' folder-EPUBs)."""
    seen = set()
    try:
        r = subprocess.run(["mdfind", 'kMDItemFSName == "*.epub"c'], capture_output=True, text=True, timeout=20)
        seen.update(r.stdout.splitlines())
    except (OSError, subprocess.TimeoutExpired):
        pass
    for d in CFG["scan_dirs"]:
        d = Path(d).expanduser()
        if d.exists():
            for p in d.glob("**/*.epub"):
                seen.add(str(p))
    lib_dir = str(DATA / "books")
    have = set(FILES)
    have_titles = {get_book(b)["title"] for b in FILES}
    out, ids = [], set()
    for s in sorted(seen, key=lambda s: ("iBooks" not in s, s)):   # Apple Books copies win ties
        p = Path(s)
        if s.startswith(lib_dir) or "/.Trash/" in s or not p.exists() or any(q.suffix == ".epub" for q in p.parents):
            continue
        try:
            info = epub.quick_info(p)
        except Exception:
            continue
        if info["id"] in ids:
            continue
        ids.add(info["id"])
        size = p.stat().st_size if p.is_file() else sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        out.append({**info, "path": s, "size": size, "imported": info["id"] in have or info["title"] in have_titles,
                    "where": where(s)})
    # one entry per title: the Apple Books copy if there is one (that is the one I read), else the largest
    groups = {}
    for f in out:
        groups.setdefault(f["title"], []).append(f)
    new, have_list = [], []
    for title, fs in groups.items():
        fs.sort(key=lambda f: ("iBooks" not in f["path"], -f["size"]))
        best = {**fs[0], "others": [f["where"] for f in fs[1:]]}
        (have_list if any(f["imported"] for f in fs) else new).append(best)
    return {"new": new, "have": have_list}


def where(path):
    home = str(Path.home())
    if "iCloud~com~apple~iBooks" in path:
        return "Apple Books"
    if "com~apple~CloudDocs" in path:
        return "iCloud Drive"
    return "~" + str(Path(path).parent)[len(home):] if path.startswith(home) else str(Path(path).parent)


def safe_name(s):
    return re.sub(r'[/\\:*?"<>|]', "_", s).strip()[:80] or "book"


def import_books(paths):
    """Copy into books/ (originals stay where they are). Folder-EPUBs are zipped into a normal .epub file."""
    done = []
    for s in paths:
        src = Path(s)
        info = epub.quick_info(src)
        if info["id"] in FILES:
            continue
        dest = DATA / "books" / f"{safe_name(info['title'])}.epub"
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}-{info['id'][:6]}.epub")
        if src.is_dir():
            with zipfile.ZipFile(dest, "w") as z:
                mt = src / "mimetype"
                if mt.exists():
                    z.write(mt, "mimetype", compress_type=zipfile.ZIP_STORED)
                for f in sorted(src.rglob("*")):
                    if f.is_file() and f.name != "mimetype" and f.name != ".DS_Store":
                        z.write(f, str(f.relative_to(src)), compress_type=zipfile.ZIP_DEFLATED)
        else:
            shutil.copy2(src, dest)
        done.append({"id": info["id"], "title": info["title"]})
    refresh_files()
    return done


# ---------- audio files ----------
def audio_dir(bid, pid):
    return DATA / "audio" / bid / pid


def audio_path(bid, pid, text, ext="wav"):
    return audio_dir(bid, pid) / f"{hashlib.sha1(text.encode()).hexdigest()[:16]}.{ext}"


def find_audio(bid, pid, text):
    for ext in ("m4a", "wav"):
        p = audio_path(bid, pid, text, ext)
        if p.exists():
            return p
    return None


# ---------- worker: one model process at a time ----------
class Worker:
    def __init__(self):
        self.proc, self.name, self.label, self.state, self.error, self.load_s = None, None, None, "stopped", None, None

    def ensure(self, name):
        if self.proc and self.proc.poll() is None and self.name == name:
            return
        self.stop()
        b = CFG["backends"][name]
        opts = {**b.get("opts", {}), "cache_dir": str(DATA / "voices")}
        self.state, self.name, self.error = "loading", name, None
        if (DATA / "worker.log").exists() and (DATA / "worker.log").stat().st_size > 5e6:   # Ming logs a progress bar per call
            (DATA / "worker.log").unlink()
        log = open(DATA / "worker.log", "a")
        log.write(f"\n--- {time.strftime('%F %T')} start {name}\n")
        log.flush()
        py = os.path.expanduser(b.get("python") or sys.executable)
        self.proc = subprocess.Popen([py, str(ROOT / "worker.py"), name, json.dumps(opts)], cwd=ROOT, text=True,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, bufsize=1)
        hello = json.loads(self.proc.stdout.readline() or '{"ready": false, "error": "worker exited, see worker.log"}')
        if not hello.get("ready"):
            self.state, self.error = "error", hello.get("error")
            self.stop(keep_state=True)
            raise RuntimeError(self.error)
        self.state, self.label, self.load_s = "ready", hello["label"], hello["load_s"]

    def synth(self, text, voice, out):
        self.proc.stdin.write(json.dumps({"text": text, "voice": voice, "out": str(out)}, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            self.state, self.error = "error", "worker died, see worker.log"
            raise RuntimeError(self.error)
        return json.loads(line)

    def stop(self, keep_state=False):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        if not keep_state:
            self.state = "stopped"


# ---------- queue: priority 0 = play now, 1 = read-ahead, 2 = preprocess ----------
# Ming 0.5B runs batch 1 and is bound by per-step overhead, not by the GPU, so several model processes side by side
# make more audio per minute (M4 24GB, 2026-10-30: 3 workers 2.1× one) though each chunk takes longer. SLOW is that
# per-chunk slowdown, used to estimate times for a worker count not measured yet.
SLOW = {1: 1.0, 2: 1.15, 3: 1.35, 4: 1.75, 5: 2.6, 6: 4.9}
IDLE_S = 90   # extra workers quit after this long without work, giving their memory back


class Queue:
    def __init__(self):
        self.cv = threading.Condition(LOCK)
        self.heap, self.best, self.jobs, self.seq = [], {}, {}, itertools.count()
        self.batches, self.running, self.paused = {}, {}, False
        self.n = max(1, int(jload(DATA / "settings.json", {}).get("workers", 1)))
        self.pool = []   # one Worker per slot; slot i works while i < self.n
        self.rate = {}   # (preset, workers) -> [generation s, chars] of recent chunks, for time estimates
        for r in read_jsonl(DATA / "runs.jsonl")[-1500:]:
            self.learn(r)
        self.grow()

    @property
    def worker(self):
        return self.pool[0]

    def grow(self):
        while len(self.pool) < self.n:
            self.pool.append(Worker())
            threading.Thread(target=self.loop, args=(len(self.pool) - 1,), daemon=True).start()

    def set_workers(self, n):
        with self.cv:
            self.n = max(1, min(8, int(n)))
            s = jload(DATA / "settings.json", {})
            s["workers"] = self.n
            jsave(DATA / "settings.json", s)
            self.grow()
            self.cv.notify_all()

    def add(self, bid, pid, ids, priority, label):
        book, p = get_book(bid), preset(pid)
        if p is None:
            raise ValueError("unknown preset")
        with self.cv:
            batch = {"id": f"b{int(time.time() * 1000)}", "label": label, "book": bid, "title": book["title"],
                     "preset": pid, "preset_name": p["name"], "backend": p["backend"], "priority": priority, "n": 0, "cached": 0,
                     "done": 0, "failed": 0, "chars": 0, "audio_s": 0.0, "gen_s": 0.0, "created": time.time(),
                     "started": None, "finished": None, "first": None}
            for cid in ids:
                text = book["index"].get(cid)
                if not text:
                    continue
                batch["n"] += 1
                batch["first"] = batch["first"] or cid
                if find_audio(bid, pid, text):
                    batch["cached"] += 1
                    continue
                key = (bid, pid, cid)
                job = self.jobs.setdefault(key, {"text": text, "batches": set()})
                job["batches"].add(batch["id"])
                if priority < self.best.get(key, 99):
                    self.best[key] = priority
                    heapq.heappush(self.heap, (priority, next(self.seq), key))
            if batch["n"] > batch["cached"]:
                self.batches[batch["id"]] = batch
            self.cv.notify_all()
            return batch

    def learn(self, run):
        k = (run["preset"], run.get("workers", 1))
        g, c = self.rate.get(k, (0.0, 0))
        if c > 1500:   # keep it recent: a faster model shows within a few pages
            g, c = g / 2, c / 2
        self.rate[k] = (g + run["total_s"], c + run["chars"])

    def s_per_char(self, pid, n=None):
        """Seconds one worker takes per character of this voice, with n workers running (measured, else scaled)."""
        n = n or self.n
        g, c = self.rate.get((pid, n), (0, 0))
        if c > 100:
            return g / c
        seen = [(m, gc) for (q, m), gc in self.rate.items() if q == pid and gc[1]] or \
               [(m, gc) for (q, m), gc in self.rate.items() if gc[1]]
        if not seen:
            return 0.75 * SLOW.get(n, n)   # Ming 0.5B on MPS when nothing is measured yet
        m, (g, c) = max(seen, key=lambda x: x[1][1])
        return g / c * SLOW.get(n, n) / SLOW.get(m, m)

    def left_s(self, chars_by_pid):
        """Wall time to make that much text with all workers on it."""
        return sum(ch * self.s_per_char(pid) for pid, ch in chars_by_pid.items()) / self.n

    def cancel_batch(self, bid):
        """Drop one job (e.g. a chapter being made ahead); chunks other jobs still want stay queued."""
        with self.cv:
            for key in [k for k, j in self.jobs.items() if bid in j["batches"]]:
                self.jobs[key]["batches"].discard(bid)
                if not self.jobs[key]["batches"]:
                    self.jobs.pop(key)
                    self.best.pop(key, None)
            self.batches.pop(bid, None)

    def cancel(self, max_priority=2, book=None):
        with self.cv:
            for key in [k for k, pr in self.best.items() if pr <= max_priority and (book is None or k[0] == book)]:
                self.best.pop(key, None)
                for b in self.jobs.pop(key, {"batches": ()})["batches"]:
                    if b in self.batches:
                        self.batches[b]["failed"] += 1
                        self.maybe_finish(self.batches[b])

    def maybe_finish(self, b):
        if b["done"] + b["failed"] + b["cached"] >= b["n"] and not b["finished"]:
            b["finished"] = time.time()
            if b["done"]:
                rec = {k: v for k, v in b.items()}
                rec["wall_s"] = round(b["finished"] - (b["started"] or b["finished"]), 2)
                rec["rtf"] = round(b["gen_s"] / b["audio_s"], 3) if b["audio_s"] else None
                append_jsonl(DATA / "batches.jsonl", rec)
            self.batches.pop(b["id"], None)

    def next_job(self, slot):
        with self.cv:
            idle = time.time()
            while True:
                # paused holds the background work (priority 2); play and read-ahead still go
                while slot < self.n and self.heap and not (self.paused and self.heap[0][0] >= 2):
                    pr, _, key = heapq.heappop(self.heap)
                    if self.best.get(key) == pr and key in self.jobs:
                        self.best.pop(key)
                        return key, pr, self.jobs.pop(key)
                if (slot >= self.n or time.time() - idle > IDLE_S) and slot and self.pool[slot].proc:
                    self.pool[slot].stop()   # not needed now: free its memory (slot 0 stays loaded)
                self.cv.wait(timeout=IDLE_S / 3)

    def loop(self, slot):
        worker = self.pool[slot]
        while True:
            key, pr, job = self.next_job(slot)
            bid, pid, cid = key
            p = preset(pid)
            bs = [self.batches[b] for b in job["batches"] if b in self.batches]
            ok, run = False, None
            try:
                if p is None:
                    raise RuntimeError("preset was deleted")
                out = audio_path(bid, pid, job["text"])
                out.parent.mkdir(parents=True, exist_ok=True)
                if not find_audio(bid, pid, job["text"]):
                    for b in bs:
                        b["started"] = b["started"] or time.time()
                    cur = self.running[slot] = {
                        "book": bid, "preset": pid, "id": cid, "chars": len(job["text"]), "started": time.time(),
                        "priority": pr, "batches": sorted(job["batches"]), "slot": slot,
                        "est_s": round(len(job["text"]) * self.s_per_char(pid), 1)}
                    worker.ensure(p["backend"])
                    cur["started"] = time.time()
                    tmp = out.with_suffix(f".tmp{slot}.wav")
                    r = worker.synth(job["text"], p["voice"], tmp)
                    if not r.get("ok"):
                        raise RuntimeError(r.get("error"))
                    tmp.replace(out)
                    run = {"ts": time.time(), "book": bid, "preset": pid, "preset_name": p["name"], "chunk": cid,
                           "chars": len(job["text"]), "backend": p["backend"], "label": worker.label,
                           "workers": sum(1 for w in self.pool if w.proc),
                           "task": p["voice"].get("task"), "total_s": r["total_s"], "audio_s": r["audio_s"],
                           "rtf": round(r["total_s"] / r["audio_s"], 3) if r["audio_s"] else None,
                           "parts_s": r.get("parts_s"), "anchor_s": r.get("anchor_s", 0), "priority": pr,
                           "batches": sorted(job["batches"])}
                    append_jsonl(DATA / "runs.jsonl", run)
                    with self.cv:
                        self.learn(run)
                ok = True
            except Exception as e:
                print(f"job {key} failed: {e}", file=sys.stderr)
                worker.error = str(e)
            finally:
                self.running.pop(slot, None)
            with self.cv:
                for b in bs:
                    if ok:
                        b["done"] += 1
                        if run:
                            b["audio_s"] += run["audio_s"]
                            b["gen_s"] += run["total_s"]
                            b["chars"] += run["chars"]
                    else:
                        b["failed"] += 1
                    self.maybe_finish(b)
            if not ok:
                time.sleep(1)

    def snapshot(self):
        """Queue state, with time left per job: its chunks still to make × the voice's measured seconds per char."""
        with self.cv:
            pend, left = {}, {}
            for key, pr in self.best.items():
                pend[pr] = pend.get(pr, 0) + 1
                job = self.jobs.get(key)
                for b in job["batches"] if job else ():
                    left[b] = left.get(b, 0) + len(job["text"]) * self.s_per_char(key[1])
            running = []
            for c in sorted(self.running.values(), key=lambda c: (c["priority"], c["started"])):
                el = time.time() - c["started"]
                running.append(dict(c, elapsed=round(el, 1)))
                for b in c["batches"]:
                    left[b] = left.get(b, 0) + max(0, c["est_s"] - el)
            # a job's chunks are shared out over all workers
            batches = [dict(b, left_s=round(left.get(b["id"], 0) / self.n)) for b in self.batches.values()]
            w = next((w for w in self.pool if w.state in ("loading", "error")), self.worker)
            chars = {}
            for (_, pid, _), j in self.jobs.items():
                chars[pid] = chars.get(pid, 0) + len(j["text"])
            return {"worker": {"state": w.state, "backend": w.name, "label": w.label, "error": w.error,
                               "load_s": w.load_s},
                    "workers": self.n, "loaded": sum(1 for x in self.pool if x.proc),
                    "current": running[0] if running else None, "running": running,
                    "pending": pend, "paused": self.paused,
                    "left_s": round(self.left_s(chars) + sum(max(0, c["est_s"] - c["elapsed"]) for c in running) / self.n),
                    "batches": sorted(batches, key=lambda b: b["created"])}


# ---------- system: memory, GPU, how many workers fit ----------
RAM = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout or 0)
WORKER_GB = 4.8   # one Ming 0.5B process on MPS, peak footprint while generating (M4, 2026-10-30)


def footprint(pid):
    """Physical footprint of a process in bytes (what Activity Monitor calls Memory; includes GPU buffers)."""
    try:
        o = subprocess.run(["footprint", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"Footprint:\s*([\d.]+)\s*([KMG])B", o)
        return float(m.group(1)) * {"K": 1e3, "M": 1e6, "G": 1e9}[m.group(2)] if m else 0
    except (OSError, subprocess.TimeoutExpired):
        return 0


def system(pid=None):
    vm = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
    page = int(re.search(r"page size of (\d+)", vm).group(1))
    pages = {k.strip().lower(): int(v.strip(" .")) for k, v in re.findall(r"^Pages ([^:]+):\s+(\d+)\.", vm, re.M)}
    avail = page * sum(pages.get(k, 0) for k in ("free", "inactive", "speculative", "purgeable"))
    gpu = {}
    io = subprocess.run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"], capture_output=True, text=True).stdout
    for k, name in (("util", "Device Utilization %"), ("alloc", "Alloc system memory"), ("in_use", "In use system memory")):
        m = re.search(rf'"{re.escape(name)}"=(\d+)', io)
        if m:
            gpu[k] = int(m.group(1))
    gpu["budget"] = int(RAM * 0.74)   # Metal's recommended working set (torch.mps.recommended_max_memory: 19.1 of 25.8 GB, M4)
    workers = []
    for i, w in enumerate(Q.pool):
        alive = w.proc and w.proc.poll() is None
        workers.append({"slot": i, "state": w.state if alive else "stopped", "backend": w.name,
                        "pid": w.proc.pid if alive else None, "bytes": footprint(w.proc.pid) if alive else 0,
                        "busy": i in Q.running})
    ours = sum(w["bytes"] for w in workers)
    per = max([w["bytes"] for w in workers if w["busy"]] + [WORKER_GB * 1e9])
    # what fits: memory free now plus what our workers already hold, keeping 3 GB for the rest of the Mac
    fit_mem = int((avail + ours - 3e9) // per)
    fit_gpu = int(gpu["budget"] // per)
    by_n = {}   # measured: per worker count, generation time per audio second of one chunk
    for r in read_jsonl(DATA / "runs.jsonl")[-3000:]:
        if r.get("backend") != "ming_torch" or not r.get("audio_s"):
            continue
        g = by_n.setdefault(r.get("workers", 1), {"chunks": 0, "gen_s": 0.0, "audio_s": 0.0})
        g["chunks"] += 1
        g["gen_s"] += r["total_s"]
        g["audio_s"] += r["audio_s"]
    return {"ram": RAM, "available": avail, "gpu": gpu, "workers": workers, "ours": ours, "per_worker": per,
            "n": Q.n, "max": max(1, min(fit_mem, fit_gpu, 8)), "fit_mem": fit_mem, "fit_gpu": fit_gpu,
            "recommended": max(1, min(fit_mem, fit_gpu, 3)), "by_n": by_n,
            # wall seconds per character of this voice with n workers: the reader's "a chapter takes…"
            "s_per_char": {n: Q.s_per_char(pid, n) / n for n in range(1, 7)} if pid else None}


# ---------- storage ----------
def storage():
    names = {p["id"]: p["name"] for p in presets()}
    rows = []
    for bd in sorted((DATA / "audio").glob("*")):
        for pd in sorted(bd.glob("*")):
            wav = [f.stat().st_size for f in pd.glob("*.wav")]
            m4a = [f.stat().st_size for f in pd.glob("*.m4a")]
            if not wav and not m4a:
                continue
            title = get_book(bd.name)["title"] if bd.name in SPECIAL else (BOOKS.get(bd.name) or {}).get("title")
            if title is None and bd.name in FILES:
                title = get_book(bd.name)["title"]
            rows.append({"book": bd.name, "title": title or bd.name, "preset": pd.name,
                         "preset_name": names.get(pd.name, "(old voice settings)"),
                         "wav_n": len(wav), "wav_bytes": sum(wav), "m4a_n": len(m4a), "m4a_bytes": sum(m4a)})
    du = shutil.disk_usage(DATA)
    return {"rows": rows, "total": sum(r["wav_bytes"] + r["m4a_bytes"] for r in rows), "free": du.free,
            "data_dir": str(DATA)}


def compress(bid, pid, kbps=48):
    n = 0
    for f in audio_dir(bid, pid).glob("*.wav"):
        out = f.with_suffix(".m4a")
        r = subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", str(kbps * 1000), str(f), str(out)],
                           capture_output=True)
        if r.returncode == 0 and out.exists():
            f.unlink()
            n += 1
    return n


def delete_audio(bid, pid):
    d = audio_dir(bid, pid)
    if d.is_dir() and d.parent.parent == DATA / "audio":
        shutil.rmtree(d)


# ---------- http ----------
Q = None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, path, ctype):
        size = path.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range")   # WebKit only plays media served with byte ranges
        m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m:
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else end
            else:
                start = size - int(m.group(2))
        self.send_response(206 if m else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if m:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            self.wfile.write(f.read(end - start + 1))

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            self.route_get(u.path, q)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    def route_get(self, path, q):
        if path == "/" or path.startswith("/web/"):
            f = ROOT / "web" / ("index.html" if path == "/" else path[5:])
            if not f.resolve().is_relative_to(ROOT / "web") or not f.is_file():
                return self.send_json({"error": "not found"}, 404)
            ct = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}.get(f.suffix, "application/octet-stream")
            return self.send_file(f, ct + "; charset=utf-8")
        if path == "/api/state":
            return self.send_json({"version": VERSION, "presets": presets(), "chunk_chars": CFG["chunk_chars"],
                                   "lookahead": CFG["lookahead"], "data_dir": str(DATA),
                                   "backends": {n: backends.options(n) for n in CFG["backends"] if n in backends.NAMES}})
        if path == "/api/library":
            return self.send_json(library())
        if path == "/api/cover":
            data = cover(q["book"])
            if not data:
                return self.send_json({"error": "no cover"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg" if data[:2] == b"\xff\xd8" else "image/png")
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            return self.wfile.write(data)
        if path == "/api/book":
            b = get_book(q["id"])
            return self.send_json({k: v for k, v in b.items() if k != "index"})
        if path == "/api/ready":
            b = get_book(q["book"])
            ch = b["chapters"][int(q["ch"])]
            ids = [c["id"] for p in ch["paras"] for c in p["chunks"] if find_audio(q["book"], q["preset"], c["t"])]
            return self.send_json({"ids": ids})
        if path == "/api/audio":
            text = get_book(q["book"])["index"][q["id"]]
            f = find_audio(q["book"], q["preset"], text)
            if not f:
                return self.send_json({"error": "not generated"}, 404)
            return self.send_file(f, "audio/mp4" if f.suffix == ".m4a" else "audio/wav")
        if path == "/api/queue":
            return self.send_json(Q.snapshot())
        if path == "/api/storage":
            return self.send_json(storage())
        if path == "/api/system":
            return self.send_json(system(q.get("preset")))
        if path == "/api/samples":   # which voices have their samples made
            return self.send_json({p["id"]: {lang: bool(find_audio("_samples", p["id"], t)) for lang, t in SAMPLES.items()}
                                   for p in presets()})
        if path == "/api/runs":
            return self.send_json({"runs": read_jsonl(DATA / "runs.jsonl")[-2000:],
                                   "batches": read_jsonl(DATA / "batches.jsonl")})
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            d = self.body()
            r = self.route_post(u.path, d)
            self.send_json(r if r is not None else {"ok": True})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    def route_post(self, path, d):
        if path == "/api/scan":
            return scan()
        if path == "/api/import":
            return {"imported": import_books(d["paths"])}
        if path == "/api/enqueue":
            if d.get("replace"):   # a new "play from here" drops the old play/read-ahead jobs of this book
                Q.cancel(max_priority=1, book=d["book"])
            return Q.add(d["book"], d["preset"], d["ids"], int(d.get("priority", 2)), d.get("label", ""))
        if path == "/api/cancel":
            Q.cancel(int(d.get("max_priority", 2)), d.get("book"))
            return None
        if path == "/api/cancel-batch":
            Q.cancel_batch(d["id"])
            return None
        if path == "/api/sample":
            ids = [SAMPLE_IDS[lang] for lang in d.get("langs") or SAMPLES]
            return Q.add("_samples", d["preset"], ids, 0, "voice sample")
        if path == "/api/pause":
            with Q.cv:
                Q.paused = bool(d.get("paused"))
                Q.cv.notify_all()
            return None
        if path == "/api/bench":
            b = bench_book()
            ids = [c["id"] for c in b["chapters"][0]["paras"][0]["chunks"]]
            if d.get("fresh"):
                delete_audio("_bench", d["preset"])
            return Q.add("_bench", d["preset"], ids, 1, "benchmark passage")
        if path == "/api/presets":
            ps = [{"name": p["name"], "backend": p["backend"], "voice": p["voice"]} for p in d["presets"]]
            jsave(DATA / "presets.json", ps)
            merge_legacy_audio()
            return {"presets": presets()}
        if path == "/api/progress":
            with LOCK:
                prog = jload(DATA / "progress.json", {})
                prog[d["book"]] = {"ch": d["ch"], "chunk": d.get("chunk"), "page": d.get("page", 0), "ts": time.time()}
                jsave(DATA / "progress.json", prog)
            return None
        if path == "/api/compress":
            return {"converted": compress(d["book"], d["preset"], int(d.get("kbps", 48)))}
        if path == "/api/delete":
            delete_audio(d["book"], d["preset"])
            return None
        if path == "/api/stop-worker":
            for w in Q.pool:
                w.stop()
            return None
        if path == "/api/workers":
            Q.set_workers(d["n"])
            return system()
        raise ValueError("not found")


def watch_parent(pid):
    while True:
        try:
            os.kill(pid, 0)
        except OSError:
            for w in Q.pool:
                w.stop()
            os._exit(0)
        time.sleep(2)


def main():
    global Q
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=CFG.get("port", 8765))
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--parent-pid", type=int)
    a = ap.parse_args()
    refresh_files()
    merge_legacy_audio()
    Q = Queue()
    srv = None
    for port in range(a.port, a.port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), H)
            break
        except OSError:
            continue
    url = f"http://127.0.0.1:{srv.server_port}/"
    print(f"Local Audiobook {VERSION} · data {DATA}\n{url}", flush=True)
    if a.parent_pid:
        threading.Thread(target=watch_parent, args=(a.parent_pid,), daemon=True).start()
    if not a.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    finally:
        for w in Q.pool:
            w.stop()


if __name__ == "__main__":
    main()
