# ADR 0130：原生救援页——sidecar 起不来时，由壳检查修复版本、重新尝试、发送最小启动诊断

日期：2026-10-15 · 状态：**Proposed**（随 PR E 评审）
相关：[0002 Tauri 桌面壳](0002-tauri-desktop-shell.md)、[0121 更新来源编排](0121-app-update-source-order-and-fallback.md)、
[0118 发送问题反馈](0118-diagnostics-send-report.md)、[0129 异常退出标记](0129-abnormal-exit-marker.md)、[0105 远程实例窗口](0105-desktop-remote-instance-window.md)；
规则见 `src-tauri/AGENTS.md`「原生救援」。

## 问题

Python sidecar 起不来（缺文件、被杀软拦、握手超时、端口 / 权限问题）时，主窗口停在 `shell/error.html`。这时 React 没有挂载、
引擎不在，用户手里只有一句报错原文：既不能检查是不是已有修复版本，也不能把「到底死在哪一步」告诉维护者。
而这恰恰是最需要这两件事的时刻。

## 决策

### 一、范围：只有「从未进入编辑态」的启动失败

救援动作只在 `RescueState.entered == false` 时可用。`boot_engine` 在把主窗口导航到应用页**之前**置位 `entered`，之后**永不复位**。
引擎启动后再失败（例如重启引擎失败）落到同一张 error.html 时，用户可能有未保存的工作、而本 PR 没有保护它的手段
（多窗口 / 远程窗口 / React 失联时的未保存工作保护是后续项，见下），所以此时页面只说明原因、不提供任何会结束进程或装包的动作
（`editing_state_unknown`）。**不靠超时推断「干净」。**

### 二、七条命令，三处同步，Rust 端再查调用方

`rescue_status` / `rescue_retry` / `rescue_update_check` / `rescue_update_install` / `rescue_diag_preview` / `rescue_diag_send` / `rescue_diag_save`，
全在 `src/rescue.rs`；`build.rs`、`generate_handler!`、独立的 `capabilities/rescue-page.json`（`windows:["main"]`、`local:true`、**无 remote**）同步。
`local:true` 且不含任何 remote URL，所以 `127.0.0.1` 上的引擎页（`main.json`）和远程实例窗口（`remote-*.json`）一条都拿不到。
ACL 之外 Rust 端再查一道（`rescue::decide`）：窗口 label 必须是 `main`，**当前 URL** 必须是壳自带的 `error.html`
（`tauri://localhost` 或 `http(s)://tauri.localhost`，主机名整串相等），且没有进过编辑态。capability 将来被配错 / 放宽，命令仍然拒绝。
`desktop_update_check` / `desktop_update_install` 也补了「只许主窗口」的 Rust 端检查。

### 三、更新走同一个协调器

救援页的检查 / 安装与应用内更新共用 `UpdateState`（同一把 `try_lock`、同一个 `Pending` 候选句柄）、同一个 `updater_builder`、同一份公钥与来源编排（ADR 0121）。
实现抽成 `update_check` / `update_install` 两个函数，旧命令与救援命令都调它们。新增 `run_install_expecting(.., expect)`：
页面把用户**看到并确认**的版本号随安装请求带给壳，句柄里的版本对不上就回 `candidate_changed`（不下载、不清句柄、可重新确认）。
网络 / 清单失败是 `Err`，页面显示「检查失败，可重试」，只有 `Ok(None)` 才说已是最新；验签失败由插件拒绝（`apply` 之前不落盘）。

### 四、重新尝试 = 重启整个应用

不做同进程重试：收 sidecar（`shutdown_sidecar`，幂等）→ 写 `clean`（`restart()` 不走 `RunEvent::Exit`）→ `restart()`。
新进程从零开始，不可能有双 sidecar、孤儿 worker、旧 nonce、旧端口页面；重复点击由 `retrying` 旗标幂等。
`Sidecar::start` 顺带修了一处泄漏：stdin 句柄拿不到时没有 kill 已 spawn 的子进程。

### 五、shell 最小报告（`src/shell_report.rs`）

* **内容**：`manifest.json`（`source=shell`、`partial=true`、`schema_version` 与引擎 `BUNDLE_SCHEMA_VERSION` 同值）+ `report.json`
  （版本 / build 档、平台 / 架构 / `distribution=desktop`、失败阶段、稳定错误码、退出码）。阶段与错误码是闭集（`rescue::STAGES` / `CODES`），
  不在闭集里的值写 `unknown`——**自由文本没有任何路径进包**。error.html 的 `msg` / `log` 只显示、不进任何 invoke 参数；
  不读项目目录、不依赖 Python；stderr、日志路径、nonce、token、环境变量、堆栈都不在。
