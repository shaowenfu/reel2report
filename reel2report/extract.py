"""Frame extraction with ffmpeg (runs locally, no model calls)."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Frame:
    path: Path
    t: float  # seconds from start of video


def _require(tool: str) -> str:
    exe = shutil.which(tool)
    if not exe:
        raise RuntimeError(f"找不到 {tool}，请先安装 ffmpeg（含 ffprobe）。")
    return exe


def probe_duration(video: Path) -> float:
    out = subprocess.run(
        [_require("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return float(out)


def sample_frames(video: Path, out_dir: Path, fps: float, width: int = 256) -> list[Frame]:
    """Low-res frames at a fixed rate, used only for change detection."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("s_*.jpg"):
        old.unlink()
    subprocess.run(
        [_require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-vf", f"fps={fps},scale={width}:-2", "-q:v", "5", str(out_dir / "s_%06d.jpg")],
        check=True,
    )
    files = sorted(out_dir.glob("s_*.jpg"))
    return [Frame(p, i / fps) for i, p in enumerate(files)]


def grab_frame(video: Path, t: float, dest: Path, max_width: int) -> Path:
    """Full-quality single frame at time t (only for frames we keep)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [_require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
         "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"scale='min({max_width},iw)':-2", "-q:v", "3", str(dest)],
        check=True,
    )
    return dest


def grab_matching(video: Path, t: float, reference: Path, dest: Path, max_width: int,
                  window: float = 0.6) -> Path:
    """Full-quality frame near time t that best matches a low-res reference sample.

    Phone recordings are variable-frame-rate: the recorder only emits a frame when
    the screen changes. The fps-sampled frame at time t therefore shows the last
    frame *before* t, while seeking to t returns the first frame *after* it, which
    may already be the next screen. Matching against the sample keeps the
    screenshot faithful to what segmentation saw.
    """
    import tempfile

    from .segment import dhash, hamming

    tmp = Path(tempfile.mkdtemp(prefix="r2r_grab_"))
    try:
        start = max(t - window, 0)
        subprocess.run(
            [_require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
             "-ss", f"{start:.3f}", "-t", f"{2 * window:.3f}", "-i", str(video),
             "-vf", f"scale='min({max_width},iw)':-2", "-fps_mode", "passthrough",
             "-q:v", "3", str(tmp / "c_%04d.jpg")],
            check=True,
        )
        cands = sorted(tmp.glob("c_*.jpg"))
        if not cands:
            return grab_frame(video, t, dest, max_width)
        ref = dhash(reference)
        best = min(cands, key=lambda c: hamming(dhash(c), ref))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(best), dest)
        return dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def grab_strip(video: Path, spans: list[tuple[float, float]], dest: Path, fps: float = 8.0,
               max_frames: int = 10, thumb_width: int = 200) -> tuple[Path, int] | None:
    """Re-sample transition spans at a higher rate and tile them into one filmstrip image.

    One strip costs the model a single image, however many frames it shows.
    Returns (path, frame_count) or None when nothing could be extracted.
    """
    import tempfile

    from PIL import Image, ImageDraw

    shots: list[tuple[float, Path]] = []
    tmp = Path(tempfile.mkdtemp(prefix="r2r_strip_"))
    try:
        for si, (start, end) in enumerate(spans):
            pattern = tmp / f"{si:02d}_%04d.jpg"
            subprocess.run(
                [_require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", f"{start:.3f}", "-t", f"{max(end - start, 1 / fps):.3f}", "-i", str(video),
                 "-vf", f"fps={fps},scale={thumb_width}:-2", "-q:v", "4", str(pattern)],
                check=True,
            )
            for k, p in enumerate(sorted(tmp.glob(f"{si:02d}_*.jpg"))):
                shots.append((start + k / fps, p))
        if not shots:
            return None
        if len(shots) > max_frames:
            idx = [round(i * (len(shots) - 1) / (max_frames - 1)) for i in range(max_frames)]
            shots = [shots[i] for i in idx]
        thumbs = [(t, Image.open(p).convert("RGB")) for t, p in shots]
        w, h = thumbs[0][1].size
        label_h, gap = 18, 4
        sheet = Image.new("RGB", (len(thumbs) * (w + gap) - gap, h + label_h), (30, 30, 30))
        draw = ImageDraw.Draw(sheet)
        t0 = thumbs[0][0]
        for i, (t, im) in enumerate(thumbs):
            x = i * (w + gap)
            sheet.paste(im.resize((w, h)), (x, label_h))
            draw.text((x + 4, 3), f"+{t - t0:.2f}s", fill=(255, 220, 0))
            im.close()
        dest.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(dest, quality=85)
        return dest, len(thumbs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
