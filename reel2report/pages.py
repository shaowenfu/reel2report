"""Page-centric analysis: steps -> pages -> per-page findings -> overview.

A 20-minute session visits a handful of screens many times. Reporting per step
(or per spoken sentence) repeats the same page over and over. Instead:

1. group_pages      text-only: assign every step to the page (screen) it belongs to
2. analyze_page     one call per page, with 1-2 representative screenshots and a few
                    transition filmstrips: static UI findings + interaction findings
3. summarize_overview  text-only: title, overview, priorities, cross-page issues

Static UI problems are illustrated with a screenshot, interactions with the
transition filmstrip that shows them, so the report needs few images.
"""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .llm import LLM, image_part, text_part
from .templates import Template

# ---------------------------------------------------------------- shared rules

JUDGE_RULES = """裁决测试者的口述（非常重要）：
- 口述是边操作边想的，零碎、重复，前面说的话后面可能被推翻；转写可能有同音错别字。
- 以测试者最终的说法为准：“哦原来是这样”“刚才说错了”“想了一下其实是合理的”表示之前的批评被撤销。撤销的批评不算问题，写进 retracted；同时提出了改进想法的，放进 suggestions。
- “点击无反应”“功能不可用”要和后续步骤、口述交叉核对：测试者说“能点进去”或后面画面已经跳转的，不算问题。
- 被撤销的怀疑不要换个说法再写成问题（例如测试者说“刚才是我没点到”，就不要再写“按钮看起来像禁用”）。
- 只是猜测、没有得到确认的，放进 to_verify，引用原话。
- 系统通知、其他 App 的广告浮层、输入法不属于被测软件，不算问题；确实影响测试的放进 to_verify。
- 截图上的灰色或半透明圆点是系统“显示点按操作”的触摸指示（用户手指的位置），不是界面元素；被它遮住或叠在上面的内容不算问题。
- 转场动画中间帧的交错、残影不算问题。横屏应用在竖屏录屏里是侧着的，属正常。
- 测试者明确的批评（包括主观的体验、视觉批评）就是要交给开发者的反馈，不要丢。
- type：bug（功能错误、跳转错误、报错、崩溃）；ux（体验、交互、视觉、文案问题）；incomplete（未实现、点不开、占位或假的功能）。severity：high（流程走不下去/核心功能错误）、medium、low。
- 用简体中文，只基于提供的记录和图片，不要编造。"""


def _compose(*parts: str) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _extra(extra: str) -> str:
    return f"用户的特别要求（优先遵守）：\n{extra}" if extra else ""


def step_line(s: dict, with_speech: bool = True) -> str:
    """One compact line per step, shared by every text prompt."""
    parts = [f"#{s['id']} [{s['time']}] 界面：{s.get('screen', '')}"]
    if s.get("revisit_of"):
        parts.append(f"重复访问（同 #{s['revisit_of']}）")
    parts.append(f"操作：{s.get('action', '')}")
    if s.get("transition_s"):
        parts.append(f"过渡 {s['transition_s']:.1f}s：{s.get('transition') or ''}")
    parts.append(f"结果：{s.get('observation', '')}" + ("（不确定）" if s.get("uncertain") else ""))
    if s.get("anomaly"):
        parts.append(f"⚠{s['anomaly'].get('description', '')}")
    if with_speech:
        if s.get("speech"):
            parts.append(f"口述要点[{s.get('speech_type') or '?'}]：{s['speech']}")
        if s.get("narration"):
            parts.append(f"口述原文：{s['narration']}")
    return "｜".join(parts)


class _Cache:
    """Tiny JSON cache keyed by the full request, so re-runs skip finished calls."""

    def __init__(self, work: Path, name: str):
        self.dir = work / name
        self.dir.mkdir(parents=True, exist_ok=True)

    def key(self, *parts) -> Path:
        h = hashlib.sha1(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]
        return self.dir / f"{h}.json"

    def get(self, path: Path):
        return json.loads(path.read_text("utf-8")) if path.exists() else None

    def put(self, path: Path, data) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


# ---------------------------------------------------------------- 1. group pages

