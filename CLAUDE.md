# Magplot — 开发约定

产品名 **Magplot**（拼写大小写固定；旧名 Magic Matplot 只在兼容读取与
历史格式说明中出现）。品牌与格式常量唯一出处：`web/src/lib/brand.ts`、
`engine/brand.py`——界面/导出格式不得手写产品名。对象层级
Project / Canvas / Tab / Object 见 `docs/adr/0001-project-canvas-tab-object.md`。

论文 Figure 排版 + 参数化图表编辑工具。Flask 后端（`src/magplot/app.py`）+
PyMuPDF（**只经 `src/magplot/pdfbackend/`**），前端 `web/`
（Vite + React 19 + TS + Tailwind v4）；旧 v1 前端已于 2026-08-15 删除（git 可找回）。

## 进程与依赖边界（重要）

- Flask 跑在 `.venv`（只有 flask + pymupdf，**没有 matplotlib**）。
  `engine/registry.py`、`engine/pool.py`、`engine/ai_bridge.py`、`engine/config.py`、
  `engine/updater.py`、`engine/runtime.py` 被 Flask import，
  **必须保持纯标准库**。
- `engine/worker.py`、`engine/manifest.py`、`engine/overrides.py` 只在 worker 子进程里跑，
  解释器由 `pool.find_worker_python()` 探测（需科学栈；可用 `MM_WORKER_PYTHON` 覆盖）。

## 打包与启动（src layout，2026-08-16）

- 代码在 `src/magplot/`，`pyproject.toml`（hatchling）声明依赖与
  `magplot = "magplot.app:main"` 入口。`run.sh` = 自建 `.venv` +
  `pip install -e .` + `exec .venv/bin/magplot`；**不要再写 `python app.py`**，
  根目录已无该文件（旧进程内存里的老路径正是「worker 进程崩溃（无响应）」的成因）。
- extras：`worker`（matplotlib/numpy，装了就用同解释器渲染）、`dev`（pytest/build）。
- 前端产物 `src/magplot/web/` 由 `scripts/build_frontend.py` 从 `web/dist` 拷入，
  进 .gitignore；hatchling 默认跳过 VCS 忽略的文件，**必须靠 pyproject 的
  `[tool.hatch.build] artifacts` 收回**，否则 wheel 里没有界面（首页 404）。
  开发态包内无 `web/` 时 `app.py` 自动回退到 `web/dist`。
- CI 的 package job 看护这条链路：build_frontend → wheel → 断言含
  `magplot/web/index.html` + entry point → 干净 venv 装 wheel 跑 `magplot --help`。
- 运行时可写数据一律走 `engine/config.data_dir()`（`MAGPLOT_DATA_DIR` 可覆盖，
  conftest 已全局隔离）：cache / layouts / exports / baked_overrides/&lt;项目id&gt;.json /
  ai_history.sqlite3 / ai_snapshots 全在那儿。**不要再往包目录或仓库根写东西**
  ——site-packages 不可写，装成 wheel 后会直接崩。

## Tauri 桌面壳（2026-08-17，与浏览器模式并行）

架构与安全模型的完整版在 `docs/adr/0002-tauri-desktop-shell.md`，改动前先读。

- **进程关系**：Tauri 壳（`src-tauri/`）→ spawn `magplot --desktop-sidecar`
  （PyInstaller onedir，无 matplotlib）→ 现有 worker 协议。前端仍由 sidecar 的
  Flask 提供，**不走 Tauri frontendDist**——桌面与浏览器跑同一份界面。
- **桌面模式差异全部收在 `src/magplot/desktop.py`**：`127.0.0.1:0` 动态端口
  （werkzeug `make_server`，可优雅 shutdown）、一次性 nonce → HttpOnly cookie
  认证（nonce 走 **stdin 首行**，环境变量对同用户进程可见；`/`、`/assets/*`、
  bootstrap 之外全部 401 兜底）、Host/Origin 校验、握手文件（无密钥、原子写、
  退出清理）、stdin EOF + 父 PID 双路「壳没了就自杀」。浏览器/CLI 模式下这些
  钩子**必须完全旁路**（`test_desktop_sidecar.py` 看护）——别让桌面逻辑漏进
  `magplot` 普通启动路径。
- **前端唯一桌面感知点是 `web/src/lib/desktop.ts`**：组件不得直接 import
  `@tauri-apps/*`；每个能力都有浏览器回退（vitest 看护）。菜单事件 id 与
  `src-tauri/src/main.rs` 严格同源（`magplot:menu`）。
- **Tauri 2 的 ACL 对应用自定义命令同样生效**：新增 `#[tauri::command]` 必须
  三处同步——`build.rs` 的 `AppManifest::commands`、`capabilities/main.json`
  加 `allow-<命令名连字符化>`、`main.rs` 的 `generate_handler`。漏掉前两处
  invoke 会被**静默拒绝**（reveal_export「点了没反应」就是这么坏的）；
  失败路径不许吞——回退时把完整文件路径告诉用户。
- **桌面模式下 Python updater 停用**（升级归 Tauri 层），`/api/update/*` 回
  禁用响应；浏览器模式照旧。
- 构建：`python scripts/build_desktop.py`；验收：`python scripts/smoke_desktop.py
  --sidecar dist/Magplot/Magplot`（真产物全链路：认证/项目/渲染/导出/退出无孤儿）。
  CI 在 `desktop-tauri.yml`——v0.3.0 起是唯一桌面发行链（旧 `desktop.yml`/
  Inno Setup/免安装 zip 已退役删除，git 可找回）；Windows NSIS 自带内置渲染
  runtime，桌面产物一律真窗口、不再有「启动后开浏览器」的形态。
- wheel/sdist 不含 `src-tauri/`（hatchling 白名单）；`src-tauri/target/`、
  `src-tauri/gen/` 进 .gitignore。
- **安装界面（2026-08-17）**：macOS dmg 带品牌版式——背景图
  `assets/brand/dmg-background.png` 由 `scripts/build_dmg_background.py` 生成
  （PyMuPDF 直绘，图标落点与 `make_dmg.sh` 的 Finder 版式严格同源），
  make_dmg.sh 里 Finder 脚本失败只降级为朴素版式、绝不断发布链。
  Windows NSIS 用 vendored 模板 `src-tauri/windows/installer.nsi`
  （上游 tauri-cli v2.11.4 + `MAGPLOT PATCH` 标注的最小补丁：去欢迎页 /
  极简进度 / 品牌配色；头图侧栏图走 tauri.conf.json 的 nsis.* 配置）。
  **@tauri-apps/cli 钉死在 2.11.4**——模板与打包器必须同源，升级 CLI 时
  取新模板重打补丁并同步 build_desktop.py / desktop-tauri.yml / nightly.yml
  （tests/test_nsis_template.py 看护四处版本一致与 BMP 形态）。

## Rust supervisor `magplot-workerd`（2026-08-18，与 Python 池并行）

架构、协议与错误码的完整版在 `docs/adr/0004-workerd-supervisor.md`，改动前先读。

- crate 在仓库根的 **`workerd/`**（不进 `src-tauri/`，壳保持薄）；`workerd/target/`
  进 .gitignore；pyproject 的 `exclude` 显式挡住它进 wheel/sdist
  （sdist 的 `include=["tests"]` 是 gitignore 风格模式，会把 `workerd/tests/` 收进去）。
- **Rust 是机制层，Python 是策略层**：解释器优先级（`pool._prioritized_candidates()`）、
  内置 runtime 的 `-B`/env、超时档位、会话与队列上限**全部留在 Python**，
  Flask 把完整 spawn 规格（argv/env/log_path/握手期限）交给 workerd。
  **别在 Rust 里重写探测或渲染**——那是制造第二个权威。
- `pool.py` 的 Python 实现**一行没删**：找不到二进制或 `MAGPLOT_WORKERD=0` 就原路走它，
  它同时是 workerd 的参考实现。**pytest 的 conftest 默认把开关钉成 `0`**，
  否则 `cargo build` 之后整套既有用例会悄悄换一条控制面跑。
- `workerd/src/patchspec.rs` + `pyfloat.rs` 必须**逐字节复现** `engine/patchspec.py`，
  硬验收是同一份 `tests/golden/patch_vectors.json`。已知坑：Python 的浮点 repr
  （`1e+22`/`1e-07`、`-0.0` 保号、`1.0` 补 `.0`）、int 与 float 是两个值
  （serde_json 必须开 `arbitrary_precision`）、转义表照抄 `ESCAPE_DCT`。
- **「起来了」= hello 握过手**，不是「进程对象还在」：Windows 关进程比 POSIX
  慢得多，握手早已失败（写管道 EINVAL）而 `poll()` 还回 None，只看后者会把
  正在退出的进程当成就绪的 workerd——重启计数一次都不加，起来就崩的二进制
  于是无限重启，每次渲染白等一轮 spawn + 握手，还永远退不到 Python 池。
  半启动的那条要先 kill 再重启（否则每次泄漏一个子进程）。
