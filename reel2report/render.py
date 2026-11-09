"""Renderers: page-centric report -> documents. New use cases = new renderers.

Layout (most compressed first):
  title, overview -> priorities -> cross-page issues -> one section per page
  (1 screenshot for static UI findings, a filmstrip per problematic interaction,
  tester feedback, suggestions, retracted, to verify) -> appendix: all steps as text.
"""

from __future__ import annotations

import json
import os
from html import escape as e
from pathlib import Path

SEV_LABEL = {"high": "🔴 高", "medium": "🟠 中", "low": "🟡 低"}
TYPE_LABEL = {"bug": "缺陷", "ux": "体验", "incomplete": "未完成"}
SEV_ORDER = {"high": 0, "medium": 1, "low": 2}
SPEECH_LABEL = {"complaint": "抱怨", "guess": "猜测", "correction": "更正", "suggestion": "建议",
                "observation": "描述", "explain": "说明", "other": "其他"}


def _rel(image: str, out: Path) -> str:
    return os.path.relpath(image, out).replace(os.sep, "/")


def _tag(it: dict) -> str:
    t = TYPE_LABEL.get(it.get("type"), it.get("type") or "")
    sev = SEV_LABEL.get(it.get("severity"), it.get("severity") or "")
    return " · ".join(x for x in (t, sev) if x)


def _issues_of(p: dict) -> list[dict]:
    return [i for i in p.get("interactions") or [] if i.get("verdict") == "issue"]


def _page_anchor(n: int) -> str:
    return f"page-{n}"


def _page_index(report: dict) -> dict[str, int]:
    return {p["name"]: n for n, p in enumerate(report.get("pages") or [], 1)}


def _speech(s: dict) -> str:
    if s.get("speech"):
        label = SPEECH_LABEL.get(s.get("speech_type") or "", "")
        return f"[{label}] {s['speech']}" if label else s["speech"]
    return s.get("narration") or ""


def _cost_line(meta: dict) -> str:
    u = meta.get("usage")
    if not u:
        return ""
    return (f"模型调用 {u.get('requests', 0)} 次，图片 {u.get('images', 0)} 张，输入 {u.get('prompt_tokens', 0):,} tokens，"
            f"输出 {u.get('completion_tokens', 0):,} tokens，估算费用 ${meta.get('cost_usd', 0):.3f}")


def count_images(report: dict, steps: list[dict]) -> tuple[int, int]:
    """(screenshots, filmstrips) a rendered report embeds."""
    by_id = {s["id"]: s for s in steps}
    shots = sum(1 for p in report.get("pages") or [] if p.get("screenshot_step_id") in by_id)
    strips = sum(1 for p in report.get("pages") or [] for it in _issues_of(p)
                 if by_id.get(it.get("strip_step_id"), {}).get("strip"))
    return shots, strips


# ---------------------------------------------------------------- Markdown (people)

