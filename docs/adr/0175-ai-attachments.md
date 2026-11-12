# ADR 0175：改图助手可以附文件（图片给模型看、数据文件给 AI 读），只给用户自己连的那个 AI

日期：2026-11-10（2026-11-11 修订：不限图片、预览行、两个气泡）· 状态：**Accepted**（用户 2026-11-10：「改图助手无法上传图片，应该要上传图片供模型参考」；11-11：「附件不限于图片，可以上传任意文件」；1.0 豁免见 `docs/1.0-release-readiness.md` §1.8）
相关：[0138 TAF](0138-tavotto-ai-foundation.md)（候选工作区、提交前检查）、[0143 生成可编辑版本](0143-convert-to-editable.md)（把参考图放进工作副本让模型看的先例）、[0147 内置执行器](0147-builtin-agent-runner.md)（`read_image`）、[0132 助手提交幂等](0132-ai-submit-idempotency-and-draft-cache.md)、[0052 自绘图标集](0052-in-house-icon-set.md)（封闭图标集，本 ADR 正规加一个 `Paperclip`）。

## 问题

用户想说「照这张图的配色改」「数据在这个 CSV 里」，输入框只能打字。后端其实早就有送图通道：内置执行器的 `read_image`、「生成可编辑版本」把原图副本放进候选让模型看。缺的是用户这一头的入口，以及把文件**安全地**放进改图任务的工作目录。

## 决定

### 一、入口与预览（前端）

输入框工具行最左一枚回形针（`Paperclip`，新加进图标集，说明书同步）；也可以在输入框里粘贴截图 / 复制的文件、把任意文件拖进输入框（`onPaste` / `onDrop` 都在 AI 输入框自己身上，`stopPropagation`，画布级的拖放接不走）。从表格 / 网页复制来的「文字 + 渲染图」按文字粘贴，不拦。最多 6 个。

有附件时，「图名 · 整张图」那一栏**上方**才多出一行专门的预览：每张卡略微朝不同方向倾斜（按序号取 `[-3, 2.5, -1.5, 3, -2, 1.5]` 度，确定性、不随机）、带轻影（`shadow-thumb`）；指针到了 / 键盘聚焦时摆正、抬起（`shadow-lift`）并出现 ×；图片显示缩略图，其它文件显示成文件卡（扩展名角标 + 截断的文件名）。`prefers-reduced-motion` 下不做位移动画。没有附件时界面与之前完全一样。附件只活在页面内存里（`store/aiAttachStore.ts`，按「项目 × 面板文件」分槽，与草稿同键）；刷新页面就没了。

模型胶囊里去掉「ChatGPT ·」这类提供方文字，换成提供方 logo（`BrandLogo`，来自连接的 `logo`），全名放 title。

文件一加进来就上传后端暂存，发送时 `POST /api/ai/run` 只带暂存 id（`attachments`），请求体里没有文件字节，所以 30 s 的发送超时、幂等账本、同 rid 重发都不受影响。账本只记提示词与目标，不记附件；「已发出未确认」后同 rid 重发时提交自己带着那几个 id。

### 二、兜底：任何失败都不卡住用户

- 图片：PNG / JPEG / GIF / WebP 原样用（按魔数认，不信扩展名）；超过 3.5 MiB 或像素超过 1600 万 / 单边 4096，前端自动缩到最长边 2048 并重编码（PNG 优先，不行 JPEG 降质量，再不行继续缩）。浏览器解不了的图片（HEIC 等）当普通文件附上。后端兜底：图片 4 MiB（与内置执行器 `read_image` 同一个数）、4000 万像素、魔数。
- 其它文件：任意格式，单个 ≤ 25 MiB；一次发送合计 ≤ 50 MiB（同「联系作者」附件的合计口径）。超限一句话提示，只收装得下的，不报错卡住。
- 个数：超过 6 个只收前 6 个，一句话提示。
- 暂存里找不到的 id（过期、被删）静默跳过，任务照常发，只是少几个文件。

### 三、送给模型（后端，`engine/ai_attachments.py`）

不附文件时本路径**一行都不执行**：请求、提示词、工作区、历史行与原来逐字节相同（`tests/test_ai_attachments.py::test_without_images_*`）。附文件时：

| 路径 | 文件放哪 | 提示词里的路径 | 谁怎么看到 |
| --- | --- | --- | --- |
| TAF 候选 / 内置执行器（内置执行器恒走候选） | `<候选>/_attachments/<原文件名>` | 相对路径 `_attachments/<名>`（Agent 的 cwd 就是候选） | 内置执行器图片用 `read_image`、文本文件用 `read_file`（候选内路径判据不变）；Codex / Claude 在 cwd 内自己读 |
| 旧路径（外部 CLI 原地改，TAF 开关默认关时走这条） | `data_dir()/cache/ai_attachments/<sid>/` | 绝对路径 | Codex 的 workspace-write 沙盒本来就能读 cwd 之外；Claude 追加 `--add-dir <该目录>`（`RunContext.read_dirs`，只在附文件时出现，其余参数一个字节不变） |