- 语义要点：generation 每 (re)spawn +1 且**上一代的迟到响应一律丢弃**；
  per (会话, stem) 的 render 队列里至多一条、新的顶掉旧的（回 `queue_superseded`）；
  export 一条都不合并；队列有界，满了立即拒绝；取消在飞 = **杀进程**
  （协议层没有协作中断）；淘汰 = kill，不等锁。
- 验证：`cd workerd && cargo test && cargo clippy --all-targets -- -D warnings && cargo fmt --check`。

## PDF 后端边界（许可证相关，勿破坏）

- `src/magplot/pdfbackend/pymupdf_backend.py` 是**全仓库唯一** import pymupdf 的
  模块；`__init__.py` 是与实现无关的契约层（probe_asset / render_preview_png /
  text_width / compose + mm2pt / hex2rgb）。`app.py` 只认这些名字。
- 为什么在意：PDF 库是可替换的实现细节，收敛成单一模块后换后端只需重写这一个
  文件，上层零改动。**别在 app.py 或别处新写 `import pymupdf`**——那会把这条
  边界废掉。许可证说明见 `docs/LICENSING.md`。
- 面板的项目路径解析与引擎重渲染留在 app 层的 `_resolve_panel_source` 回调里，
  后端只管画。几何公式仍与前端严格同源，pytest 用 get_drawings() 做几何级看护。
- **`/api/render` 的磁盘缓存键 = `sha1(id|内容 sha1|宽度|后端-版本)`**（2026-08-18）。
  **不许用 mtime 当身份**：它回答的是「什么时候被碰过」，内容没变而 mtime 变了
  （touch / 从备份还原 / 同步工具）会白丢一张 3200px 预览；换了 PyMuPDF 版本
  像素可能已经不同却照旧命中，所以 `BACKEND_NAME/BACKEND_VERSION` 进契约层。
  内容哈希走进程内 `(mtime,size)→sha1` memo（memo 失效信号 ≠ 身份）。写入一律
  临时文件 + `os.replace`（同键并发会读到半个 PNG），零字节缓存当场删掉重建
  ——临时文件后缀**必须还是 .png**，后端按扩展名定格式。
  **Windows 上 `os.replace` 盖不掉正被读的目标**（werkzeug 的 `send_file` 拿着
  没有 FILE_SHARE_DELETE 的句柄 → WinError 5，并发请求当场 500）：撞上就
  **退让**给已经在磁盘上的那份——键含内容哈希，同键必然逐字节相同；只有目标
  不存在或是零字节时才重试，重试完仍不行照旧抛出（假装成功 = 一个永远画不
  出来的面板）。看护 `tests/test_render_cache.py` 与
  `tests/test_windows_regressions.py`。

## Windows 内置渲染 runtime（2026-08-17）

Windows 桌面安装包**自带一套 Magplot 私有的 Python 渲染环境**，用户不需要先装
Python，首次渲染也不联网：

    Magplot.exe → _internal\runtime\python.exe → engine/worker.py → 用户的图表脚本

- **版本锁 `packaging/runtime-lock.json` 是唯一输入**：CPython 3.13.x embeddable 的
  官方下载地址 + SHA-256，以及科学栈的**完整传递闭包**（精确版本，不允许范围/
  latest）。构建脚本 `scripts/build_worker_runtime.py` 只执行、不做版本决策
  （`--resolve` 是维护者更新锁文件时才跑的那一档）。**别手写闭包**——漏掉的传递
  依赖会在用户机器上以 ModuleNotFoundError 出现。产物在仓库根的 `runtime/`，
  进 .gitignore，并在 pyproject 里显式 exclude（wheel/sdist 绝不能被它污染）。
- **`engine/runtime.py` 是路径判断的唯一出处**（frozen 的 `_MEIPASS` / exe 同级 /
  源码树 / `MAGPLOT_RUNTIME_DIR` 覆盖）。这一段**全程 os.path 拼字符串，一个
  pathlib 都不用**：`Path()` 按 `os.name` 分派，在别的平台上构造另一半直接抛
  UnsupportedOperation，连在 macOS 上单测 Windows 分支都做不到
  （test_runtime_path_logic_never_instantiates_a_foreign_pathlib 看护）。
- **解释器优先级（`pool._prioritized_candidates()` 是唯一出处）**：
  `MM_WORKER_PYTHON` → 用户在设置里指定的 → **内置 runtime** → 自身
  （非 frozen）→ 系统 Python/Conda 探测。用户显式指定的永远优先；
  第 5 条是兼容回退，不是摆设（脚本要 rdkit 这类内置环境没有的包时靠它）。
  来源标签 `env_override/configured/managed_venv/bundled/current_process/system`
  经环境状态 API、诊断包与冒烟断言一路暴露出来。
- **不往安装目录写任何东西**：内置 runtime 起 worker 时注入
  `PYTHONPYCACHEPREFIX` / `MPLCONFIGDIR`（改道到数据目录）+ `PYTHONNOUSERSITE`。
  安装目录可能在 Program Files（没写权限），卸载后也不该留垃圾。
- **缺失/损坏报专用 code**（`bundled_runtime_missing` / `bundled_runtime_invalid`），
  提示「安装文件不完整，请重新安装」——**不是**「请先安装 Python」。那时
  `can_install` 必须为 false：embeddable 里连 pip 都没有，现场建 venv 只是
  把包装问题伪装成用户的环境问题。macOS / pip / 源码模式不带 runtime，
  那里 runtime 缺失是正常状态，两个 code 都不给。
- **本阶段不做包管理**：脚本缺包时报结构化的 `missing_dependency` + 包名，
  引导用户换成自己的环境；**绝不按 ModuleNotFoundError 自动 pip install**——
  那会让内置环境不再可复现，也让「重装就能修」这条退路失效。
- 验证：`tests/test_bundled_runtime.py`（定位/优先级/失败路径，平台无关）+
  `tests/test_runtime_build.py`（锁文件、`._pth`、打包卫生）+ CI 的
  `windows-exe-smoke`（真 .exe、**不给 MM_WORKER_PYTHON**、断言
  `--expect-source bundled` 并逐个 import 内置科学栈）。

## 检查更新

- `engine/updater.py`（纯标准库）：查 GitHub Releases 最新 tag → 与
  `magplot.__version__` 比 → 按安装方式给升级命令。仓库地址等常量在
  `engine/brand.py`，别处不得手写。
- 默认每天一次、可在设置里关（关了**一个包都不发**）；升级永不静默进行，
  且升级后 `restart_required`（进程内存里还是旧代码）。
- **桌面版走另一条（2026-08-18）**：`tauri-plugin-updater` 下载签名过的安装包
  就地替换，装完 `relaunch`。两条通道互斥——后端在桌面模式把 `/api/update/*`
  整个关掉，前端 `checkUpdateOnStartup()` 按 `isDesktop()` 只查一条。
  前端唯一入口仍是 `web/src/lib/desktop.ts`（每个能力有浏览器回退）。
  更新包的 minisign 私钥只在 CI，公钥写死在 tauri.conf.json；**没配私钥时
  构建就地关掉 createUpdaterArtifacts 并打 warning**，安装包照发。
  **macOS 的更新包必须在签名/公证之后重做**——tauri build 顺手打的那份装的是
  没签名的 .app，换上去 Gatekeeper 当场拦。清单 `latest.json` 由
  `scripts/make_updater_manifest.py` 在两条 matrix 腿都跑完后合成
  （少了它壳永远显示「已是最新」而 CI 全绿）。细节见 ADR 0002 末节。
- 安装方式探测：包上两级有 `pyproject.toml` = source（只提示 `git pull`，
  绝不在源码树里跑 pip 覆盖用户工作副本）；`sys.prefix` 含 pipx = pipx；否则 pip。
- 升级目标优先取 Release 里的 `.whl` 资产 URL（没发 PyPI 也能升），
  退回按包名装。

## 渲染引擎核心机制

- **worker 协议 v1（2026-08-18）**：请求带 `protocol_version/request_id/
  worker_generation/render_revision/canonical_patch_hash` 信封，命令
  ping/build/render/render_png/preview_png/export/cancel/shutdown，
  错误带 code + retryable；generation/revision/hash 由 worker **原样回显**
  （校验归调用方，`request_id` 对不上当场 kill 会话）。无 `protocol_version`
  的老信封仍按旧形状回应（双栈，手工调试用）。patch 规范化与哈希的**唯一
  权威实现**是 `engine/patchspec.py`（纯标准库，父子进程共用同一份），
  golden vectors 在 `tests/golden/patch_vectors.json`——Rust supervisor 要
  逐字节复现，改任一侧必须同步另一侧。cancel 只是尽力而为的 no-op，硬取消 =
  kill + 重启。完整契约见 `docs/adr/0003-worker-protocol-v1.md`，改协议前先读。