def render_human(steps: list[dict], report: dict, meta: dict, out: Path) -> Path:
    by_id = {s["id"]: s for s in steps}
    idx = _page_index(report)
    L = [f"# {report.get('title') or '录屏测试报告'}", "",
         f"> 源文件：`{meta['video']}` ｜ 时长 {meta['duration']} ｜ {len(report.get('pages') or [])} 个页面"
         f" ｜ {len(steps)} 个关键步骤 ｜ 生成于 {meta['generated']}", ""]
    if report.get("overview"):
        L += ["## 概述", "", report["overview"], ""]
    if report.get("priorities"):
        L += ["## 优先处理", ""]
        for n, it in enumerate(report["priorities"], 1):
            link = f"（[{it.get('page')}](#{_page_anchor(idx[it['page']])})）" if it.get("page") in idx else ""
            L.append(f"{n}. **{it.get('title', '')}** {_tag(it)}{link}")
        L.append("")
    if report.get("cross_page"):
        L += ["## 跨页面问题", ""]
        for it in report["cross_page"]:
            L.append(f"- **{it.get('title', '')}**（{_tag(it)}；涉及：{'、'.join(it.get('pages') or [])}）：{it.get('detail', '')}")
        L.append("")

    L += ["## 按页面", ""]
    for n, p in enumerate(report.get("pages") or [], 1):
        L += [f'<a id="{_page_anchor(n)}"></a>', f"### {n}. {p['name']}", ""]
        if p.get("purpose"):
            L += [f"_{p['purpose']}_", ""]
        shot = by_id.get(p.get("screenshot_step_id"))
        if shot:
            L += [f'<img src="{_rel(shot["image"], out)}" width="240" title="#{shot["id"]} {shot["time"]}">', ""]
        if p.get("ui_issues"):
            L += ["**界面问题**", ""]
            for it in p["ui_issues"]:
                L.append(f"- **{it.get('title', '')}**（{_tag(it)}）：{it.get('detail', '')}")
            L.append("")
        if p.get("interactions"):
            L += ["**交互**", ""]
            for it in p["interactions"]:
                if it.get("verdict") != "issue":
                    L.append(f"- ✓ {it.get('title', '')}：{it.get('description', '')}")
                    continue
                L.append(f"- ⚠ **{it.get('title', '')}**（{_tag(it)}）：{it.get('description', '')}")
                L.append(f"  - 预期：{it.get('expected', '-')}；实际：{it.get('actual', '-')}")
                if it.get("repro"):
                    L.append("  - 复现：" + " → ".join(it["repro"]))
                st = by_id.get(it.get("strip_step_id"))
                if st and st.get("strip"):
                    L.append(f'  <br><img src="{_rel(st["strip"], out)}" width="640">')
            L.append("")
        for key, title, fmt in (
            ("feedback", "测试者评价", None),
            ("suggestions", "建议", lambda x: x.get("content", "")),
            ("retracted", "已排除的怀疑", lambda x: f"{x.get('claim', '')} → {x.get('resolution', '')}"),
            ("to_verify", "待确认", lambda x: f"{x.get('title', '')}：{x.get('reason', '')}"),
        ):
            v = p.get(key)
            if not v:
                continue
            if fmt is None:
                L += [f"**{title}**：{v}", ""]
            else:
                L += [f"**{title}**", ""] + [f"- {fmt(x)}" for x in v] + [""]

    L += ["## 附录：全部步骤", "", "| # | 时间 | 界面 | 操作 | 结果 | 口述 |", "|---|---|---|---|---|---|"]
    clean = lambda t: (t or "").replace("|", "／").replace("\n", " ")  # noqa: E731
    for s in steps:
        L.append(f"| {s['id']} | {s['time']} | {clean(s.get('screen'))} | {clean(s.get('action'))} | "
                 f"{clean(s.get('observation'))} | {clean(_speech(s))} |")
    if meta.get("usage"):
        L += ["", f"> {_cost_line(meta)}"]
    path = out / "report.md"
    path.write_text("\n".join(L) + "\n", "utf-8")
    return path


# ---------------------------------------------------------------- Markdown (agents)

def render_agent(steps: list[dict], report: dict, meta: dict, out: Path) -> Path:
    """Compact, page-by-page. Screenshot only for pages with static UI findings,
    a filmstrip only for problematic interactions. Full step data: steps.json."""
    by_id = {s["id"]: s for s in steps}
    L = [f"# Test report: {report.get('title') or meta['video']}", "",
         f"Source: {meta['video']} ({meta['duration']}). Organized by page. "
         "Screenshots illustrate static UI findings; filmstrips (left→right, timestamps top-left) illustrate interactions. "
         "Full per-step data: steps.json.", ""]
    if report.get("overview"):
        L += [report["overview"], ""]
    if report.get("priorities"):
        L += ["## Priorities", ""]
        L += [f"{n}. [{it.get('type', '?')}/{it.get('severity', '?')}] {it.get('title', '')} — page: {it.get('page', '?')}"
              for n, it in enumerate(report["priorities"], 1)] + [""]
    if report.get("cross_page"):
        L += ["## Cross-page issues", ""]
        L += [f"- [{it.get('type', '?')}/{it.get('severity', '?')}] {it.get('title', '')} (pages: {', '.join(it.get('pages') or [])}): "
              f"{it.get('detail', '')}" for it in report["cross_page"]] + [""]
    for n, p in enumerate(report.get("pages") or [], 1):
        issues = _issues_of(p)
        if not (p.get("ui_issues") or issues or p.get("to_verify") or p.get("retracted") or p.get("suggestions")):
            continue
        L += [f"## {n}. {p['name']}", ""]
        if p.get("purpose"):
            L += [p["purpose"], ""]
        if p.get("ui_issues"):
            shot = by_id.get(p.get("screenshot_step_id"))
            if shot:
                L.append(f"![{p['name']} #{shot['id']}]({_rel(shot['image'], out)})")
            L += ["UI findings:"] + [f"- [{it.get('type', '?')}/{it.get('severity', '?')}] {it.get('title', '')}: {it.get('detail', '')}"
                                     for it in p["ui_issues"]] + [""]
        for it in issues:
            L += [f"Interaction issue — [{it.get('type', '?')}/{it.get('severity', '?')}] {it.get('title', '')}",
                  f"- What happens: {it.get('description', '')}",
                  f"- Expected: {it.get('expected', '')}",
                  f"- Actual: {it.get('actual', '')}"]
            if it.get("repro"):
                L.append("- Repro: " + " → ".join(it["repro"]))
            st = by_id.get(it.get("strip_step_id"))
            if st and st.get("strip"):
                L.append(f"![transition before #{st['id']}]({_rel(st['strip'], out)})")
            L.append("")
        if p.get("retracted"):
            L += ["Ruled out by the tester (do not chase):"] + [f"- {x.get('claim', '')} → {x.get('resolution', '')}" for x in p["retracted"]] + [""]
        if p.get("to_verify"):
            L += ["To verify:"] + [f"- {x.get('title', '')}: {x.get('reason', '')}" for x in p["to_verify"]] + [""]
        if p.get("suggestions"):
            L += ["Suggestions (not bugs):"] + [f"- {x.get('content', '')}" for x in p["suggestions"]] + [""]
    path = out / "agent.md"
    path.write_text("\n".join(L) + "\n", "utf-8")
    return path


