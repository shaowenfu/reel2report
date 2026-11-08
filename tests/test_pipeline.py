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

    def chat_json(self, system, content, **kw):
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
    assert len([p for p in (out / "frames").glob("*.jpg") if "transition" not in p.name]) == 4


def test_transitions_attached_not_dropped(tmp_path):
    video = make_video(tmp_path)
    frames = sample_frames(video, tmp_path / "s", fps=5)
    segs = segment_frames(frames, fps=5)
    with_tr = [i for i, s in enumerate(segs) if s.transitions]
    assert with_tr == [1, 2], [(s.start, s.transitions) for s in segs]


def test_strip_files_and_prompt(tmp_path, monkeypatch):
    import reel2report.llm as llm_mod
    seen = []

    class Spy(FakeLLM):
        def chat_json(self, system, content, **kw):
            seen.append((system, content))
            return super().chat_json(system, content, **kw)

    monkeypatch.setattr(llm_mod, "LLM", Spy)
    video = make_video(tmp_path)
    out = tmp_path / "out"
    assert cli.main([str(video), "-o", str(out), "--fps", "5", "-t", "ux", "-i", "重点看支付"]) == 0
    assert len(list((out / "frames").glob("*_transition.jpg"))) == 2
    system, content = seen[0]
    assert "交互设计师" in system and "重点看支付" in system and '"steps"' in system
    assert any("过渡过程" in c.get("text", "") for c in content)
    data = json.loads((out / "steps.json").read_text("utf-8"))
    assert sum(1 for s in data["steps"] if s.get("strip")) == 2


def test_templates(tmp_path):
    from reel2report import templates
    assert {"bug", "ux", "tutorial"} <= set(templates.builtin_names())
    custom = tmp_path / "mine.md"
    custom.write_text("只关注登录流程的安全提示。", "utf-8")
    t = templates.load(str(custom))
    assert t.step.startswith("只关注") and t.role and t.summary  # missing sections fall back to bug
    with pytest.raises(ValueError):
        templates.load("nope")


def test_resume_skips_extraction_and_parallel_order(tmp_path, monkeypatch):
    import reel2report.llm as llm_mod
    monkeypatch.setattr(llm_mod, "LLM", FakeLLM)
    video = make_video(tmp_path)
    out = tmp_path / "out"
    assert cli.main([str(video), "-o", str(out), "--fps", "5", "--batch", "1", "--workers", "3"]) == 0
    first = json.loads((out / "steps.json").read_text("utf-8"))
    assert [s["id"] for s in first["steps"]] == [1, 2, 3, 4]
    assert (out / "report.html").stat().st_size > 10_000  # images embedded

    calls = []
    monkeypatch.setattr(cli, "sample_frames", lambda *a, **k: calls.append(1) or [])
    assert cli.main([str(video), "-o", str(out), "--fps", "5", "--batch", "1", "--workers", "3"]) == 0
    assert calls == []  # keyframes reused
    assert cli.main([str(video), "-o", str(out), "--fps", "4", "--batch", "1"]) == 0
    assert calls == [1]  # changed settings -> re-extract


def test_align_narration():
    from reel2report.transcribe import align
    kfs = [{"id": 1, "stable_to": 3.0}, {"id": 2, "stable_to": 6.0}, {"id": 3, "stable_to": 9.0}]
    align([{"start": 0.5, "end": 1.5, "text": "登录"}, {"start": 3.2, "end": 4.0, "text": "怎么这么慢"},
           {"start": 4.1, "end": 5.0, "text": "还在转"}, {"start": 9.5, "end": 10.0, "text": "结束"}], kfs)
    assert kfs[0]["narration"] == "登录"
    assert kfs[1]["narration"] == "怎么这么慢 还在转"
    assert kfs[2]["narration"] == "结束"


def test_split_phrases():
    from reel2report.transcribe import split_phrases
    words = [(0.5, 0.8, "我先"), (0.8, 1.2, "登录,"), (8.8, 9.0, "点"), (9.0, 9.3, "加号"), (13.2, 13.5, "保存。")]
    assert [p["text"] for p in split_phrases(words)] == ["我先登录", "点加号", "保存。"]


def test_truncated_batch_is_split(tmp_path, monkeypatch):
    import reel2report.llm as llm_mod

    class Truncating(FakeLLM):
        def chat_json(self, system, content, **kw):
            n = sum(1 for c in content if c["type"] == "text" and c["text"].startswith("截图编号 ") and "，画面" in c["text"])
            if n > 1:
                raise llm_mod.OutputTruncated("too long")
            return super().chat_json(system, content, **kw)

    monkeypatch.setattr(llm_mod, "LLM", Truncating)
    video = make_video(tmp_path)
    out = tmp_path / "out"
    assert cli.main([str(video), "-o", str(out), "--fps", "5", "--batch", "4", "--audio", "off"]) == 0
    steps = json.loads((out / "steps.json").read_text("utf-8"))["steps"]
    assert [s["id"] for s in steps] == [1, 2, 3, 4]
    assert all(s.get("screen") for s in steps)  # every step answered, none dropped


def test_upright_images(tmp_path):
    from reel2report.render import upright_images
    img = tmp_path / "a.jpg"
    Image.new("RGB", (100, 200), "white").save(img)
    steps = [{"image": str(img), "rotation": 90}, {"image": str(img), "rotation": 0}]
    upright_images(steps, tmp_path)
    assert Image.open(steps[0]["image_view"]).size == (200, 100)
    assert "image_view" not in steps[1]