- **计时管道与性能基线（2026-08-18）**：worker 的 build/render/export 响应带
  `timings`（`script_build_ms` / `patch_apply_ms` / `canvas_draw_ms` /
  `manifest_ms`；**没有 `svg_ms`**——SVG 序列化与 draw 在 matplotlib 里分不开），
  `pool` 补 `queue_wait_ms` / `total_ms` 并把冷启动那次的 build 计时折叠进来，
  `app.py` 再补 `worker_get_ms`（取/spawn 会话，既不属于 worker 也不属于 build，
  **漏了它冷启动的十几秒在数据里就凭空消失**）。全部是加字段，协议不升版；
  legacy 信封一个字节不加。基线报告与「值得做的优化」清单在
  `docs/perf-baseline.md`，重测走 `python scripts/bench_render.py`。
  **先测量后优化**：那份文档里被数据否掉的两条（挪 SVG 顺序省 draw = 图例 bbox
  错 0.18–0.32 分数，写回自检必报 divergence；`draw_without_rendering` 只省 8%）
  别再重试。
- **前端渲染态按「文件 + 变体」分键（2026-08-18，Phase F）**：键 =
  `fileId + ' ' + JSON.stringify(overrides)`，唯一出处
  `renderStore.renderKeyOf(panel)`；消费方一律 `usePanelRender/usePanelManifest`
  （或非 hook 的 `panelRender(state, panel)`）。**旧约定「每个 fileId 只能有一个
  说了算的面板」已废除**——那条裁决（`pickRenderTargets`）是为了绕开
  「两个同文件不同 override 的副本互顶 wantPatches → React #185」，代价是输家
  永远显示赢家的图；现在各存各的，去重只剩「完全相同的两个副本共用一次渲染」
  （`renderTargets`）。live figure 仍是一个 stem 一份，靠轮流全量重放
  （patch_apply≈0ms、热画 17–28ms，数据见 perf-baseline）。配套：
  ① **SVG 与 manifest 必须同一次响应**（render 请求带 `inline_svg`，worker
  在响应里内联刚写完的那份）——第二跳 GET `/api/engine/svg` 读磁盘，另一个
  变体插进来就会图框错配（端点保留兼容，前端不再用）；
  ② 位图显示走 `POST /api/engine/preview_png`（按 patches 出图、状态中立、
  文件名带 patch 哈希前 12 位），`/api/engine/png` 是「谁最后渲染谁说了算」，
  只留兼容；③ 自己那份变体还没画出来时退回该文件最近画好的那张
  （`latest` 表），否则每敲一个字画布都会闪回磁盘原图；④ 连续调整期间
  **只给含 `role=="image"` 的面板**发 `preview_dpi: 100`，松手/结束事务由
  `flushRender(panelId)` 按默认 dpi 定稿（纯矢量图上降 dpi 零收益，见基线补测）；
  ⑤ 编辑期每改一个值就多一条变体，`prune(live)` 按文档现存面板清理，
  只留在用的与每个文件最近成功的那份；⑥ SSE 的 render.started/done 只带
  fileId，写**文件级** `building` 表，绝不盖任何变体条目（盖了的话另一个
  副本会永远转圈）。看护：`web/src/store/renderStore.test.ts`、
  `web/src/hooks/useEngineSync.test.ts`、`tests/test_engine_variants.py`。
- **假实时预览：预览平面与历史平面严格分开（2026-08-18，Phase G）**。
  预览平面（`web/src/store/svgPreviewStore.ts` + `lib/svgStyle.ts`）只活在内存与
  SVG DOM 里，rAF 合并成一帧，**不 commit、不进历史、不发后端**；历史平面照旧
  是 `documentStore.commit / beginTxn / endTxn`，**没有任何一条路径绕开它**。
  数据流：`pointerdown → beginPreview`（只记账）→ `pointermove → previewTransform /
  previewStyle`（只改 DOM）→ `pointerup → setOverride(…) + commitElementPreview`
  （一条历史 + 一次权威渲染）→ 权威 SVG 换上来时 `reattachPreview` 收工。
  * **临时 transform 必须写成 `translate(…) <原始 transform>`，永远从 base 现算**
    ——旧实现直接 `setAttribute('transform', 'translate(…)')` 把 matplotlib 自己的
    变换整个盖掉（`<image>` 的 `scale(1 -1) translate(…)` 就是这么没的），
    字符串累加则会让位移翻倍。base 与账本挂在「面板 + 这一版 SVG」上，不挂在
    session 上：连着拖两个元素时，第二次绝不能把第一次的预览位移当成 base。
  * **`pointercancel` / `lostpointercapture` 与 `pointerup` 必须分开**
    （`trackPointer` 的 `TrackEnd.cancelled`）：取消 = 还原 DOM、不写 override、
    不进历史、不渲染。以前两者走同一条路，被系统打断的拖动会静默落成真实改动。
  * **`reattachPreview` 只在 DOM 真的被换过时才重放**（`domIntact` 比节点引用）：
    每写一条 override PanelView 都会重跑，此时重新采 base 采到的是「已经挪过的
    位置」——位移翻倍且再也还原不回去。
  * **局部样式预览是白名单**（`lib/svgStyle.ts` 的 `STYLE_ADAPTERS`），默认不支持。
    通用规则是「只改本来就声明了该属性、且值不是 `none` 的叶子」，因此
    `fill: none` 的线不会被 facecolor 填实、箭头杆与箭头帽各得其所。文字是唯一
    例外（颜色在字形组上，默认黑色时那条 style 根本不存在，必须允许新增）。
    还原记的是**整条 style 属性原文**而不是逐条属性：CSSOM 会把颜色规范化成
    `rgb(...)`，逐条还原写回去的已经不是 matplotlib 给的那份文本了。
    **实测不可预览、必须回退后端的**：`image.alpha`（透明度烤进 PNG 栅格）、
    `errorbar.*` / `bar_series.*` / `ticks.*` / `ticklabel.*`（manifest 的伪元素，
    gid 在 SVG 里根本不存在）。能力表的断言全部打在**真实 matplotlib 输出**上，
    fixture 由 `python scripts/dump_svg_fixture.py` 生成（`--check` 可比对）。
  * **渲染策略与历史无关**：`setOverride/setOverrides/requestRender` 的
    `'immediate' | 'defer' | 'none'` 只决定「什么时候麻烦 matplotlib」。`'none'`
    **仍然要写 `wantPatches` 占位**——不占位的话 `syncEngine` 会立刻替它发一次；
    对应地 `flushRender` 的判据是「这一版还没画出来」而不是「有没有挂着计时器」。
  * **历史粒度 `historyMode`**（`gesture` 默认 / `granular`）只改事务边界，
    两种模式下后端渲染都推迟到手势结束；无论哪种，文档改动都经过
    `documentStore.commit`。
  * 看护：`web/src/lib/svgStyle.test.ts`（真实 SVG fixture 的适配器矩阵）、
    `store/svgPreviewStore.test.ts`、`canvas/fakeRealtimeDrag.test.tsx`
    （100 次 move 零后端 / 取消语义 / 撤销重做）、
    `components/inspector/elementStylePreview.test.tsx`、
    `e2e/fake-realtime.spec.ts`（真浏览器，顺带产出 perf-baseline 的 Phase G 数字）。
- **live-figure 会话**：worker 跑一次脚本（拦截 `Figure.savefig` + `paper_style.save`，
  不写真实文件），Figure 常驻内存；override 直接 mutate artist 再导出带 gid 的
  SVG（dpi≈120 预览）——冷启动秒到分钟级，热态 ~40ms。
- override 是**全量列表**语义：worker 维护 applied/originals 两表，缺失的 key 自动
  恢复原值（undo 的基础）。前端永远发完整 `o.overrides`。
- **export / preview_png 都是状态中立的一次性动作**：应用自己那组 patches 出图后
  必须把 `state.applied` 还原回去（还原那次的 warnings 丢弃）。不还原的话历史版本
  恢复与画布导出（每个面板各带一套 overrides）会把别人的状态留在常驻 figure 上，
  前端的 lastPatches 与 worker 真实状态错位，「全量列表」的还原就还错了东西
  （test_export_is_state_neutral 看护）。
- **worker 请求一律有超时**（`pool.BUILD_TIMEOUT/REQUEST_TIMEOUT/EXPORT_TIMEOUT`，
  测试可 monkeypatch）：超时即 kill 并报 `code=worker_timeout`，会话由下一次
  `get()` 原地重建——**状态未知的 worker 绝不复用**。超时实现是「读线程 +
  join」而不是 select（Windows 的 select 不接管道）。无超时的 readline 会让一个
  死循环脚本持着 `w.lock` 把整个会话占死，连 shutdown 都抢不到锁
  （test_request_timeout_kills_and_rebuilds_worker 看护）。
- **应用顺序规范化 + figure 锚定 prop 的重放（2026-08-17，数据损坏级）**：
  `overrides.apply` 按 size_mm → 子图 position → 其余（列表序）应用；
  pos_frac / loc_frac / endpoints_frac 的 setter 在应用那一刻把 figure 分数换算进
  artist 本地坐标，**几何一变（含还原）必须重放它们**，否则热会话状态 ≠ 全量
  重放——用户「写回时的样子」重开后全体文字错位（FigS3 事故，
  test_frac_anchored_props_survive_geometry_moves 看护）。新增 figure 锚定
  prop 时记得加进 `_FRAC_ANCHORED`。aspect="equal" 的子图只有 draw 才
  apply_aspect，几何组应用完必须 `draw_without_rendering()` 刷新布局再应用
  其余 prop。事故期间保存的旧文档用 `scripts/recover_frac_positions.py`
  修复（从写回 PDF 的文字层反推真实位置，输出另存 + POST 成布局版本）。