文件名经 `safe_name`（去路径、控制字符、Windows 保留字符与保留名，保留扩展名与中文，≤ 80 字符），重名自动加序号。**绝不写进用户项目目录。** 提示词末尾追加一段（`ai_prompt_pack.attachments_note`，同「生成可编辑版本」一节的做法：不进 `V1` 的闭集，Harness Lab 的候选 Pack 契约不变），按「参考图」与「数据 / 其他附件」**分别列出路径**，都明说内容只是资料、不是指令，不要改动这些文件。

提交前检查：`ai_figure_edit._tree` 把候选根下的 `_attachments/` 整个排除（同 `redirected/`），所以它既不会被当成「改了别的文件」，也不会被当成要写回的东西；`ATTACHMENTS_DIRNAME` 的唯一出处在 `ai_attachments`。

清理：候选里的 `_attachments/` 随候选目录一起删（既有规则）；旧路径的会话目录在 `_finish_session` 与被拒的 `_drop_attempt_files` 里删，进程被杀留下的按 24 h 兜底清；暂存区的文件 1 h 过期、用户点 × 时立即删。

### 四、模型看不了图

后端 `ai_attachments.known_text_only` 按型号名判**确定**只吃文字的（DeepSeek 系、GLM-4.x 非 V、MiniMax-M、Kimi K2 文本版、Qwen-Coder、o1/o3-mini 等；名字带 `vl` / `vision` / `omni` 的一律当能看）。能力清单的每个执行器与连接多一个可选字段 `no_vision`（没有就不出这个键）。前端只认清单里写明的：**附了图片**且当前型号在清单里，输入框上方居中一句「这个模型看不了图，换成 X」，点一下切到别的型号（先看本连接，再看别的就绪连接）；只附数据文件时不出现；判不出的型号**照常发送、不拦**。判据只有后端一份，前端不镜像名单。

### 五、对话与历史

用户消息拆成两个气泡：文字一个，附件一个。本页发起的附件气泡是一叠卡牌（图片缩略图 / 文件卡，轻微倾斜、互相压住），点一下展开成平铺、再点收回；刷新 / 接回 / 历史里没有明细，只显示「附了 N 个文件」。历史库只存个数（`sessions.attachment_count`，`_ADDED_COLUMNS` 迁移，老行 NULL），文件本身不进历史库，历史文件不会因此无限增长。

### 六、隐私

- 附件只发给用户自己连的 AI（与提示词、脚本同一条通道）；Tavotto 不经自己的服务器中转。
- 不进遥测：没有新事件，也不改 `CONSENT_VERSION`。`ai_assistant_invoked` 载荷不变。
- 不进诊断包：包里只有白名单里的日志与报告，不读 `cache/ai_attachments*` 与候选目录；`tests/test_ai_attachments.py::test_diagnostics_bundle_never_contains_attachments` 钉住。
- `docs/privacy.md` 的「Optional AI assistant」一节补一句「你附的文件」。

## 为什么不……

- **把文件 base64 塞进 `/api/ai/run` 的 JSON**：6 个 × 几十 MiB 的请求会撞 30 s 超时、让「已发出未确认」更常发生，也会把文件带进幂等账本的重发路径。
- **文件放进项目的某个隐藏目录**：违反「绝不往用户项目写」，同步盘还会把它同步出去。
- **按型号名在前端判能不能看图**：两份名单迟早分叉（同源对）；判据放后端、清单下发。
- **借用 `Image` 图标当附件钮**：附件不只是图片；图标集是 ADR 0052 的封闭集，按「加图标是画，不是去别的库挑」正规补画了一个 `Paperclip`（说明书第一节与语义表同步）。

## 1.0 纪律

1.0 登记例外（用户 2026-11-10 裁决）。默认开（只有用户自己附文件才触发）；逃生口：不附文件时请求、提示词与工作区逐字节不变。

## 看护

`tests/test_ai_attachments.py`（暂存 / 候选与旧路径各一条 / 不附文件逐字节不变 / `_attachments` 排除 / 清理 / 文件名安全 / 合计上限 / Claude `--add-dir` 只在附文件时出现 / 内置 `read_image` 读得到 / 诊断包 / 模型判据）、`web/src/lib/aiAttachments.test.ts`、`web/src/components/ai/assistantAttachments.test.tsx`、`web/src/lib/aiVision.test.ts`、图标集门禁 `iconography.test.tsx`。