GROUP_SYSTEM = """【任务：页面归并】
你会拿到一段软件使用录屏的逐步记录。请把每一步归到它所在的“页面”（界面）。
- 页面指用户看到的一个界面，例如“首页”“练习列表”“音高比较练习”“设置”。同一个页面的上下滚动、重复进入、来回跳转都归到同一个页面。
- 弹窗、全屏加载页归到它所属或即将进入的页面，不单独成页；但内容和用途明显不同的全屏界面（如横屏练习页、课堂总结页）单独成页。
- 页面数量通常在 5-20 个之间，每一步必须且只能属于一个页面。
- 页面名用界面上的标题或测试者的叫法，简洁。
只输出 JSON：{"pages": [{"name": "页面名", "step_ids": [步骤编号, ...]}]}，按页面第一次出现的顺序排列。"""


def _norm_screen(name: str) -> str:
    name = re.sub(r"[（(【\[].*?[)）】\]]", "", name or "").strip()
    return re.split(r"[·\-—：:|｜]", name)[0].strip() or "未命名页面"


def fallback_pages(steps: list[dict]) -> list[dict]:
    """Group by normalized screen name; revisits follow the step they revisit."""
    page_of: dict[int, str] = {}
    order: list[str] = []
    for s in steps:
        name = page_of.get(s.get("revisit_of")) or _norm_screen(s.get("screen", ""))
        page_of[s["id"]] = name
        if name not in order:
            order.append(name)
    return [{"name": n, "step_ids": [i for i, p in page_of.items() if p == n]} for n in order]


def _clean_pages(raw: list[dict], steps: list[dict]) -> list[dict]:
    """Every step exactly once; steps the model skipped join the page of the step before them."""
    ids = [s["id"] for s in steps]
    owner: dict[int, int] = {}
    pages: list[dict] = []
    for p in raw:
        sids = []
        for i in p.get("step_ids") or []:
            try:
                i = int(i)
            except (TypeError, ValueError):
                continue
            if i in ids and i not in owner:
                sids.append(i)
        if sids:
            owner.update({i: len(pages) for i in sids})
            pages.append({"name": str(p.get("name") or "未命名页面"), "step_ids": sids})
    if not pages:
        return []
    last = owner.get(ids[0], 0)
    for i in ids:
        if i in owner:
            last = owner[i]
        else:
            owner[i] = last
            pages[last]["step_ids"].append(i)
    for p in pages:
        p["step_ids"].sort()
    pages.sort(key=lambda p: p["step_ids"][0])
    return pages


def group_pages(llm: LLM, steps: list[dict], work: Path, thinking: bool | None = False,
                log=print) -> list[dict]:
    cache = _Cache(work, "pages_group")
    lines = "\n".join(step_line(s, with_speech=False) for s in steps)
    path = cache.key(llm.model, GROUP_SYSTEM, lines, thinking)
    raw = cache.get(path)
    if raw is None:
        try:
            raw = llm.chat_json(GROUP_SYSTEM, [text_part("【逐步记录】\n" + lines)], max_tokens=16000,
                                thinking=thinking)
            cache.put(path, raw)
        except Exception as e:  # noqa: BLE001 - grouping must never sink the report
            log(f"      页面归并失败（{e}），改用界面名称分组")
            raw = {}
    pages = _clean_pages(raw.get("pages") or [], steps)
    return pages or fallback_pages(steps)


# ---------------------------------------------------------------- 2. analyze one page

PAGE_SYSTEM = """【任务：页面分析】
你在分析录屏中的一个页面。测试者可能多次进出这个页面；把这个页面相关的所有画面、交互和口述合在一起分析，写成这个页面的一个板块。

按两类组织发现：
- ui_issues：页面本身的静态问题（布局、视觉、图标、文案、信息量、看起来没做完的入口）。报告里用一张代表截图来示例。同类小问题合并成一条，在 detail 里分点写，不要一句话一条。
- interactions：在这个页面上发生的操作过程（点击后的跳转、加载、弹窗、提交、录音、返回等）。每条从提供的过渡胶片里选一个最能展示这个过程的 strip_step_id；没有合适的胶片填 null。正常的交互 verdict 为 "ok"，一句话带过；有问题的 verdict 为 "issue"，写清预期、实际和复现步骤。
- 跨页面的跳转问题（例如点了这里却去了错误的页面）记在发起操作的这个页面。记录里“之后 → #N 进入「某页面」”表示紧接着离开了本页、到了哪里：点击后离开了本页就不是“点击无反应”，要看去的页面对不对。
- screenshot_step_id 从提供的代表截图里选一张最能展示这个页面的。
- 一个页面通常 0-5 条 ui_issues、0-6 条 interactions。测试者对这个页面的总体评价写进 feedback。"""