- 坐标约定：manifest bbox/anchor 均为 figure 分数坐标、**y 向下**（top-origin）；
  worker 内部转 matplotlib 的 bottom-origin。
- 特殊 artist：轴标签拖动走 `set_label_coords`（恢复时 transform 也要还原）；
  标题拖动要设 `ax._autotitlepos=False`；3D axes 暴露文字类元素 +
  position/visible（可拖动缩放；Axes3D 会按盒比例微调落位，以重建后
  manifest 的真实 bbox 为准）+ 视角（elev/azim/roll，setter 全量带三角避免
  view_init 重置）+ 轴线/背景面板/网格（x/y/z 三轴统一应用、按轴还原）+
  x/y/z 刻度组（TickSet/TickLabel 已泛化到 z；3D 不出 direction/visible）+
  投影方式 proj_type + **轴箭头**（axis_arrows 开关 + arrow_color/width/head；
  `_AxisArrow3D` 在 do_3d_projection 里用 axis3d 的私有几何助手
  `_get_coord_info`/`_get_axis_line_edge_points` 每帧现算落边，隐藏原生
  axis.line、箭头指向坐标增大端，视角旋转/换边自动跟随；matplotlib 升版
  破坏点由 test_axes3d_axis_arrows_roundtrip 看护）。
  盒内数据属性（spines/lim/scale）仍禁用。
- **图内箭头（FancyArrowPatch）**：脚本 `add_patch` 的独立箭头 manifest 带
  `arrow_endpoints`（figure 分数、y 向下），可整体拖动 / 拖单个端点
  （override `endpoints_frac`=[ax,ay,bx,by]，setter 经箭头自身 transform 逆变换
  后 `set_positions`）；arrowstyle / linestyle 两类箭头都可改
  （识别不出的自定义样式报 "custom"，选它=不动）。**annotate 的 arrow_patch
  端点由注释机制每次 draw 重定位，绝不出端点**——出了用户拖完下一帧就弹回
  （test_arrowpatch_endpoints_and_style_roundtrip 看护）。前端交互与画布箭头
  同语义（2026-08-17，elementArrowEditing.test 看护）：命中/框选按**线本身**
  不按 bbox 空白矩形、选中/hover 沿线描示无矩形外框、拖端点 shift 锁 15°、
  整体拖 shift 锁水平/垂直/45°（分数坐标锁角必须换算到内容像素系）；
  图内文字/子图拖动同样有 shift 锁向，画布对象拖动可吸附图内元素中心线
  （elementSnapCandidates）。
- 散点 marker 可整体替换（set_paths，首改前缓存原始路径，"original" 还原）；
  图例条目顺序 entry_order（重建型，manifest type="order"）。**图例重建后必须
  `_legend_box.set_offset(leg._findoffset)` 重挂定位回调**，否则导出时图例整块
  消失（ncol 等旧重建路径同修）。散点/扁平线的 bbox 走 `_padded_bbox`
  （PathCollection 用 datalim 换算，零厚度边垫 4px，否则进不了 manifest）。
- 色条方向仍明确不支持：翻转需销毁重建色条轴，会打乱 axes gid 稳定编号。
- 面板翻转（flip_h/flip_v，先翻转后旋转）：导出按 dpi 位图嵌入
  （show_pdf_page 无镜像；flipH = 行倒序 + 旋转 180°），与 opacity<1 同一取舍。
- 安全：worker `cwd=沙盒`（挡相对路径写出/删除）+ `Path.unlink` 守卫
  （挡 fig6 的绝对路径删除）；脚本 stdout 重定向到 stderr 保护 JSON 协议。
- **paper_style 是图库方言，不是引擎依赖**：worker 的 `import paper_style` 必须留在
  try/except 里，捕获靠通用的 `_patched_savefig` 兜底。曾经这行是硬 import，
  任何不带 paper_style.py 的图库（论文的 supporting_information、外部用户的图库）
  都以 ModuleNotFoundError 开局，一张图都渲染不了（test_build_without_paper_style 看护）。
- 素材扫描用 `os.walk` 当场剪枝隐藏目录（.venv/.git/.rendered/.qa_*）与隐藏文件，
  不是 rglob 后过滤——既是噪音也是性能（图库旁边常年躺着工具产物）。
- 新脚本 / stem 变化：改**图库目录下的 `mm_registry.json`**（注册表随图库走；
  `engine/registry.py` 只负责加载校验，重复 stem 仍直接报错；
  一脚本多产物 / 归属有歧义的 stem，裁决结果记在各图库自己的注册表文件里，勿改）。
  `entry` **不限于 main/render/__main__**——worker 就是 `getattr(module, entry)()`，
  任何合法标识符都行；`script` 键是图库**相对路径**（POSIX 分隔符），
  子目录里的脚本照样登记（worker 会把脚本自己所在目录也加进 sys.path）。
- **静态扫描（`engine/discover.py`）不再只认字符串字面量**：抽象求值覆盖
  模块级常量、f-string、`Path(...) / "x"`、`.with_suffix()/.with_name()/.joinpath()`、
  `os.path.join()`、`.format()`、`%`、`+` 拼接、`Path(__file__)` 自命名，
  以及**跨函数传播**（`save_panel(fig, "Fig1")` → 包装函数里的 `OUT / f"{stem}.pdf"`）
  与常量 for 循环展开。递归扫子目录（剪掉 .venv/__pycache__/node_modules…）。
  手动生成/合并：`python -m magplot.engine.discover <figures_dir> --write`
  （现有条目永远优先，冲突 stem 只报告不裁决）。
- **试运行探测（`engine/probe.py`）**：stem 真的只有运行期才知道时（遍历数据
  目录、读配置、命令行参数），把脚本**跑一遍**按真实产出登记——worker 本来
  就在 build 阶段拦 savefig 并按真实文件名捕获，跑得起来 = 能参数化。
  静态仍解不出的报 `dynamic_names`，交给这条路；**绝不猜，也绝不静默跳过**
  （静默跳过 = 用户拿到空注册表却不知道为什么）。
  界面入口：顶栏项目菜单 / 设置 →「脚本注册表」（扫描 / 试运行 / 手工裁决）。
- worker 里 **`sys.argv` 必须换成脚本自己的**。不换的话按参数命名输出的脚本
  会拿到 worker 的 `--script/--out-dir/--entry`，存出一堆叫 `--entry` 的图
  （试运行探测时当场撞见过，`test_script_sees_its_own_argv_not_the_workers` 看护）。

## 布局层新增（R18）

- **布局版本**：`/api/versions/<docId>` 系列，快照存 `layouts/_versions/`，
  自动检查点去重 + 滚动清理（保手动裁自动）；恢复=前端 commit（可撤销），
  与「写回原始文件」的 baked 历史完全无关。
- **论文样式**：`/api/styles`（`layouts/_styles.json`）；前端按角色映射成
  override / 标注属性一次 commit 应用，绝不写回源文件。
- **项目包**：`POST /api/package` 打 zip（layout+素材+脚本+sha1 清单）；
  `POST /api/package/open` 检视（缺失/sha1 漂移），素材永不自动写入图库。
- **导出**：请求可带 `proof` 对象 → 随成图写 `_proof.json`。
- **项目文件统一收纳在项目内的 `magplotfile/`（2026-08-17 定版）**：命名画布
  布局直接放 `magplotfile/`，导出默认 `magplotfile/export/`（settings.export_dir
  可覆盖；建不出来退回数据目录，测试读响应里的 export_dir 而不是猜路径），
  布局版本历史 `magplotfile/versions/`。旧位置（项目 `canvases/`、项目同级
  `<项目名>-exports/`、数据目录 layouts/ 与 layouts/_versions/）只读兼容、
  合并列出，重名以 magplotfile 为准；**素材扫描的 EXCLUDE_DIRS 必须含
  magplotfile**，否则导出成图会混进素材面板。autosave / styles 等
  跨项目或内部机制仍留在数据目录。
- 前端文档模型新增可选字段（schema 仍为 2，旧文档兼容）：
  `PanelObject.lockedGids / flipH / flipV`、`ObjectBase.layoutPinned`、
  `FigureDocument.layoutGroups`（行/列/网格约束，id 即 groupId，
  尺寸变化自动重排、undo/redo 不触发）。

## 项目系统与多画布（Magplot 成熟化，2026-08-15）

- **项目（多开，2026-08-16 起）**：`app.py` 无默认路径；`PROJECTS: dict[id, ProjectCtx]`
  同时端着多个图库，`DEFAULT_PROJECT` 只是「不带 pj 的请求落到哪」。未打开项目时
  API 回 409 `code=no_project`，前端渲染 ProjectPicker。用户级配置在
  `engine/config.py`（macOS `~/Library/Application Support/Magplot/config.json`，
  测试用 `MAGPLOT_CONFIG_DIR` 重定向——conftest 已全局隔离）。
  每项目设置（导出/备份目录、`allow_write_back` 只读）经
  `PATCH /api/project/settings`；写回类端点先过 `_write_back_forbidden()`。
