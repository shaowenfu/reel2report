"""Model stage: describe each key frame, then summarize."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .llm import LLM, image_part, text_part
from .templates import Template

# Fixed parts: rules that keep the output honest, and the JSON format the code parses.
STEP_RULES = """你会按时间顺序看到一段软件使用录屏中的关键截图。请为每张截图推断：用户在上一画面到这一画面之间做了什么操作、当前是什么界面、出现了什么结果，以及是否有异常。

通用规则：
- 只描述截图中能看到的内容，不要编造。无法确定时把 uncertain 设为 true，并在描述里写明“推测”。
- 有的截图前面附有“过渡过程”胶片图：从左到右是两个稳定画面之间按时间顺序截取的缩略图，左上角标着相对时间，并给出过渡总耗时。请据此判断过渡期间发生了什么（加载、动画、闪屏、布局跳动、白屏等），写进 transition 字段；没有胶片图的步骤 transition 为 null。
- 每张截图都标注了画面“出现–结束”的时间段，截图取自该画面最后稳定的时刻。计算耗时、等待时长时以时间段为准，不要用截图时刻推算。
- anomaly 只用于有画面证据的问题。“不确定”用 uncertain 表达，不要因为不确定就标 anomaly；正常的翻页、滑入、淡入淡出等转场动画不是异常。
- 画面上的灰色或半透明圆点通常是系统“显示点按操作”的触摸指示，表示用户手指的位置，可用来判断点了哪里；它不是界面元素，被它遮住的内容不算异常。
- 第一张截图之前没有画面时，action 写“（录屏开始）”。
- 用简体中文，描述简洁具体，写出界面上关键的按钮或文字。"""

STEP_FORMAT = """只输出 JSON，格式：
{"steps": [{"id": <截图编号>, "screen": "界面名称", "action": "用户操作", "observation": "画面内容与结果",
  "transition": null 或 "过渡过程描述",
  "anomaly": null 或 {"description": "...", "severity": "high|medium|low"}, "uncertain": false}],
 "context": "到目前为止的流程概括（一两句话，供下一批参考）"}"""

SUMMARY_RULES = """只基于逐步记录中的内容，不要编造。用简体中文。
- issues 只放有明确画面证据的问题（confidence 为 "high"）。
- 依赖推测、标了“不确定”、或可能是录屏/误操作造成的，放进 to_verify，留给人工确认。
- 宁缺毋滥：同一个根因只列一条。"""

SUMMARY_FORMAT = """只输出 JSON，格式：
{"title": "标题", "overview": "流程概述（2-4 句）",
 "issues": [{"title": "...", "severity": "high|medium|low", "step_ids": [相关步骤编号],
   "repro": ["步骤1", "步骤2"], "expected": "预期结果", "actual": "实际结果", "notes": "补充/不确定之处"}],
 "to_verify": [{"title": "...", "step_ids": [相关步骤编号], "reason": "为什么需要人工确认"}]}
