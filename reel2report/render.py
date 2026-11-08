"""Renderers: structured steps -> documents. New use cases = new renderers.

Report layout (most compressed first):
  overview -> issues -> to verify -> retracted -> suggestions -> test tasks
  (with 1-3 key screenshots each) -> appendix: every step.
"""

from __future__ import annotations

import json
import os
from html import escape as e
from pathlib import Path

SEV_LABEL = {"high": "🔴 高", "medium": "🟠 中", "low": "🟡 低"}
SPEECH_LABEL = {"complaint": "抱怨", "guess": "猜测", "correction": "更正", "suggestion": "建议",
                "observation": "描述", "explain": "说明", "other": "其他"}


def _rel(image: str, out: Path) -> str:
    return os.path.relpath(image, out).replace(os.sep, "/")


def _issues(summary: dict) -> list[dict]:
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(summary.get("issues") or [], key=lambda i: order.get(i.get("severity"), 3))


def _ids(item: dict, key: str = "step_ids") -> list[int]:
    out = []
    for i in item.get(key) or []:
        try:
            out.append(int(i))
        except (TypeError, ValueError):
            pass
    return out


def _speech(s: dict) -> str:
    """Prefer the model's cleaned-up speech summary; fall back to the raw transcript."""
    if s.get("speech"):
        label = SPEECH_LABEL.get(s.get("speech_type") or "", "")
        return f"[{label}] {s['speech']}" if label else s["speech"]
    return s.get("narration") or ""


# ---------------------------------------------------------------- Markdown (people)

def render_human(steps: list[dict], summary: dict, meta: dict, out: Path) -> Path:
    by_id = {s["id"]: s for s in steps}

    def refs(ids):
        return "、".join(f"[#{i} {by_id[i]['time']}](#step-{i})" for i in ids if i in by_id) or "-"

    L = [f"# {summary.get('title') or '录屏测试报告'}", "",
         f"> 源文件：`{meta['video']}` ｜ 时长 {meta['duration']} ｜ 关键步骤 {len(steps)} 个"
         f" ｜ 生成于 {meta['generated']}", ""]
    if summary.get("overview"):
        L += ["## 概述", "", summary["overview"], ""]

    issues = _issues(summary)
    L += ["## 问题", ""]
    if not issues:
        L += ["未发现明显问题。", ""]
    for n, it in enumerate(issues, 1):
        L += [f"### {n}. {it.get('title', '')}", "",
              f"- 严重程度：{SEV_LABEL.get(it.get('severity'), it.get('severity', '-'))}",
              f"- 相关步骤：{refs(_ids(it))}",
              f"- 预期：{it.get('expected', '-')}",
              f"- 实际：{it.get('actual', '-')}"]
        if it.get("repro"):
            L += ["- 复现步骤："] + [f"  {k}. {r}" for k, r in enumerate(it["repro"], 1)]
        if it.get("notes"):
            L.append(f"- 备注：{it['notes']}")
        L.append("")

    if summary.get("to_verify"):
        L += ["## 待确认", ""]
        L += [f"- **{t.get('title', '')}**（{refs(_ids(t))}）：{t.get('reason', '')}" for t in summary["to_verify"]]
        L.append("")
    if summary.get("retracted"):
        L += ["## 已排除的怀疑", "", "测试者曾怀疑、后来自己确认没问题的点：", ""]
        L += [f"- {r.get('claim', '')} → {r.get('resolution', '')}（{refs(_ids(r))}）" for r in summary["retracted"]]
        L.append("")
    if summary.get("suggestions"):
        L += ["## 建议与需求", ""]
        L += [f"- {g.get('content', '')}（{refs(_ids(g))}）" for g in summary["suggestions"]]
        L.append("")

    tasks = summary.get("tasks") or []
    if tasks:
        L += ["## 测试过程", ""]
        for n, t in enumerate(tasks, 1):
            L += [f"### {n}. {t.get('title', '')}", "",
                  f"- 目标：{t.get('goal', '-')}",
                  f"- 过程：{t.get('process', '-')}",
                  f"- 结果：{t.get('outcome', '-')}",
                  f"- 步骤：{refs(_ids(t))}", ""]
            keys = [i for i in _ids(t, "key_step_ids") if i in by_id][:3]
            if keys:
                L.append(" ".join(f'<img src="{_rel(by_id[i]["image"], out)}" width="200" title="#{i}">' for i in keys))
                L.append("")

    L += ["## 附录：全部步骤", ""]
    for s in steps:
        flag = " ⚠️" if s.get("anomaly") else ""
        rv = f"（重复访问，同 #{s['revisit_of']}）" if s.get("revisit_of") else ""
        unc = "（不确定）" if s.get("uncertain") else ""
        L += [f'<a id="step-{s["id"]}"></a>', f"### #{s['id']} · {s['time']} · {s.get('screen', '')}{rv}{flag}", "",
              f"- **操作**：{s.get('action', '')}{unc}",
              f"- **结果**：{s.get('observation', '')}"]
        if _speech(s):
            L.append(f"- **口述**：🎙 {_speech(s)}")
        if s.get("transition_s"):
            L.append(f"- **过渡**（{s['transition_s']:.1f}s）：{s.get('transition') or '-'}")
        if s.get("anomaly"):
            an = s["anomaly"]
            L.append(f"- **异常**（{SEV_LABEL.get(an.get('severity'), an.get('severity', ''))}）：{an.get('description', '')}")
        L += ["", f'<img src="{_rel(s["image"], out)}" width="280">', ""]
        if s.get("strip"):
            L += ["<details><summary>过渡过程胶片</summary>", "",
                  f'<img src="{_rel(s["strip"], out)}">', "", "</details>", ""]
    path = out / "report.md"
    path.write_text("\n".join(L), "utf-8")
    return path


