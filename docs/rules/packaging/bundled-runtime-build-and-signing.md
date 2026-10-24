# 内置渲染 runtime：构建三道闸、macOS 签名与冒烟

> 原文出自 `packaging/AGENTS.md`「内置渲染 runtime（Windows 2026-08-17；macOS 2026-08-18）」（2026-10-24 迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`packaging/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

<!-- agents-route
sheet: packaging/AGENTS.md
order: 30
改到: `scripts/build_worker_runtime.py`、`scripts/codesign_macos.py`、`packaging/tavotto.spec`、`scripts/smoke_app.py`、`tavotto-cli`
主题: 内置 runtime 的构建链、macOS 签名、CLI 双入口、冒烟 → `bundled-runtime-build-and-signing.md`
必守要点: 构建三道闸（构建机自检 / spec 同尺 / 打包后冒烟）漏一道就发出不能渲染的包；签名验收只走 `codesign_macos.py`（`--deep` 签不到也验不出）；GUI exe 不当 CLI 调；冒烟不许「借一个解释器」
-->

- **构建链的三道闸**（漏一道就会安静地发出「装完不能渲染」的包）：
  ① 构建脚本自己逐个 import + 画真图，不过就失败在构建机；
  ② `TAVOTTO_REQUIRE_RUNTIME=1` 时 `tavotto.spec` 经
  `build_worker_runtime.check_runtime_dir()`（**与 build_desktop.py 共用同一把尺**）
  确认 schema / 平台架构 / 冒烟状态；③ 打包后 `smoke_app.py
  --expect-source bundled --expect-runtime` 真启动真渲染。
- **macOS 签名**：内置 runtime 让嵌套 Mach-O 从几十个变成五百多个，且全在
  `Contents/Resources` 下——**`codesign --deep` 既签不到也验不出**（它们被当作
  *资源*封进签名，封条本身合法）。签名与验收统一走 `scripts/codesign_macos.py`
  （读魔数找 Mach-O、深度降序自内向外、只给可执行文件挂 entitlements、
  最后逐个 `--verify` 并核对架构）。私有 Python tar.gz 同样要展开签里面的 Mach-O、保留模式/链接重打包，
  最终再展开验同 sidecar Team ID、timestamp/runtime、架构/minOS；生成固定资源 manifest 后才能封 .app
  （ADR 0111 2026-10-04 修订）。原产品锁不改，重试原料仍核原 SHA / size。最终 .app 冒烟还必须从 frozen
  sidecar 真供应这份归档并建 managed venv / ensurepip，不能拿内置渲染 runtime 的冒烟替它交差。
- **CLI 双入口**：`packaging/tavotto.spec` 从同一个 Analysis 产出 GUI 的
  `Tavotto` 与 `console=True` 的 `tavotto-cli`（共用 `_internal/`）。GUI exe
  不能当 CLI 调（无终端时 stdout 落日志），交接与安装清单都指 `tavotto-cli`
  （见 `docs/rules/backend/external-handoff.md`）。
- 验证：`tests/test_bundled_runtime.py`（定位/优先级/布局/架构/失败路径，
  **全部平台无关**）+ `tests/test_runtime_build.py`（锁文件分层、布局、
  `._pth`、构建判据、打包卫生，另有几条只在本机构建过 runtime 时才跑的
  **真 import + 真绘图**用例）+ CI 的 `windows-exe-smoke` 与 desktop-tauri 的
  三条腿（Windows / macOS arm64 / macOS x86_64，见 `.github/AGENTS.md`）。
- **别把「借一个解释器」加回冒烟**：macOS 这条腿一度现建 worker-env 再设
  `TAVOTTO_WORKER_PYTHON`，于是「runtime 根本没打进去」全程绿灯——空转的门禁比
  没有门禁更坏（`test_macos_ci_no_longer_fakes_a_worker_env` 看护）。
