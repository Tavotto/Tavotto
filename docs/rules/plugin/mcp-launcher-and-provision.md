# MCP 启动器、运行时解析与自管环境

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「首次使用契约 / MCP server 与内嵌画布」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。

## 双语启动器（原「首次使用契约」一节）

- **`.mcp.json` 的 `command` 是插件自带的 `./mcp/launch.cmd`**（#172 → #266，2026-09-24）。
  Codex 的 `.mcp.json` 没有按平台分支的字段、没有候选链，`command` 也**不过 shell**
  （实测：`command` 与 `args` 分开传，相对路径按 `cwd` 解析），一个裸名字盖不住 POSIX 与
  Windows（`python3` 在 Windows 上常是商店别名：命令存在、9009、零输出，连降级 server
  都起不来）。所以 command 是一份 **sh / cmd 双语**启动器：POSIX 上就是
  `exec python3 "$@"`（与改动前逐字同义），Windows 上按 显式 `TAVOTTO_MCP_PYTHON` →
  插件自管环境（**仅当它在引擎区间内**，区间与 server.py 的 `PYTHON_MIN`/`PYTHON_MAX_EXCLUSIVE`
  同源；区间外的自管 venv 要被 `--provision` 重建，Windows 删不掉正在跑的 python.exe，
  所以它只垫底）→ `py -3` → PATH 上 `python`/`python3` → `%LOCALAPPDATA%\Programs\Python`
  → 区间外的自管环境 的顺序**真跑**一句判版本的探测（≥ 3.8；Python 2 也能 `import sys`，
  却解析不了 server.py），第一个过得了的接过全部参数；引擎定位仍只归
  `server.py` 的 resolver。**形态约束**（`test_the_dual_launcher_keeps_its_platform_contract`
  看护）：第一行 shebang（Rust 起不认无 shebang 的脚本，实测 Exec format error）、git 模式
  100755（Codex 缓存副本保留执行位，0.156.1 实测）、全文 LF（`.gitattributes` 钉 `eol=lf`：
  Windows 检出默认 autocrlf，CRLF 下 shebang 与 heredoc 终止行都失效）、批处理段纯 ASCII、不用
  goto / call :label；**每条探测都经 `call`**（候选可能本身是批处理——pyenv-win 的 shim 是
  python.bat——不经 call 跑批处理不会返回，启动器会停在第一条探测）。cmd 会把第一行回显进 stdout **一次**：rmcp 3.2+ 跳过非 JSON 行，
  2.x 回一条 parse error 后继续，≤1.x 会断连——`test_codex_style_spawn_…` 钉住「最多这一行」。
  `args` 仍是 `["./mcp/server.py"]`：`tavotto codex install` 的 interpreter 步照旧按执行
  判（按**插件根**解析相对 command，不按本进程 cwd），起不来才把**已装副本**的 command
  钉成解释器绝对路径，**`.mcp.json` 与 `openai.yaml` 两侧一起换**（stdio 依赖按 command
  匹配）。发行件里只许裸名字或这种 `./` 相对、真在插件里的启动器
  （`pluginmanifest._is_bundled_launcher`，且须 100755），机器相关的绝对路径只属于已装副本。插件升级会把
  钉过的绝对路径换回启动器——它自己找 Python，多数机器上不用再做什么。
  真 Windows + Codex Desktop 上的一次实跑是 #266 的关闭条件，CI 的 windows 腿只替它跑了
  cmd.exe 那半边（`test_windows_launcher_skips_a_python_that_does_not_run`）。

## 运行时解析器与 `--provision`（原「MCP server 与内嵌画布」一节）

