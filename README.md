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

需要转写测试者语音解说时，安装 audio 扩展（本地运行 Whisper，首次使用会自动下载模型）：

```bash
pip install "reel2report[audio] @ git+https://github.com/shaowenfu/reel2report"
```

## 使用

```bash
export DEEPSEEK_API_KEY=sk-...
reel2report recording.mp4 --context examples/context.md
```

输出目录（默认 `recording_report/`）：

| 文件 | 用途 |
|---|---|
| `report.md` | 给人看：确认问题、待确认项，以及每一步的时间段、截图、过渡过程和描述 |
| `agent.md` | 给编码 agent：确认问题与复现步骤 + 文字时间线，**只附带确认问题的截图**，节省 token |
| `report.html` | 单文件网页报告，图片内嵌，直接发给别人就能看，支持手机和深色模式 |
| `steps.json` | 结构化数据，可供其他工具或渲染器使用 |
| `frames/` | 关键帧截图，文件名带时间戳 |
| `.work/` | 中间结果和缓存：参数不变时重跑会复用关键帧；模型结果按批次缓存，中断后重跑会跳过已完成的批次；`--force` 可清空缓存 |

不调用模型、只抽取关键帧：

```bash
reel2report recording.mp4 --no-ai
```

### 提示词：场景模板 + 自定义要求

```bash
reel2report --list-templates
```

| 模板 | 用途 |
|---|---|
| `bug`（默认） | 内测找 bug：报错、卡顿、界面错乱、与预期不符 |
| `ux` | 体验评审：操作路径、引导、等待反馈、视觉细节 |
| `tutorial` | 教程记录：把操作整理成可照做的分步教程素材 |

在模板基础上追加要求：

```bash
reel2report rec.mp4 -t bug -i "重点关注支付流程，忽略开屏广告"
```

```bash
reel2report rec.mp4 --instructions-file my_focus.md
```

也可以写一份自己的模板文件，用 `-t path/to/my.md` 指定。格式参考 [reel2report/templates/bug.md](reel2report/templates/bug.md)，包含 `## role`、`## step`、`## summary` 三节，缺的节会沿用默认模板。输出的 JSON 格式固定在代码里，模板只改变关注点和语气，不会破坏解析。

### 报告结构

报告按“先结论、后细节”的顺序组织：

1. **问题**：有画面证据或测试者明确认定的问题，附复现步骤，分为缺陷（bug）、体验（ux）、未完成（incomplete）三类
2. **待确认**：猜测、证据不足的点，引用原话
3. **已排除的怀疑**：测试者曾怀疑、后来自己推翻的，开发者不用再查
4. **建议与需求**：口述中的功能建议（不是 bug）
5. **测试过程**：把来回跳转的步骤按“想验证什么”归并成若干任务，每个任务只配 1～3 张关键截图
6. **附录**：全部步骤（HTML 报告中默认折叠）

`agent.md` 只列出上面引用到的步骤，完整数据见 `steps.json`。

### 语音解说

录屏时开着麦克风边操作边说（“这里怎么没反应”“加载好慢”），口述会被转写，并按时间对应到当时的画面上，和截图一起交给模型。

口述可以很随意：说错、改口、跑题都没关系。处理分两个阶段：

- 逐步看图时，只记录每一步说了什么，以及属于什么性质（抱怨、猜测、更正、建议……），不下结论；
- 汇总时，拿着完整时间线和完整口述稿统一裁决，以最终说法为准。被自己推翻的怀疑进入“已排除”；没有确认的猜测进入“待确认”。

在界面之间来回跳转时，会在本地检测“重复访问之前的画面”并标注给模型，汇总时再合并成测试任务。

视频有音轨并且安装了 audio 扩展时，会自动转写（`--audio auto`）。可以用 `--asr-model` 选择 Whisper 模型大小，默认 `small`，中文效果和速度比较均衡。

