"""Renderers: structured steps -> documents. New use cases = new renderers."""

from __future__ import annotations

import json
import os
from pathlib import Path

SEV_LABEL = {"high": "🔴 高", "medium": "🟠 中", "low": "🟡 低"}


def _rel(image: str, out: Path) -> str:
    return os.path.relpath(image, out).replace(os.sep, "/")


def _issues(summary: dict) -> list[dict]:
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(summary.get("issues") or [], key=lambda i: order.get(i.get("severity"), 3))


def render_human(steps: list[dict], summary: dict, meta: dict, out: Path) -> Path:
    """Full report for people: every step with its screenshot."""
    by_id = {s["id"]: s for s in steps}
    L = [f"# {summary.get('title') or '录屏测试报告'}", ""]
    L.append(f"> 源文件：`{meta['video']}` ｜ 时长 {meta['duration']} ｜ 关键步骤 {len(steps)} 个"
             f" ｜ 生成于 {meta['generated']}")
    L.append("")
    if summary.get("overview"):
        L += ["## 概述", "", summary["overview"], ""]

    issues = _issues(summary)
    L += ["## 问题汇总", ""]
    if not issues:
        L += ["未发现明显问题。", ""]
    for n, it in enumerate(issues, 1):
        refs = "、".join(f"[#{i} {by_id[i]['time']}](#step-{i})" for i in it.get("step_ids", []) if i in by_id)
        L += [f"### {n}. {it.get('title', '')}", "",
              f"- 严重程度：{SEV_LABEL.get(it.get('severity'), it.get('severity', '-'))}",
              f"- 相关步骤：{refs or '-'}",
              f"- 预期：{it.get('expected', '-')}",
              f"- 实际：{it.get('actual', '-')}"]
        if it.get("repro"):
            L += ["- 复现步骤："] + [f"  {k}. {r}" for k, r in enumerate(it["repro"], 1)]
        if it.get("notes"):
            L.append(f"- 备注：{it['notes']}")
        L.append("")

    tv = summary.get("to_verify") or []
    if tv:
        L += ["## 待确认", ""]
        for it in tv:
            refs = "、".join(f"[#{i}](#step-{i})" for i in it.get("step_ids", []) if i in by_id)
            L.append(f"- **{it.get('title', '')}**（{refs or '-'}）：{it.get('reason', '')}")
        L.append("")

    L += ["## 详细步骤", ""]
    for s in steps:
        flag = " ⚠️" if s.get("anomaly") else ""
        unc = "（不确定）" if s.get("uncertain") else ""
        L += [f'<a id="step-{s["id"]}"></a>', f"### #{s['id']} · {s['time']} · {s.get('screen', '')}{flag}", "",
              f"- **操作**：{s.get('action', '')}{unc}",
              f"- **结果**：{s.get('observation', '')}"]
        if s.get("transition_s"):
            L.append(f"- **过渡**（{s['transition_s']:.1f}s）：{s.get('transition') or '-'}")
        if s.get("anomaly"):
            an = s["anomaly"]
            L.append(f"- **异常**（{SEV_LABEL.get(an.get('severity'), an.get('severity', ''))}）：{an.get('description', '')}")
        L += ["", f'<img src="{_rel(s["image"], out)}" width="280">', ""]
        if s.get("strip"):
            L += [f"<details><summary>过渡过程胶片</summary>", "",
                  f'<img src="{_rel(s["strip"], out)}">', "", "</details>", ""]
    path = out / "report.md"
    path.write_text("\n".join(L), "utf-8")
    return path


def render_agent(steps: list[dict], summary: dict, meta: dict, out: Path) -> Path:
    """Compact report for coding agents: text timeline, screenshots only where issues are."""
    issues = _issues(summary)
    # screenshots only for steps the summary actually cites; other anomalies are marked in text
    issue_ids = {i for it in issues for i in it.get("step_ids", [])}
    L = [f"# Bug report: {summary.get('title') or meta['video']}", "",
         f"Source: {meta['video']} ({meta['duration']}). Screenshots are attached only for steps involved in issues.", ""]
    if summary.get("overview"):
        L += [summary["overview"], ""]
    L += ["## Issues", ""]
    if not issues:
        L += ["None found.", ""]
    for n, it in enumerate(issues, 1):
        L += [f"### {n}. [{it.get('severity', '?')}] {it.get('title', '')}",
              f"- Expected: {it.get('expected', '')}",
              f"- Actual: {it.get('actual', '')}",
              f"- Steps: {', '.join('#' + str(i) for i in it.get('step_ids', []))}"]
        if it.get("repro"):
            L += ["- Repro:"] + [f"  {k}. {r}" for k, r in enumerate(it["repro"], 1)]
        if it.get("notes"):
            L.append(f"- Notes: {it['notes']}")
        L.append("")
    tv = summary.get("to_verify") or []
    if tv:
        L += ["## To verify (low confidence, no screenshots)", ""]
        for it in tv:
            L.append(f"- {it.get('title', '')} (steps {', '.join('#' + str(i) for i in it.get('step_ids', []))}): "
                     f"{it.get('reason', '')}")
        L.append("")
    L += ["## Timeline", ""]
    for s in steps:
        mark = " ⚠" if s["id"] in issue_ids else ""
        unc = " (uncertain)" if s.get("uncertain") else ""
        tr = ""
        if s.get("transition_s"):
            tr = f" (transition {s['transition_s']:.1f}s" + (f": {s['transition']}" if s.get("transition") else "") + ")"
        L.append(f"- #{s['id']} {s['time']} [{s.get('screen', '')}] {s.get('action', '')}{tr} → "
                 f"{s.get('observation', '')}{unc}{mark}")
        if s["id"] in issue_ids:
            if s.get("strip"):
                L.append(f"  ![#{s['id']} transition]({_rel(s['strip'], out)})")
            L.append(f"  ![#{s['id']}]({_rel(s['image'], out)})")
    path = out / "agent.md"
    path.write_text("\n".join(L) + "\n", "utf-8")
    return path


def write_json(steps: list[dict], summary: dict, meta: dict, out: Path) -> Path:
    data = {"meta": meta, "summary": summary,
            "steps": [{**s, "image": _rel(s["image"], out),
                       **({"strip": _rel(s["strip"], out)} if s.get("strip") else {})} for s in steps]}
    path = out / "steps.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return path
