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
        if s.get("narration"):
            L.append(f"- **口述**：🎙 {s['narration']}")
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
        said = f' (tester said: "{s["narration"]}")' if s.get("narration") else ""
        L.append(f"- #{s['id']} {s['time']} [{s.get('screen', '')}] {s.get('action', '')}{tr} → "
                 f"{s.get('observation', '')}{unc}{said}{mark}")
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


def _data_uri(path: str) -> str:
    import base64
    return "data:image/jpeg;base64," + base64.b64encode(Path(path).read_bytes()).decode()


def render_html(steps: list[dict], summary: dict, meta: dict, out: Path) -> Path:
    """Self-contained single-file report (images embedded) for sharing."""
    from html import escape as e

    by_id = {s["id"]: s for s in steps}
    sev_cls = {"high": "high", "medium": "med", "low": "low"}
    parts: list[str] = []
    issues = _issues(summary)
    for n, it in enumerate(issues, 1):
        refs = " ".join(f'<a href="#step-{i}">#{i}</a>' for i in it.get("step_ids", []) if i in by_id)
        repro = "".join(f"<li>{e(r)}</li>" for r in it.get("repro") or [])
        parts.append(
            f'<div class="issue {sev_cls.get(it.get("severity"), "")}"><h3>{n}. {e(it.get("title", ""))}'
            f' <span class="sev">{e(SEV_LABEL.get(it.get("severity"), it.get("severity", "")))}</span></h3>'
            f'<p><b>预期</b>：{e(it.get("expected", "-"))}</p><p><b>实际</b>：{e(it.get("actual", "-"))}</p>'
            + (f"<p><b>复现</b></p><ol>{repro}</ol>" if repro else "")
            + (f'<p class="muted">{e(it["notes"])}</p>' if it.get("notes") else "")
            + f'<p class="muted">相关步骤：{refs or "-"}</p></div>')
    issues_html = "".join(parts) or "<p>未发现明显问题。</p>"
    tv = summary.get("to_verify") or []
    tv_html = "".join(
        f'<li><b>{e(t.get("title", ""))}</b>（' + " ".join(f'<a href="#step-{i}">#{i}</a>' for i in t.get("step_ids", []) if i in by_id)
        + f'）：{e(t.get("reason", ""))}</li>' for t in tv)
    cards = []
    for s in steps:
        rows = [f'<p><b>操作</b>：{e(s.get("action", ""))}{"（不确定）" if s.get("uncertain") else ""}</p>',
                f'<p><b>结果</b>：{e(s.get("observation", ""))}</p>']
        if s.get("narration"):
            rows.append(f'<p class="said">🎙 {e(s["narration"])}</p>')
        if s.get("transition_s"):
            rows.append(f'<p><b>过渡</b>（{s["transition_s"]:.1f}s）：{e(s.get("transition") or "-")}</p>')
        if s.get("anomaly"):
            an = s["anomaly"]
            rows.append(f'<p class="anom">⚠ {e(an.get("description", ""))}</p>')
        strip = (f'<details><summary>过渡过程</summary><img class="strip" src="{_data_uri(s["strip"])}"></details>'
                 if s.get("strip") else "")
        cards.append(
            f'<section class="step" id="step-{s["id"]}"><img class="shot" loading="lazy" src="{_data_uri(s["image"])}">'
            f'<div><h3>#{s["id"]} · <span class="muted">{e(s["time"])}</span> · {e(s.get("screen", ""))}</h3>'
            + "".join(rows) + strip + "</div></section>")
    title = e(summary.get("title") or "录屏测试报告")
    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>
:root{{--bg:#f7f7f8;--fg:#1d1d1f;--card:#fff;--muted:#6b6b70;--line:#e3e3e6;--high:#d93025;--med:#e37400;--low:#c9a400}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151517;--fg:#ececf0;--card:#202024;--muted:#9a9aa2;--line:#33333a}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
main{{max-width:960px;margin:0 auto;padding:24px 16px}} h1{{font-size:24px;margin:0 0 4px}} h2{{margin-top:32px}}
h3{{font-size:16px;margin:0 0 8px}} p{{margin:4px 0}} a{{color:inherit}} .muted{{color:var(--muted)}}
.issue{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--muted);border-radius:8px;padding:12px 16px;margin:12px 0}}
.issue.high{{border-left-color:var(--high)}} .issue.med{{border-left-color:var(--med)}} .issue.low{{border-left-color:var(--low)}}
.sev{{font-size:13px;font-weight:normal;color:var(--muted)}}
.step{{display:flex;gap:16px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin:12px 0}}
.shot{{width:220px;flex:none;border-radius:6px;border:1px solid var(--line);align-self:flex-start}}
.strip{{max-width:100%;margin-top:6px;border-radius:4px}} .anom{{color:var(--high)}} .said{{color:var(--muted);font-style:italic}}
@media (max-width:600px){{.step{{flex-direction:column}} .shot{{width:100%}}}}
</style></head><body><main>
<h1>{title}</h1><p class="muted">{e(meta["video"])} · 时长 {e(meta["duration"])} · {len(steps)} 个步骤 · {e(meta.get("model", ""))} · {e(meta["generated"])}</p>
<p>{e(summary.get("overview", ""))}</p>
<h2>问题</h2>{issues_html}
{f"<h2>待确认</h2><ul>{tv_html}</ul>" if tv_html else ""}
<h2>详细步骤</h2>{"".join(cards)}
</main></body></html>"""
    path = out / "report.html"
    path.write_text(html, "utf-8")
    return path