def write_json(steps: list[dict], report: dict, meta: dict, out: Path) -> Path:
    data = {"meta": meta, "report": report,
            "steps": [{**s, "image": _rel(s["image"], out),
                       **({"strip": _rel(s["strip"], out)} if s.get("strip") else {})} for s in steps]}
    path = out / "steps.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return path


# ---------------------------------------------------------------- HTML (share)

def _data_uri(path: str, max_width: int = 360, quality: int = 70) -> str:
    """Embed a downscaled JPEG; the page shows images small."""
    import base64
    import io

    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        if im.width > max_width:
            im = im.resize((max_width, round(im.height * max_width / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


CSS = """
:root{--bg:#f7f7f8;--fg:#1d1d1f;--card:#fff;--muted:#6b6b70;--line:#e3e3e6;--high:#d93025;--med:#e37400;--low:#c9a400;--ok:#1e8e3e;--accent:#2563eb}
@media (prefers-color-scheme:dark){:root{--bg:#151517;--fg:#ececf0;--card:#202024;--muted:#9a9aa2;--line:#33333a;--accent:#7aa2ff}}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px} h1{font-size:24px;margin:0 0 4px} h2{margin:32px 0 8px}
h3{font-size:18px;margin:0 0 4px} h4{font-size:14px;margin:14px 0 4px;color:var(--muted)} p{margin:4px 0} a{color:var(--accent)} .muted{color:var(--muted)}
ol.prio li{margin:4px 0} .tag{font-size:12px;color:var(--muted);white-space:nowrap}
.page{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:16px 0;display:flex;gap:18px}
.page .shot{width:200px;flex:none;align-self:flex-start} .page .shot img{width:100%;border-radius:8px;border:1px solid var(--line)}
.page .body{flex:1;min-width:0} ul{margin:4px 0;padding-left:20px} li{margin:3px 0}
.inter{border-left:3px solid var(--med);padding:6px 10px;margin:8px 0;background:color-mix(in srgb,var(--med) 6%,transparent);border-radius:4px}
.inter.high{border-left-color:var(--high)} .inter.low{border-left-color:var(--low)}
.inter img{max-width:100%;margin-top:6px;border-radius:4px;border:1px solid var(--line)}
.ok{color:var(--muted)} .ok::before{content:"✓ ";color:var(--ok)}
.cross{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin:8px 0}
details.all>summary{cursor:pointer;font-size:20px;font-weight:bold;margin:32px 0 8px}
table{border-collapse:collapse;width:100%;font-size:13px} td,th{border-bottom:1px solid var(--line);padding:4px 6px;vertical-align:top;text-align:left}
footer{margin-top:32px;font-size:13px;color:var(--muted)}
@media (max-width:640px){.page{flex-direction:column} .page .shot{width:60%}}
"""


def render_html(steps: list[dict], report: dict, meta: dict, out: Path) -> Path:
    """Self-contained single-file report: few images, all embedded."""
    by_id = {s["id"]: s for s in steps}
    idx = _page_index(report)
    sev_cls = {"high": "high", "medium": "", "low": "low"}
    parts: list[str] = []

    if report.get("priorities"):
        items = []
        for it in report["priorities"]:
            link = f' <a href="#{_page_anchor(idx[it["page"]])}">{e(it["page"])}</a>' if it.get("page") in idx else ""
            items.append(f"<li><b>{e(it.get('title', ''))}</b> <span class='tag'>{e(_tag(it))}</span>{link}</li>")
        parts.append("<h2>优先处理</h2><ol class='prio'>" + "".join(items) + "</ol>")
    if report.get("cross_page"):
        parts.append("<h2>跨页面问题</h2>" + "".join(
            f"<div class='cross'><b>{e(it.get('title', ''))}</b> <span class='tag'>{e(_tag(it))}</span>"
            f"<p>{e(it.get('detail', ''))}</p><p class='muted'>涉及：{e('、'.join(it.get('pages') or []))}</p></div>"
            for it in report["cross_page"]))

    cards = []
    for n, p in enumerate(report.get("pages") or [], 1):
        body = [f"<h3>{n}. {e(p['name'])}</h3>"]
        if p.get("purpose"):
            body.append(f"<p class='muted'>{e(p['purpose'])}</p>")
        if p.get("ui_issues"):
            body.append("<h4>界面问题</h4><ul>" + "".join(
                f"<li><b>{e(it.get('title', ''))}</b> <span class='tag'>{e(_tag(it))}</span><br>{e(it.get('detail', ''))}</li>"
                for it in p["ui_issues"]) + "</ul>")
        if p.get("interactions"):
            body.append("<h4>交互</h4>")
            for it in p["interactions"]:
                if it.get("verdict") != "issue":
                    body.append(f"<p class='ok'>{e(it.get('title', ''))}：{e(it.get('description', ''))}</p>")
                    continue
                st = by_id.get(it.get("strip_step_id"))
                img = (f"<img loading='lazy' src='{_data_uri(st['strip'], max_width=900, quality=60)}' "
                       f"alt='#{st['id']} 之前的过渡过程'>") if st and st.get("strip") else ""
                repro = (f"<p><b>复现</b>：{e(' → '.join(it['repro']))}</p>") if it.get("repro") else ""
                body.append(
                    f"<div class='inter {sev_cls.get(it.get('severity'), '')}'><b>{e(it.get('title', ''))}</b> "
                    f"<span class='tag'>{e(_tag(it))}</span><p>{e(it.get('description', ''))}</p>"
                    f"<p><b>预期</b>：{e(it.get('expected', '-'))}</p><p><b>实际</b>：{e(it.get('actual', '-'))}</p>{repro}{img}</div>")
        if p.get("feedback"):
            body.append(f"<h4>测试者评价</h4><p>{e(p['feedback'])}</p>")
        for key, title, fmt in (
            ("suggestions", "建议", lambda x: e(x.get("content", ""))),
            ("retracted", "已排除的怀疑", lambda x: f"{e(x.get('claim', ''))} → {e(x.get('resolution', ''))}"),
            ("to_verify", "待确认", lambda x: f"<b>{e(x.get('title', ''))}</b>：{e(x.get('reason', ''))}"),
        ):
            if p.get(key):
                body.append(f"<h4>{title}</h4><ul>" + "".join(f"<li>{fmt(x)}</li>" for x in p[key]) + "</ul>")
        shot = by_id.get(p.get("screenshot_step_id"))
        shot_html = (f"<div class='shot'><img loading='lazy' src='{_data_uri(shot['image'])}' alt='{e(p['name'])}'>"
                     f"<p class='tag'>#{shot['id']} · {e(shot['time'])}</p></div>") if shot else ""
        cards.append(f"<section class='page' id='{_page_anchor(n)}'>{shot_html}<div class='body'>{''.join(body)}</div></section>")
    parts.append("<h2>按页面</h2>" + "".join(cards))

    rows = "".join(f"<tr><td>{s['id']}</td><td>{e(s['time'])}</td><td>{e(s.get('screen', ''))}</td>"
                   f"<td>{e(s.get('action', ''))}</td><td>{e(s.get('observation', ''))}</td><td>{e(_speech(s))}</td></tr>"
                   for s in steps)
    parts.append(f"<details class='all'><summary>附录：全部 {len(steps)} 个步骤</summary><table>"
                 f"<tr><th>#</th><th>时间</th><th>界面</th><th>操作</th><th>结果</th><th>口述</th></tr>{rows}</table></details>")
    title = e(report.get("title") or "录屏测试报告")
    footer = f"<footer>{e(_cost_line(meta))}</footer>" if meta.get("usage") else ""
    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>{CSS}</style></head>
<body><main>
<h1>{title}</h1><p class="muted">{e(meta["video"])} · 时长 {e(meta["duration"])} · {len(report.get("pages") or [])} 个页面 · {len(steps)} 个步骤 · {e(meta.get("model", ""))} · {e(meta["generated"])}</p>
<p>{e(report.get("overview", ""))}</p>
{"".join(parts)}
{footer}
</main></body></html>"""
    path = out / "report.html"
    path.write_text(html, "utf-8")
    return path
