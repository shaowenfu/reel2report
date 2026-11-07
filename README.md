# reel2report

把软件使用录屏变成**带时间戳截图 + AI 文字描述**的步骤报告，供开发者或编码 agent 直接复现问题。

Turn screen recordings of app usage into timestamped, AI-annotated step-by-step reports for developers and coding agents.

```
录屏 mp4 ──► 本地采样 + 去重（ffmpeg）──► 关键帧 ──► DeepSeek 多模态看图 ──► report.md / agent.md / steps.json
```

项目目标与路线图见 [docs/VISION.md](docs/VISION.md)。

## 安装

需要 Python 3.10+ 和 [ffmpeg](https://ffmpeg.org/)（含 ffprobe）。

```bash
pip install git+https://github.com/shaowenfu/reel2report
```

## 使用

```bash
export DEEPSEEK_API_KEY=sk-...
reel2report recording.mp4 --context examples/context.md
```

输出目录（默认 `recording_report/`）：

| 文件 | 用途 |
|---|---|
| `report.md` | 给人看：问题汇总 + 每一步的时间戳、截图和描述 |
| `agent.md` | 给编码 agent：问题与复现步骤 + 文字时间线，**只附带有问题的步骤截图**，节省 token |
| `steps.json` | 结构化数据，可供其他工具或渲染器使用 |
| `frames/` | 关键帧截图，文件名带时间戳 |
| `.work/` | 中间结果；模型结果按批次缓存，中断后重跑会跳过已完成的批次 |

不调用模型、只抽取关键帧：

```bash
reel2report recording.mp4 --no-ai
```

### 常用参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--context FILE` | – | 产品背景：页面、功能、预期行为。模型据此判断“哪里不对” |
| `--fps` | 2 | 检测画面变化的采样率 |
| `--threshold` | 8 | 判定为同一画面的哈希距离阈值；漏掉了小变化就调低，步骤太多就调高 |
| `--min-stable` | 0.5 | 画面至少停留多少秒才算一个步骤（过滤动画和加载中的过渡帧） |
| `--motion-interval` | 3 | 持续滚动时每隔多少秒保留一帧 |
| `--batch` | 8 | 每次请求发送的截图数 |
| `--model` / `--base-url` | `deepseek-v4-flash-vision-exp` / `https://api.deepseek.com` | 任何兼容 OpenAI 接口且支持图片输入的模型都可以用 |

也可以用环境变量 `REEL2REPORT_MODEL`、`REEL2REPORT_BASE_URL` 设置模型和接口地址。

## 录制建议

- 打开系统的**显示触摸**选项（Android：开发者选项 → 显示点按操作反馈；iOS：辅助功能 → 触控 → AssistiveTouch），模型更容易判断点了哪里。
- 遇到问题时多停留一两秒，确保画面被识别为一个独立步骤。

## 工作原理

1. **采样**：ffmpeg 以低分辨率按固定帧率采样。
2. **分段**：计算差分哈希（忽略顶部状态栏），连续相似的帧合并为一个“稳定画面”；停留太短的过渡帧丢弃，持续滚动时定期取样。
3. **截图**：每个稳定画面取最后一帧（加载完成后的状态），以高清晰度保存。
4. **理解**：截图按时间顺序分批发给多模态模型，同时附上上一批的流程概括和最后一帧，逐步描述操作、结果和异常。
5. **汇总**：用纯文本请求合并问题、整理复现步骤，再渲染成文档。

## 开发

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
