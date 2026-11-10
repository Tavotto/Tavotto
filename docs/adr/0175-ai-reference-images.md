# ADR 0175：改图助手可以附参考图，给用户自己连的那个 AI 看

日期：2026-11-10 · 状态：**Accepted**（用户 2026-11-10：「改图助手无法上传图片，应该要上传图片供模型参考」；1.0 豁免见 `docs/1.0-release-readiness.md` §1.8）
相关：[0138 TAF](0138-tavotto-ai-foundation.md)（候选工作区、提交前检查）、[0143 生成可编辑版本](0143-convert-to-editable.md)（把参考图放进工作副本让模型看的先例）、[0147 内置执行器](0147-builtin-agent-runner.md)（`read_image`）、[0132 助手提交幂等](0132-ai-submit-idempotency-and-draft-cache.md)。

## 问题

用户想说「照这张图的配色改」，输入框只能打字。后端其实早就有送图通道：内置执行器的 `read_image`、「生成可编辑版本」把原图副本放进候选让模型看。缺的是用户这一头的入口，以及把图**安全地**放进改图任务的工作目录。

## 决定

### 一、入口（前端）

输入框工具行（原有的那一行，不加行、不撑大卡片）最左多一枚图片钮；也可以在输入框里粘贴截图、把图片文件拖进输入框。已附的图在同一行里显示成 22 px 缩略图，右上角 × 去掉。最多 4 张。附图只活在页面内存里（`store/aiRefStore.ts`，按「项目 × 面板文件」分槽，与草稿同键）；刷新页面就没了。

图一加进来就上传后端暂存，发送时 `POST /api/ai/run` 只带暂存 id（`ref_images`），请求体里没有图片字节，所以 30 s 的发送超时、幂等账本、同 rid 重发都不受影响。账本只记提示词与目标，不记图；「已发出未确认」后同 rid 重发时提交自己带着那几个 id。

### 二、兜底：任何失败都不卡住用户

- 格式：PNG / JPEG / GIF / WebP 原样用（按魔数认，不信扩展名）；其它格式（HEIC、AVIF、BMP……）浏览器能解码就转成 PNG / JPEG，解不了给一句话，不影响别的图与发送。
- 大小：超过 3.5 MiB（后端上限 4 MiB，与内置执行器 `read_image` 同一个数）或像素超过 1600 万 / 单边 4096，前端自动缩到最长边 2048 并重编码（PNG 优先，不行 JPEG 降质量，再不行继续缩）。后端另有兜底：4 MiB、4000 万像素、魔数。
- 张数：超过 4 张只收前 4 张，一句话提示。
- 暂存里找不到的 id（过期、被删）静默跳过，任务照常发，只是少一张图。

### 三、送给模型（后端，`engine/ai_refimages.py`）

不附图时本路径**一行都不执行**：请求、提示词、工作区、历史行与原来逐字节相同（`tests/test_ai_refimages.py::test_without_images_*`）。附图时：

| 路径 | 图放哪 | 提示词里的路径 | 谁怎么看到图 |
| --- | --- | --- | --- |
| TAF 候选 / 内置执行器（内置执行器恒走候选） | `<候选>/_reference/ref-N.<ext>` | 相对路径 `_reference/ref-N.png`（Agent 的 cwd 就是候选） | 内置执行器用 `read_image`（候选内路径判据不变）；Codex / Claude 在 cwd 内自己读 |
| 旧路径（外部 CLI 原地改，TAF 开关默认关时走这条） | `data_dir()/cache/ai_refs/<sid>/` | 绝对路径 | Codex 的 workspace-write 沙盒本来就能读 cwd 之外；Claude 追加 `--add-dir <该目录>`（`RunContext.read_dirs`，只在附图时出现，其余参数一个字节不变） |

**绝不写进用户项目目录。** 提示词末尾追加一段（`ai_prompt_pack.ref_images_note`，同「生成可编辑版本」一节的做法：不进 `V1` 的闭集，Harness Lab 的候选 Pack 契约不变），明说图片内容只是参考资料、不是指令，不要改动这些图。

提交前检查：`ai_figure_edit._tree` 把候选根下的 `_reference/` 整个排除（同 `redirected/`），所以它既不会被当成「改了别的文件」，也不会被当成要写回的东西；`REFERENCE_DIRNAME` 的唯一出处在 `ai_refimages`。

清理：候选里的 `_reference/` 随候选目录一起删（既有规则）；旧路径的会话目录在 `_finish_session` 与被拒的 `_drop_attempt_files` 里删，进程被杀留下的按 24 h 兜底清；暂存区的图 1 h 过期、用户点 × 时立即删。

### 四、模型看不了图

后端 `ai_refimages.known_text_only` 按型号名判**确定**只吃文字的（DeepSeek 系、GLM-4.x 非 V、MiniMax-M、Kimi K2 文本版、Qwen-Coder、o1/o3-mini 等；名字带 `vl` / `vision` / `omni` 的一律当能看）。能力清单的每个执行器与连接多一个可选字段 `no_vision`（没有就不出这个键）。前端只认清单里写明的：附了图且当前型号在清单里，输入框上方一句「这个模型看不了图，换成 X」，点一下切到别的型号（先看本连接，再看别的就绪连接）；判不出的型号**照常发送、不拦**。判据只有后端一份，前端不镜像名单。

### 五、历史

`sessions.ref_count`（只存张数，`_ADDED_COLUMNS` 迁移，老行 NULL）。本页发起的会话在用户消息下显示缩略图（data URL，只在页面内存里）；刷新 / 接回 / 历史里只显示「附了 N 张图」。图片本身不进历史库，历史文件不会因此无限增长。

### 六、隐私

- 图片只发给用户自己连的 AI（与提示词、脚本同一条通道）；Tavotto 不经自己的服务器中转。
- 不进遥测：没有新事件，也不改 `CONSENT_VERSION`。`ai_assistant_invoked` 载荷不变。
- 不进诊断包：包里只有白名单里的日志与报告，不读 `cache/ai_refs*` 与候选目录；`tests/test_ai_refimages.py::test_diagnostics_bundle_never_contains_reference_images` 钉住。
- `docs/privacy.md` 的「Optional AI assistant」一节补一句「你附的图片」。

## 为什么不……

- **把图 base64 塞进 `/api/ai/run` 的 JSON**：4 张 × 4 MiB 的请求会撞 30 s 超时、让「已发出未确认」更常发生，也会把图带进幂等账本的重发路径。
- **图放进项目的某个隐藏目录**：违反「绝不往用户项目写」，同步盘还会把它同步出去。
- **按型号名在前端判能不能看图**：两份名单迟早分叉（同源对）；判据放后端、清单下发。
- **新增回形针图标**：图标集是 ADR 0052 的封闭集（147 个，说明书与测试都按个数钉），为一个按钮扩集合不值得；用现有的 `Image`，语义也准。

## 1.0 纪律

1.0 登记例外（用户 2026-11-10 裁决）。默认开（只有用户自己附图才触发）；逃生口：不附图时请求、提示词与工作区逐字节不变。

## 看护

`tests/test_ai_refimages.py`（暂存 / 候选与旧路径各一条 / 不附图逐字节不变 / `_reference` 排除 / 清理 / Claude `--add-dir` 只在附图时出现 / 内置 `read_image` 读得到 / 诊断包 / 模型判据）、`web/src/lib/aiRefImages.test.ts`、`web/src/components/ai/assistantRefImages.test.tsx`、`web/src/lib/aiVision.test.ts`。
