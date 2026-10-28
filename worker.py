"""One model process: `python worker.py <backend> '<opts json>'`, run with the backend's own python (e.g. a conda env).
Loads the model once, then answers JSON lines on stdin/stdout:

  → {"text": "...", "voice": {...}, "out": "/path/x.wav"}
  ← {"ok": true, "audio_s": 3.2, "total_s": 12.9, "parts_s": {...}}   or  {"ok": false, "error": "..."}

The first line written is {"ready": true, "label": ..., "sample_rate": ..., "load_s": ...}.
Anything the model prints goes to stderr, so stdout stays the protocol.
"""
import importlib
import json
import os
import sys
import time
import traceback

proto = os.fdopen(os.dup(1), "w", buffering=1)
os.dup2(2, 1)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def send(d):
    proto.write(json.dumps(d, ensure_ascii=False) + "\n")


def main():
    name, opts = sys.argv[1], json.loads(sys.argv[2])
    t = time.perf_counter()
    try:
        b = importlib.import_module(f"backends.{name}").Backend(**opts)
    except Exception as e:
        send({"ready": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()})
        return
    send({"ready": True, "label": b.label, "sample_rate": b.sample_rate, "load_s": round(time.perf_counter() - t, 2)})
    for line in sys.stdin:
        req = json.loads(line)
        t = time.perf_counter()
        try:
            r = b.synthesize(req["text"], req.get("voice") or {}, req["out"])
            send({"ok": True, "total_s": round(time.perf_counter() - t, 3), **r})
        except Exception as e:
            traceback.print_exc()
            send({"ok": False, "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
