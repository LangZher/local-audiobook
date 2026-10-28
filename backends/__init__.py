"""TTS backends. The model protocol (DESIGN.md § Model protocol):

Each backend is a module `backends/<name>.py` with

    OPTIONS = {...}            # what the voice editor shows; importing the module must stay cheap (stdlib only)
    class Backend:
        def __init__(self, **opts)                      # load the model once (runs in worker.py, in the backend's python)
        label: str                                      # names the speed setup, e.g. "ming-0.5b torch mps bf16"
        sample_rate: int
        def synthesize(self, text, voice, out_path) -> dict
            # write a wav to out_path; return {"audio_s": float, "parts_s": {part: seconds}, ...}

`voice` is the preset's dict (task, controls, seed, ...); its meaning belongs to the backend.
A new model = a new module here + an entry in config.json "backends" (which python runs it, and its opts).
"""
import importlib

NAMES = ("ming_torch", "say")


def options(name):
    return importlib.import_module(f"backends.{name}").OPTIONS
