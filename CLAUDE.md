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
  conftest 已全局隔离）：cache / layouts / exports / baked_overrides.json /
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
- **桌面模式下 Python updater 停用**（升级归 Tauri 层），`/api/update/*` 回
  禁用响应；浏览器模式照旧。
- 构建：`python scripts/build_desktop.py`；验收：`python scripts/smoke_desktop.py
  --sidecar dist/Magplot/Magplot`（真产物全链路：认证/项目/渲染/导出/退出无孤儿）。
  CI 在 `desktop-tauri.yml`——v0.3.0 起是唯一桌面发行链（旧 `desktop.yml`/
  Inno Setup/免安装 zip 已退役删除，git 可找回）；Windows NSIS 自带内置渲染
  runtime，桌面产物一律真窗口、不再有「启动后开浏览器」的形态。
- wheel/sdist 不含 `src-tauri/`（hatchling 白名单）；`src-tauri/target/`、
  `src-tauri/gen/` 进 .gitignore。

## PDF 后端边界（许可证相关，勿破坏）

- `src/magplot/pdfbackend/pymupdf_backend.py` 是**全仓库唯一** import pymupdf 的
  模块；`__init__.py` 是与实现无关的契约层（probe_asset / render_preview_png /
  text_width / compose + mm2pt / hex2rgb）。`app.py` 只认这些名字。
- 为什么在意：PDF 库是可替换的实现细节，收敛成单一模块后换后端只需重写这一个
  文件，上层零改动。**别在 app.py 或别处新写 `import pymupdf`**——那会把这条
  边界废掉。许可证说明见 `docs/LICENSING.md`。
- 面板的项目路径解析与引擎重渲染留在 app 层的 `_resolve_panel_source` 回调里，
  后端只管画。几何公式仍与前端严格同源，pytest 用 get_drawings() 做几何级看护。

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
- 安装方式探测：包上两级有 `pyproject.toml` = source（只提示 `git pull`，
  绝不在源码树里跑 pip 覆盖用户工作副本）；`sys.prefix` 含 pipx = pipx；否则 pip。
- 升级目标优先取 Release 里的 `.whl` 资产 URL（没发 PyPI 也能升），
  退回按包名装。

## 渲染引擎核心机制

- **live-figure 会话**：worker 跑一次脚本（拦截 `Figure.savefig` + `paper_style.save`，
  不写真实文件），Figure 常驻内存；override 直接 mutate artist 再导出带 gid 的
  SVG（dpi≈120 预览）——冷启动秒到分钟级，热态 ~40ms。
- override 是**全量列表**语义：worker 维护 applied/originals 两表，缺失的 key 自动
  恢复原值（undo 的基础）。前端永远发完整 `o.overrides`。
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
  SSE 事件带 `pj`，前端只处理属于本标签页项目的那些。
- **schema 3**：`ProjectDocument{project, canvases[], activeCanvasId}`；
  运行时激活画布仍是 schema 2 形状的 `documentStore.doc`（画布编辑代码零改动），
  持久化/读档统一走 `migrateToProject()`（接受 2/3）。画布切换换入换出
  undo 栈（canvasSessions）与 UI 会话（`store/canvasSession.ts`）。
  标签页 openTabs 按 documentId 存本机。后端 versions/package 接受 schema 2/3。
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
- **空状态**：一律用 `components/ui/EmptyState`（图标+短标题+≤1 句+≤1 动作）。
- 前端测试：`cd web && pnpm test`（vitest+jsdom；NODE_OPTIONS 里禁用了
  node 内建 webstorage，否则 jsdom localStorage 被遮蔽）。

## AI 桥

- `POST /api/ai/run` → spawn `codex exec`（默认）或 `claude -p`，cwd=figures 目录；
  修改前快照到 `cache/ai_snapshots/`，结束后 diff 经 SSE `ai.done` 推送；
  revert 恢复快照。脚本被改后 mtime watcher 自动作废渲染会话。
- `GET /api/ai/capabilities` 实测本机 CLI（安装/版本/模型/推理强度，两家不同构：
  claude 无强度选项就不给）；CLI 路径可经 `PATCH /api/ai/settings` 指定，
  **不允许硬编码私人路径**（pytest 看护）。run 可带 model/effort。
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
  `code=file_locked`，带上是哪个文件、已经换掉了哪些，并清掉 `.updating`
  半成品——不接住的话用户拿到 500 + traceback，图库里还多个垃圾文件。

## 验证

- 测试：`.venv/bin/python -m pytest`（tests/ 跑在 .venv；worker round-trip
  用例自行 spawn 科学栈解释器，无 matplotlib 则跳过）。
- **端到端冒烟**：`python scripts/smoke_app.py --python .venv/bin/python`
  （或 `--exe dist/Magplot/Magplot.exe`）。隔离用户目录 → **渲染环境自检** →
  打开项目 → 渲染 → 导出 → 覆盖导出 → **干净退出**（走 `/api/shutdown`，需
  `MAGPLOT_ALLOW_SHUTDOWN`；退出后断言没有残留 worker 子进程）。
  `--expect-source bundled` / `--expect-packages numpy,pandas,…` 是 Windows 桌面版
  的核心验收：少了它，一台碰巧装着 matplotlib 的 CI 机器会让「内置 runtime 根本
  没打进去」全程绿灯。CI 的 windows-exe-smoke 与 nightly 共用它。
  验收项目在 `examples/runtime_check/`（一个把整套内置科学栈都用一遍的脚本）。
- **黄金路径 E2E**：`cd web && pnpm e2e`（Playwright，`MAGPLOT_EXE` 指打包产物、
  缺省用 `python -m magplot`）。跑之前先 `python scripts/build_frontend.py`——
  包内 `src/magplot/web/` 优先于 `web/dist`，只跑 `pnpm build` 测的还是旧界面。
- **Windows 回归**：`tests/test_windows_regressions.py`。约定是
  **每个「只在别人电脑上发生」的 bug 先变成这里的用例再谈修**（cp936 编码、
  文件占用、盘符/反斜杠/中文路径、端口占用、CLI 只有 .cmd、解释器探测）。
- 后端冒烟（示例项目）：`magplot --figures examples/figures --no-browser` 后
  `curl -X POST /api/engine/render -d '{"id":"Fig1_kinetics.pdf","patches":[]}'`
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