PAGE_FORMAT = """只输出 JSON：
{"page": "页面名", "purpose": "这个页面是做什么的（一句话）", "screenshot_step_id": 步骤编号,
 "ui_issues": [{"title": "...", "type": "bug|ux|incomplete", "severity": "high|medium|low", "detail": "...", "step_ids": [...]}],
 "interactions": [{"title": "...", "step_ids": [...], "strip_step_id": 步骤编号或null, "description": "过程描述",
   "verdict": "ok|issue", "type": "bug|ux|incomplete", "severity": "high|medium|low",
   "expected": "...", "actual": "...", "repro": ["..."]}],
 "feedback": "测试者对这个页面的整体评价（合并口述，保留关键原话）",
 "suggestions": [{"content": "...", "step_ids": [...]}],
 "retracted": [{"claim": "起初的说法", "resolution": "后来如何撤销/确认", "step_ids": [...]}],
 "to_verify": [{"title": "...", "reason": "...（引用原话）", "step_ids": [...]}]}
verdict 为 ok 的交互不需要 type/severity/expected/actual/repro。没有内容的数组字段用 []。

输出前逐条自查：
1. retracted 里的每一条，都不能再以任何形式出现在 ui_issues 或 interactions 的问题里（只能出现在 suggestions）。
2. 系统通知、其他 App 的广告浮层、输入法不属于被测软件：不能出现在 ui_issues 或 interactions，最多放进 to_verify。
3. 每条有问题的交互都尽量填 strip_step_id：选 step_ids 里带过渡过程的那一步。
4. 同一件事不要同时写成 ui_issue 和 interaction：静态外观写 ui_issues，操作过程写 interactions。
5. feedback 里测试者明确批评的每一点（“不对”“有问题”“需要解决”“不合理”“太丑”等），都要在 ui_issues 或 interactions 里有对应的问题条目，除非已被撤销。"""


def pick_media(page_steps: list[dict], max_shots: int = 2, max_strips: int = 4) -> tuple[list[dict], list[dict]]:
    """Representative screenshots (longest on screen, visually distinct) and the
    filmstrips most likely to matter (anomaly, complaint, speech, long transitions)."""
    from .segment import dhash, hamming

    shots: list[dict] = []
    hashes: list[int] = []
    for s in sorted(page_steps, key=lambda s: s.get("stable_to", 0) - s.get("stable_from", 0), reverse=True):
        try:
            h = dhash(s["image"])
        except Exception:  # noqa: BLE001
            continue
        if all(hamming(h, o) > 12 for o in hashes):
            shots.append(s)
            hashes.append(h)
        if len(shots) >= max_shots:
            break

    def weight(s: dict) -> float:
        return (3 * bool(s.get("anomaly")) + 2 * (s.get("speech_type") == "complaint")
                + bool(s.get("speech") or s.get("narration")) + min(s.get("transition_s", 0), 3) / 3)

    strips = sorted((s for s in page_steps if s.get("strip")), key=weight, reverse=True)[:max_strips]
    return sorted(shots, key=lambda s: s["id"]), sorted(strips, key=lambda s: s["id"])