- **每标签页一个项目**：请求靠 `pj` 认领（`_request_ctx()`）——**查询参数与
  请求头两条路都必须认**：fetch 统一带头，但 `<img src>` 与 EventSource 加不了头，
  只做一条会让一半 API 串到别的项目上。前端把 pj 存 **sessionStorage**
  （`lib/session.ts`，按标签页隔离，「不同标签页开不同图库」就是靠它）；
  `?pj=` 出现在地址栏时认下并立刻抹掉。指名一个不存在的 pj 一律 409，
  **绝不悄悄落到默认项目**（那会让标签页对着另一个图库继续编辑）。
  `open_project()` 复用已打开的 ctx，**不再拆别人的台**（旧实现每次切换都
  stop_watcher + shutdown_all + interrupt_all）；关项目走 `close_project()`。
  registry 因此不能再是模块全局：`engine/registry.Registry` 可实例化，
  模块级函数代理到默认实例（老调用方式与测试不动）。worker 池键与
  watcher 也都带项目路径（`pool._norm_dir`）。
  **写回基线（baked overrides）同样按项目分键**：`baked_overrides/<项目id>.json`，
  `load_baked(ctx)` / `append_baked(stem, patches, ctx)` 默认取 `current_ctx()`；
  旧的全局 `baked_overrides.json` 只作一次性迁移源（按 `ctx.registry.for_stem`
  过滤搬入，**不删旧文件**——别的项目还要迁；迁过一次分键文件即唯一权威，
  哪怕是空 dict）。`scan_panels` 里的 baked 表是**局部变量**，绝不再做模块级
  缓存——那就是「A 项目扫一遍素材，B 项目的基线全被换掉」。
  SSE 事件带 `pj`，前端只处理属于本标签页项目的那些。
- **schema 3**：`ProjectDocument{project, canvases[], activeCanvasId}`；
  运行时激活画布仍是 schema 2 形状的 `documentStore.doc`（画布编辑代码零改动），
  持久化/读档统一走 `migrateToProject()`（接受 2/3）。画布切换换入换出
  undo 栈（canvasSessions）与 UI 会话（`store/canvasSession.ts`）。
  标签页 openTabs 按 documentId 存本机。后端 versions/package 接受 schema 2/3。
- **剪贴板（2026-08-17）**：⌘C/⌘V 的主路径是**原生 copy/paste ClipboardEvent**
  （`e.clipboardData` 同步读写，`lib/clipboard.ts` 的 handleCopyEvent /
  handlePasteEvent）——WebKit（Safari / 桌面壳）不给非编辑区的异步
  readText/writeText，跨标签页粘贴只有这条路全浏览器通。keydown 层不再拦
  ⌘C/⌘V；按钮触发的复制仍走 writeText（点击是用户手势）。e2e
  cross-tab-paste.spec.ts 看护。
- **撤销防线（2026-08-17，数据损坏级）**：`txnUpdate` 在无事务时**丢弃更新**
  ——绝不静默直写 doc（拖动中事务被外部 endTxn/undo 结束后，pointermove 落进
  静默分支 = 位移绕过历史、撤销永远找不回，真实用户撞见过）。一切撤销入口
  （键盘 / 顶栏按钮 / 桌面菜单加速键）必须走 `runUndoRedo`（带
  undoRedoBlocked 守卫）；undo/redo 的 applyPatches 有 try/catch，坏补丁丢弃
  该条而不是让栈与文档错位。
- **画布标签常驻图层**：每个打开的标签一个图层，非激活的用 canvases 快照渲染
  并 display:none——docToCanvas/canvasToDoc 共享同一 objects 数组引用 +
  ObjectView memo，切换标签 = 纯 CSS 显隐，不重建 DOM / 不重新解码图片。
- **自动保存**：磁盘为主（`PUT /api/autosave/<docId>` 原子写
  `layouts/_autosave/`），localStorage 只留索引 + 崩溃兜底副本
  （写盘成功即清、读取按 updatedAt 取新）。失败发
  `magplot:autosave-error` 事件 → 常驻错误 toast。
- **标注**：任意角度 `rotationDeg`（面板除外；导出走 PyMuPDF morph，
  CSS 顺时针 = Matrix(deg)）；形状 triangle/diamond/polygon/brace + 圆角/
  虚线/填充透明度；箭头 headStart/headEnd（triangle/open/bar，旧 head 字段
  兼容推导）；文字下划线/行距/内边距/背景/描边。**前后端几何公式同源**
  （shapeGeometry.ts ↔ pdfbackend/pymupdf_backend.py `_polygon_points`/`_dash_pattern`
  同名注释），
  改一边必须同步另一边，pytest 用 get_drawings() 做几何级看护。
  科研预设在 `lib/presets.ts`（纯既有对象组合）。
- **混排对齐（2026-08-17）**：图内编辑态里 **shift 点画布标注**（文字/箭头/
  形状）= 加入混排选区、不退编辑态（ObjectView 的唯一例外分支）；元素检查器
  的 AlignSection 接受 `MixedEntry`（元素写 override、标注改画布 x/y），经
  `applyMixedAlign` **同一次 commit**——一条撤销回滚两边。标注框由
  `annotationAlignEntries` 换算进面板内容分数空间；面板带旋转/翻转不给条目。
- **写回原图可携带画布标注（2026-08-17）**：写回对话框勾选后，与目标面板
  重叠的标注（重叠面积最大者得、一条只进一张图）由
  `lib/writeBackAnnotations.ts` 换算成**图自身 mm**（长度类字段按显示比例
  同缩），后端 `pdfbackend.annotate_asset` 用导出合成同一组 `_draw_*` 矢量
  画进 PDF、PNG 由注好的 PDF 重栅格化（两载体同源）；只有 PNG 的素材回
  `annotations_need_pdf`。写回成功后画布原件移除（可撤销）。面板带旋转/
  翻转不支持（UI 给原因）。
- **空状态**：一律用 `components/ui/EmptyState`（图标+短标题+≤1 句+≤1 动作）。
- 前端测试：`cd web && pnpm test`（vitest+jsdom；NODE_OPTIONS 里禁用了
  node 内建 webstorage，否则 jsdom localStorage 被遮蔽）。

## 外部交接与 Codex 插件（2026-08-18）

完整版在 `docs/adr/0005-external-handoff-and-codex-plugin.md`，改动前先读。

- **发现链的唯一权威是 `engine/locate.py`**（纯标准库）：`MAGPLOT_CLI` → PATH →
  安装清单 `install.json` → 已知安装位置 → HKCU（只当补充）→ 当前解释器。
  **只装了桌面版也必须能被发现**——这是 2026-08-18 修的那个 bug：装出来的
  `Magplot.exe` 与 sidecar 都是 GUI 子系统可执行文件，没有真终端时
  `sys.stdout is None`、输出被 `entry.py` 改道进 app.log，**调用方拿到的是空
  stdout**。所以 `packaging/magplot.spec` 从同一个 Analysis 多出一个
  `console=True` 的 `magplot-cli`（共用 `_internal/`，只多 ~1.5 MB）。
  **别把 GUI exe 当 CLI 调**，哪怕它接受同样的参数。
  安装清单落在**用户配置目录**（安装目录可能只读、卸载会被删）：安装器装完跑
  `magplot-cli doctor --json --write-manifest`（让 CLI 自己写，NSIS 不拼 JSON），
  应用每次启动 `locate.refresh_manifest()` 刷一遍（**只补充不抹掉**：pip 装的
  那份是非冻结进程、只去惯例位置找壳，无条件写下去会把桌面版记的非惯例路径
  抹成空，一次 `magplot --figures …` 就够），卸载器在**删文件之前**移除。
  读的一方要核实里面的路径还在——清单是缓存不是真相。**任何单一机制都不是
  唯一依据**（清单可能没写成、注册表可能被策略锁住），也**不动用户 PATH**。
  `sidecar/Magplot` 这一段的出处只有 `tauri.conf.json` 的 `bundle.resources`，
  Rust 壳 / locate / NSIS 三处同源。协议与错误码全文在 `docs/handoff-protocol.md`。
- **子命令在 `engine/cli.py`（`open` / `doctor`）**，`app.main()` 与
  `packaging/entry.py` 都先问它一句。分派**必须在 import Flask 之前**：一次交接
  用不上任何 HTTP 端点，冻结产物却要为它付整个 Flask 的冷启动。
- **`HandoffError` 一律带稳定 `code`**（`registry_write_failed` /
  `path_not_found` / `launch_failed` …），`--json` 失败也输出一行 JSON。
  文案随时可改，code 不行——调用方按它分诊。裸抛的那条由
  `test_every_handoff_error_carries_a_code` 挡住。
