"""Command line entry: reel2report VIDEO [options]."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import __version__, templates
from .analyze import describe_steps, fmt_time, span, summarize
from .extract import grab_frame, grab_strip, probe_duration, sample_frames
from .render import render_agent, render_human, write_json
from .segment import segment_frames


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="reel2report",
        description="把软件操作录屏变成带时间戳截图和 AI 描述的步骤报告。",
    )
    p.add_argument("video", type=Path, nargs="?", help="录屏文件（mp4/mov 等 ffmpeg 支持的格式）")
    p.add_argument("-o", "--out", type=Path, help="输出目录（默认：<视频名>_report）")
    p.add_argument("--context", type=Path, help="产品背景文件：页面列表、功能说明、预期行为（Markdown/文本）")

    t = p.add_argument_group("提示词")
    t.add_argument("-t", "--template", default=templates.DEFAULT,
                   help=f"场景模板：内置 {'/'.join(templates.builtin_names())}，或自定义模板文件路径（默认 bug）")
    t.add_argument("-i", "--instructions", help="追加的特别要求，例如 \"重点关注支付流程，忽略广告弹窗\"")
    t.add_argument("--instructions-file", type=Path, help="从文件读取追加要求")
    t.add_argument("--list-templates", action="store_true", help="列出内置模板后退出")

    g = p.add_argument_group("抽帧与去重（本地）")
    g.add_argument("--fps", type=float, default=4.0, help="检测画面变化的采样率（默认 4）")
    g.add_argument("--threshold", type=int, default=8, help="判定为同一画面的哈希距离阈值，越大越不敏感（默认 8）")
    g.add_argument("--min-stable", type=float, default=0.5, help="画面停留多少秒以上算一个步骤，更短的算过渡过程（默认 0.5）")
    g.add_argument("--transitions", choices=["strip", "drop"], default="strip",
                   help="过渡过程处理：strip=高帧率重采样拼成胶片图交给模型（默认）；drop=丢弃")
    g.add_argument("--strip-fps", type=float, default=8.0, help="过渡胶片的重采样帧率（默认 8）")
    g.add_argument("--motion-interval", type=float, default=3.0, help="持续滚动/动画时每隔多少秒保留一帧（默认 3）")
    g.add_argument("--max-width", type=int, default=720, help="保存截图的最大宽度（默认 720）")
    g.add_argument("--max-frames", type=int, default=300, help="关键帧数量上限，超过时均匀抽稀（默认 300）")

    m = p.add_argument_group("模型")
    m.add_argument("--model", help="模型名（默认 deepseek-flash，或环境变量 REEL2REPORT_MODEL）")
    m.add_argument("--base-url", help="OpenAI 兼容接口地址（默认 https://api.deepseek.com）")
    m.add_argument("--detail", choices=["auto", "low", "high", "original"], default="auto",
                   help="图片精度：low 先缩到 512×512，更省但看不清小字（默认 auto）")
    m.add_argument("--batch", type=int, default=8, help="每次请求发送的截图数（默认 8）")
    m.add_argument("--no-ai", action="store_true", help="只抽取关键帧并生成带时间戳的截图报告，不调用模型")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _thin(items: list, limit: int) -> list:
    if len(items) <= limit:
        return items
    step = len(items) / limit
    return [items[int(i * step)] for i in range(limit)]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_templates:
        for tp in templates.list_templates():
            print(f"{tp.name:10s} {tp.description}")
        return 0
    if args.video is None:
        parser.error("需要提供录屏文件")
    video: Path = args.video
    if not video.exists():
        print(f"文件不存在：{video}", file=sys.stderr)
        return 2
    try:
        tpl = templates.load(args.template)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    extra = "\n".join(x for x in [
        args.instructions or "",
        args.instructions_file.read_text("utf-8") if args.instructions_file else "",
    ] if x.strip())

    out: Path = args.out or video.with_name(video.stem + "_report")
    work = out / ".work"
    frames_dir = out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    duration = probe_duration(video)
    print(f"[1/4] 采样画面：{video.name}（{fmt_time(duration)}，{args.fps} fps）")
    samples = sample_frames(video, work / "samples", args.fps)

    print(f"[2/4] 识别稳定画面：{len(samples)} 帧采样")
    segments = segment_frames(samples, args.fps, args.threshold, args.min_stable, args.motion_interval)
    segments = _thin(segments, args.max_frames)
    key_frames = []
    n_strips = 0
    for i, seg in enumerate(segments, 1):
        t = min(seg.key_time, max(duration - 0.05, 0))
        stamp = fmt_time(t).replace(":", "-")
        kf = {"id": i, "t": round(t, 2), "kind": seg.kind,
              "stable_from": round(seg.start, 2), "stable_to": round(seg.end, 2),
              "image": str(grab_frame(video, t, frames_dir / f"{i:04d}_{stamp}.jpg", args.max_width))}
        if seg.transitions:
            kf["transition_s"] = round(sum(e - s for s, e in seg.transitions), 2)
            if args.transitions == "strip":
                # widen by half a sample on each side: the change began/ended between samples
                pad = 0.5 / args.fps
                spans = [(max(s - pad, 0), min(e + pad, duration)) for s, e in seg.transitions]
                strip = grab_strip(video, spans, frames_dir / f"{i:04d}_{stamp}_transition.jpg", args.strip_fps)
                if strip:
                    kf["strip"], kf["strip_frames"] = str(strip[0]), strip[1]
                    n_strips += 1
        key_frames.append(kf)
    (work / "keyframes.json").write_text(json.dumps(key_frames, ensure_ascii=False, indent=2), "utf-8")
    print(f"      得到 {len(key_frames)} 个关键步骤，{n_strips} 段过渡过程")

    meta = {"video": video.name, "duration": fmt_time(duration), "template": tpl.name,
            "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "tool": f"reel2report {__version__}"}
    product_context = args.context.read_text("utf-8") if args.context else ""

    if args.no_ai:
        steps = [{**f, "time": span(f), "screen": "", "action": "", "observation": "",
                  "transition": None, "anomaly": None, "uncertain": False} for f in key_frames]
        summary = {"title": f"{video.stem} 关键帧", "overview": "（--no-ai 模式：仅抽取关键帧，未调用模型）", "issues": []}
        print("[3/4] 跳过模型分析（--no-ai）")
    else:
        from . import llm as llm_mod
        llm_mod.IMAGE_DETAIL = args.detail
        llm = llm_mod.LLM(model=args.model, base_url=args.base_url)
        meta["model"] = llm.model
        print(f"[3/4] 模型分析：{llm.model}，模板 {tpl.name}" + ("，含追加要求" if extra else ""))
        steps = describe_steps(llm, key_frames, work, tpl, extra, args.batch, product_context)
        print("      汇总")
        summary = summarize(llm, steps, tpl, extra, product_context)
        meta["usage"] = llm.usage

    print("[4/4] 生成文档")
    paths = [render_human(steps, summary, meta, out), render_agent(steps, summary, meta, out),
             write_json(steps, summary, meta, out)]
    for pth in paths:
        print(f"      {pth}")
    if "usage" in meta:
        u = meta["usage"]
        print(f"      tokens：输入 {u['prompt_tokens']}，输出 {u['completion_tokens']}")
    return 0