- **启动器 `mcp/server.py` 是运行时解析器（2026-08-20 重做）**：候选链
  当前解释器 → `TAVOTTO_MCP_PYTHON`（显式，失败要指名道姓报
  `engine_unavailable`）→ `TAVOTTO_WORKER_PYTHON`/设置里的 worker.python →
  **插件自管 venv**（`<配置目录>/mcp-runtime/venv`，`--provision` 建、
  钉插件版本、绝不碰用户全局环境）→ 从 CLI 反推 shebang → PATH。**每个候选
  都要真的验证 `import tavotto.engine`**；frozen `tavotto-cli` 永远出不了
  候选。降级 server 的 tools/list **只列 `tavotto_health`**（不把七个不可用
  工具伪装成可用），`serverInfo.version` 固定 "0"，七个工具名的调用回结构化
  错误 + 恢复步骤，不声明任何资源。`--health` 输出一行 JSON 体检（引擎/
  画布/桌面版/每个候选的结论与耗时）。真 server 也有 `tavotto_health` 工具
  （出图前的能力门槛）；widget 缺失时 open/apply 在 structuredContent 里带
  `canvas_ui: {available: false, code: "widget_missing"}` 并在文字里说出口，
  `resources/read` 对缺失产物报「缺失 + 修法」而不是回空 HTML。
  看护 `tests/test_mcp_resolver.py` + `tests/test_mcp_stdio.py`。
- **`--provision` 建 venv 之前先验基础解释器的版本**（2026-09-20）：启动器允许在很老的
  `python3` 上跑（纯标准库），但 venv 继承它的版本——macOS 上 `python3` 常是 Xcode CLT
  的 3.9，而引擎的 `requires-python` 是 `>=3.10,<3.15`，区间外的解释器上 pip 只会说一句
  "No matching distribution found"（3.9 自带的 pip 21 连被 Requires-Python 忽略的版本都
  不列），Codex 把它读成「这一版还没发」。`find_venv_base()` 按 当前解释器 → PATH 上的
  `python3.14…3.10` → Homebrew / python.org / `py` 启动器的常见位置 → 裸 `python3` 的顺序
  **真的跑一遍**每个候选问版本（判据是执行不是文件名），第一个在区间内的当 base；上次
  在区间外建出来的 venv 用 `venv --clear` 重建；一个都没有就以 `no_supported_python`
  失败并逐个说出版本，**不在区间外的解释器上起 pip**；`--python` 显式指定时只认那一个——
  先验它、已有的 venv 也换到它上面（已有环境在区间内不是跳过它的理由，#453 评审 P2）。
  区间常量 `PYTHON_MIN` / `PYTHON_MAX_EXCLUSIVE` 是 `engine/projectenv.py` 的镜像
  （`test_provision_python_range_mirrors_the_engine` 对拍），改 `requires-python` 要一起改。
  **装完插件/引擎必须新开 Codex 会话**——已开的会话不重载工具，
  `codex plugin list` 的 enabled 不代表 server 健康（README 里写明了）。
- **自管环境落后于插件时启动器自己重装**（#487，2026-09-24）：插件升级会换掉插件目录，
  配置目录里的 `mcp-runtime/venv` 却原样留着上一版引擎，import 不过新桥就落到降级。
  这一格单独报 `managed_runtime_stale`（不是 `tavotto_missing`——恢复步骤不许把人支去
  另装 pipx），并且 `main()` 在降级前 **spawn 一个脱离本进程的 `--provision`**（不在
  启动路径上同步跑 pip：`startup_timeout_sec` 只有 30 s）。互斥用 `mcp-runtime/provision.lock`
  上的**内核文件锁**（POSIX `fcntl.flock` / Windows `msvcrt.locking`），**不许**退回「锁文件
  + mtime + 令牌」：那套在纯文件语义下「核对所有权再删 / 续 / 接管」永远不原子，#548 的
  Codex 评审一轮轮挖出新的竞态；内核锁随持有进程退出（含崩溃、被杀）自动释放，没有
  过期锁可言。**改环境的一方拿锁**：`--provision`（后台的与手动 / `tavotto codex install`
  跑的同一条路）动 venv 之前非阻塞地拿，拿不到就不动、报 `provision_in_progress`；
  启动器只探一下锁（拿到即放）省掉明显多余的 spawn，多起一个子进程也只会有一个真跑 pip。
  `TAVOTTO_MCP_NO_AUTO_PROVISION=1` 关掉。本次会话仍是降级、payload 带 `auto_provision`，
  文案说「后台在装、装完新开会话」。**只管「在、却 import 不过」**：能 import 但版本旧的
  自管环境不在这里重装（它此刻正被本会话用着）。看护 `tests/test_mcp_resolver.py` 末节。
