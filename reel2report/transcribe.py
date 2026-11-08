"""Optional narration transcription (local, via faster-whisper).

Testers who talk while recording ("this button does nothing", "why is it so
slow") give the most reliable signal of what went wrong. Install with:

    pip install "reel2report[audio]"
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .extract import _require


def has_audio(video: Path) -> bool:
    out = subprocess.run(
        [_require("ffprobe"), "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True,
    ).stdout.strip()
    return bool(out)


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def transcribe(video: Path, work: Path, model: str = "small", language: str | None = "zh",
               hints: str = "", log=print) -> list[dict]:
    """Returns [{"start", "end", "text"}]. Cached per model + hints in work/.

    hints: product names and on-screen terms ("Tunemate, 首页, 听辨") that bias
    Whisper towards the right spelling of words it would otherwise mishear.
    """
    import hashlib

    tag = hashlib.sha1(hints.encode()).hexdigest()[:6] if hints else ""
    cache = work / (f"transcript_{model}_{tag}.json" if tag else f"transcript_{model}.json")
    if cache.exists():
        return json.loads(cache.read_text("utf-8"))
    from faster_whisper import WhisperModel

    import numpy as np

    # decode with ffmpeg ourselves: avoids faster-whisper's PyAV dependency and its version quirks
    pcm = subprocess.run([_require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(video),
                          "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         check=True, capture_output=True).stdout
    audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
    log(f"      加载语音模型 {model}（首次使用会自动下载）")
    wm = WhisperModel(model, device="auto", compute_type="int8")
    segments, _info = wm.transcribe(
        audio, language=language or None, vad_filter=True, word_timestamps=True,
        # nudges Whisper towards simplified Chinese punctuation/characters
        initial_prompt=_prompt(language, hints),
        hotwords=hints or None,
    )
    words = [(w.start, w.end, w.word) for seg in segments for w in (seg.words or [])]
    result = split_phrases(words)
    cache.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    return result


def _prompt(language: str | None, hints: str) -> str | None:
    zh = (language or "").startswith("zh")
    base = "以下是软件测试过程中的普通话口述。" if zh else ""
    if hints:
        base += ("涉及的词：" if zh else "Terms: ") + hints
    return base or None


PUNCT = set("，。！？、,.!?;；")


def split_phrases(words: list[tuple[float, float, str]], max_gap: float = 0.5,
                  max_len: float = 6.0) -> list[dict]:
    """Whisper segments can span many sentences; re-split on punctuation and pauses
    so each phrase can be pinned to the screen it was said on."""
    out: list[dict] = []
    cur: list[tuple[float, float, str]] = []

    def flush():
        text = "".join(w for _, _, w in cur).strip().strip("，,、")
        if text:
            out.append({"start": round(cur[0][0], 2), "end": round(cur[-1][1], 2), "text": text})
        cur.clear()

    for w in words:
        if cur and (w[0] - cur[-1][1] > max_gap or w[1] - cur[0][0] > max_len):
            flush()
        cur.append(w)
        if w[2].strip() and w[2].strip()[-1] in PUNCT:
            flush()
    if cur:
        flush()
    return out


def align(segments: list[dict], key_frames: list[dict]) -> None:
    """Attach each speech segment to the step during which it was spoken.

    Step i "owns" the time from the end of step i-1 up to its own end, so a
    remark made while a screen loads lands on the screen that finally appears.
    """
    if not key_frames:
        return
    bounds = [kf["stable_to"] for kf in key_frames]
    for seg in segments:
        mid = (seg["start"] + seg["end"]) / 2
        idx = next((i for i, b in enumerate(bounds) if mid <= b), len(bounds) - 1)
        kf = key_frames[idx]
        kf["narration"] = (kf.get("narration", "") + " " + seg["text"]).strip()
