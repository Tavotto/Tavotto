# packaging/ — 打包、内置渲染 runtime 与签名规则（速查表）

仓库级路由与不变量在根 `AGENTS.md`；桌面壳与安装器在 `src-tauri/AGENTS.md`，引擎侧在
`src/tavotto/AGENTS.md`。本层规则的**全文按主题**在 `docs/rules/packaging/`：先在下表按
改动路径找到主题，读那一份细则（各 2–8 KB）与它点名的 ADR，再动手。看护用例在细则头部与正文里。
下表是生成的：改细则头部的 `agents-route` 块，再 `python scripts/dev/gen_agents_sheets.py --write`，
别手改；这里不放第二份全文。

## 本层不可破坏

- **版本锁是唯一输入**：内置 runtime 只认 `packaging/runtime-lock.json`，三个目标的科学栈闭包逐字相同，不手写闭包。
- **路径判断只有一处**：`engine/runtime.py` 全程 os.path、不用 pathlib；`TAVOTTO_RUNTIME_DIR` 覆盖是排他的。
- **不往安装目录写**：`-B` 是硬保证，macOS 往签过名的 `.app` 写一个 `__pycache__` 就破坏签名；`child_env()` 摘掉 `PYTHON*`。
- **缺失 / 损坏报专用 code**（`bundled_runtime_missing` / `_invalid`），提示重装；绝不按 ModuleNotFoundError 自动 pip install。
- **macOS 签名与验收只走 `scripts/codesign_macos.py`**；构建链三道闸、冒烟真启动真渲染一道都不许漏、不许「借一个解释器」。
- **冻结产物的内容要显式声明**：按名字 / 延后装载的模块进 hiddenimports，包内数据与原生库进 `tavotto.spec`；字体不进 git、构建机先 `fetch_fonts.py`。
- **wheel / sdist 卫生**：不含 `src-tauri/`、`workerd/`、`codex-plugin/`、`runtime/`（pyproject exclude 是权威）；前端产物靠 artifacts 收回。

<!-- BEGIN GENERATED: agents-routes -->
| 改到 | 主题（细则在 `docs/rules/packaging/`） | 必守要点 |
| --- | --- | --- |
| `pyproject.toml`、`run.sh`、`scripts/build_frontend.py`、`[tool.hatch.build]` 的 artifacts / exclude、extras | 打包与启动（src layout）、wheel / sdist 卫生 → `src-layout-and-wheel.md` | `src/tavotto/web/` 靠 artifacts 收回否则 wheel 无界面；wheel/sdist 不含 `src-tauri/`、`workerd/`、`codex-plugin/`、`runtime/` 等（pyproject exclude 为准）；入口是纯标准库轻量入口，不再有 `python app.py` |
| `packaging/runtime-lock.json`、`engine/runtime.py`、`pool._prioritized_candidates()`、`child_env()` / `child_args()`、`bundled_runtime_*` code | 内置渲染 runtime：版本锁、路径判断、解释器优先级、不往安装目录写 → `bundled-runtime-contract.md` | 锁是唯一输入且三个目标闭包逐字相同；`engine/runtime.py` 全程 os.path；`TAVOTTO_RUNTIME_DIR` 排他；`-B` + 摘掉 `PYTHON*`，macOS 往 .app 写 `__pycache__` 会破坏签名；缺失报专用 code、绝不自动 pip install |
| `scripts/build_worker_runtime.py`、`scripts/codesign_macos.py`、`packaging/tavotto.spec`、`scripts/smoke_app.py`、`tavotto-cli` | 内置 runtime 的构建链、macOS 签名、CLI 双入口、冒烟 → `bundled-runtime-build-and-signing.md` | 构建三道闸（构建机自检 / spec 同尺 / 打包后冒烟）漏一道就发出不能渲染的包；签名验收只走 `codesign_macos.py`（`--deep` 签不到也验不出）；GUI exe 不当 CLI 调；冒烟不许「借一个解释器」 |
| `src/tavotto/resources/private_python_lock.json`、`scripts/stage_private_python.py`、`build/private-python-bundle/`、`TAVOTTO_REQUIRE_PRIVATE_PYTHON` | 私有 Python 的锁（ADR 0063）与随桌面安装包的归档（ADR 0111） → `private-python-lock-and-bundle.md` | 两个 macOS 目标的 CPython 块与 `runtime-lock.json` 逐字相同、升级一起改；它不是渲染 runtime；归档不是解释器，运行时 sha256 等于锁才用；wheel/sdist 绝不带 |
| `packaging/tavotto.spec`、`packaging/playground-runtime.json`、`scripts/frozen_ssl_gate.py`、`engine/tlstrust.py`、`pdfbackend/__init__.py` | 冻结产物：按名字 / 延后装载的模块、OpenSSL DLL 同源、包内数据文件 → `pyinstaller-hiddenimports-and-datas.md` | 按名字或延后 import 的模块必须显式进 hiddenimports（清单从契约层取、不手写）；Windows OpenSSL DLL 与 `_ssl.pyd` 同源；新增包内数据目录要在 spec 的 datas 加一行；playground 锁钉死 Pyodide |
| `src/tavotto/resources/fonts/`、`scripts/fetch_fonts.py`、`fonts_allowlist.json`、`packaging/entry.py`、`scripts/ci/retirement_scan.py` | 批准字体（ADR 0060）与 RenderCore 原生闭包（ADR 0072） → `fonts-and-rendercore-closure.md` | 字体不进 git，构建机先跑 `fetch_fonts.py`、缺字体拒绝打包；PDFium / qpdf 原生库显式收进冻结产物；`entry.py` 最先分派 `--render-child`；产物用 `retirement_scan.py --dist` 扫 |
<!-- END GENERATED: agents-routes -->

## 验证

- 内置 runtime：`tests/test_bundled_runtime.py`（全部平台无关）+ `tests/test_runtime_build.py`；私有 Python：`tests/test_private_python_bundle.py`。
- 改了 `tavotto.spec` / 打包链：另跑 `tests/test_frozen_ssl_gate.py`、`tests/test_merge_queue_workflows.py`；其余看护用例见细则。
- 完整验证链见 `.github/AGENTS.md`。