- **入口是 `magplot open <产物|脚本|目录>`**（`engine/handoff.py`，纯标准库）：
  解析目标 → 登记 stem → 唤起界面。子命令在 argparse **之前**分派——主入口是纯
  flag 形态（`magplot --figures …`），改成 subparsers 会把既有命令行整个换掉。
  项目 = 含 `mm_registry.json` 的那一层（向上找 ≤3 层，**有上限**：静默把上层目录
  当图库会把一整棵源码树当素材扫）。注册表合并复用 `discover.merge`，
  **不另写裁决**；读不懂就报错，绝不重写用户手写的注册表。
- **桌面契约是 argv `--open <目录> [--stem <stem>]`**：生产者唯一
  `handoff.desktop_argv()`，消费者唯一 `src-tauri/src/main.rs::parse_open_args()`，
  两侧各有单测，改一边必须同步另一边。macOS 上**直接 exec 包内二进制**——App
  已在跑时 `open -a … --args` 送不到，交接会静默失败。
  首启：项目 → sidecar 的 `--figures`，stem → 落地 URL 的 `?open=`；
  已开着窗口：单实例转发 argv → emit `magplot:open`。两条路汇进前端同一个
  `lib/openRequest.ts`（浏览器模式共用 `?open=`，定位逻辑只有一份）。
- **前端交接三条纪律**（`applyOpenRequest`）：① 同项目**绝不**调
  `projectStore.open`（那条路 switchDocument 成空白文档，用户排的版当场没）；
  ② 必须重扫素材（交接的图刚写到磁盘，实例手里那份 panels 是旧的）；
  ③ 找不到就说找不到，绝不退而求其次选别的面板。重复交接同一张只选中，不叠第二份。
- **Codex 插件在 `codex-plugin/`**，市场清单在仓库根 `.agents/plugins/marketplace.json`
  （仓库即市场根）。**已不再是 skills-only**：2026-08-18 起同时带一个本地 stdio
  MCP server 与内嵌画布（见下面「Codex MCP server 与内嵌画布」一节与 ADR 0006）；
  交接这条路一字未改。**仍不做 `.app.json`**（需要 OpenAI 侧注册的托管 App id）。
  pyproject 的 `exclude` 显式挡住 `codex-plugin/` 进 wheel/sdist。插件版本 ==
  `magplot.__version__`（`tests/test_codex_plugin.py` 看护）。
- **插件里那份路径规则是 `engine/locate.py` 的镜像**（插件 import 不到 magplot，
  这份重复无法避免）。能避免的是两边悄悄漂开：
  `tests/test_install_locate.py::test_plugin_mirrors_the_locator` 在
  Windows/macOS/Linux × 有无环境变量 × 空格与中文的矩阵上逐条比对两侧输出，
  改一边必须同步另一边。两侧都**一个 pathlib 都不用**（`Path()` 按 `os.name`
  分派，在 macOS 上连构造一条 Windows 路径都做不到）。
- **插件自己的更新检查在 `codex-plugin/.../scripts/update_check.py`**：
  每 24 小时一次（失败 1 小时后可重试）、1.5 秒超时、缓存落
  `config_dir()/codex-plugin-update.json`（**绝不往插件目录写**——那儿归 Codex
  管、可能只读、升级时整个被换掉）。四条底线：不阻塞出图、**不污染 stdout**
  （调用方读的是最后一行 JSON）、不自动下载执行、**插件版本 ≠ Magplot 版本**
  （当前版本只从 plugin.json 读，`min_magplot_version` 比的是 `magplot open`
  回报的那个版本）。清单由 `scripts/make_plugin_manifest.py` 在 **release.yml**
  生成——**不能挪进 desktop-tauri.yml 的 updater-manifest**，那个 job 没配
  minisign 私钥就整个跳过，插件的更新通道会跟着悄悄停而且全绿。
- **技能的第一条硬约定：脚本与产物同目录、且必须先落成文件**（禁 `python -c` 出图）
  ——「stem ↔ 产出它的脚本」是图能不能双击进去改的全部依据。自检不靠祈祷：
  `scripts/handoff.py` 读 `magplot open --json` 的 `registry.parameterizable`，
  为 false 时**退出码 4**。图出来了但只是死图，那不是成功。

## 出版规范 profile 与预检（2026-08-18）

- **规则的唯一权威文件是 `src/magplot/profiles/publication.json`**（随 wheel 分发）。
  Python 走 `engine/profiles.py`（`importlib.resources` 定位，装成 wheel 后源码树的
  相对路径不存在），TypeScript 经 **`@profiles` 路径别名**整份 import 进 bundle
  （`web/vite.config.ts` / `vitest.config.ts` / `vite.mcp.config.ts` **各配一次**）。
  **绝不在任一侧硬编码同一条规则**——旧代码里 `preflight.ts` 的 6pt/300dpi 与
  ExportDialog 的 85/150/180mm 就是各写一份，规范一改两处同时开始撒谎。
- **预检有两个求值器**（`engine/preflight.py` 给 MCP，`web/src/lib/preflight.ts` 给
  画布与导出对话框）——浏览器跑不了 Python，这是必需的第二份，不是重复。
  两份靠 `tests/golden/preflight_vectors.json` 对齐：**pytest 与 vitest 各跑一遍同一份
  向量**（与 patchspec ↔ Rust 同一套纪律），只比 `id/severity/object_ids/gids/detail`，
  不比中文措辞。改任一侧跑 `python scripts/gen_preflight_vectors.py --write` 并人工读 diff。
- 输入是**规范化的 figure spec**（页面 + 面板 + 文字 + 对象几何），不是画布文档也不是
  manifest 本身：同一套规则同时服务「一张图」（MCP，scale=1）与「多面板拼版」
  （画布，每面板带自己的 scale）。
- **字号按最终物理尺寸判**：manifest 的 `fontsize` 是脚本坐标系里的 pt，面板缩到 60%
  时读者量到的是 `fontsize × scale`。只看原始值 = 「缩一缩就放行」。阈值 8.5pt（严格）
  与 8.0pt（绝对下限，**正好 8.0 不算过**）。
- 四档：`error`（默认阻止导出，显式确认才放行且写进 proof）/ `warn`（放行必展示）/
  `not_verifiable`（**查不了**，如位图内部文字，需人工确认并写进 proof）/ `suggestion`
  （数据语义类全在这档，**绝不替用户裁决**）。**没登记的检查项兜底为 warn**，
  刻意不是 suggestion——忘了登记会让用户以为它通过了。
- 文档里**只存 `{id, journal}`，不存规则**（`FigureDocument.profile`，可选，schema 仍是 2）。
  期刊自定义走覆盖（浅合并 + 几个子对象深合并），结果带 `derived_from`/`journal` 并进
  proof report。整套换掉用 `MAGPLOT_PROFILES_FILE`。
- 导出目录规则收在 `engine/config.project_export_dir(project, fallback)` —— Flask 与
  MCP server 都调它（`fallback` 是参数不是常量：app 的 `EXPORT_DIR` 会被测试 monkeypatch）。

## Codex MCP server 与内嵌画布（2026-08-18）

完整版在 `docs/adr/0006-codex-mcp-app-and-publication-profile.md`，改动前先读。
ADR 0005 的「skills-only / 不做 MCP server」这一条**已被它推翻**（交接那条路不变）。

- 插件清单加 `"mcpServers": "./.mcp.json"`；`.mcp.json` 是**本地 stdio**
  （`command: python3` + `args: ["./mcp/server.py"]` + `cwd/env_vars/tool_timeout_sec`）。
  字段形状取自 Codex 官方插件装出来的清单，**不要猜**。
- **`codex-plugin/mcp/magplot_mcp/` 只翻译不实现**：会话、manifest、override、patch 规范化、
  导出全部落回 `magplot.engine.{pool,registry,handoff,patchspec,profiles,preflight}`。
  发给 worker 的 patches 与 Flask `/api/engine/render` 走同一条路径，所以 ADR 0003 的
  等价性不变式原样成立（`tests/test_mcp_roundtrip.py` 用真 matplotlib + 真 stdio 逐条验：
  热态 == 全新 worker 重放、figure 尺寸变、axes 几何变、关掉重开）。
- **stdout 归协议独占**：`rpc.hijack_stdout()` 把 `sys.stdout` 改道到 stderr，**必须先
  存下真正的 stdout 句柄**（`_REAL_STDOUT`）。顺序反了协议帧全写到 stderr 上，症状是
  「initialize 永远等不到响应」且零报错（开发期真撞到过）。
- **路径范围**：`MAGPLOT_MCP_ROOTS`（缺省进程 cwd），越界一律拒，**绝不「就近找一个
  能用的」**。**没装 Magplot 时降级而不是退出**（降级 server 握手正常、每个工具说人话）
  ——静默退出在 Codex 里表现为「插件没有工具」。