### 常用参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--context FILE` | – | 产品背景：页面、功能、预期行为。模型据此判断“哪里不对” |
| `--fps` | 4 | 检测画面变化的采样率 |
| `--threshold` | 8 | 判定为同一画面的哈希距离阈值；漏掉了小变化就调低，步骤太多就调高 |
| `--min-stable` | 0.5 | 画面停留多少秒以上算一个步骤；更短的变化算作过渡过程 |
| `--transitions` | strip | `strip`：把过渡过程高帧率重采样，拼成一张胶片图交给模型；`drop`：丢弃 |
| `--motion-interval` | 3 | 持续滚动时每隔多少秒保留一帧 |
| `--batch` | 8 | 每次请求发送的截图数 |
| `--model` / `--base-url` | `deepseek-flash` / `https://api.deepseek.com` | 任何兼容 OpenAI 接口且支持图片输入的模型都可以用 |
| `--workers` | 4 | 并发请求数。设为 1 则按顺序分析，并把前文概括传给下一批，连贯性最好但最慢 |
| `--audio` | auto | 语音转写：`auto` / `on` / `off` |
| `--asr-model` | small | Whisper 模型；中文长录屏建议用 `medium`，更准但更慢 |
| `--asr-hints` | – | 产品名和界面专有词（逗号分隔），帮助语音识别拼对 |
| `--step-thinking` / `--summary-thinking` | off / on | 推理模型是否先思考。逐步看图时关掉，更快、更省，也不会输出失控；汇总时打开，裁决口述反复、归并任务更准 |
| `--detail` | auto | 图片精度，`low` 会先缩到 512×512，更省但看不清小字 |

也可以用环境变量 `REEL2REPORT_MODEL`、`REEL2REPORT_BASE_URL` 设置模型和接口地址。

## 录制建议

- 打开系统的**显示触摸**选项（Android：开发者选项 → 显示点按操作反馈；iOS：辅助功能 → 触控 → AssistiveTouch），模型更容易判断点了哪里。
- 遇到问题时多停留一两秒，确保画面被识别为一个独立步骤。

## 工作原理

1. **采样**：ffmpeg 以低分辨率按固定帧率采样。
2. **分段**：计算差分哈希（忽略顶部状态栏），连续相似的帧合并为一个“稳定画面”，记录它出现和结束的时间；持续滚动时定期取样。
3. **过渡过程**：两个稳定画面之间的短暂变化（加载、动画、闪屏）不单独成为步骤，而是用 8fps 重新采样，拼成一张带相对时间的胶片图，附在下一步前面。只多一张图的成本，模型就能看出白屏、闪烁、布局跳动，过渡耗时则在本地算出。
4. **截图**：每个稳定画面取最后一帧（加载完成后的状态），以高清晰度保存。
5. **口述**（可选）：本地 Whisper 转写语音，按词级时间戳在停顿和标点处切成短句，对应到说话时所在的画面。
6. **理解**：截图分批发给多模态模型，每批附上上一批的最后一帧（顺序模式下还附上流程概括），逐步描述操作、结果、过渡过程和异常。
7. **汇总**：用纯文本请求合并问题，区分“确认问题”和“待确认”，整理复现步骤，再渲染成文档。

## 端到端试跑

[examples/mock_app.py](examples/mock_app.py) 会生成一段 30 秒的模拟记账 App 录屏，里面埋了几个 bug（保存后首页不刷新、导出报 500、保存加载过慢、白屏闪烁），可以用来检验效果或做回归测试：

```bash
python examples/mock_app.py mock.mp4
```

实测数据：
- 30 秒的模拟录屏：约 1 万输入 token。
- 一段真实的 20 分钟安卓录屏（带口述）：251 个关键步骤；约 33 万输入、4.5 万输出 token；本地抽帧约 3 分钟，模型分析约 8 分钟；`medium` 语音转写在 4 核 CPU 上约 15 分钟（之后会缓存）。

```bash
reel2report mock.mp4 --context examples/context.md
```

## 开发

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
