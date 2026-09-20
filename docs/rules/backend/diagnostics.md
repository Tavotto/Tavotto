# 诊断包

> 原文出自 `src/tavotto/AGENTS.md`「诊断与排障」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- `engine/diagnostics.py` 出**一键诊断包**（`GET /api/diagnostics/bundle`）：
  版本 / 系统与编码 / 安装方式 / 数据目录 / 渲染解释器 + matplotlib /
  AI CLI 探测 / 项目概况 / 最近错误 + app.log + 用户配置。
  **密钥与个人路径必须先脱敏再交出去**（用户会把它贴进 issue 或发到群里）。
  `recent_projects` / `projects` **只留条数**：那是用户所有课题的名字与路径，
  排障一次都用不到（当前项目在 report.json 的 project 段里）。
- **诊断包 schema 2（ADR 0016，改前先读）**：老三件
  （report.json / app.log / config.json）名字与语义一个字节没动，新增
  `frontend-state.json` / `interaction-trace.jsonl` / `manifest.json`。
  `manifest.json` 自报三个 schema 版本——**读包的人不该靠 Tavotto 版本号猜格式**。
  * 前端状态只活在浏览器内存里，所以多了 `POST /api/diagnostics/bundle`
    收前端载荷；**老的 GET 原样保留**（出的包 `contains_frontend_state: false`）。
  * `engine/diagnostics_frontend.py` 是**服务端第二道校验**。理由与
    `/api/telemetry/event` 一致：这个端点接受请求体，白名单是结构性防线。
    两侧判据**刻意不同**——前端管「这种事件允许哪些字段」，后端管「任何字段的
    值只能是什么形状」+ 一张**扁平的字段名 allowlist**。后端**不复制**前端那种
    逐事件的表（迟早分叉）；两处同源对由
    `tests/test_diagnostics_bundle.py` 的两条 `*_match_frontend_*` 看护。
  * 身份字段（`*_hash` / `*_variant` / panel / file / session / version）
    **必须是 `前缀:十六进制` 的 hash**，gid 必须**小写开头**——光靠字符集挡不住
    `SUPER_SECRET_PAPER_TITLE_12345` 那种全大写下划线串。
  * 坏载荷（超限 / 畸形 JSON / 类型不对）**一律退化成不带前端那两个文件的包**，
    并在 manifest 记 `trace_truncated`。用户是来排障的，不该拿到一个 400。
  * **不写磁盘、不自动上传、不进 telemetry**。trace 只在用户点导出那一刻进 zip。
- **报告要答得出「渲染进程死在哪一句」（#435）**：`recent_errors` 把每段 traceback 与
  它收尾的异常行配成一条（帧行不进——读的人要的是那一句，脱敏面也更小）；
  `render.worker_logs` 带**当前项目**最近 `WORKER_LOG_FILES` 份 `worker.log` 尾巴里的
  **证据块**（`evidence_lines` 只认两种结构块：Python traceback = 头 + `File "…",
  line N` 帧行 + 收尾的异常行；faulthandler 崩溃栈 = 头 + `Current thread` / 帧行 +
  `Extension modules`），块外的一切**一律略去、只留计数 `omitted`**——脚本 `print`
  的（哪怕长得像 `RuntimeError: …` 或 `[guard] …`，用户 stdout 也在这份日志里）、
  帧下面那行源码、引擎自己的标记行。**不按行首长相放行、块要完整才算**：traceback 块 =
  头 + ≥1 帧 + 合法收尾（`ExcType: …`），faulthandler 块 = 头 + ≥1 线程行 + ≥1 帧，凑不齐的
  整块按用户输出略去（一个 `print("Fatal Python error: …")` 不是通行证）；链式异常的两句
  连接语要**逐字**相同且夹在两段 traceback 之间。`recent_errors` 里配对的异常行与 ERROR 行
  过同一道路径缩写。**先扫 `WORKER_LOG_SCAN_LINES`（400）行再抽块、再按块截到
  `WORKER_LOG_TAIL_LINES`（`last_blocks_within`：最后那块再长也整块要）**——先按行截
  再抽块会把一段长崩溃栈截成没有头的帧行，状态机一条都不认。README 承诺包里不含
  脚本源码与数据，这条段落不许把它变成空话。三条边界（评审 #443）：**只取目录名哈希等于
  `pool.cache_digest(当前项目)` 的会话**（含 `_replay-…` 重放目录），没打开项目一份
  都不带——别的项目的脚本名与报错不跟着出门；**留下的行里绝对路径缩成
  `…/site-packages/包/模块.py` 或 `…/文件名`**（`shorten_paths`：D 盘、外接盘、
  `\\wsl.localhost\…` 上的路径 `_redact_text` 只认主目录、一个字都不动）；
  `empty` 标出「进程一个字没留下就没了」（硬崩溃的形状）。整段再过同一道
  `_redact_obj`。看护：`tests/test_diagnostics_worker_evidence.py`。
