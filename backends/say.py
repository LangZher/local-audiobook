"""macOS `say`: instant, robotic. The floor every real model is compared against, and a way to test the app
without a model."""
import subprocess
import tempfile
import wave
from pathlib import Path

OPTIONS = {
    "label": "macOS say",
    "fields": [
        {"key": "voice", "label": "Voice", "type": "text", "default": "Tingting",
         "hint": "`say -v ?` lists voices (Tingting, Meijia, Samantha...)"},
        {"key": "rate", "label": "Words per minute", "type": "number", "default": 220},
    ],
}


class Backend:
    label = "macOS say"
    sample_rate = 22050

    def __init__(self, **opts):
        pass

    def synthesize(self, text, voice, out_path):
        with tempfile.TemporaryDirectory() as d:
            aiff = Path(d) / "a.aiff"
            cmd = ["say", "-o", str(aiff)]
            if voice.get("voice"):
                cmd += ["-v", voice["voice"]]
            if voice.get("rate"):
                cmd += ["-r", str(voice["rate"])]
            subprocess.run(cmd + [text], check=True)
            subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{self.sample_rate}", str(aiff), str(out_path)],
                           check=True)
        with wave.open(str(out_path)) as w:
            return {"audio_s": w.getnframes() / w.getframerate(), "parts_s": {}}