# ---------------------------------------------------------------- Markdown (agents)

def render_agent(steps: list[dict], summary: dict, meta: dict, out: Path) -> Path:
    """Compact report for coding agents.

    Only steps cited by issues / to-verify / tasks appear in the timeline, and
    only confirmed-issue steps get screenshots. steps.json has everything else.
    """
    by_id = {s["id"]: s for s in steps}
    issues = _issues(summary)
    shot_ids = {i for it in issues for i in _ids(it)}
    cited = set(shot_ids)
    for key in ("to_verify", "retracted"):
        cited |= {i for it in summary.get(key) or [] for i in _ids(it)}
    cited |= {i for t in summary.get("tasks") or [] for i in _ids(t, "key_step_ids")}

    def tag(ids):
        return ", ".join(f"#{i}" for i in ids)

    L = [f"# Bug report: {summary.get('title') or meta['video']}", "",
         f"Source: {meta['video']} ({meta['duration']}, {len(steps)} steps). "
         "Only cited steps are listed below; screenshots only for confirmed issues. Full data: steps.json.", ""]
    if summary.get("overview"):
        L += [summary["overview"], ""]
    L += ["## Issues", ""]
    if not issues:
        L += ["None found.", ""]
    for n, it in enumerate(issues, 1):
        L += [f"### {n}. [{it.get('severity', '?')}] {it.get('title', '')}",
              f"- Expected: {it.get('expected', '')}",
              f"- Actual: {it.get('actual', '')}",
              f"- Steps: {tag(_ids(it))}"]
        if it.get("repro"):
            L += ["- Repro:"] + [f"  {k}. {r}" for k, r in enumerate(it["repro"], 1)]
        if it.get("notes"):
            L.append(f"- Notes: {it['notes']}")
        L.append("")
    if summary.get("to_verify"):
        L += ["## To verify (low confidence)", ""]
        L += [f"- {t.get('title', '')} ({tag(_ids(t))}): {t.get('reason', '')}" for t in summary["to_verify"]]
        L.append("")
    if summary.get("retracted"):
        L += ["## Already ruled out by the tester (do not chase)", ""]
        L += [f"- {r.get('claim', '')} → {r.get('resolution', '')} ({tag(_ids(r))})" for r in summary["retracted"]]
        L.append("")
    if summary.get("suggestions"):
        L += ["## Feature suggestions (not bugs)", ""]
        L += [f"- {g.get('content', '')} ({tag(_ids(g))})" for g in summary["suggestions"]]
        L.append("")
    if summary.get("tasks"):
        L += ["## What was tested", ""]
        L += [f"- {t.get('title', '')}: {t.get('outcome', '')} ({tag(_ids(t))})" for t in summary["tasks"]]
        L.append("")
    if cited:
        L += ["## Cited steps", ""]
        for i in sorted(cited):
            s = by_id.get(i)
            if not s:
                continue
            tr = ""
            if s.get("transition_s"):
                tr = f" (transition {s['transition_s']:.1f}s" + (f": {s['transition']}" if s.get("transition") else "") + ")"
            said = f' (tester: "{_speech(s)}")' if _speech(s) else ""
            L.append(f"- #{i} {s['time']} [{s.get('screen', '')}] {s.get('action', '')}{tr} → "
                     f"{s.get('observation', '')}{' (uncertain)' if s.get('uncertain') else ''}{said}")
            if i in shot_ids:
                if s.get("strip"):
                    L.append(f"  ![#{i} transition]({_rel(s['strip'], out)})")
                L.append(f"  ![#{i}]({_rel(s['image'], out)})")
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


