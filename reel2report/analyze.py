"""Model stage: describe each key frame, then summarize issues."""

from __future__ import annotations

import json
from pathlib import Path

from .llm import LLM, image_part, text_part

STEP_SYSTEM = """你是一名严谨的软件测试记录员。你会按时间顺序看到一段手机软件使用录屏中的关键截图。
请为每张截图推断：用户在上一画面到这一画面之间做了什么操作、当前是什么界面、出现了什么结果，以及是否有异常。

规则：
- 只描述截图中能看到的内容，不要编造。无法确定时把 uncertain 设为 true，并在描述里写明“推测”。
- 异常包括：报错/崩溃/白屏、长时间加载、界面错乱或遮挡、文字截断、与产品预期不符的行为、用户反复尝试同一操作等。
- 第一张截图之前没有画面时，action 写“（录屏开始）”。
- 用简体中文，描述简洁具体，写出界面上关键的按钮或文字。

只输出 JSON，格式：
{"steps": [{"id": <截图编号>, "screen": "界面名称", "action": "用户操作", "observation": "画面内容与结果",
  "anomaly": null 或 {"description": "...", "severity": "high|medium|low"}, "uncertain": false}],
 "context": "到目前为止的流程概括（一两句话，供下一批参考）"}"""

SUMMARY_SYSTEM = """你是资深测试工程师。根据录屏的逐步记录，整理一份交给开发者或编码 agent 的问题报告。
要求：只基于记录中的内容，合并重复问题，给出可直接复现的步骤。没有发现问题时 issues 为空数组。
用简体中文。只输出 JSON，格式：
{"title": "报告标题", "overview": "测试流程概述（2-4 句）",
 "issues": [{"title": "...", "severity": "high|medium|low", "step_ids": [相关步骤编号],
   "repro": ["步骤1", "步骤2"], "expected": "预期结果", "actual": "实际结果", "notes": "补充/不确定之处"}]}"""


def fmt_time(t: float) -> str:
    m, s = divmod(t, 60)
    h, m = divmod(int(m), 60)
    return f"{h:d}:{m:02d}:{s:04.1f}" if h else f"{m:02d}:{s:04.1f}"


def describe_steps(llm: LLM, frames: list[dict], work: Path, batch_size: int = 8,
                   product_context: str = "", log=print) -> list[dict]:
    """frames: [{"id", "t", "image"}]. Results are cached per batch in work/batches."""
    cache = work / "batches"
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
            log(f"  批次 {bi + 1}/{len(batches)}：分析 {len(batch)} 张截图")
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
                content.append(text_part(f"截图编号 {f['id']}，时间 {fmt_time(f['t'])}："))
                content.append(image_part(Path(f["image"])))
            result = llm.chat_json(STEP_SYSTEM, content)
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
            steps.append({**s, "id": f["id"], "t": f["t"], "time": fmt_time(f["t"]),
                          "image": f["image"], "kind": f.get("kind", "stable")})
        running_context = result.get("context", running_context)
        prev_frame = batch[-1]
    return steps


def summarize(llm: LLM, steps: list[dict], product_context: str = "") -> dict:
    lines = []
    for s in steps:
        an = s.get("anomaly")
        an_txt = f" ⚠异常[{an.get('severity', '?')}]：{an.get('description', '')}" if an else ""
        unc = "（不确定）" if s.get("uncertain") else ""
        lines.append(f"#{s['id']} [{s['time']}] 界面：{s.get('screen', '')}｜操作：{s.get('action', '')}"
                     f"｜结果：{s.get('observation', '')}{unc}{an_txt}")
    content = []
    if product_context:
        content.append(text_part(f"【产品背景与预期行为】\n{product_context}"))
    content.append(text_part("【逐步记录】\n" + "\n".join(lines)))
    return llm.chat_json(SUMMARY_SYSTEM, content)
