# Tavotto issue 实机验收汇总（macOS）

**环境**: macOS 26.6.2 / Apple M4 Pro（VM）/ 12C16G。系统无 python.org Python（满足 #439「干净机器」前提）。产物：Tavotto-0.18.0.dmg（Developer ID 签名，公证通过，未 stapler）与 0.17.0；源码 @ main（`~/repos/Tavotto`），Python 3.13 + uv + miniconda。

- ✅ = 验收通过 / 已修复可关闭｜❌ = 复现仍存在｜⚠️ = 部分验证
- GUI 证据：`shots/`（17 张）、videos/（2 段录屏）、gui-acceptance-report.md

## 一、桌面产物验收（GUI 实测）

| Issue | 结论 | 证据 |
|---|---|---|
| [#35](https://github.com/Tavotto/Tavotto/issues/35) N-1 升级走查 | ⚠️ 升级链通，**跨版本状态丢失仍存在** | 0.17.0→0.18.0 应用内下载/安装/自动重启成功（A1–A7），导出+写回正常（A11/A12）。但升级后排版「N1验收排版」不在最近列表、端口号变化（56940→24240）、localStorage 随端口重置（A8/A9）——与 #715 同根因 |
| [#715](https://github.com/Tavotto/Tavotto/issues/715) 随机端口丢状态 | ✅ **0.18.0 已修复，可关闭** | 同版本 ⌘Q 重启端口稳定 24240（`desktop-port {"misses":0}`），排版自动恢复含未保存编辑，1200ppi 保留（B1/B2）。注：仅保证同版本内稳定 |
| [#276](https://github.com/Tavotto/Tavotto/issues/276) ⌘Q 绕过关闭闸 | ❌ **复现，且比 issue 描述更重** | dirty 与 conflict 两种状态下，⌘Q **和**红色关闭按钮都静默退出，closeGuard 三路弹窗均未出现（C1/C2，录屏）。靠 autosave 兜底没丢数据。`docs/acceptance/desktop-close-guard.md` 验收流程在 0.18.0 上整体失败 |
| [#82](https://github.com/Tavotto/Tavotto/issues/82) 无障碍收尾 | ✅ 通过 | 系统「提高对比度 + 减少动态效果」下渲染/交互正常；无页面缩放入口（⌘± 是画布缩放）（D1/D2，录屏） |
| [#182](https://github.com/Tavotto/Tavotto/issues/182) 许可证随产物分发 | ⚠️ 大部分满足，顶层 notice 仍缺 | `Contents/Resources/LICENSE` 存在；`_internal` 内有逐包许可；运行目录 `THIRD-PARTY-NOTICES.md` 覆盖捆绑 CPython/科学计算栈。**但没有覆盖 Tauri shell（Rust crates，含 MPL-2.0 义务）与前端闭包的顶层第三方声明**，按 issue 验收口径仍未完全关闭 |
| [#439](https://github.com/Tavotto/Tavotto/issues/439) sidecar 公网 HTTPS/CA | ✅ **已修复（正向验证）** | 冻结 sidecar 的 PyInstaller PYZ 内含 `tlstrust`/`truststore._macos`。实测：本地自签 CA 加入登录钥匙串后，sidecar 经 `PATCH /api/telemetry/settings` 触发的事件 POST 成功完成 TLS 握手送达 `https://localhost:8443`（payload 含 `app_version 0.18.0 / platform macos / arch arm64 / distribution desktop`）；CA 未信任时握手被拒（验证未被绕过）。日志中 `检查更新失败：HTTPError（信任来源 platform）` 亦佐证走 truststore |

## 二、源码级 macOS/POSIX 复现

| Issue | 结论 | 证据 |
|---|---|---|
| [#479](https://github.com/Tavotto/Tavotto/issues/479) 冷渲染无进度信号 | ❌ 复现（代码+计时双重确认） | `app.py` 中 `_engine_worker()`（worker 拉起 + matplotlib 导入）在 `sse_publish("render.started")` **之前**执行，冷启动大段时间无任何 SSE 事件。实测冷渲染总耗时 ~17.5s，render.started 到 ~1.3–2s 才发出；缺 sympy 项目要 ~12s 才报 render.failed + 「项目环境自动接手失败」 |
| [#971](https://github.com/Tavotto/Tavotto/issues/971) POSIX 通配扫描不受预算约束 | ❌ 复现 | `pool.py` 的 `scan_glob()`（POSIX 分支）直接 `_glob(pattern)` 一次性枚举全部命中，预算只对 glob 模式记一次账：3000 个假 `Python.framework/*/Python` 全部枚举完才逐条 charge，glob 本身不可中断 |
| [#962](https://github.com/Tavotto/Tavotto/issues/962) venv 软链导致系统 python 误判托管 | ❌ 复现 | `managedenv.is_managed_python()` 用 `os.path.realpath` 比较：venv `bin/python` 是 brew python 的软链，realpath 相同 → 裸系统 python 被判为托管环境 |
| [#842](https://github.com/Tavotto/Tavotto/issues/842) FIFO `.py` 卡死扫描 | ❌ 复现 | 项目内 `mkfifo blocked.py` 后 `discover.inspect_script()` 经 `path.read_bytes()` 永久阻塞（`timeout 12` 强杀，exit 124）。`scanbudget.read_regular_text()` 的 O_NOFOLLOW+S_ISREG 是正确读法，inspect 没走它 |
| [#640](https://github.com/Tavotto/Tavotto/issues/640) 缺字体 findfont 刷屏 | ❌ 复现 | `font.family="NoSuchFamilyXYZ"` 项目两次渲染后 worker.log 内 **1594 行** findfont 告警 |
| [#649](https://github.com/Tavotto/Tavotto/issues/649) Ctrl-C 窗口期丢下子进程 | ❌ 代码确认（动态复现窗口极窄） | `runcli.py::_run()`：`_spawn_user_python()` 返回后、进入 `_wait_for_child_process()` 前有 `relay.wait_for_child`+`start_pump` 窗口；此间 KeyboardInterrupt 直接逃逸，finally 只 `relay.close()` 不 wait/kill 子进程 → 子进程短暂成孤儿 |
| [#1018](https://github.com/Tavotto/Tavotto/issues/1018) 任意前缀 conda env 信任/激活 | ⚠️ 半验证 | `conda create -p /tmp/fifoproj/.conda-env` 后写入 `~/.conda/environments.txt`，`_conda_prefixes()` 能发现该前缀；但执行是**直接调 `prefix/bin/python`**（`projectenv.py` 明确不走 activate，`runspec.py` 注释声明不支持 `conda run`），不设 `CONDA_PREFIX`——即「能发现、不激活」，符合 #975 后设计但 #1018 诉求（激活能力）未实现 |
| [#985](https://github.com/Tavotto/Tavotto/issues/985) / [#998](https://github.com/Tavotto/Tavotto/issues/998) | ⚠️ 同源结论 | 与 #1018 同一激活面；本机验证覆盖 discovery + 直调路径，激活相关诉求维持打开 |
| [#1011](https://github.com/Tavotto/Tavotto/issues/1011) / [#806](https://github.com/Tavotto/Tavotto/issues/806) CI 首开 30s 超时 | ⚪ 本机未复现 | 属 CI 偶发 flake 追踪，本机多次首开未触发；保留监控价值 |

## 三、未做（本机做不了）

- Windows-only：#266 #961 #652 #801–#804 #603 —— 需 Windows 会话
- 需 Codex Desktop 登录：#959 #939
- [#1006](https://github.com/Tavotto/Tavotto/issues/1006) 性能基线：VM 数字不能当真机基线

## 结论建议

可关：#715（0.18.0 已修）、#439（TLS 正向验证通过）。
高风险待修：#276（关闭闸整体失效，比 issue 更重）、#35 的状态丢失尾巴。
源码级确认的打开项：#479 #971 #962 #842 #640 #649 #1018。