# ---------------------------------------------------------------- HTML (share)

def _data_uri(path: str) -> str:
    import base64
    return "data:image/jpeg;base64," + base64.b64encode(Path(path).read_bytes()).decode()


CSS = """
:root{--bg:#f7f7f8;--fg:#1d1d1f;--card:#fff;--muted:#6b6b70;--line:#e3e3e6;--high:#d93025;--med:#e37400;--low:#c9a400;--ok:#1e8e3e}
@media (prefers-color-scheme:dark){:root{--bg:#151517;--fg:#ececf0;--card:#202024;--muted:#9a9aa2;--line:#33333a}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
main{max-width:960px;margin:0 auto;padding:24px 16px} h1{font-size:24px;margin:0 0 4px} h2{margin-top:32px}
h3{font-size:16px;margin:0 0 8px} p{margin:4px 0} a{color:inherit} .muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--muted);border-radius:8px;padding:12px 16px;margin:12px 0}
.card.high{border-left-color:var(--high)} .card.med{border-left-color:var(--med)} .card.low{border-left-color:var(--low)} .card.task{border-left-color:var(--ok)}
.sev{font-size:13px;font-weight:normal;color:var(--muted)}
.thumbs{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px} .thumbs a{display:block;width:150px}
.thumbs img{width:100%;border-radius:6px;border:1px solid var(--line)} .thumbs span{font-size:12px;color:var(--muted)}
.step{display:flex;gap:16px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin:12px 0}
.shot{width:200px;flex:none;border-radius:6px;border:1px solid var(--line);align-self:flex-start}
.strip{max-width:100%;margin-top:6px;border-radius:4px} .anom{color:var(--high)} .said{color:var(--muted);font-style:italic}
details.all>summary{cursor:pointer;font-size:20px;font-weight:bold;margin:32px 0 8px}
@media (max-width:600px){.step{flex-direction:column} .shot{width:100%}}
"""


