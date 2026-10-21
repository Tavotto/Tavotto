# ADR 0143：生成可编辑版本——位图库出的图，由改图助手另写一份 Matplotlib 新脚本

日期：2026-10-21 · 状态：**Accepted**（1.0 登记例外，见 `docs/1.0-release-readiness.md` §1.8）
相关：[0138 TAF](0138-tavotto-ai-foundation.md)、[0139 来源驱动采用（对照确认）](0139-source-backed-figure-adoption.md)（含 2026-10-21
修订：`no_figure_hint.library` / `path`）、[0132 助手提交幂等](0132-ai-submit-idempotency-and-draft-cache.md)。
起因：脚本用 Pillow / OpenCV / plotly / ROOT 画布直接画图时，Tavotto 捕获不到 Matplotlib 图，图只能当位图摆放（原因卡 D，#1271）。

## 决定

### 一、任务：`convert_to_editable`，登记在 TAF 任务适配器注册表

不另起 AI 通道：沿用 `ai_bridge.run` 的会话 / 历史 / 取消 / 幂等 / SSE，`run(..., convert={"library", "original_rel"})` 走
`ai_convert.ADAPTER`（`ai_tasks.ADAPTERS["convert_to_editable"]`）。契约：`allowed_writes` 恰好一个**候选目录里的**新文件名
`<原名>_matplotlib.py`，`new_figures=true`，预算 Agent ≤ 1 + 修正 ≤ 1、校验运行 ≤ 3。提示词在 `ai_prompt_pack` 单独一节
（不进 figure_edit 的 `V1` 模板闭集，Harness Lab 的候选 Pack 契约不变）：保留原脚本的数据读取与计算（ROOT / RooFit 拟合仍用 ROOT，
只把绘图改成 Matplotlib；Pillow 热图用 imshow / pcolormesh 复现色标、格子标注、标题与脚注），图幅 / 字号对齐原图，不运行脚本。

### 二、权限边界

1. **绝不修改用户的任何脚本**。原脚本字节在整条流程里不变（`tests/test_ai_convert.py::test_original_script_bytes_never_change_through_the_whole_flow`）；
   转换会话的回滚入口被拒（`ai_revert_not_applicable`，回滚会重写用户脚本）。
2. **Agent 只写候选工作区里的一个新文件**。候选 `data_dir()/cache/ai_convert/<sid>/` 只**复制**原脚本（与 ≤ 8 MB 的原输出图当参考），
   不建链接；提交前检查：除这一个新文件外候选树逐字节不变（含原脚本副本）、原项目指纹不变、项目里原脚本仍是起跑那一版。
   CLI 把写入挡在 cwd 之外是 CLI 的行为，指纹只能发现、不能阻止（同 `figure_edit`，R-03）。
3. **提交不碰用户文件**：任务的 `commit` 只把通过检查的候选留存待采用（候选目录 + `meta.json`，24 h 过期清理）。用户点「采用」才由后端
   `ai_convert.adopt` 把候选**新建**进项目——新原语 `atomicio.create_new_bytes`（同目录临时文件写满 fsync → `os.link` 到目标，已存在由内核
   原子拒绝；不支持硬链接的文件系统退到 `O_CREAT|O_EXCL`）经 `scriptlock.create_script`（必须持目标的 `script_guard`）。存在即失败、
   不覆盖（含悬空符号链接），重名自动加序号 `_matplotlib_2.py` … 至 99。采用前再核：原脚本 sha256 仍是起跑基线（`convert_source_changed`）、
   候选字节仍是留存时的摘要（`convert_candidate_changed`）、目标路径经 `contained_path` 钉在项目内。
4. **校验**：用户同一解释器 / 同一路由下真的跑一遍候选（`run_validation` → app 注入的 `runner` 走 `engine_probe.probe`，带原脚本的默认运行配置，
   返回前 `invalidate(force=True)` 退役 worker）。必须捕获到 Matplotlib 图，否则判失败并给一句原因（`no_matplotlib_figure` / `run_failed`，
   一轮有界修正）。候选以紧挨原脚本的**临时文件** `tavotto_convert_probe_<sid>.py`（后端 O_EXCL 建、跑完即删；worker 按模块名 import，
   不能用点开头的隐藏名）存在几秒，使相对路径数据读取与 cwd 判据与原脚本一致——Agent 没有写它的路径。存在期里文件监视器可能看见它，
   删除后登记不变。**校验运行的写入是隔离的（Codex #1277 P1）**：运行带 ADR 0141 的写入根绊线（`probe(write_roots=)` →
   `pool.build` / `invalidate` / `force_cancel` 同一份根，池键多一段），根只有候选目录——候选把相对路径输出（`out.png` / `data.csv`）写进
   项目目录时在写发生的那一刻以 `write_outside_roots` 失败，用户文件不被打开写；跑完的指纹复核只是兜底。绊线的盲区同 ADR 0141
   （C 层写、子进程、位于系统临时目录 / 用户缓存目录之下的项目）。
5. 对照数据（`comparison`）：原图像素宽高 / 字节 / 摘要（PNG / JPEG / GIF 头部）、新版图幅 mm 与图个数、宽高比相对差、新版 SVG 预览
   （沙盒 CSP + nosniff 的 `GET /api/ai/convert/<sid>/preview.svg`，只给 `<img>`）。**不做像素差**（`pixel_diff: null`）：
   原件是位图而新版是矢量重画，逐像素差没有可辩护的含义，交给用户并排看。

### 三、默认开与逃生口

用户 2026-10-21 裁决：全部默认开，不是 beta-only，不挂默认关的实验开关（与 TAF 的 `ai.taf.enabled` 不同，不看它）。保留一个总开关作为逃生口：
`ai.convert.enabled=false`（只认显式 `false`）或环境变量 `TAVOTTO_NO_CONVERT_TO_EDITABLE=1`，关掉后 `POST /api/ai/convert` 回 404
`convert_disabled`，界面显示一句话（不再起任何会话）；原因卡与次按钮不受影响。

### 四、端点

`POST /api/ai/convert`（`{script, library, path?, agent?, client_request_id?}`；`library` 取 `rasterhint.LIBRARIES`；`script` / `path` 经
`contained_path`）、`GET /api/ai/convert/<sid>`、`GET /api/ai/convert/<sid>/preview.svg`、`POST /api/ai/convert/<sid>/adopt`、
`POST /api/ai/convert/<sid>/discard`。全部过会话认证，候选按**项目**隔离（别的项目的 sid 一律 `convert_not_found`）。

### 五、遥测

本 ADR **不加新遥测事件**（新事件须攒齐一次升 `CONSENT_VERSION`）。后续候选：`convert_started` / `convert_outcome`（闭集：library、
outcome、corrections 个数、是否采用）——不带脚本、路径或图内文字。

## 不做

- 不自动采用；不替用户改任何已有脚本；不在原脚本上叠改。
- 不把新版标成“与原图一致”：它是重画，用户看过对照才采用。
- 不做像素差度量；不为转换放宽 safe 档或新增 native 路径。

## 回滚

关总开关即回到 D 的纯提示卡；采用产生的只是项目里一个新文件，删掉即恢复。
