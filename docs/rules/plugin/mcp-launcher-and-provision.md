# MCP 启动器、运行时解析与自管环境

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「首次使用契约 / MCP server 与内嵌画布」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。

## 启动器对（原「首次使用契约」一节的「双语启动器」）

- **`.mcp.json` 的 `command` 是插件自带的 `./mcp/launch`**（#172 → #266：2026-09-24 先做成
  sh / cmd 同一文件的双语启动器 `./mcp/launch.cmd`；2026-09-29 真 Windows 实测它零工具，拆成
  一对文件）。Codex 的 `.mcp.json` 没有按平台分支的字段、没有候选链，`command` 也**不过 shell**
  （实测：`command` 与 `args` 分开传，相对路径按 `cwd` 解析），一个裸名字盖不住 POSIX 与
  Windows（`python3` 在 Windows 上常是商店别名：命令存在、9009、零输出，连降级 server
  都起不来）。所以 command 指向一对启动器：
  - **POSIX**：Codex 直接执行 `mcp/launch`（sh），就是 `exec python3 "$@"`（与 #266 之前逐字同义）。
  - **Windows**：Codex 的 program resolver 给无扩展名的 `./mcp/launch` 按 PATHEXT 补扩展名，
    **先于**无扩展名的原名，落到同目录的 `mcp/launch.cmd`（真 Windows 11 + Codex Desktop 26.924 /
    codex-cli 0.158 的 debug 日志：`Resolved "./mcp/launch" to "…\mcp\launch.cmd"`，彼时两份
    同在）。它按 显式 `TAVOTTO_MCP_PYTHON` → 插件自管环境（**仅当它在引擎区间内**，区间与
    server.py 的 `PYTHON_MIN`/`PYTHON_MAX_EXCLUSIVE` 同源；区间外的自管 venv 要被 `--provision`
    重建，Windows 删不掉正在跑的 python.exe，所以它只垫底）→ `py -3` → PATH 上
    `python`/`python3` → `%LOCALAPPDATA%\Programs\Python` → 区间外的自管环境 的顺序**真跑**
    一句判版本的探测（≥ 3.8；Python 2 也能 `import sys`，却解析不了 server.py），第一个过得了
    的接过全部参数；引擎定位仍只归 `server.py` 的 resolver。
- **为什么不能是同一个文件**（#548 的形态，已撤）：同文件双语要求第一行是 shebang，cmd 必然把
  它连同提示符**回显到 stdout**，排在第一个 JSON-RPC 帧前面。#548 按 rmcp 源码推断「3.2+ 跳过
  非 JSON 行」而放行了这一行；真机上 rmcp 记下 `Ignoring unparsable incoming message` 后握手以
  `connection closed: initialize response` 失败——插件启用、技能在、**零工具**，正是 #266 的
  原样（引擎装好了也一样）。stdout 在第一个协议帧之前**一个字节都不许有**，两个平台一样。
- **形态约束**（`test_the_launcher_pair_keeps_its_platform_contract` 看护）：`launch` 第一行
  shebang（Rust 起不认无 shebang 的脚本，实测 Exec format error）、git 模式 100755（Codex 缓存
  副本保留执行位，0.156.1 实测）、LF（`.gitattributes` 钉 `eol=lf`：CRLF 下 shebang 与
  `python3\r` 失效）；`launch.cmd` **第一行是 `@echo off`**、纯 ASCII、不用 goto / call :label、
  钉 LF（LF 的批处理在真机上照常跑）；**每条探测都经 `call`**（候选可能本身是批处理——pyenv-win
  的 shim 是 python.bat——不经 call 跑批处理不会返回，启动器会停在第一条探测）。
  `test_codex_style_spawn_…` 按 Codex 的解析法起它、要求 stdout 里**只有** JSON-RPC。
- **stderr 一律 UTF-8**：Codex 按 UTF-8 读 server 的 stderr，Windows 管道默认 ANSI 代码页（中文
  系统 GBK），第一行中文诊断就让它记下 `stream did not contain valid UTF-8` 并停读——降级那行
  人话被整行丢掉。`server.py` 的 `main()` 一进来就把 stderr 钉成 UTF-8（`_utf8_stderr`，
  `test_diagnostics_reach_the_host_as_utf8_…` 看护）；`launch.cmd` 自己的那行报错是纯 ASCII。