def analyze_page(llm: LLM, page: dict, steps_by_id: dict[int, dict], work: Path, tpl: Template,
                 extra: str = "", product_context: str = "", thinking: bool | None = False,
                 exits: dict[int, str] | None = None) -> dict:
    """exits: step id -> where the *next* step went when it left this page. Without it a tap
    that navigates elsewhere looks like 'nothing happened' from inside the page."""
    page_steps = [steps_by_id[i] for i in page["step_ids"] if i in steps_by_id]
    exits = exits or {}
    shots, strips = pick_media(page_steps)
    system = _compose(PAGE_SYSTEM, tpl.summary, JUDGE_RULES, _extra(extra), PAGE_FORMAT)
    content: list[dict] = []
    if product_context:
        content.append(text_part(f"【产品背景与预期行为】\n{product_context}"))
    content.append(text_part(f"【页面】{page['name']}（共 {len(page_steps)} 步）\n【这个页面的逐步记录】\n"
                             + "\n".join(step_line(s) + (f"｜之后 → {exits[s['id']]}" if s["id"] in exits else "")
                                         for s in page_steps)))
    for s in shots:
        content.append(text_part(f"代表截图 #{s['id']}（{s['time']}）："))
        content.append(image_part(Path(s["image"])))
    for s in strips:
        content.append(text_part(f"过渡胶片 #{s['id']}（进入 #{s['id']} 之前的过程，耗时约 {s.get('transition_s', 0):.1f}s，从左到右）："))
        content.append(image_part(Path(s["strip"])))

    cache = _Cache(work, "pages_analysis")
    path = cache.key(llm.model, system, [c.get("text") or c["image_url"]["url"][-64:] for c in content], thinking)
    result = cache.get(path)
    if result is None:
        result = llm.chat_json(system, content, max_tokens=8000, thinking=thinking)
        cache.put(path, result)
    return _clean_page_result(result, page, page_steps, shots)


def _ids(v) -> list[int]:
    out = []
    for i in v or []:
        try:
            out.append(int(i))
        except (TypeError, ValueError):
            pass
    return out


def _bigrams(t: str) -> set[str]:
    t = re.sub(r"[\s，。、“”‘’「」（）()：:,.!！?？]", "", t)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _overlaps(title: str, text: str, threshold: float = 0.4) -> bool:
    """Chinese-friendly fuzzy match: share of the title's character bigrams found in text."""
    a = _bigrams(title)
    return bool(a) and len(a & _bigrams(text)) / len(a) >= threshold


def _clean_page_result(r: dict, page: dict, page_steps: list[dict], shots: list[dict]) -> dict:
    """Keep the model honest about ids: screenshots/strips must exist on this page."""
    valid = {s["id"] for s in page_steps}
    with_strip = {s["id"] for s in page_steps if s.get("strip")}
    shot = r.get("screenshot_step_id")
    try:
        shot = int(shot)
    except (TypeError, ValueError):
        shot = None
    if shot not in valid:
        shot = shots[0]["id"] if shots else page_steps[0]["id"]
    inters = []
    for it in r.get("interactions") or []:
        sid = it.get("strip_step_id")
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            sid = None
        it["step_ids"] = [i for i in _ids(it.get("step_ids")) if i in valid]
        if sid not in with_strip:
            # interactions are illustrated by filmstrips: fall back to one of the cited steps
            sid = next((i for i in it["step_ids"] if i in with_strip), None)
        it["strip_step_id"] = sid
        inters.append(it)
    for key in ("ui_issues", "suggestions", "retracted", "to_verify"):
        for it in r.get(key) or []:
            it["step_ids"] = [i for i in _ids(it.get("step_ids")) if i in valid]
    # a static finding that mentions the touch indicator is the indicator itself, not the UI
    ui = [it for it in r.get("ui_issues") or []
          if not re.search(r"触摸点|触摸指示|触点|点按操作", f"{it.get('title', '')}{it.get('detail', '')}")]
    # a retracted criticism must not also be reported as an issue
    retracted = [f"{x.get('claim', '')}{x.get('resolution', '')}" for x in r.get("retracted") or []]
    if retracted:
        gone = lambda t: any(_overlaps(t, c) for c in retracted)  # noqa: E731
        ui = [it for it in ui if not gone(it.get("title", ""))]
        inters = [it for it in inters if it.get("verdict") != "issue" or not gone(it.get("title", ""))]
    r["ui_issues"] = ui
    return {
        "name": page["name"], "step_ids": page["step_ids"], "purpose": r.get("purpose", ""),
        "screenshot_step_id": shot, "ui_issues": r.get("ui_issues") or [], "interactions": inters,
        "feedback": r.get("feedback", ""), "suggestions": r.get("suggestions") or [],
        "retracted": r.get("retracted") or [], "to_verify": r.get("to_verify") or [],
    }