- **导出先预检**：有 error 且没有 `explicit_confirm` 时一张图都不出；强制导出记进 proof。
- **内嵌画布 = Magplot 前端那一份代码**（`CanvasStage`/`OverlaySvg`/`interactions.ts`/
  `ElementInspector`/既有 stores），拖拽、命中、吸附、undo、patch 状态**没有第二份实现**。
  唯一改动是 `web/src/lib/engineTransport.ts`：一个**可选覆盖**（HTTP ↔ `tools/call`）。
  它**不 import `lib/api`**——搬默认实现进去会与 api 绕成环（TDZ），而且既有单测大量
  `vi.mock('@/lib/api')` 打桩 `engineRender`，实测会炸 7 个文件。
- UI 只挂在 `magplot_open_figure` / `magplot_apply_overrides` 上（其余工具的产出是文字与
  文件，挂 UI 只会让画布不停重建）；CSP 的 `connectDomains` **是空的**（sidecar 端口动态，
  写不进白名单，这也是必须走 `tools/call` 的原因）；**绝不用「开浏览器」冒充内嵌画布**；
  iframe 的 `localStorage`/`widgetState` **不存业务数据**。
- 画布产物 `codex-plugin/mcp/widget/canvas.html` 是**受管构建物**（进 git）：
  `python scripts/build_mcp_widget.py`，`--check` 在 CI 的 frontend job 与 pytest 里各看一道。
  **改了 `web/src` 就得重跑**，否则用户装到的是上一版画布（功能全在、只是旧、零报错）。
- **Codex Desktop 里的 iframe 渲染尚未实测**——协议层与画布逻辑都有自动化看护，
  但「真桌面应用把这块 HTML 跑起来」这一步没验过，README 里如实写着。

## AI 桥

- `POST /api/ai/run` → spawn `codex exec`（默认）或 `claude -p`，cwd=figures 目录；
  修改前快照到 `cache/ai_snapshots/`，结束后 diff 经 SSE `ai.done` 推送；
  revert 恢复快照。脚本被改后 mtime watcher 自动作废渲染会话。
- `GET /api/ai/capabilities` 实测本机 CLI（安装/版本/模型/推理强度，两家不同构：
  claude 无强度选项就不给）；CLI 路径可经 `PATCH /api/ai/settings` 指定，
  **不允许硬编码私人路径**（pytest 看护）。run 可带 model/effort。
- **CLI 子进程一律用 `_spawn_env()` 的增强 PATH**（探测与运行同一份）：
  桌面壳从 Finder / 开始菜单启动时继承 GUI 的最小 PATH，npm shim 的
  `#!/usr/bin/env node` 解析不到 node（`env: node: No such file or directory`）
  ——把 CLI 所在目录 + 常见安装目录补到 PATH 末尾即可，不改用户已有排序。
- **CLI 探测（Windows 尤其）**：`_search_dirs()` 在 PATH 之外把 npm 全局、
  `%LOCALAPPDATA%\Microsoft\WindowsApps`（**商店版 codex 的执行别名——真身在
  受 ACL 保护的 WindowsApps 包体里，只能走这个入口**）、WinGet/scoop/choco/
  bun/volta 全翻一遍；找不到时 capabilities 回 `searched` 告诉用户找过哪儿。
  npm 装出来的 `codex.cmd` 外壳经 `_resolve_shim()` 解析成真正的 exe/node 脚本——
  经 cmd.exe 中转会吃掉提示词里的 `%`、`&`、`^`、`<`、`>`、`|`（中文提示里
  写个「透明度调到 50%」就够出事）。路径拼接一律用字符串，不用 pathlib
  （`os.name` 一变 Path 就分派到另一半实现，连跨平台测这段都做不到）。
- **第三方 API 接入（`engine/ai_providers.py`）**：claude 走 `ANTHROPIC_BASE_URL`/
  `ANTHROPIC_AUTH_TOKEN` 环境变量，codex 走 `-c model_provider=magplot` +
  `[model_providers.magplot]` 临时覆盖 + `MAGPLOT_CODEX_API_KEY`。
  **一律 spawn 时注入，绝不改写用户的 `~/.claude/settings.json` 或
  `~/.codex/config.toml`**（cc-switch 是改文件的，那对它合理，对我们是越权：
  用户在别的终端里跑 claude/codex 必须还是他自己那套）。密钥存用户配置
  （目录权限收到 0700），接口一律只回「有没有 + 尾四位」。
- **codex 模型清单读 `$CODEX_HOME/config.toml`（默认 `~/.codex`）的 `model` +
  `[profiles.*].model`，绝不在源码里写模型名**——OpenAI 换代后旧名会被服务端
  直接拒收（400 `The 'gpt-5' model is not supported when using Codex with a
  ChatGPT account.`）；读不到就交白卷，让 CLI 用自己的默认值。强度档位
  minimal/low/medium/high/max 是 CLI 侧开关，与模型清单无关。
  前端 `aiStore.prunePrefs` 在每次探测后丢弃 localStorage 里已失效的选择，
  否则用户会永远卡在一个报错的旧模型上（vitest + pytest 双侧看护）。
- 会话历史在 `engine/ai_history.py`（SQLite `cache/ai_history.sqlite3`，
  按 project 列过滤）；启动时 running→interrupted + purge(180d，pinned 豁免)；
  历史 API：list（分页/搜索/筛选）/delete/pin。前端默认只显示人类可读目标，
  脚本名收在「技术详情」。

## 文字：行内上下标与大小写

