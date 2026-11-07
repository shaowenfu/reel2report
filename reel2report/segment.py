"""Group sampled frames into stable screens (local, no model calls).

A "segment" is a run of consecutive frames that look the same. Short runs are
transitions (animations, page loads) and are dropped; long runs of continuous
change (scrolling, video playback) are sampled periodically so content is not
lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image

from .extract import Frame


def dhash(path, size: int = 16, crop_top: float = 0.06) -> int:
    """Difference hash. The top strip is cropped so the status-bar clock is ignored."""
    with Image.open(path) as im:
        im = im.convert("L")
        w, h = im.size
        im = im.crop((0, int(h * crop_top), w, h)).resize((size + 1, size), Image.LANCZOS)
        px = im.tobytes()
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


@dataclass
class Segment:
    start: float
    end: float
    frames: list[Frame] = field(default_factory=list)
    hashes: list[int] = field(default_factory=list)
    kind: str = "stable"  # "stable" | "motion"

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def key_time(self) -> float:
        """Timestamp of the representative frame: the last settled frame."""
        return self.frames[-1].t


def segment_frames(
    frames: list[Frame],
    fps: float,
    threshold: int = 8,
    min_stable: float = 0.5,
    motion_interval: float = 3.0,
) -> list[Segment]:
    if not frames:
        return []
    step = 1.0 / fps
    hashes = [dhash(f.path) for f in frames]

    # 1. Split into raw runs: a frame joins the run if it is close to the run's anchor.
    runs: list[Segment] = []
    cur = Segment(frames[0].t, frames[0].t + step, [frames[0]], [hashes[0]])
    for f, h in zip(frames[1:], hashes[1:]):
        if hamming(h, cur.hashes[0]) <= threshold:
            cur.frames.append(f)
            cur.hashes.append(h)
            cur.end = f.t + step
        else:
            runs.append(cur)
            cur = Segment(f.t, f.t + step, [f], [h])
    runs.append(cur)

    # 2. Keep stable runs; turn long stretches of short runs into periodic motion samples.
    kept: list[Segment] = []
    pending: list[Segment] = []

    def flush_pending() -> None:
        if not pending:
            return
        span_start, span_end = pending[0].start, pending[-1].end
        if span_end - span_start >= motion_interval:
            next_t = span_start + motion_interval
            for r in pending:
                if r.start >= next_t - 1e-6:
                    r.kind = "motion"
                    kept.append(r)
                    next_t = r.start + motion_interval
        pending.clear()

    for r in runs:
        if r.duration >= min_stable - 1e-6:
            flush_pending()
            kept.append(r)
        else:
            pending.append(r)
    flush_pending()

    # 3. Merge neighbours that ended up identical (A -> flicker -> A).
    merged: list[Segment] = []
    for s in kept:
        if merged and hamming(merged[-1].hashes[-1], s.hashes[0]) <= threshold:
            m = merged[-1]
            m.end = s.end
            m.frames += s.frames
            m.hashes += s.hashes
            if s.kind == "stable":
                m.kind = "stable"
        else:
            merged.append(s)
    return merged