- `args` 仍是 `["./mcp/server.py"]`：`tavotto codex install` 的 interpreter 步照旧按执行判，
  按 Codex 的解析法解析相对 command（`codexinstall.plugin_relative_command`：按**插件根**、
  Windows 上按 PATHEXT；`launcher_starts` 要求 stdout 里除体检 JSON 外一行都没有——只看「最后
  一行是 JSON」会把 0.17.0 那种回显 shebang 的启动器判成起得来），起不来才把**已装副本**的
  command 钉成解释器绝对路径，**`.mcp.json`
  与 `openai.yaml` 两侧一起换**（stdio 依赖按 command 匹配）。发行件里只许裸名字或这个 `./`
  相对、真在插件里的启动器（`pluginmanifest._is_bundled_launcher`：`mcp/launch` 须 100755，且
  Windows 半边 `mcp/launch.cmd` 同在），机器相关的绝对路径只属于已装副本。插件升级会把钉过的
  绝对路径换回启动器——它自己找 Python，多数机器上不用再做什么。
- **真机证据**（2026-09-29，Windows 11 build 26200 + Codex Desktop 26.924.2738 自带的
  codex-cli 0.158.0-alpha.2.1，临时 `CODEX_HOME` + 本机假 Responses 端点，脚本与判据在
  `scripts/acceptance/codex-windows-launcher/`）：发行的 0.17.0（同文件双语）按 README 从 GitHub
  git 市场装上、引擎也在 PATH 上——零工具；本启动器对——11 个工具全在；同一个 git 市场从旧提交
  `marketplace upgrade` 到新提交——零工具变 11 个；没有引擎——只有 `tavotto_health`（降级
  server 说人话）；机器上一个 Python 都没有——零工具，Codex 日志里有启动器那行英文说明（没有
  Python 就没有能说话的 server，只剩技能那条路）。

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
- **降级诊断窄的先判、宽的兜底（#285、#721）**：`found["cmd"]` 有东西时依次判
  `engine_too_old`（清单有下限、版本低于它）→ `engine_incompatible`（CLI 背后**有**装着 tavotto
  的解释器，桥却 import 不全，而说不出是不是太旧：插件没带 `plugin-build.json`，或版本够了却
  装残了）→ `desktop_only`（只剩 CLI 背后没有解释器的 frozen `tavotto-cli`）。版本先问 CLI 背后
  那个解释器的 `importlib.metadata`（`engine_behind_cli`，只读分发元数据、不 import tavotto），
  问不出再问 `tavotto doctor --json`；「不知道」各是独立一档，不许并进相邻取值。这两格的恢复
  是**升级引擎**（`upgrade_commands`：`pipx upgrade tavotto` / `pipx install --force
  "tavotto[worker]==<版本>"`），不给 `--provision`。pip 的 index-url 指向镜像时
  （`pip_index`：`PIP_INDEX_URL` + pip 配置文件，只读，地址里的口令抹掉；配置文件的位置（按平台）、
  编码（本地首选编码）与覆盖顺序照 pip 自己的 `Configuration`；Windows 商店版 Python
  的配置被虚拟化在 `%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.*\LocalCache\Roaming\pip\pip.ini`，
  那几份任一指向非 PyPI 即判镜像；启动器解释器 ≠ 装引擎的解释器时，`effective_pip_index` 再在
  引擎背后那个解释器里跑一遍 `pip_index()`，任一侧是镜像就按镜像报）文案说镜像可能滞后、
  每条命令带 `--index-url https://pypi.org/simple`、不给裸的 `pipx upgrade`。`--health` 带
  `engine_version` / `min_tavotto_version` / `pip_index`；`tavotto codex doctor` 原样转述插件
  这份话术（`codexinstall._health_step`），不写第二份。看护 `tests/test_mcp_diagnose.py`。
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