def page_exits(steps: list[dict], page_of: dict[int, str], look_ahead: int = 3,
               max_gap: float = 8.0) -> dict[int, str]:
    """For each step, where the session went next if it left the page shortly after.

    Looks past intermediate steps (typically a "loading…" screen that was grouped
    with the page being left), so a tap is linked to the page it actually opened.
    """
    exits: dict[int, str] = {}
    for i, cur in enumerate(steps):
        here = page_of.get(cur["id"])
        via = []
        for nxt in steps[i + 1:i + 1 + look_ahead]:
            if nxt.get("stable_from", 0) - cur.get("stable_to", 0) > max_gap:
                break
            if page_of.get(nxt["id"]) != here:
                hop = f"经 {'、'.join(via)} " if via else ""
                exits[cur["id"]] = f"{hop}#{nxt['id']} 进入「{page_of.get(nxt['id'], '?')}」（{nxt.get('screen', '')}）"
                break
            via.append(f"#{nxt['id']}")
    return exits


def analyze_pages(llm: LLM, pages: list[dict], steps: list[dict], work: Path, tpl: Template, extra: str = "",
                  product_context: str = "", workers: int = 4, thinking: bool | None = False,
                  log=print) -> list[dict]:
    by_id = {s["id"]: s for s in steps}
    page_of = {i: p["name"] for p in pages for i in p["step_ids"]}
    exits = page_exits(steps, page_of)

    def one(p: dict) -> dict:
        log(f"  页面「{p['name']}」：{len(p['step_ids'])} 步")
        return analyze_page(llm, p, by_id, work, tpl, extra, product_context, thinking, exits)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(one, pages))


# ---------------------------------------------------------------- 3. overview

OVERVIEW_SYSTEM = """【任务：总览】
你会拿到一份录屏测试报告中每个页面的分析结果。请写报告的开头部分。
- title：一句话标题，点出被测软件和最主要的问题。
- overview：3-5 句，测了哪些页面和流程，主要结论，测试者的总体评价。
- priorities：全局最值得先处理的 5-10 条，按重要性排序；每条注明所在页面（page 必须是给出的页面名之一）。
- cross_page：在多个页面重复出现的同一类问题（例如每次切换标签都整屏加载），合并成一条，列出涉及的页面。没有就填 []。
只基于给出的内容，不要编造。用简体中文。"""

OVERVIEW_FORMAT = """只输出 JSON：
{"title": "...", "overview": "...",
 "priorities": [{"title": "...", "page": "页面名", "type": "bug|ux|incomplete", "severity": "high|medium|low"}],
 "cross_page": [{"title": "...", "type": "bug|ux|incomplete", "severity": "high|medium|low", "pages": ["页面名"], "detail": "..."}]}"""


def _page_digest(p: dict) -> dict:
    return {
        "page": p["name"], "purpose": p.get("purpose", ""), "feedback": p.get("feedback", ""),
        "ui_issues": [f"[{i.get('type')}/{i.get('severity')}] {i.get('title')}：{i.get('detail', '')}" for i in p["ui_issues"]],
        "interaction_issues": [f"[{i.get('type')}/{i.get('severity')}] {i.get('title')}：{i.get('actual', '')}"
                               for i in p["interactions"] if i.get("verdict") == "issue"],
        "suggestions": [s.get("content") for s in p["suggestions"]],
    }


def summarize_overview(llm: LLM, pages: list[dict], work: Path, tpl: Template, extra: str = "",
                       thinking: bool | None = False) -> dict:
    system = _compose(OVERVIEW_SYSTEM, _extra(extra), OVERVIEW_FORMAT)
    digest = json.dumps([_page_digest(p) for p in pages], ensure_ascii=False, indent=1)
    cache = _Cache(work, "overview")
    path = cache.key(llm.model, system, digest, thinking)
    result = cache.get(path)
    if result is None:
        result = llm.chat_json(system, [text_part("【各页面分析】\n" + digest)], max_tokens=8000, thinking=thinking)
        cache.put(path, result)
    names = {p["name"] for p in pages}
    result["priorities"] = [x for x in result.get("priorities") or [] if x.get("page") in names] or result.get("priorities") or []
    result.setdefault("cross_page", [])
    return result
