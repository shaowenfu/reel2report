"""Model stage 1: describe each key frame (pages.py turns steps into the report)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .llm import LLM, OutputTruncated, image_part, text_part
from .templates import Template

# Fixed parts: rules that keep the output honest, and the JSON format the code parses.
STEP_RULES = """你会按时间顺序看到一段软件使用录屏中的关键截图。请为每张截图推断：用户在上一画面到这一画面之间做了什么操作、当前是什么界面、出现了什么结果，以及是否有异常。

通用规则：
- 只描述截图中能看到的内容，不要编造。无法确定时把 uncertain 设为 true，并在描述里写明“推测”。
- 有的截图前面附有“过渡过程”胶片图：从左到右是两个稳定画面之间按时间顺序截取的缩略图，左上角标着相对时间，并给出过渡总耗时。请据此判断过渡期间发生了什么（加载、动画、闪屏、布局跳动、白屏等），写进 transition 字段；没有胶片图的步骤 transition 为 null。
- 每张截图都标注了画面“出现–结束”的时间段，截图取自该画面最后稳定的时刻。计算耗时、等待时长时以时间段为准，不要用截图时刻推算。
- 因为截图是这段时间的最后一刻，输入框里已有的内容通常是用户在这段时间里刚输入的，不要当成预填数据或残留数据。
- anomaly 只用于有画面证据的问题。“不确定”用 uncertain 表达，不要因为不确定就标 anomaly。
- 转场动画的中间帧里，元素位置交错、新旧页面叠在一起、上一页的残影都是正常现象，不是布局错乱。只有转场后的最终画面有问题，或者过渡中出现整屏白屏/黑屏、闪烁、内容先错后对，才算异常。
- 画面上的灰色或半透明圆点通常是系统“显示点按操作”的触摸指示，表示用户手指的位置，可用来判断点了哪里；它不是界面元素，被它遮住的内容不算异常。
- 标注了“重复访问”的截图，表示画面与之前某一步几乎相同（用户在界面之间来回跳转）。screen 沿用同一个界面名称，observation 只写和上次相比的变化，没有变化就写“同 #编号”。
- 第一张截图之前没有画面时，action 写“（录屏开始）”。
- 用简体中文，描述简洁具体，写出界面上关键的按钮或文字。控制长度：action 不超过 30 字，observation 不超过 80 字。speech 不限长度：长段口述要保留每一个观点和每一次改口（例如先批评、后来又说“还是合理的”），宁可写长，不要丢。
- 推断 action 时，以上一张截图里触摸点的位置为准：触摸点落在哪个控件上，就是点了哪个控件，画面变化就是这次点击的结果。没看到触摸点时不要编造点击（例如不要因为底部某个标签高亮了，就认为用户点了这个标签），写“推测：……”并标 uncertain。
- 截图里出现触摸点而画面还没变化，只说明用户刚点下去，结果往往出现在下一张截图里。判断“点击无反应”之前，先看下一张截图和口述；本批看不到下一张时标 uncertain，不要直接下结论。
- 系统通知、其他 App 的广告浮层、输入法、录屏控件等不属于被测软件。照实描述，但不要算作被测软件的异常，除非测试者明确指出。
- 横屏应用在竖屏录屏里会整体旋转 90°。遇到这种画面，按正常横屏界面来描述，不要当成异常。

