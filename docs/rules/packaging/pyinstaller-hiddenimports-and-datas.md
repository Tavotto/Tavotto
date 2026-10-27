# PyInstaller：hiddenimports、OpenSSL DLL 与 datas

> 原文出自 `packaging/AGENTS.md`「内置渲染 runtime（Windows 2026-08-17；macOS 2026-08-18）」（2026-10-24 迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`packaging/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> `packaging/AGENTS.md`「主题」压缩前原文（2026-10-24，与正文同等有效）：冻结产物：按名字 / 延后装载的模块、OpenSSL DLL 同源、包内数据文件 → `pyinstaller-hiddenimports-and-datas.md`
> `packaging/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效）：按名字或延后 import 的模块必须显式进 hiddenimports（清单从契约层取、不手写）；Windows OpenSSL DLL 与 `_ssl.pyd` 同源；新增包内数据目录要在 spec 的 datas 加一行；playground 锁钉死 Pyodide

<!-- agents-route
sheet: packaging/AGENTS.md
order: 50
改到: `packaging/tavotto.spec`、`packaging/playground-runtime.json`、`scripts/frozen_ssl_gate.py`、`engine/tlstrust.py`、`pdfbackend/__init__.py`
主题: 冻结产物：延后装载的模块、包内数据文件 → `pyinstaller-hiddenimports-and-datas.md`
必守要点: 按名字或延后 import 的模块必须显式进 hiddenimports；Windows OpenSSL DLL 与 `_ssl.pyd` 同源；新增包内数据目录要加 datas
-->

- **浏览器 playground 的运行时锁**：`packaging/playground-runtime.json`
  钉死 Pyodide 版本与包白名单（前端 JSON import + 构建脚本共读），
  细节见 `docs/rules/frontend/browser-playground.md`。
- **按名字装载的模块要显式进 PyInstaller 的 hiddenimports**（2026-09-22，U08 / ADR 0067；U10 / ADR 0072）：
  `pdfbackend/__init__.py` 用 `importlib` 按 `TAVOTTO_RENDER_BACKEND` 装载实现，静态分析看不见这条
  边——冻结产物里没有那个模块，`probe_asset` 一调就 ModuleNotFoundError、「一个面板都没扫到」，
  而源码模式一切正常（#476 三条冒烟腿）。`tavotto.spec` 从契约层 `_IMPL_MODULES` 取清单铺进
  hiddenimports，**不手写模块名**（U10 起闭集只剩 `rendercore/facade.py`，退役模块的 hidden import
  随模块一起消失）；`tests/test_runtime_build.py::test_spec_ships_every_backend_the_contract_layer_can_select` 看护。
- **延后 import 的 truststore 要显式进 PyInstaller 的 hiddenimports**（2026-09-28）：出站 HTTPS 的平台原生证书
  校验在 `engine/tlstrust.py` 里**延后** `import truststore`（Flask 进程启动不为它付代价），静态分析看不见这条边。
  漏收的表现是冻结产物里退回 OpenSSL 默认信任库（app.log 一条 WARNING）——干净 Windows 缺 ISRG Root X1 时私有
  Python 下载以 `private_python_tls` 失败，而源码树 / wheel 一切正常。`tavotto.spec` 用 `collect_submodules("truststore")`
  把整包子模块铺进 hiddenimports（`_windows` / `_macos` / `_openssl` 按平台条件 import），打包环境里没装它就拒绝打包；
  wheel 那边它是 `dependencies` 的一员（`requirements.txt` 钉死镜像）。看护 `tests/test_private_python_tls.py::
  TestStructure`。
- **Windows 冻结产物里 OpenSSL 的 DLL 必须与 `_ssl.pyd` 同源**（2026-10-15，#1051）：内置 runtime（两个平台）于 Analysis **之后**以 `Tree` 加入（不进 `datas`，否则里面的 PE 被重新归类为二进制、它的 `libcrypto-3.dll` / `libssl-3.dll` 作为依赖抢先占住顶层，打包 Python 一升 OpenSSL 就 `DLL load failed while importing _ssl`）；spec 在 Analysis 后跑 `scripts/frozen_ssl_gate.py`，构建后 `tavotto-cli --ssl-selftest`（`build_desktop.py` 与 `smoke_app.py --exe` 都跑）。macOS 同一机制会把扩展模块改写成指回 `_internal/` 的 `@rpath`、openblas 等复制到顶层（v0.18.0 多 ≈ 48 MB）；打包后 `build_worker_runtime.diff_collected_runtime()` 逐字节核对 runtime（`build_desktop.py` 与 CI 两条冒烟腿）。看护 `tests/test_frozen_ssl_gate.py`、`tests/test_runtime_build.py`。
- **`tavotto-cli mcp` 的服务端要整包进 hiddenimports**（2026-10-27，「接入 AI 编辑器」）：`engine/mcpserve.py` 在运行时
  才 `import tavotto_mcp`（插件那一份服务端，住在 `codex-plugin/mcp/`，不在 `src/` 下、wheel 也不带），静态分析看不见。
  `tavotto.spec` 把 `codex-plugin/mcp` 加进 `pathex`、`collect_submodules("tavotto_mcp")` 铺进 hiddenimports，收不到就
  拒绝打包；`scripts/smoke_app.py --exe` 用同目录的 `tavotto-cli` 真跑一次 `mcp` 握手 + `tavotto_health`。漏收的表现是
  宿主里 Tavotto 没有工具、stderr 一行 `mcp_server_not_bundled`。看护 `tests/test_desktop_mcp_serve.py`。
- **包内数据文件要显式进 PyInstaller 的 datas**（2026-09-02，ADR 0039）：
  `Analysis` 只把 .py 编进 PYZ，`tavotto/profiles/publication.json` 与
  `tavotto/resources/tutorial_project/` 这类数据在冻结产物里**本来是没有的**
  （源码树 / wheel 里都在，所以 lab_acceptance 那条结构检查看不出来）。
  `tavotto.spec` 的 datas 现在列了 `profiles/` 与 `resources/` 两条；新增包内数据
  目录时在那里加一行，`importlib.resources.files("tavotto")` 在冻结产物里落在
  `_MEIPASS/tavotto`，datas 的目的地就写 `tavotto/<目录>`。wheel / sdist 那边
  `packages = ["src/tavotto"]` 自然收进，前提是文件不被 `.gitignore` 挡
  （`tests/test_tutorial.py` 读产物成员对账，不在源码树上断言）。桌面冒烟①带
  `--tutorial`：教程两张图在内置 runtime 上真渲染一次再重置。
