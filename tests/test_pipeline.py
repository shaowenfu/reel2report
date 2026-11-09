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
    """Answers the four call types: per-step, page grouping, page analysis, overview."""
    model = "fake"

    def __init__(self, *a, **k):
        self.usage = {"prompt_tokens": 1000, "completion_tokens": 100, "cache_hit_tokens": 0,
                      "reasoning_tokens": 0, "requests": 0, "images": 0}
        self.thinking_seen = []

    def chat_json(self, system, content, **kw):
        self.usage["requests"] += 1
        self.thinking_seen.append(kw.get("thinking"))
        texts = [c["text"] for c in content if c["type"] == "text"]
        if "【任务：页面归并】" in system:
            ids = [int(m) for t in texts for m in re.findall(r"^#(\d+)", t, re.M)]
            half = len(ids) // 2
            return {"pages": [{"name": "登录页", "step_ids": ids[:half]}, {"name": "错误弹窗页", "step_ids": ids[half:]}]}
        if "【任务：页面分析】" in system:
            name = re.search(r"【页面】(\S+)", texts[0] if not texts[0].startswith("【产品") else texts[1]).group(1)
            strips = [int(m) for t in texts for m in re.findall(r"过渡胶片 #(\d+)", t)]
            shots = [int(m) for t in texts for m in re.findall(r"代表截图 #(\d+)", t)]
            return {"page": name, "purpose": "p", "screenshot_step_id": shots[0] if shots else None,
                    "ui_issues": [{"title": f"{name}按钮太丑", "type": "ux", "severity": "low", "detail": "d"}],
                    "interactions": [{"title": "弹窗报错", "step_ids": [], "strip_step_id": strips[0] if strips else None,
                                      "description": "点击后报错", "verdict": "issue", "type": "bug", "severity": "high",
                                      "expected": "e", "actual": "x", "repro": ["a", "b"]},
                                     {"title": "返回", "description": "正常", "verdict": "ok"}],
                    "feedback": "f", "suggestions": [], "retracted": [{"claim": "c", "resolution": "r"}], "to_verify": []}
        if "【任务：总览】" in system:
            return {"title": "T", "overview": "O",
                    "priorities": [{"title": "弹窗报错", "page": "错误弹窗页", "type": "bug", "severity": "high"}],
                    "cross_page": []}
        ids = [int(re.match(r"截图编号 (\d+)", t).group(1)) for t in texts if t.startswith("截图编号")]
        return {"steps": [{"id": i, "screen": f"界面{i}", "action": "点击", "observation": "ok",
                           "anomaly": {"description": "报错", "severity": "high"} if i == ids[-1] else None}
                          for i in ids], "context": "c"}


def test_full_pipeline_with_fake_model(tmp_path, monkeypatch, capsys):
    import reel2report.llm as llm_mod
    monkeypatch.setattr(llm_mod, "LLM", FakeLLM)
    video = make_video(tmp_path)
    out = tmp_path / "out"
    assert cli.main([str(video), "-o", str(out), "--fps", "5", "--batch", "3", "--audio", "off"]) == 0
    data = json.loads((out / "steps.json").read_text("utf-8"))
    assert len(data["steps"]) == 4
    pages = data["report"]["pages"]
    assert [p["name"] for p in pages] == ["登录页", "错误弹窗页"]
    assert sorted(i for p in pages for i in p["step_ids"]) == [1, 2, 3, 4]
    agent = (out / "agent.md").read_text("utf-8")
    assert "弹窗报错" in agent and "do not chase" in agent
    html = (out / "report.html").read_text("utf-8")
    # one screenshot per page + one filmstrip per problematic interaction that has a strip
    from reel2report.render import count_images
    shots, strips = count_images(data["report"], [{**s, "image": str(out / s["image"]),
                                                   **({"strip": str(out / s["strip"])} if s.get("strip") else {})}
                                                  for s in data["steps"]])
    assert html.count("data:image/jpeg") == shots + strips == 2 + strips
    appendix = html[html.index("附录"):]
    assert "data:image" not in appendix  # appendix is text only
    printed = capsys.readouterr().out
    assert "估算费用" in printed and "按页面归并" in printed


def test_thinking_off_by_default(tmp_path, monkeypatch):
    import reel2report.llm as llm_mod
    seen = []

    class Spy(FakeLLM):
        def chat_json(self, system, content, **kw):
            seen.append(kw.get("thinking"))
            return super().chat_json(system, content, **kw)

    monkeypatch.setattr(llm_mod, "LLM", Spy)
    video = make_video(tmp_path)
    assert cli.main([str(video), "-o", str(tmp_path / "o"), "--fps", "5", "--audio", "off"]) == 0
    assert seen and all(t is False for t in seen)


def test_page_grouping_fallback():
    from reel2report.pages import _clean_pages, fallback_pages
    steps = [{"id": 1, "screen": "首页"}, {"id": 2, "screen": "设置（弹窗）"}, {"id": 3, "screen": "首页·滚动", "revisit_of": 1},
             {"id": 4, "screen": "设置"}]
    pages = fallback_pages(steps)
    assert [(p["name"], p["step_ids"]) for p in pages] == [("首页", [1, 3]), ("设置", [2, 4])]
    # model skipped step 3 and repeated step 1: every step ends up exactly once
    cleaned = _clean_pages([{"name": "A", "step_ids": [1, 2]}, {"name": "B", "step_ids": [1, 4]}], steps)
    assert sorted(i for p in cleaned for i in p["step_ids"]) == [1, 2, 3, 4]


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



def test_page_result_guards():
    from reel2report.pages import _clean_page_result
    steps = [{"id": 1, "image": "a"}, {"id": 2, "image": "b", "strip": "s2"}, {"id": 3, "image": "c"}]
    r = {"screenshot_step_id": 99,
         "ui_issues": [{"title": "声音素材区为静态波形，无信息量"}, {"title": "按钮太小"},
                       {"title": "保存按钮无文字", "detail": "被触摸点遮挡处疑似空白"}],
         "interactions": [{"title": "提交", "verdict": "issue", "step_ids": [1, 2], "strip_step_id": None}],
         "retracted": [{"claim": "声音素材静态波形没有信息量", "resolution": "后来认为合理"}]}
    out = _clean_page_result(r, {"name": "P", "step_ids": [1, 2, 3]}, steps, [])
    assert [i["title"] for i in out["ui_issues"]] == ["按钮太小"]  # retracted criticism dropped
    assert out["interactions"][0]["strip_step_id"] == 2  # filmstrip filled from cited steps
    assert out["screenshot_step_id"] == 1  # invalid id replaced


def test_page_exits_look_past_loading():
    from reel2report.pages import page_exits
    steps = [{"id": 1, "stable_from": 0, "stable_to": 3, "screen": "探索"},
             {"id": 2, "stable_from": 3.2, "stable_to": 3.8, "screen": "正在加载"},
             {"id": 3, "stable_from": 4, "stable_to": 9, "screen": "学习计划"}]
    exits = page_exits(steps, {1: "探索", 2: "探索", 3: "学习计划"})
    assert exits[1].startswith("经 #2 #3 进入「学习计划」") and exits[2].startswith("#3 进入")