def render_html(steps: list[dict], summary: dict, meta: dict, out: Path) -> Path:
    """Self-contained single-file report (images embedded) for sharing."""
    by_id = {s["id"]: s for s in steps}
    sev_cls = {"high": "high", "medium": "med", "low": "low"}

    def refs(ids):
        return " ".join(f'<a href="#step-{i}">#{i}</a>' for i in ids if i in by_id) or "-"

    sec: list[str] = []
    parts = []
    for n, it in enumerate(_issues(summary), 1):
        repro = "".join(f"<li>{e(r)}</li>" for r in it.get("repro") or [])
        parts.append(
            f'<div class="card {sev_cls.get(it.get("severity"), "")}"><h3>{n}. {e(it.get("title", ""))}'
            f' <span class="sev">{e(SEV_LABEL.get(it.get("severity"), it.get("severity", "")))}</span></h3>'
            f'<p><b>预期</b>：{e(it.get("expected", "-"))}</p><p><b>实际</b>：{e(it.get("actual", "-"))}</p>'
            + (f"<p><b>复现</b></p><ol>{repro}</ol>" if repro else "")
            + (f'<p class="muted">{e(it["notes"])}</p>' if it.get("notes") else "")
            + f'<p class="muted">相关步骤：{refs(_ids(it))}</p></div>')
    sec.append("<h2>问题</h2>" + ("".join(parts) or "<p>未发现明显问题。</p>"))
    if summary.get("to_verify"):
        sec.append("<h2>待确认</h2><ul>" + "".join(
            f'<li><b>{e(t.get("title", ""))}</b>（{refs(_ids(t))}）：{e(t.get("reason", ""))}</li>'
            for t in summary["to_verify"]) + "</ul>")
    if summary.get("retracted"):
        sec.append('<h2>已排除的怀疑</h2><p class="muted">测试者曾怀疑、后来自己确认没问题的点</p><ul>' + "".join(
            f'<li>{e(r.get("claim", ""))} → {e(r.get("resolution", ""))}（{refs(_ids(r))}）</li>'
            for r in summary["retracted"]) + "</ul>")
    if summary.get("suggestions"):
        sec.append("<h2>建议与需求</h2><ul>" + "".join(
            f'<li>{e(g.get("content", ""))}（{refs(_ids(g))}）</li>' for g in summary["suggestions"]) + "</ul>")
    if summary.get("tasks"):
        cards = []
        for n, t in enumerate(summary["tasks"], 1):
            keys = [i for i in _ids(t, "key_step_ids") if i in by_id][:3]
            thumbs = "".join(f'<a href="#step-{i}"><img loading="lazy" src="{_data_uri(by_id[i]["image"])}">'
                             f'<span>#{i} · {e(by_id[i]["time"])}</span></a>' for i in keys)
            cards.append(
                f'<div class="card task"><h3>{n}. {e(t.get("title", ""))}</h3>'
                f'<p><b>目标</b>：{e(t.get("goal", "-"))}</p><p><b>过程</b>：{e(t.get("process", "-"))}</p>'
                f'<p><b>结果</b>：{e(t.get("outcome", "-"))}</p><p class="muted">步骤：{refs(_ids(t))}</p>'
                + (f'<div class="thumbs">{thumbs}</div>' if thumbs else "") + "</div>")
        sec.append("<h2>测试过程</h2>" + "".join(cards))

    step_cards = []
    for s in steps:
        rv = f'<span class="muted">（重复访问，同 #{s["revisit_of"]}）</span>' if s.get("revisit_of") else ""
        rows = [f'<p><b>操作</b>：{e(s.get("action", ""))}{"（不确定）" if s.get("uncertain") else ""}</p>',
                f'<p><b>结果</b>：{e(s.get("observation", ""))}</p>']
        if _speech(s):
            rows.append(f'<p class="said">🎙 {e(_speech(s))}</p>')
        if s.get("transition_s"):
            rows.append(f'<p><b>过渡</b>（{s["transition_s"]:.1f}s）：{e(s.get("transition") or "-")}</p>')
        if s.get("anomaly"):
            rows.append(f'<p class="anom">⚠ {e(s["anomaly"].get("description", ""))}</p>')
        strip = (f'<details><summary>过渡过程</summary><img class="strip" src="{_data_uri(s["strip"])}"></details>'
                 if s.get("strip") else "")
        step_cards.append(
            f'<section class="step" id="step-{s["id"]}"><img class="shot" loading="lazy" src="{_data_uri(s["image"])}">'
            f'<div><h3>#{s["id"]} · <span class="muted">{e(s["time"])}</span> · {e(s.get("screen", ""))}{rv}</h3>'
            + "".join(rows) + strip + "</div></section>")
    # the appendix starts collapsed when the report has a task view to read first
    open_attr = "" if summary.get("tasks") else " open"
    title = e(summary.get("title") or "录屏测试报告")
    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>{CSS}</style></head>
<body><main>
<h1>{title}</h1><p class="muted">{e(meta["video"])} · 时长 {e(meta["duration"])} · {len(steps)} 个步骤 · {e(meta.get("model", ""))} · {e(meta["generated"])}</p>
<p>{e(summary.get("overview", ""))}</p>
{"".join(sec)}
<details class="all"{open_attr}><summary>附录：全部 {len(steps)} 个步骤</summary>{"".join(step_cards)}</details>
</main></body></html>"""
    path = out / "report.html"
    path.write_text(html, "utf-8")
    return path