- **画布标注**用行内标记 `^{…}` / `_{…}`（`web/src/lib/richText.ts` ↔
  `src/magplot/richtext.py` **严格同源**：`SCRIPT_SIZE/SUP_RISE/SUB_DROP`
  三个常量与 parse 规则改一边必须同步另一边，pytest 用真实 PDF 的字形
  字号与基线做几何级看护）。只有 `^{`/`_{` 才触发，正文里孤立的 `^`/`_`
  原样显示；`\^`、`\_`、`\\` 是转义。序列化**按需转义**——无脑加反斜杠会让
  用户点一次「大小写」正文里就凭空多出 `\`。
- **图内元素文字**走 matplotlib mathtext（`cm$^{-1}$`），不是上面那套标记；
  大小写转换要 `protectMath`（`$…$` 里是 `\alpha` 这类命令，改大小写就废）。
- 大小写是**一次性动作**，直接改文本内容（可撤销），不新增字段、导出零改动。

## 诊断与排障

- `engine/diagnostics.py` 出**一键诊断包**（`GET /api/diagnostics/bundle`）：
  版本 / 系统与编码 / 安装方式 / 数据目录 / 渲染解释器 + matplotlib /
  AI CLI 探测 / 项目概况 / 最近错误 + app.log + 用户配置。
  **密钥与个人路径必须先脱敏再交出去**（用户会把它贴进 issue 或发到群里）。
- 「写回原始文件」撞上 Windows 独占锁（PDF 被阅读器打开）回 409
  `code=file_locked`，带上是哪个文件、回滚结果如何，并清掉 `.updating`
  半成品——不接住的话用户拿到 500 + traceback，图库里还多个垃圾文件。
- **写回是个事务（`_write_source_files`，数据损坏级）**：不变式是
  **热态所见 == 写进文件的 == 重开后重放出来的**，三段
  prepare → verify → commit，任一环不过一律 409 且**原文件零改动**。

  * **prepare**：请求可带 `expected_mtime`（前端 assetStore 里该素材的
    mtime，只比**点名的那一份**——同 stem 的另一载体客户端没有 mtime，硬比
    必然误报），对不上回 409 `source_changed`；worker 在 spawn 时记下脚本的
    `script_sha1`（`pool.script_sha1()`，两条控制面同源），写回前重算，
    对不上回 409 `script_changed`——watcher 轮询 2 秒，那个窗口里热会话跑的
    还是旧代码，而写回是覆盖原件的动作。
  * **verify**：staging 的 PDF/PNG **由 `pool.one_shot()` 起的一次性 worker
    全量重放产出，不用热会话**。热会话是增量的，「现在的样子」未必等于
    「从零按这组 patches 重放一次的样子」（FigS3 事故就是这个差）。一次性
    worker 不进 `_workers`，目录独立（`cache/engine/_replay-…`，登记进
    `_oneshot_bases` 免得被 prune 删掉），workerd 那边靠独立 out_dir +
    `MAGPLOT_REPLAY_NONCE` salt env 绕开 spec 哈希复用，用完 `discard()`。
    同一次写回只 build 一次（override + PDF + PNG 共用）。
    热会话最后应用的正是这组 patches 时（`worker.last_patch_hash`），把两份
    manifest 逐元素比 bbox/anchor（容差 0.5% figure 分数）与 size_mm（0.01mm），
    有分歧回 409 `replay_divergence` + 分歧清单。**热态不是这组 patches 就
    不比**（历史恢复、跨面板同步都是），响应据实回 `replay: "fresh_only"`
    ——假报一次，用户学到的就是「这个提示可以无视」。manifest 经 JSON 落盘，
    numpy 标量可能被 `default=` 写成字符串，比之前一律 `float()` 化。
    worker 的 warnings（元素不存在 / 属性不支持 / 应用失败 / 还原失败）
    **一条即阻断**，回 409 `code=write_back_warnings` + warnings 列表。
    staging 阶段**任何异常都要 unlink 掉所有 `.updating` 临时文件**
    （以前只有 file_locked 那条路径清理，PDF 成功 PNG 失败就留垃圾）。
  * **commit**：备份 → 逐个 `tmp.replace(target)`。第 2+ 个撞锁时**把已经
    换掉的从本次备份恢复回去**（PDF 新 / PNG 旧比整件事失败糟糕得多），
    响应带 `rolled_back` / `rollback_failed`，`updated` 的语义是「仍处于已被
    换掉状态的文件」（回滚成功即为空）。落盘后用 `probe_asset` 比页面尺寸与
    重放 manifest 的 size_mm（容差 0.5mm），不符只记 ERROR + 响应
    `post_check: "size_mismatch"`——**此时不再自动回滚**，文件已换、备份仍在，
    如实报告就是最有用的。
  * 成功响应（update_source 与 history/restore 同构）：`updated` /
    `backup_dir` / `warnings: []` / `patch_hash`（patchspec 权威）/
    `source_sha1` / `manifest_hash`（canonical JSON 的 sha256）/
    `verification`。baked 版本条目同样带 `patch_hash`（旧条目无该键，读取兼容）。
  * 代价要认：每次写回都多跑一遍脚本（heavy 的分钟级）。这是正确性优先的
    自觉取舍，**不许为省时间跳过 verify**。
  * `/api/export`（画布合成）同样收集 warnings，但**只透出不阻断**——成图
    已经出来了，前端在结果区列出。
  * 看护：`tests/test_write_back.py`（假 worker，全部分支）+
    `tests/test_worker_roundtrip.py` 末节（真 matplotlib + Flask 全链路，
    含 workerd 路径的一次性会话不泄漏）+ `web` 的 `WriteBackDialog.test.tsx`。

## 验证

- 测试：`.venv/bin/python -m pytest`（tests/ 跑在 .venv；worker round-trip
  用例自行 spawn 科学栈解释器，无 matplotlib 则跳过）。
- **四路等价性矩阵**（`tests/test_equivalence_matrix.py`，引擎的最终验收物）：
  `hot_apply(patches) == 清空后全量重放 == 全新 worker 重放 == 写回文件后全新
  worker 重放`，六个场景 × 十组 patch，判据直接复用 `app._compare_manifests`
  （与写回放行/阻断同一把尺）。四条腿各起独立 worker，核心场景在 workerd 控制面
  再走一遍。缺 matplotlib / 缺 CJK 字体各自 skip 并注明理由。
- **端到端冒烟**：`python scripts/smoke_app.py --python .venv/bin/python`
  （或 `--exe dist/Magplot/Magplot.exe`）。隔离用户目录 → **渲染环境自检** →
  打开项目 → 渲染 → 导出 → 覆盖导出 → **干净退出**（走 `/api/shutdown`，需
  `MAGPLOT_ALLOW_SHUTDOWN`；退出后断言没有残留 worker 子进程）。
  `--expect-source bundled` / `--expect-packages numpy,pandas,…` 是 Windows 桌面版
  的核心验收：少了它，一台碰巧装着 matplotlib 的 CI 机器会让「内置 runtime 根本
  没打进去」全程绿灯。CI 的 windows-exe-smoke 与 nightly 共用它。
  验收项目在 `examples/runtime_check/`（一个把整套内置科学栈都用一遍的脚本）。
  `--expect-control-plane workerd` 同理盯另一件静默失灵：桌面产物必须自带
  Rust supervisor（`build_desktop.py` 先 cargo build，`magplot.spec` 收进
  `_internal/`），少了它渲染回退到 Python 池——功能全在、只是慢、零报错。
  两条冒烟腿都**不设 `MAGPLOT_WORKERD`**：要验的正是自动发现。
- **nightly 的安装链路（`nightly.yml`，每晚一次）**：三档代表性环境
  （无 Python / 官方 Python / Conda）× 中文用户名 + 中文区域 + cp936。
  冒烟项目**按档给**——`examples/runtime_check` 要整套科学栈，只有内置 runtime
  满足；指向用户自己解释器的两档用 `examples/figures`（numpy + matplotlib），
  它们验的是解释器优先级与中文路径。「无 Python」那档还会现打一个 NSIS
  安装器，走**装一遍再冒烟**：静默安装 → 断言安装目录里有 sidecar + 内置
  runtime + workerd → 起真壳确认它能拉起 sidecar 且退出不留孤儿 → 对装出来的
  sidecar 冒烟 → 覆盖安装（升级）再冒一次 → 静默卸载。这条链路只有真装一遍
  才知道，而且必须挂在**在发的那个发行形态**上：它一度判的是早已退役删除的
  `packaging/magplot.iss`，于是每晚只打一条 notice 就过——**空转的门禁比没有
  门禁更坏**，它还在报平安。
- **黄金路径 E2E**：`cd web && pnpm e2e`（Playwright，`MAGPLOT_EXE` 指打包产物、
  缺省用 `python -m magplot`）。跑之前先 `python scripts/build_frontend.py`——
  包内 `src/magplot/web/` 优先于 `web/dist`，只跑 `pnpm build` 测的还是旧界面。
- **Windows 回归**：`tests/test_windows_regressions.py`。约定是
  **每个「只在别人电脑上发生」的 bug 先变成这里的用例再谈修**（cp936 编码、
  文件占用、盘符/反斜杠/中文路径、端口占用、CLI 只有 .cmd、解释器探测）。
- 后端冒烟（示例项目）：`magplot --figures examples/figures --no-browser` 后
  `curl -X POST /api/engine/render -d '{"id":"Fig1_kinetics.pdf","patches":[]}'`
- **Codex 插件**：`.venv/bin/python -m pytest tests/test_mcp_server.py
  tests/test_mcp_roundtrip.py tests/test_codex_plugin.py tests/test_preflight.py`；
  画布产物 `python scripts/build_mcp_widget.py --check`（改了 web/src 就得重跑构建）；
  预检向量 `python scripts/gen_preflight_vectors.py`（`--write` 重新生成后人工读 diff，
  并让 `cd web && pnpm test` 也绿——两个求值器跑的是同一份向量）；
  MCP 手动冒烟 `python codex-plugin/mcp/server.py --self-check`。
- **性能基线**：`python scripts/bench_render.py --python .venv/bin/python`
  （真 HTTP 链路、冷启动/热 override 中位/导出、两条控制面对照）。结论与前后对照
  都写进 `docs/perf-baseline.md`——**改性能前先在那儿指出一个数字**。
  注意它**默认不隔离 HOME**：重置 HOME 会让每次冷启动多出 9 秒的 matplotlib
  字体缓存重建，盖掉所有别的数字（要量首次体验用 `--fresh-home`）。
- 导出保真：导出 PDF 用 pymupdf `get_text()` 验证矢量文字。
- 前端（web/）：`pnpm test && pnpm build`；界面用 agent-browser 实测。
  **别用 `tsc --noEmit` 当类型检查**：根 tsconfig 是 `files:[]`+references 的方案文件，
  `--noEmit` 不走项目引用、什么都不编、恒假绿；`pnpm build` 里的 `tsc -b` 才是真检查。
  跑过 `scripts/build_frontend.py` 之后包内 `src/magplot/web/` 优先于 `web/dist`，
  改完前端要么再同步一次，要么把它删掉退回开发态。
- 引擎改动后重启服务：
  `lsof -ti:5089 -sTCP:LISTEN | xargs kill; ./run.sh --no-browser`
  （不加 `-sTCP:LISTEN` 会连浏览器的连接进程一起列出来）。

## UI 视觉纪律（web/）

暖灰白 `#F2F2EF` 底 + 白色 surface；层级靠留白 / 字号 / 轻微背景差，
边框只给真实输入框、区域边界、选择状态与浮层。**持久表面不用 shadow，
浮层（菜单/popover/dialog/tooltip）可使用唯一轻投影 `--shadow-pop`**。
radius：控件 6px、浮层 10px、上限 14px。UI 字号 11-14px；控件高 28px、
树行高 28px、图标点击区 ≥28px。主按钮近黑色（`bg-ink`）；蓝色只用于
选择 / 焦点 / 链接；每个上下文最多一个填色主动作（顶栏=导出、助手=发送、
弹窗=确认）。文字对比：`ink-2`/`ink-3` 均 ≥4.5:1，`ink-faint` 仅装饰 / 禁用。
选中态不只靠颜色（左侧 2px 竖条 / check / 形状变化）。支持
`prefers-reduced-motion`。Document 字体（Times）与 UI 字体严格分离。

工作台结构：顶栏 44px（左=品牌/文档名/autosave，中=撤销重做+工具，
右=缩放/导出/更多）；左侧 44px 常驻图标轨道（素材/结构/图内元素）+
280–360px 上下文抽屉（再点收起）；右栏 296–320px 三模式（属性/改图助手/
画布），无选择且未钉住时不占位；断点 ≥1440 双栏可钉住、1024–1439 左右
互斥、<1024 覆盖式抽屉。底部无常驻状态栏：坐标/选区尺寸只在拖动中出现，
普通状态走短暂 toast，错误常驻可关，autosave 显示在顶栏文档名旁。
