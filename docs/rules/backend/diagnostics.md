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