* **协议**：与引擎 `diagsend.py` 同一套外层协议——`init`（`client_request_id` = 每次新生成的 UUIDv4）→ multipart 上传（字段按服务端顺序、文件字段最后）→ `complete`
  （丢响应 / `in_progress` / `upload_missing` 有界重试）→ 回执 `TVD-…`；失败码归并同一张表；上传地址只认 `https://<桶>-<appid>.cos.<区域>.myqcloud.com/`；
  不跟随任何重定向；响应里的 sha256 / size 与本机不一致是失败不是回执。
* **跨语言契约样例**：`tests/golden/diag_shell_report.json` 一份，Rust 测试与 `tests/test_diag_shell_contract.py` 各读一遍：端点、schema 版本、类别、开关环境变量名、
  失败码词汇与可重试性、`classify` 向量、上传地址向量、两份文档的内容、init 请求体的键集合，以及 **Rust 写出的整包字节**
  （Python 用标准库 `zipfile` 与 `diagsend.zip_problem` 读它）。两侧不各抄一份 schema。
* **目的地不可改写**：端点是编译期常量 `PRODUCTION_ENDPOINT`；环境变量只有 `TAVOTTO_NO_DIAG_UPLOAD=1` / `TAVOTTO_DIAG_UPLOAD=0` 两个，**只能关**，没有任何一个能改地址。
  回环形态 `Endpoint::loopback` 只在 `#[cfg(test)]` 里存在。
* **授权**：预览（`rescue_diag_preview`）与保存（`rescue_diag_save`）只在本机备包、不联网，且用的是**同一份字节**与同一个幂等键；
  只有 `rescue_diag_send` 联网，而它只由用户在页面上单独点「发送」触发（结构测试：`shell_report::send` 在 `rescue.rs` 里只有一个调用点）。
  这里没有统计开关可读，统计关闭不影响发送。同一时刻只有一次发送（双击不发两份）；失败保留同一份包与同一个幂等键，
  服务端会话已关 / 过期（`expired` / `session_closed` / `not_found`）才丢弃换新键。
* **依赖**：只用已在依赖树里的 `reqwest`（updater 插件带的 rustls）、`sha2`（Cargo.toml 显式声明，树内已有）；ZIP 用「只存不压」的手写写手（几百字节，不引入 zip crate）。

### 六、页面（`shell/error.html`）

默认一句话 + 一个主按钮「重新启动」；检查修复版本、发送启动诊断、保存、技术详情（报错原文与日志路径）全部折叠。没配置的能力如实说明
（无更新通道不放检查按钮；发送被关闭时发送键不可用、保存仍可用）。中英文文案内联，沿用 `?lang=`；行为由 `web/src/lib/rescuePage.test.ts` 在 jsdom 里对假 `invoke` 跑页面脚本验证。

## 边界（发行说明必须写明）

这只能救援**已经包含此能力、且原生壳还能运行**的客户端：

* 已发布的旧客户端没有这张页面，仍需外部安装器（官网下载新安装包）；
* 壳自身无法运行（缺运行库、被系统阻止启动、签名 / 公证失败）时，这里的一切都不会出现；
* 更新通道不可达（国内镜像与 GitHub 同时不通）时，救援页只能如实显示「检查失败」并提供保存诊断。

## 本 PR 不做（后续项）

* **原生菜单走原生对话框**（菜单「检查更新」在主 WebView 失联时直接用原生对话框展示候选 / 错误 / 确认）；本 PR 的菜单行为不变。
* **安装前保护未保存工作**：多窗口 / 远程窗口 / React 失联时的全应用未保存工作协调、「放弃本次未保存修改并安装」的原生确认。正常入口的直接安装路径也尚未迁入统一的安装前守卫
  （本 PR 只统一了协调器与候选身份）。
* 取消进行中的发送（`/v1/reports/cancel`）：页面没有「取消」按钮，失败后服务端会话 15 分钟自然过期。
* 真窗口验证：Windows 半边只在 CI 编译；macOS / Windows 真窗口的重启、保存对话框、真实接收服务联调均未验。

## 接收服务

契约不变（`v1.0.0`，`InitBody` 拒绝未知字段，所以壳**不新增**任何 init 字段）。来源标记 `source=shell` / `partial=true` 写在包内的 `manifest.json`，
服务端从 `manifest.json` 只读 `schema_version` / `tavotto_version`，多出的键被忽略（已用 infra 检出的 `zipcheck.inspect_bundle` 实测接受）。
若希望管理端列表里把 shell 报告单独分诊，需要 infra 另行读取 `manifest.partial`；这不是接收的前置条件。