关于测试者的口述（语音转写）：
- 口述是边操作边想、边想边说的，常常零碎、重复、跑题，前面说的话后面可能被推翻；转写也可能有同音错别字。
- 这一阶段你只看得到局部，不要替测试者下结论。在 speech 字段如实记录这一步口述的要点：去掉口头禅和重复，保留原意和关键原话，不要美化或补充。
- 语音转写常把产品里的词转成同音字。请对照画面上的文字纠正明显的错字（例如画面上是“首页”而转写是“手页”，就写“首页”），不确定的保持原样。
- speech_type 标注口述的性质：observation（描述看到的）、complaint（抱怨/认为有问题）、guess（猜测/不确定）、correction（更正或推翻自己之前的说法）、suggestion（功能建议/需求）、explain（解释自己在做什么/要测什么）、other。
- 口述与画面矛盾时，两者都如实记录，并标 uncertain；不要因为口述就认定有 bug，也不要因为画面正常就忽略口述。"""

STEP_FORMAT = """只输出 JSON，格式：
{"steps": [{"id": <截图编号>, "screen": "界面名称", "action": "用户操作", "observation": "画面内容与结果",
  "transition": null 或 "过渡过程描述",
  "speech": null 或 "口述要点", "speech_type": null 或 "observation|complaint|guess|correction|suggestion|explain|other",
  "anomaly": null 或 {"description": "...", "severity": "high|medium|low"}, "uncertain": false}],
 "context": "到目前为止的流程概括（一两句话，供下一批参考）"}"""

def _compose(*parts: str) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def step_system(tpl: Template, extra: str = "") -> str:
    return _compose(tpl.role, STEP_RULES, f"场景要求：\n{tpl.step}" if tpl.step else "",
                    f"用户的特别要求（优先遵守）：\n{extra}" if extra else "", STEP_FORMAT)


def fmt_time(t: float) -> str:
    m, s = divmod(t, 60)
    h, m = divmod(int(m), 60)
    return f"{h:d}:{m:02d}:{s:04.1f}" if h else f"{m:02d}:{s:04.1f}"


def span(f: dict) -> str:
    """Human-readable time range a screen was on display."""
    if "stable_from" in f and "stable_to" in f:
        return f"{fmt_time(f['stable_from'])}–{fmt_time(f['stable_to'])}"
    return fmt_time(f["t"])


def _batch_content(batch: list[dict], prev_frame: dict | None, running_context: str,
                   product_context: str) -> list[dict]:
    content: list[dict] = []
    if product_context:
        content.append(text_part(f"【产品背景与预期行为】\n{product_context}"))
    if running_context:
        content.append(text_part(f"【之前的流程概括】\n{running_context}"))
    if prev_frame:
        content.append(text_part(
            f"【参考】上一批最后一张截图（编号 {prev_frame['id']}，{span(prev_frame)}），不需要再描述："))
        content.append(image_part(Path(prev_frame["image"])))
    content.append(text_part("【本批截图】请逐张描述："))
    for f in batch:
        if f.get("strip"):
            content.append(text_part(
                f"截图编号 {f['id']} 之前的过渡过程（耗时约 {f['transition_s']:.1f}s，"
                f"{f['strip_frames']} 帧，从左到右）："))
            content.append(image_part(Path(f["strip"])))
        head = f"截图编号 {f['id']}，画面 {span(f)}"
        if f.get("revisit_of"):
            head += f"（重复访问：与 #{f['revisit_of']} 画面几乎相同）"
        if f.get("narration"):
            head += f"，测试者此时口述：「{f['narration']}」"
        head += "："
        content.append(text_part(head))
        content.append(image_part(Path(f["image"])))
    return content


def describe_steps(llm: LLM, frames: list[dict], work: Path, tpl: Template, extra: str = "",
                   batch_size: int = 8, product_context: str = "", workers: int = 1,
                   step_thinking: bool | None = False, log=print) -> list[dict]:
    """frames: [{"id", "t", "image", "strip"?, "transition_s"?, "narration"?}].

    workers=1 runs batches in order and passes a running summary forward (best
    continuity). workers>1 runs batches concurrently; each batch still sees the
    previous batch's last screenshot, but not the text summary.
    Results are cached per batch, keyed by everything that shapes the prompt.
    """
    system = step_system(tpl, extra)
    mode = "seq" if workers <= 1 else "par"
    key = hashlib.sha1(f"{llm.model}\n{system}\n{product_context}\n{batch_size}\n{mode}\n{step_thinking}".encode()).hexdigest()[:10]
    cache = work / "batches" / key
    cache.mkdir(parents=True, exist_ok=True)
    batches = [frames[i : i + batch_size] for i in range(0, len(frames), batch_size)]
    results: list[dict | None] = [None] * len(batches)

    def _ask(batch: list[dict], prev: dict | None, running_context: str) -> dict:
        """One request; if the reply is cut off, split the batch and ask for each half."""
        try:
            return llm.chat_json(system, _batch_content(batch, prev, running_context, product_context),
                                 thinking=step_thinking)
        except OutputTruncated:
            if len(batch) == 1:
                # one screenshot alone still runs away (repetition loop): record it as
                # unreadable rather than failing the whole run
                f = batch[0]
                log(f"    截图 #{f['id']} 的输出异常，已标记为待人工查看")
                return {"steps": [{"id": f["id"], "screen": "", "action": "",
                                   "observation": "（模型对这张截图的输出异常，请人工查看截图）",
                                   "anomaly": None, "uncertain": True}],
                        "context": running_context}
            mid = len(batch) // 2
            log(f"    输出过长，拆成 {mid} + {len(batch) - mid} 张重试")
            a = _ask(batch[:mid], prev, running_context)
            b = _ask(batch[mid:], batch[mid - 1], a.get("context", running_context))
            return {"steps": a.get("steps", []) + b.get("steps", []), "context": b.get("context", "")}

    def run(bi: int, running_context: str = "") -> dict:
        cache_file = cache / f"batch_{bi:04d}.json"
        # the cache also remembers frame content, so re-segmented videos are not mixed up
        sig = [(f["id"], f["t"], f.get("narration", ""), f.get("revisit_of")) for f in batches[bi]]
        if cache_file.exists():
            cached = json.loads(cache_file.read_text("utf-8"))
            if cached.get("_sig") == json.loads(json.dumps(sig)):
                log(f"  批次 {bi + 1}/{len(batches)}：使用缓存")
                return cached
        n_strip = sum(1 for f in batches[bi] if f.get("strip"))
        log(f"  批次 {bi + 1}/{len(batches)}：{len(batches[bi])} 张截图 + {n_strip} 张过渡胶片")
        prev = batches[bi - 1][-1] if bi > 0 else None
        result = _ask(batches[bi], prev, running_context)
        result["_sig"] = sig
        cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
        return result

    if workers <= 1:
        ctx = ""
        for bi in range(len(batches)):
            results[bi] = run(bi, ctx)
            ctx = results[bi].get("context", ctx)
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for bi, r in zip(range(len(batches)), pool.map(run, range(len(batches)))):
                results[bi] = r

    steps: list[dict] = []
    for batch, result in zip(batches, results):
        by_id = {}
        for s in (result or {}).get("steps", []):
            try:
                by_id[int(s.get("id"))] = s
            except (TypeError, ValueError):
                continue
        for f in batch:
            s = by_id.get(f["id"], {"screen": "", "action": "", "observation": "（模型未返回该步骤）",
                                    "anomaly": None, "uncertain": True})
            steps.append({**s, **{k: v for k, v in f.items() if k != "id"},
                          "id": f["id"], "time": span(f)})
    return steps
