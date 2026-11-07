import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from reel2report import cli
from reel2report.extract import sample_frames
from reel2report.segment import segment_frames

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")

# (screen label, seconds shown). "~" entries are 0.2s transitions that must be dropped.
SCREENS = [("登录页", 3), ("~", 0.2), ("首页", 3), ("~", 0.2), ("设置", 2.5), ("错误弹窗", 2)]


def _screen(label: str, idx: int) -> Image.Image:
    im = Image.new("RGB", (360, 640), (240, 240, 240))
    d = ImageDraw.Draw(im)
    # distinct layout per screen so hashes differ clearly
    for k in range(6):
        y = 80 + k * 90
        x0 = 20 + ((idx * 37 + k * 53) % 120)
        d.rectangle([x0, y, x0 + 150 + (idx * 23 % 80), y + 50], fill=((idx * 60) % 255, 80 + k * 20, 200 - idx * 20))
    d.text((20, 30), label, fill=(0, 0, 0))
    return im


def make_video(tmp: Path) -> Path:
    lines = []
    for i, (label, dur) in enumerate(SCREENS):
        p = tmp / f"img{i}.png"
        im = _screen(label, i) if label != "~" else Image.effect_noise((360, 640), 80).convert("RGB")
        im.save(p)
        lines += [f"file '{p}'", f"duration {dur}"]
    lines.append(f"file '{tmp / f'img{len(SCREENS) - 1}.png'}'")
    (tmp / "list.txt").write_text("\n".join(lines))
    video = tmp / "rec.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(tmp / "list.txt"), "-vf", "fps=30,format=yuv420p", str(video)], check=True)
    return video


def test_segments_drop_transitions(tmp_path):
    video = make_video(tmp_path)
    frames = sample_frames(video, tmp_path / "s", fps=5)
    segs = segment_frames(frames, fps=5)
    assert len(segs) == 4, [(s.start, s.end) for s in segs]
    starts = [round(s.start) for s in segs]
    assert starts == [0, 3, 6, 9] or all(abs(a - b) <= 1 for a, b in zip(starts, [0, 3, 6, 9]))


class FakeLLM:
    model = "fake"

    def __init__(self, *a, **k):
        self.usage = {"prompt_tokens": 1, "completion_tokens": 1}

    def chat_json(self, system, content):
        ids = [int(re.match(r"截图编号 (\d+)", c["text"]).group(1)) for c in content
               if c["type"] == "text" and c["text"].startswith("截图编号")]
        if ids:
            return {"steps": [{"id": i, "screen": f"界面{i}", "action": "点击", "observation": "ok",
                               "anomaly": {"description": "报错", "severity": "high"} if i == ids[-1] else None}
                              for i in ids], "context": "c"}
        return {"title": "T", "overview": "O", "issues": [
            {"title": "弹窗报错", "severity": "high", "step_ids": [4], "repro": ["a"], "expected": "e", "actual": "x"}]}


def test_full_pipeline_with_fake_model(tmp_path, monkeypatch):
    import reel2report.llm as llm_mod
    monkeypatch.setattr(llm_mod, "LLM", FakeLLM)
    video = make_video(tmp_path)
    out = tmp_path / "out"
    assert cli.main([str(video), "-o", str(out), "--fps", "5", "--batch", "3"]) == 0
    data = json.loads((out / "steps.json").read_text("utf-8"))
    assert len(data["steps"]) == 4
    assert all((out / s["image"]).exists() for s in data["steps"])
    agent = (out / "agent.md").read_text("utf-8")
    assert "弹窗报错" in agent and agent.count("![") >= 1
    assert "错误弹窗" not in agent  # labels are pixels, not leaked text
    assert (out / "report.md").exists()


def test_no_ai(tmp_path):
    video = make_video(tmp_path)
    out = tmp_path / "o"
    assert cli.main([str(video), "-o", str(out), "--no-ai", "--fps", "5"]) == 0
    assert len(list((out / "frames").glob("*.jpg"))) == 4
