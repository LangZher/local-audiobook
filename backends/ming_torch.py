"""Ming-3.0-tts (inclusionAI) through its own PyTorch code on MPS/CPU — the baseline every faster port is measured
against. opts (config.json): src (the Ming-3.0-tts checkout), model (weights folder), device (mps|cpu),
dtype (bf16), cache_dir (set by the server; voice-design anchors live there).

Voice tasks follow MingTTS.synthesize (https://github.com/inclusionAI/Ming-3.0-tts):
  IP            controls {"ip": "灵小柔"}, zero speaker embedding — the same named voice on every chunk
  VOICE_DESIGN  controls {"style": "..."} — one anchor clip is generated per style and reused as the prompt for
                every chunk, otherwise each chunk would invent a new voice
  FREEFORM      instruction "..." (anchored like VOICE_DESIGN)
  TTS           zero-shot clone: prompt_wav + prompt_text
"""
import functools
import hashlib
import os
import sys
import time
from pathlib import Path

IP_VOICES = ["灵小萌", "灵小雅", "灵婉", "灵朵儿", "灵小柔", "灵小梨", "灵小棠", "灵小芸", "灵小川", "灵楷",
             "灵旭", "灵江", "灵小锋", "灵空", "US_Emma", "UK_George", "UK_Charlotte"]
ANCHOR_TEXT = "这是一段用于确定音色的参考语音，之后的每一段都会沿用这个声音。"

OPTIONS = {
    "label": "Ming-3.0-tts 0.5B (PyTorch)",
    "fields": [
        {"key": "task", "label": "Task", "type": "select", "default": "IP",
         "choices": ["IP", "VOICE_DESIGN", "FREEFORM", "TTS"]},
        {"key": "ip", "label": "IP voice", "type": "select", "default": "灵小萌", "choices": IP_VOICES,
         "when": "IP", "hint": "Built-in voices; Ming's cookbooks/instructions.md describes each"},
        {"key": "style", "label": "Voice design", "type": "textarea", "when": "VOICE_DESIGN",
         "default": "温暖沉稳的中年男声，语速适中，像在深夜电台里给人读小说，叙述自然，不夸张。"},
        {"key": "instruction", "label": "Instruction", "type": "textarea", "when": "FREEFORM", "default": ""},
        {"key": "prompt_wav", "label": "Prompt wav path", "type": "text", "when": "TTS", "default": ""},
        {"key": "prompt_text", "label": "Prompt transcript", "type": "text", "when": "TTS", "default": ""},
        {"key": "raw_text", "label": "Raw text (no 24-char split inside Ming)", "type": "bool", "default": False},
        {"key": "max_length", "label": "Ming fragment length", "type": "number", "default": 24},
        {"key": "cfg", "label": "CFG", "type": "number", "default": 2.0},
        {"key": "seed", "label": "Seed", "type": "number", "default": 1895},
    ],
}

# Where the time goes per AR step (modeling_bailingmm.py `sample`), plus the VAE decode at the end.
PARTS = {"lm": ("model", "forward"), "flow": ("flowloss", "sample"),
         "proj": ("linear_proj_audio", "forward"), "vae_decode": ("audio", "decode")}


class Backend:
    def __init__(self, src, model, device="mps", dtype="bf16", cache_dir=None, **_):
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        sys.path.insert(0, str(Path(src).expanduser()))
        import torch
        from ming_tts import MingTTS
        self.torch, self.device, self.dtype = torch, device, dtype
        self.tts = MingTTS(str(Path(model).expanduser()), device=device, text_normalization=False)
        ext = self.tts.spkemb_extractor   # onnxruntime CAM++ takes fp32 only

        def spkemb(waveform):
            with torch.autocast(device_type=device, enabled=False):
                return ext(waveform.float())
        self.tts.spkemb_extractor = spkemb
        self.sample_rate = self.tts.sample_rate
        self.label = f"ming-0.5b torch {device} {dtype}"
        self.cache = Path(cache_dir or ".").expanduser()
        self.stats = {n: 0.0 for n in PARTS}
        for name, (attr, meth) in PARTS.items():
            obj = getattr(self.tts.model, attr)
            setattr(obj, meth, self._timed(name, getattr(obj, meth)))

    def _sync(self):
        if self.device == "mps":
            self.torch.mps.synchronize()

    def _timed(self, name, fn):
        @functools.wraps(fn)
        def w(*a, **k):
            self._sync()
            t = time.perf_counter()
            r = fn(*a, **k)
            self._sync()
            self.stats[name] += time.perf_counter() - t
            return r
        return w

    def _run(self, text, out_path, voice, **kw):
        from infer import seed_everything
        seed_everything(int(voice.get("seed", 1895)))
        # Ming's own autocast is CUDA-only; bf16 on MPS/CPU needs it here (fp32 + bf16 adds abort on MPS)
        with self.torch.autocast(device_type=self.device, dtype=self.torch.bfloat16, enabled=self.dtype == "bf16"):
            wav = self.tts.synthesize(text=text, output_path=str(out_path), cfg=float(voice.get("cfg", 2.0)),
                                      max_length=int(voice.get("max_length", 24)),
                                      raw_text=bool(voice.get("raw_text")), **kw)
        return wav.shape[-1] / self.sample_rate

    def _anchor(self, task, voice, kw):
        """VOICE_DESIGN/FREEFORM: one generated clip per description, reused as the prompt of every chunk."""
        key = hashlib.sha1(f"{task}|{kw}|{voice.get('seed')}".encode()).hexdigest()[:12]
        path = self.cache / f"anchor-{key}.wav"
        if not path.exists():
            self.cache.mkdir(parents=True, exist_ok=True)
            self._run(ANCHOR_TEXT, path, voice, task=task, use_zero_spk_emb=True, **kw)
        return path

    def synthesize(self, text, voice, out_path):
        for n in self.stats:
            self.stats[n] = 0.0
        task = voice.get("task", "IP").upper()
        if task == "IP":
            kw = dict(task="IP", controls={"ip": voice.get("ip") or "灵小萌"}, use_zero_spk_emb=True)
        elif task in ("VOICE_DESIGN", "FREEFORM"):
            ctl = {"controls": {"style": voice["style"]}} if task == "VOICE_DESIGN" else {"instruction": voice["instruction"]}
            anchor = self._anchor(task, voice, ctl)
            kw = dict(task=task, prompt_wav_path=str(anchor), prompt_text=ANCHOR_TEXT, use_spk_emb=True, **ctl)
        else:
            kw = dict(task="TTS", prompt_wav_path=voice["prompt_wav"], prompt_text=voice["prompt_text"], use_spk_emb=True)
        anchor_s = sum(self.stats.values())   # >0 only on the first chunk of a new voice design
        for n in self.stats:
            self.stats[n] = 0.0
        audio_s = self._run(text, out_path, voice, **kw)
        return {"audio_s": round(audio_s, 3), "parts_s": {n: round(s, 3) for n, s in self.stats.items()},
                "anchor_s": round(anchor_s, 3) if anchor_s else 0}