没有需要列出的条目时对应字段为空数组。"""


def _compose(*parts: str) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def step_system(tpl: Template, extra: str = "") -> str:
    return _compose(tpl.role, STEP_RULES, f"场景要求：\n{tpl.step}" if tpl.step else "",
                    f"用户的特别要求（优先遵守）：\n{extra}" if extra else "", STEP_FORMAT)


def summary_system(tpl: Template, extra: str = "") -> str:
    return _compose(tpl.summary, SUMMARY_RULES,
                    f"用户的特别要求（优先遵守）：\n{extra}" if extra else "", SUMMARY_FORMAT)


def fmt_time(t: float) -> str:
    m, s = divmod(t, 60)
    h, m = divmod(int(m), 60)
    return f"{h:d}:{m:02d}:{s:04.1f}" if h else f"{m:02d}:{s:04.1f}"


def span(f: dict) -> str:
    """Human-readable time range a screen was on display."""
    if "stable_from" in f and "stable_to" in f:
        return f"{fmt_time(f['stable_from'])}–{fmt_time(f['stable_to'])}"
    return fmt_time(f["t"])


def describe_steps(llm: LLM, frames: list[dict], work: Path, tpl: Template, extra: str = "",
                   batch_size: int = 8, product_context: str = "", log=print) -> list[dict]:
    """frames: [{"id", "t", "image", "strip"?, "transition_s"?}]. Results are cached per batch."""
    system = step_system(tpl, extra)
    # cache is keyed by prompt + model, so changing the template invalidates it
    key = hashlib.sha1(f"{llm.model}\n{system}\n{product_context}\n{batch_size}".encode()).hexdigest()[:10]
    cache = work / "batches" / key
    cache.mkdir(parents=True, exist_ok=True)
    steps: list[dict] = []
    running_context = ""
    prev_frame: dict | None = None
    batches = [frames[i : i + batch_size] for i in range(0, len(frames), batch_size)]

    for bi, batch in enumerate(batches):
        cache_file = cache / f"batch_{bi:04d}.json"
        if cache_file.exists():
            result = json.loads(cache_file.read_text("utf-8"))
            log(f"  批次 {bi + 1}/{len(batches)}：使用缓存")
        else:
            n_strip = sum(1 for f in batch if f.get("strip"))
            log(f"  批次 {bi + 1}/{len(batches)}：{len(batch)} 张截图 + {n_strip} 张过渡胶片")
            content: list[dict] = []
            if product_context:
                content.append(text_part(f"【产品背景与预期行为】\n{product_context}"))
            if running_context:
                content.append(text_part(f"【之前的流程概括】\n{running_context}"))
            if prev_frame:
                content.append(text_part(
                    f"【参考】上一批最后一张截图（编号 {prev_frame['id']}，{fmt_time(prev_frame['t'])}），不需要再描述："))
                content.append(image_part(Path(prev_frame["image"])))
            content.append(text_part("【本批截图】请逐张描述："))
            for f in batch:
                if f.get("strip"):
                    content.append(text_part(
                        f"截图编号 {f['id']} 之前的过渡过程（耗时约 {f['transition_s']:.1f}s，"
                        f"{f['strip_frames']} 帧，从左到右）："))
                    content.append(image_part(Path(f["strip"])))
                content.append(text_part(f"截图编号 {f['id']}，画面 {span(f)}："))
                content.append(image_part(Path(f["image"])))
            result = llm.chat_json(system, content)
            cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")

        by_id = {}
        for s in result.get("steps", []):
            try:
                by_id[int(s.get("id"))] = s
            except (TypeError, ValueError):
                continue
        for f in batch:
            s = by_id.get(f["id"], {"screen": "", "action": "", "observation": "（模型未返回该步骤）",
                                    "anomaly": None, "uncertain": True})
            steps.append({**s, **{k: v for k, v in f.items() if k != "id"},
                          "id": f["id"], "time": span(f)})
        running_context = result.get("context", running_context)
        prev_frame = batch[-1]
    return steps


def summarize(llm: LLM, steps: list[dict], tpl: Template, extra: str = "",
              product_context: str = "") -> dict:
    lines = []
    for s in steps:
        an = s.get("anomaly")
        an_txt = f" ⚠异常[{an.get('severity', '?')}]：{an.get('description', '')}" if an else ""
        unc = "（不确定）" if s.get("uncertain") else ""
        tr = ""
        if s.get("transition_s"):
            tr = f"｜过渡 {s['transition_s']:.1f}s：{s.get('transition') or ''}"
        lines.append(f"#{s['id']} [{s['time']}] 界面：{s.get('screen', '')}｜操作：{s.get('action', '')}"
                     f"{tr}｜结果：{s.get('observation', '')}{unc}{an_txt}")
    content = []
    if product_context:
        content.append(text_part(f"【产品背景与预期行为】\n{product_context}"))
    content.append(text_part("【逐步记录】\n" + "\n".join(lines)))
    return llm.chat_json(summary_system(tpl, extra), content)
