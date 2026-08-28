# src/tavotto/ — 后端与渲染引擎规则

仓库级路由、跨仓库不变量与验证命令在根 `AGENTS.md`。本文件覆盖 Flask 应用、
渲染引擎（`engine/`）、PDF 后端、写回、编码 Agent 桥、遥测、出版规范与外部交接。
前端规则在 `web/AGENTS.md`；插件与 MCP server 在 `codex-plugin/AGENTS.md`；
打包与内置 runtime 在 `packaging/AGENTS.md`。

## 品牌与命名

产品名 **Tavotto**（拼写大小写固定）。品牌与格式常量唯一出处：
`web/src/lib/brand.ts`、`engine/brand.py`——界面/导出格式不得手写产品名。
对象层级 Project / Canvas / Tab / Object 见
`docs/adr/0001-project-canvas-tab-object.md`。

**2026-08-20 从 Magplot 改名为 Tavotto，选的是干净断裂**：`magplot-package` /
`.magplot` / `magplot-proof` / `magplot/objects@1` / `magplot.*` 的 localStorage 键
一律不再认，Magic Matplot 时代那一档 `LEGACY_*` 也一并删了——只认两代前的名字、
却不认上一代的，比干净断裂更难解释。`brand.py` / `brand.ts` 因此**没有
LEGACY_ 常量**，别照着旧模式再加一档。两个例外都在 mm 前缀那一族，它们指的是
**用户自己磁盘上的东西、我们改不到**：图库里的 `mm_registry.json`（读取端回退，
唯一判据 `registry.existing_registry_path()`，写出永远新名）与用户 shell 里的
`MM_WORKER_PYTHON`（读取端回退，唯一判据 `pool.worker_python_env()`）。
文档 schema 的迁移（`migrateToProject`，接受 2/3）与品牌无关，照旧。
桌面标识符换成了 `com.tavotto.tavotto`：存量 0.7.0 桌面版**不会原地升级**，
发版说明里要写明先卸载旧版。

论文 Figure 排版 + 参数化图表编辑工具。Flask 后端（`src/tavotto/app.py`）+
PyMuPDF（**只经 `src/tavotto/pdfbackend/`**），前端 `web/`
（Vite + React 19 + TS + Tailwind v4）；旧 v1 前端已于 2026-08-15 删除（git 可找回）。

## 进程与依赖边界（重要）

- Flask 跑在 `.venv`（只有 flask + pymupdf，**没有 matplotlib**）。
  `engine/registry.py`、`engine/pool.py`、`engine/ai_bridge.py`、`engine/config.py`、
  `engine/updater.py`、`engine/runtime.py` 被 Flask import，
  **必须保持纯标准库**。
- `engine/worker.py`、`engine/manifest.py`、`engine/overrides.py`、
  `engine/figsession.py`、`engine/wireproto.py` 只在执行侧子进程里跑，
  解释器由 `pool.find_worker_python()` 探测（需科学栈；可用 `TAVOTTO_WORKER_PYTHON` 覆盖）。
- `engine/bridge_runner.py` 与 `engine/bridgeboot.py` 跑在**用户自己的解释器**里
  （native bridge，ADR 0020）：**纯标准库、必须在 3.10 上跑得起来、启动阶段
  绝不 import matplotlib**——用户环境的版本我们说了不算，而提前 import
  matplotlib 会抢走用户脚本对 backend 的决定权。
- 运行时可写数据一律走 `engine/config.data_dir()`（`TAVOTTO_DATA_DIR` 可覆盖，
  conftest 已全局隔离）：cache / layouts / exports / baked_overrides/&lt;项目id&gt;.json /
  ai_history.sqlite3 / ai_snapshots 全在那儿。**不要再往包目录或仓库根写东西**
  ——site-packages 不可写，装成 wheel 后会直接崩。

## PDF 后端边界（许可证相关，勿破坏）

- `src/tavotto/pdfbackend/pymupdf_backend.py` 是**全仓库唯一** import pymupdf 的
  模块；`__init__.py` 是与实现无关的契约层（probe_asset / render_preview_png /
  text_width / compose + mm2pt / hex2rgb）。`app.py` 只认这些名字。
- 为什么在意：PDF 库是可替换的实现细节，收敛成单一模块后换后端只需重写这一个
  文件，上层零改动。**别在 app.py 或别处新写 `import pymupdf`**——那会把这条
  边界废掉。许可证说明见 `docs/legal/LICENSING.md`。
- **图内元素的命中判据跟渲染器走，不跟直觉走**（`web/src/lib/pathGeom.ts`）：
  填充用 **nonzero** 缠绕数（实测 matplotlib 3.10.8 + Agg：同向嵌套的中心
  像素是实心的，反向才是洞；even-odd 会让点在填了色的像素上选不中），
  填充路径没有 CLOSEPOLY 时按**隐式闭合**处理，框选**先把选择框裁进 clip**
  再比（否则只与不可见的延长线相交也算命中），命中容差取「可用性容差」与
  **描边半宽**的大者（`stroke_pt` 由 `engine/pathgeom.py` 随几何下发，
  前端不推算）。共线线段必须再比一维区间，否则框选会收走老远的水平/垂直线。
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

## 检查更新

- `engine/updater.py`（纯标准库）：查 GitHub Releases 最新 tag → 与
  `tavotto.__version__` 比 → 按安装方式给升级命令。仓库地址等常量在
  `engine/brand.py`，别处不得手写。
- 默认每天一次、可在设置里关（关了**一个包都不发**）；升级永不静默进行，
  且升级后 `restart_required`（进程内存里还是旧代码）。
- **桌面版走另一条通道**（tauri-plugin-updater）：后端在桌面模式把
  `/api/update/*` 整个关掉。细节见 `src-tauri/AGENTS.md`。
- 安装方式探测：包上两级有 `pyproject.toml` = source（只提示 `git pull`，
  绝不在源码树里跑 pip 覆盖用户工作副本）；`sys.prefix` 含 pipx = pipx；否则 pip。
- 升级目标优先取 Release 里的 `.whl` 资产 URL（没发 PyPI 也能升），
  退回按包名装。

## 渲染引擎核心机制

- **Figure 捕获策略是共享语义（`engine/figcapture.py`，2026-08-21）**：
  桌面 worker 与浏览器 playground **各调一次同一份实现**。三件事只有这一个
  出处：`savefig` 的 stem 怎么取、脚本跑完还活着的 pyplot Figure 怎么补进来
  （去重按 Figure 身份、上限 `MAX_PYPLOT_FALLBACK=8`）、相对路径只读回退。
  * **没有 savefig 的脚本也要捕获**（`plt.plot(...); plt.show()` 是 AI 最常见
    的输出形态）。以前只有 browser.py 有兜底，桌面一张都捕获不到——同一份
    脚本两个入口两个答案，是数据级的分叉。
  * **fallback stem 按「本次捕获里的第几张」编号**（`<脚本名>`、`-2`、`-3`），
    **不按 `plt.get_fignums()` 的 figure 号**：脚本中途 `plt.close()` 过一次
    号就跳，用户的 override 于是挂在一个不存在的 stem 上，表现是「打开是
    空白的，什么都没报错」。
  * build 响应按 stem 带 `source`（`savefig` / `pyplot`）。`pyplot` 的那些
    **没有原始产物**：渲染 / 编辑 / 导出都成立，「写回原始文件」无从谈起
    （面板列表扫的是磁盘产物，因此它们天然不成为可写回的面板——这条结构性
    保证由 `test_compat_capture_parity.py` 看护）。
  * **相对路径只读回退**：worker 的 cwd 在沙盒里（那是**写入**边界），而
    `pd.read_csv("data.csv")` 在 `python figure.py` 下天经地义。只有「只读
    模式 + 相对路径（或**指向沙盒内部的**绝对路径）+ 按真正的 open 会用的那条
    路径判确实不存在 + 换算后仍在图库内」四条同时成立才改指到脚本目录；
    写 / 改 / 删 / 重命名一个字节都不经过它。沙盒**之外**的绝对路径一个都不碰。
    **`builtins.open` 与 `io.open` 两个都要 patch**——它们指向同一个 C 函数
    却是两个独立绑定，`pathlib.Path.read_text` 走的是后者，只补前者会让
    `open("x")` 好使而 `Path("x").read_text()` 报 FileNotFoundError；
    **3.10 还要第三个 patch 打在 `pathlib.Path.open` 上**（那一版
    `_NormalAccessor.open` 在类定义时就绑好了，前两个都够不着它）。
    **只认裸相对路径是不够的**：不少库在 open 之前先 realpath 一下
    （Pillow 10.4.0 的 `Image.open` 就是，12.x 已改回 fspath），回退看到的是
    `<沙盒>/x.png`——CompatBench 的 minimum 档抓到的正是这个。存在性判据
    **必须按真正的 open 会用的那条路径走**，拿沙盒根去拼的话，脚本
    `os.chdir()` 进子目录后自己写出来的中间结果会被无声换成图库里的原件。
  * 浏览器侧**刻意没有**这条回退：playground 是单文件的，相对读报
    `missing_file` 才是对的。桌面的 `entry` 机制同样是超集（浏览器按
    `python figure.py` 跑，只有 `def main():` 而没人调用的脚本在原生 Python
    下也不画图）——这两条差异是**记录在案的**，不是疏漏。
- **统一执行描述与捕获描述符（2026-08-25，ADR 0013/0014）**：
  * 「跑一个脚本」的语义收在 `engine/execspec.py`：safe 档默认值唯一出处
    `safe_spec()`，worker 子进程 argv 唯一出处 `worker_argv()`——
    `EngineWorker.__init__` 与 `_spawn_spec()` 都是它的消费者
    （`test_workerd_pool.py` 对拍 + `test_execspec.py` golden 看护）。
    新入口不得再手拼 entry/cwd/argv。`spec.env` 只存**注入增量**，
    序列化绝不携带整份父进程环境。
  * 每张捕获 Figure 的结构化描述（`CapturedFigureDescriptor`）唯一实现在
    `figcapture`：asset id `runtime:<script>#<stem>`（不透明标识，entry
    刻意不进 id）、`source_fingerprint`（只是 stale hint，别声称覆盖数据
    依赖）、writeback 能力**只能派生不能指定**（pyplot 捕获结构上拿不到
    原件）。worker v1 build 响应与 browser load 响应各带一份 `descriptors`
    （加字段不升版；legacy 信封零改动），probe 原样透传——worker/browser
    的逐字段对拍在 `test_compat_capture_parity.py`。
  * 「什么算一份图产物」唯一出处 `figcapture.ARTIFACT_EXTS`
    （`discover.OUT_EXTS` / `handoff.OUT_EXTS` 是镜像别名）；「stem 的原始
    产物在哪」唯一判据 `figcapture.find_original_artifact`。
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
- 前端渲染态分键与假实时预览（渲染平面 / 历史平面）的规则在
  `web/AGENTS.md`——引擎侧只需知道：render 请求带 `inline_svg` 时 SVG 与
  manifest 必须同一次响应返回；SSE 的 render.started/done 只带 fileId。
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
- **关停必须闭环：`kill()` ≠「进程已经退出并释放了文件」**。`Popen.kill()`
  两个平台上都只是发出请求（POSIX 是 SIGKILL，Windows 是 TerminateProcess），
  调用返回时进程可能还在，它打开的句柄一定还在。`EngineWorker.shutdown()` /
  `force_kill()` 统一走 `_terminate_and_reap()`：**发 shutdown → 等自然退出 →
  超时 kill → 再 `wait()` 一次 → 关 stdin/stdout/log**，每一步有界、幂等、
  不碰模块级 `_lock`。worker 收到 shutdown 就 `raise SystemExit(0)`，**协议上
  不回普通成功信封**——父进程读到 EOF 是预期现象，不是故障，但 EOF 之后仍然
  必须 reap。少了这一步，Windows 上后脚的 `rmtree` 撞 sharing violation
  （merge_group run 32937999297：`_replay-…` 目录残留污染了后面的测试文件）。
  一次性目录的删除走 `_remove_oneshot_tree()`：**不许 `ignore_errors=True`**
  （它把失败变成静默的空操作），撞锁做 0.35 秒封顶的有限退让，最终失败要记
  exact path + 异常 + 尝试次数 + 脚本名。看护：`test_windows_regressions.py`
  的假 Popen 锁窗口三条 + `test_worker_roundtrip.py` 的真 worker exact-base 用例。
  **supervisor 那一侧同一条纪律**：`workerd_client` 的两处 kill（半启动回收、
  shutdown 超时）走 `_kill_and_reap()`，收尸排在「重新 open 同一个日志文件」
  与 `self._log.close()` 之前——workerd 的 stderr 就绑在那个文件上。看护：
  `test_workerd_client.py` 的假 supervisor 两条（每处 call site 各一条，
  合并成一条就抓不到只漏改一处的回归）。
- **应用顺序规范化 + figure 锚定 prop 的重放（2026-08-17，数据损坏级）**：
  `overrides.apply` 按**七档规范顺序**应用（`_apply_rank` 是唯一出处）：
  图幅 size_mm → 色条方向 → 色条 extend → 子图 position → 刻度类型
  （set_[xy]scale 会把 locator/formatter 整套换掉）→ 其余（列表序）→
  刻度定位模型 → 单条刻度文字（冻结整条轴，必须最后）。色条方向必须先于
  extend：方向要拿色条**当前**的矩形反解厚度与间距。跨档的先后不是
  口味问题：顺序一乱，同一组 patch 在热会话与全量重放里会落成两张图。
  刻度类的 prop 还必须**每次都重放**（`_must_replay`）——它们按当前状态重算，
  而 applied 表里的值一个字节没变，走「值没变就跳过」的捷径就会停在旧刻度上；
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
  （test_arrowpatch_endpoints_and_style_roundtrip 看护）。前端交互语义见
  `web/AGENTS.md`。
- 散点 marker 可整体替换（set_paths，首改前缓存原始路径，"original" 还原）；
  图例条目顺序 entry_order（重建型，manifest type="order"）。**图例重建后必须
  `_legend_box.set_offset(leg._findoffset)` 重挂定位回调**，否则导出时图例整块
  消失（ncol 等旧重建路径同修）。散点/扁平线的 bbox 走 `_padded_bbox`
  （PathCollection 用 datalim 换算，零厚度边垫 4px，否则进不了 manifest）。
- **色条方向（2026-08-18）**：**就地**结构改造，不是普通 setter，也不是销毁
  重建。`overrides._cb_reorient` 在同一个 Axes 对象上换 orientation/ticklocation
  → 按 `_cb_place` 重算落位（竖↔横逐位可逆）→ `_reset_locator_formatter_scale()`
  + `_draw_all()` 让 matplotlib 自己重建色带/outline/刻度/xlim,ylim → 把长轴标签
  搬到新长轴（**旧轴那份要清掉**）。`fig.axes` 顺序一个字节不动 → gid 稳定 →
  撤销 / 写回 / 重开全链路照旧。落位参照取 `state.pending` 里**这一次改完之后**
  的宿主 position（只看实况的话，热会话与全量重放会算出两个位置）；用户自己
  摆过色条轴时不动它的落位，交给 position override。翻完要 `invalidate_tick_cfg`
  （locator 被整套换过）并重算 `axes_follow`。色条另有**稳定语义身份**
  `cbar:<宿主 gid>:<序号>`（manifest 的 `colorbar_key`），与 `axes_i.colorbar`
  一起登记在 index 里。
  **两端延伸三角 `extend`**（neither/both/min/max）同样是就地结构改造，两个坑：
  ① `cb._inside` 是按 extend 切出来的那段 boundaries，**只在 `__init__` 里设过
  一次**——只改 `cb.extend` 就 `_draw_all()` 会拿 259 条边界配 256 块颜色，当场
  TypeError，两者必须一起改；② 落位其实由 matplotlib 自己的
  `_ColorbarAxesLocator` 每帧从 `get_position(original=True)` 重算，它顺手把
  `box_aspect` 改成 `aspect*shrink`，却在 extend=='neither' 时**提前 return
  不收回去**——不管这一点的话「开了又关」的色条比从没开过的宽 10%，而且回不去。
  修法是每次改 extend 前把 box_aspect 放回基线（基线在 `ColorbarProxy.__init__`
  即 instrument 时采，那一刻才是脚本原样），做完的落位与原生
  `fig.colorbar(..., extend=…)` **逐位相同**（用例是这么断言的）。翻转之后
  `_colorbar_info['aspect']=False`：落位归我们，locator 不能再按 aspect 反推厚度。
  **色条轴上的 patch 一律不登记成可编辑形状**——延伸三角就是 PathPatch，而且每次
  `_draw_all()` 都被删掉重建。看护 `tests/test_colorbar_orientation.py`。
- **刻度定位走 Locator / Formatter，不是改已经生成出来的 Text**（2026-08-18）：
  刻度标签每次 draw 由 locator 现算、Text 对象现建，改 Text 属性只能靠
  tick_params 持久（字号/颜色/朝向那一档），而「几个刻度、落在哪、写成什么」
  只有 locator 与 formatter 说了算。模型存在**轴对象**上
  （`axis._mm_tick_cfg`，`tick_cfg` / `apply_tick_model` 是唯一出处）：
  major_mode(auto|step|fixed) / major_step / major_values / minor_visible /
  minor_mode / minor_step / format / minor_format。次刻度的格式多一档 "none"
  （不标数字）——**那才是默认**；开了之后 `TickSet.labels` 把次刻度标签也算进
  刻度组，否则那一排点不中、对齐也对不准。**没表态 = 用脚本原样**，不是我们另挑一个
  AutoLocator（对数轴的 LogLocator 换成 AutoLocator 就是把用户的图改了）；
  setter 一律写进 cfg 再**整体重建**，所以 prop 之间的应用顺序不影响结果；
  `set_[xy]scale` 之后必须 `invalidate_tick_cfg` 重采「脚本原样」。
  单条刻度文字（`ticklabel.text`）冻结整条轴（FixedLocator + FixedFormatter），
  身份是**序号**：冻结前先回模型态、再把该轴上全部仍在生效的编辑一起盖上，
  序号越界就**抛异常**（→ warning → 写回阻断），绝不静默返回。刻度伪元素
  每次 `build_manifest` 按当前状态重登记（`manifest.sync_tick_elements`），
  `FigState.resolve` 还能按 gid 形状**现解**尚未登记的那些——「先改刻度定位、
  再改新出现的那条刻度」在全量重放里才不会报「元素不存在」。
- **边框模型（2026-08-18）**：与刻度模型同一套路数（写进 cfg 再**整体重建**，
  `spine_cfg` / `apply_spine_model` 是唯一出处）。一档「全部」（`spine_color` /
  `spine_linewidth`，作用于 `ax.spines` 的**每一条**，含色条轴的 'outline'）+
  四条各自可覆盖（`spine_<side>_color` / `spine_<side>_linewidth`）。优先级：
  自己的设定 > 「全部」 > 脚本原样；撤销一条 = 退回未表态（落回上一档），
  不是把当前推断出来的值钉死。**为什么要模型化**：「全部灰色」与「上边红色」
  是两条会互相盖写的 setter，直接改的话谁先谁后就是两张图——而 patch 列表序
  在热会话与全量重放之间并不保证同序。
- **路径几何 `geometry`（2026-08-18）**：manifest 给曲线 / fill_between /
  `ax.fill()` 的 Polygon / PathPatch 带上**真正画出来的那条路径**（figure 分数、
  y 向下，与 bbox 同一套），前端据此沿路径描边与命中——bbox 里绝大部分是空白，
  拿它画选择框会画出与图形对不上的矩形，拿它做命中会让用户在空白处误选。
  唯一实现 `engine/pathgeom.py`：`Path.cleaned()` 一次拿 numpy 数组（逐段迭代
  在两万点谱线上要 +550ms）、非仿射先 `transform_path_non_affine`、贝塞尔在
  display 空间细分、NaN 拆子路径、超长路径先按段取极值再 RDP（见
  docs/perf-baseline.md 的「路径几何」一节）。它是**渲染派生数据**：不进用户
  文档、不是 override、不参与写回，几何一变下一版自然就是新的。
  **散点与只有 marker 的曲线有意不给**（bbox 降级，记录在案）；
  **箭头也不给**（它有 `arrow_endpoints` 那套契约，两套并存只会打架）。
  `ax.fill()` 的 Polygon 与 PathPatch 现在登记成 `axes_i.patches_j`（role=patch）。
  前端消费规则见 `web/AGENTS.md`。看护 `tests/test_manifest_geometry.py`。
- **Artist family 能力层（2026-08-21）**：`_cls_key` 从「逐个类名的 isinstance 表」
  改成**按 family 认**——任何 `Patch` 子类归 `patch`、任何 `Collection` 子类归
  `collection`、认不出来的 Artist 归 `artist`。**唯一的例外是线组**
  （`LineCollection` / `EventCollection` → `linecoll`，排在 `collection` 之前）：
  它对外那套 prop 名是 `color`（Line2D 口径）而不是 `edgecolor`，gid 也是
  `axes_i.linecoll_j`，两者都已经发出去了——族抽象省的是实现里的重复，不是
  改掉已承诺接口的理由（裁决记在 `docs/audit/2026-08-21-matplotlib-source-audit.md` §14）。同一条 prop 只写一次：
  `_COLLECTION_CAPS` / `_PATCH_CAPS` / `_GENERIC_CAPS` 三张表经 `_install_caps`
  （**setdefault**，族里的专用契约永远优先）注册给 family key。于是 pie 的
  Wedge、axhspan 的 Rectangle、stairs 的 StepPatch、`pcolormesh` 的 QuadMesh、
  `contour` 的 ContourSet、`eventplot` 的 EventCollection、以及**用户自己继承的
  子类**都不用再各写一份。完整对象模型与支持矩阵在
  `docs/architecture/matplotlib-artist-capability-map.md`，升级 matplotlib 走
  `docs/ci/matplotlib-upgrade-checklist.md`。
  * **能力按真实 getter 实况判，不按类名**（`collection_caps()`）。颜色映射中的
    Collection **不给 facecolor**：它的 facecolors 每次 draw 由
    `update_scalarmappable()` 从数组重算，`set_facecolor` 在屏幕上一个像素都不
    会变（3.10.8 / 3.11.1 实测一致）。`pcolor` 的 PolyQuadMesh 与 `hexbin` 的
    PolyCollection 都是 PolyCollection 的子类却永远映射——按类名开放就是
    「界面说改了、画面没动」。反过来 **stroke 对任何 Collection 都开放**：
    此刻没有边不代表加不上边（给 pcolormesh 加网格线是常见需求）。
  * **gid 一个都没变**：`axes_i.scatter_j` / `axes_i.fill_j` / `axes_i.patches_j`
    的序号取的一直是所属列表（`ax.collections` / `ax.patches`）的下标，不是
    「第几个散点」，所以把从前没登记的那些补登记进来不挪动任何已有名字。
    被 stem 容器消费掉的 markerline 另外登记**旧 gid 别名**（只进 `state.index`、
    不进元素表）——历史 override 仍落在同一个 artist 上，界面上不多出条目。
  * **Collection 的包围盒有第二条路**：多数 Collection 的 `get_window_extent`
    回的是无穷大空框（`pcolor` / `hexbin` / `contour` / LineCollection 实测都是），
    老代码判 `width<=0 and height<=0` 恰好成立，于是元素被**静默丢掉**。退路是
    `get_tightbbox(renderer)`（公开 API，与裁剪框求交，永远有限、永远在子图里）。
    已经量得出有限框的继续走原路，包围盒一个像素不变——写回自检比的就是它。
  * **认不出来的 Artist 只开 `visible` / `zorder`**，不开 alpha：前两者由 draw
    的公共机制兑现、任何子类都逃不掉，alpha 要靠每个 artist 自己在 draw 里读。
    宁可少开放，不可开放了却不生效。
  * **manifest 多一个可选的 `unsupported` 诊断清单**（`manifest.census`，
    instrument 时采一次，不是每帧）：画在图上、既没进元素表也不是结构件的那些，
    按类名 + 归属报出来。容器消费掉的成员不算漏。旧前端不认识这个键会原样忽略，
    写回自检只比 gid 集合与几何。
  * 开发工具 `scripts/dev/matplotlib_artist_census.py`（`--api --with-seaborn`）
    普查任意脚本或代表性 API 的 artist 图与 Tavotto 覆盖度。**只用于开发/审计，
    产品路径不依赖它**——`instrument()` 的语义化遍历才是权威。
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
- 新脚本 / stem 变化：改**图库目录下的 `tavotto_registry.json`**（注册表随图库走；
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
  手动生成/合并：`python -m tavotto.engine.discover <figures_dir> --write`
  （现有条目永远优先，冲突 stem 只报告不裁决）。
- **试运行探测（`engine/probe.py`）**：stem 真的只有运行期才知道时（遍历数据
  目录、读配置、命令行参数），把脚本**跑一遍**按真实产出登记——worker 本来
  就在 build 阶段拦 savefig 并按真实文件名捕获，跑得起来 = 能参数化。
  静态仍解不出的报 `dynamic_names`，交给这条路；**绝不猜，也绝不静默跳过**
  （静默跳过 = 用户拿到空注册表却不知道为什么）。
  界面入口：顶栏项目菜单 / 设置 →「脚本注册表」（扫描 / 试运行 / 手工裁决）。
  * **任意项目内 `.py` 都可主动 probe（2026-08-26，Compatibility Bridge
    Session 3）**：`probe.script_inventory()` 是「项目里有哪些 .py、各自
    什么状态」的唯一清单（稳定 reason code：registered / static_candidate /
    dynamic_stems / no_static_output / infrastructure / unparseable），
    walk 规则复用 `discover._iter_py`（同一个实现的两个视图）——**发现维
    放宽只影响「列给用户挑」，自动静态起草的候选口径（`iter_scripts` +
    SAVE_FUNCS）一字不变**。`/api/registry/probe` 的路径校验一律在 realpath
    之后（`..` 回溯 / symlink 逃逸 / 项目外绝对路径 / 目录 / 非 .py 各有
    稳定 code），解释器仍走 pool 的 runtime selection，前端指定不了。
  * **probe 错误是结构化的**（`probe.ERROR_*` 稳定码表：script_not_found /
    script_path_outside_project / unsupported_script_type /
    script_probe_failed / script_no_figure / missing_dependency /
    execution_timeout / execution_cancelled / invalid_entry /
    multiple_stem_conflict）：主文案按 code 由前端换语言，traceback 只进
    诊断详情。**失败不写注册表**；产出 stem 已被另一份**仍在磁盘上的**脚本
    登记时报 `multiple_stem_conflict` 而不是静默抢走（裁决走 PUT
    /api/registry 的手工路——那才是用户显式指认；归属脚本已不存在的死条目
    照旧顺畅重登记）。
  * **probe 可取消、同脚本互斥（2026-08-26，Session 5）**：app 层
    `_PROBES` 按 (项目 id, script) 登记在跑的试运行（第二个请求 409
    `probe_in_progress`）；`POST /api/registry/probe/cancel` 置取消
    Event 并 `pool.force_cancel`（**当场 kill**，不走优雅关停——shutdown
    要抢被 build 占着的 `w.lock`，等到超时的取消不叫取消）。
    `probe(should_cancel=...)` 一旦判取消**不再尝试下一个 entry**，被杀
    worker 的失败如实归类 `execution_cancelled`（不报「脚本坏了」）；
    取消输给成功——脚本在取消前跑完就照常登记。SSE `probe.started` 在
    执行开始前发出（前端状态机 starting_runtime → running 的边界）。
    看护 `tests/test_asset_library.py`（cancel sentinel：30s 内返回 +
    会话从池里消失 + 注册表零改动）。
  * **entry 候选静态化**（`discover.probe_entry_candidates`，绘图宽口径
    `PLOT_FUNCS` 只喂它，不进起草）：main/render 零参可调才试、裸顶层绘图
    直接 `__main__`、自定义零参绘图函数上限 4 个——盲试不存在的 entry 也要
    把顶层跑一遍，纯属浪费冷启动。**成功路径只执行一次**：热会话留池复用，
    失败 entry 各自新建 worker（看护 `tests/test_script_probe.py` 的
    execution-count 用例）。
- **RuntimeFigureAsset（2026-08-26，Compatibility Bridge Session 4，ADR 0013
  定稿）**：没有磁盘产物的捕获 Figure 是正式素材类型，面板 fileId 是
  `runtime:<script>#<stem>`（**不透明标识**）。引擎侧唯一实现
  `engine/runtimeasset.py`（纯标准库，Flask import）：
  * **解析正向重算**：`resolve()` 拿注册表里每对 (script, stem) 重算
    `figcapture.runtime_asset_id` 与目标比对——任何消费方**不得反解 id**
    （脚本名里可以有 `#`）。app 层 runtime 判别只看前缀
    （`is_runtime_id`），`_engine_worker` / 导出 / 写回拒绝都从这里走。
  * **materialized cache 是派生物不是原件**：落
    `data_dir()/cache/runtime/<slug>/`（preview.svg + metadata.json，
    metadata 标 `generated_by: "Tavotto"`）；**metadata 永远最后写**（两个
    文件都 tmp + os.replace），预览写一半失败磁盘上就没有 metadata，整个
    cache 按不存在处理；坏/错版 metadata 一律当没有。cache 可删除可重建
    （probe 成功与 runtime 渲染成功时物化/刷新，描述符取
    `worker.last_build_descriptors`，**绝不为物化二次执行**），清理走
    `prune_cache`（app 启动线程，与引擎会话缓存同一治理）。
  * **lazy rehydrate（总纲原则 5）**：重开文档先显示 cache 预览/占位，
    `/api/runtime/status`、`/api/runtime/preview` **只读绝不执行**；进入
    对象级编辑 / 显式刷新 / 导出才 build 并重放 overrides。前端的门在
    `useEngineSync.renderTargets` 的 runtime 分支（editing 或本会话
    latest 才入队；tracked 不构成 runtime 自动重跑理由）。
  * **stale 只是提示**：`stale_status` 比脚本 sha256 + 注册表 entry（六档
    `fresh/possibly_stale/missing_source/missing_environment/needs_rerun/
    rerun_failed`，最后一档 producer 在前端）；数据依赖不追踪，文案说
    「可能已变化」。注册表条目丢了用文档描述块兜底，但 **fail closed**：
    重算 id 对不上就是未知，绝不套到猜出来的脚本上。
  * **写回硬拒绝**：runtime id 的 update_source / history/restore 一律
    400 `runtime_asset_has_no_original_artifact`（裁决唯一出处
    `writeback_rejection`；savefig 来源且磁盘有产物的走它的 FileAsset
    身份写回）；source writeback（改脚本）v1 整个不存在，码
    `runtime_source_writeback_unsupported` 先落表。**导出必须当次 live
    worker 渲染**（`_resolve_panel_source` runtime 分支），worker 起不来
    就报错，绝不拿 cache 旧文件冒充。项目包只带描述符 + 脚本。
  * **素材库清单（2026-08-26，Session 5）**：`runtimeasset.list_assets`
    是「图 → RuntimeFigureAsset」条目的唯一实现——注册表里**磁盘无原件**
    的 (script, stem) 各成一条（有原件的归 FileAsset/scan_panels，同一张
    图绝不双列）；`GET /api/runtime/assets` 只读绝不执行（零执行用例看护
    `tests/test_asset_library.py`）。**「有没有原件」按捕获来源判，不按
    文件名巧合**（Session 6 评审修复）：物化描述符说 pyplot 捕获的
    （结构上从没有过原件，figcapture 工厂钉死），同名磁盘文件是旧样本，
    不得把 runtime 素材顶掉——判据唯一出处
    `runtimeasset.is_pyplot_capture`，消费点 `list_assets` /
    `handoff._script_figures` / 前端 `applyOpenRequest` 三处同步。六档 stale 阶梯抽成
    `_status_ladder`（`stale_status` 与 `list_assets` 共用，列表场景只探
    一次解释器）。条目带物化 cache 里的描述符（前端「添加到画布」的数据
    源）；没跑过的条目没有尺寸没有描述符——不给假值。
  * 看护 `tests/test_runtime_asset.py`；`tavotto open` 的自动 probe 仍
    刻意未动（Session 6）。素材库普通入口已落地（Session 5，前端规则见
    `web/AGENTS.md`）。
- worker 里 **`sys.argv` 必须换成脚本自己的**。不换的话按参数命名输出的脚本
  会拿到 worker 的 `--script/--out-dir/--entry`，存出一堆叫 `--entry` 的图
  （试运行探测时当场撞见过，`test_script_sees_its_own_argv_not_the_workers` 看护）。

## 编辑预览的表示法与复杂度预算（ADR 0021，issue #181）

**预览怎么画** 与 **能编辑什么** 是两件事。动这一带之前先读
`docs/adr/0021-complexity-aware-editor-preview.md` 与
`docs/perf-baseline.md` 的「大图预览基线」。

- **常量与判据唯一出处 `engine/previewbudget.py`**（前端镜像
  `web/src/lib/previewBudget.ts` 是**二道闸**，不是第二份权威；两侧的数字由
  `tests/test_preview_budget.py` 逐个比对）。`vector` / `hybrid` / `raster`
  三档写进协议的 `preview` 字段——**加字段不升协议版本**（ADR 0003 §1），
  老 worker 不返回它时前端按 `vector` 解读，行为一字不改。
- **判定必须在 `read_text()` 之前**（`figsession.do_render`，吃的是
  `stat().st_size`）。「先读 126 MB 再说太大」不算保护：实测那一读加上两次
  JSON 编解码就让 Flask 进程峰值 RSS 到 **1.2 GB**，而 SVG 一个字节都还没到
  浏览器。**这条是本轮唯一的硬验收**，看护是
  `tests/support/preview_guard_probe.py`——它把 `Path.read_text` 换成记账实现，
  在阈值两侧各跑一次；只跑一侧的绿是样本，不是对照。
- **超限是一次成功的渲染**：`manifest` / `warnings` / `timings` / `rev` 一样
  不少，只是 `svg` 整个不出现。别让错误路径接住它——把我们主动做出的一个显示
  决定说成「渲染失败」，用户会去修一个不存在的问题。
- **降级 ≠ 只读**：raster 档下命中层与 exact manifest 一个字都不放松
  （不变量 4 = ADR 0017）。#181 的用户要的恰恰是编辑这张图。
- **MCP 那条路上 raster 的位图与 manifest 在同一次响应里**
  （`bridge._render` 的 `preview_png_base64`，宽度钉死
  `previewbudget.RASTER_PREVIEW_WIDTH_PX`）。内嵌画布里没有可连的 HTTP 服务，
  不带上它就是一张全白的画布；另开一跳去取则会拿到另一组 patches 的像素——
  与 SVG/manifest 的原子配对是同一条纪律。**绝不把 giant SVG 转成 base64
  塞回去**，那只是把同一个 payload 换个编码再放大三分之一。
- **不按 artist 类型特判**。#181 的表面成因是 `pcolormesh`，成本的真实来源是
  primitive 数量——`scatter` 十万点、`contourf` 上千条等值线是同一个问题。
  判据问「有多少 primitive」，不问「你是谁」。
- 合成复现在 `tests/fixtures/large_figures/`，摊成可用图库用
  `tests/support/large_figures.py`。**跑出来的 SVG/PDF 绝不提交**
  （默认规模下 SVG 一百多 MB）。

## 受控依赖修复（ADR 0019，2026-08-27）

缺包时「一键装上并继续」。**动它之前先读 ADR 0019**——它是本仓库唯一一个会
往磁盘装第三方代码的子系统，边界比实现重要得多。

- **四个模块各是自己那件事的唯一出处**：`engine/depresolve.py`（import 名 →
  distribution 的可信解析 + 包名语法）、`engine/managedenv.py`（Tavotto 替
  项目管的隔离环境）、`engine/deprepair.py`（计划 / 安装 / 取消 / 验证 /
  记账）、`engine/pool.py` 增量（环境改动期间的 worker 生命周期）。
  全部纯标准库（Flask 父进程 import 链上）。
- **内置 runtime 永远不是安装目标**。它是「重装就能修」这条退路的前提。
  缺包时它只是触发器。安装目标只有两种：用户的项目 `.venv`（要明确确认）
  与 Tavotto 受管环境（我们自己的，可删可重建）。
- **import 名不是包名**。只认 `project_declared` / `curated` 两档高置信解析，
  外加用户手填的 `user_specified`。**没有「同名试试看」这一档**——那是抢注
  攻击的入口。依赖声明只读：不改 requirements.txt / pyproject.toml，不
  `pip install -r`。
- **包名语法是安全边界不是输入校验**：`shell=False` 挡不住 pip 自己把 `-r` /
  `--index-url` / `--target` 解析成选项。白名单语法在
  `depresolve.parse_requirement`，安装前在 `_pip_install` 里**再验一次**。
- **计划绑定，不是 `confirmed=true`**：plan（说清楚装什么装到哪）与 install
  （只发 plan_id）分两步；执行端一个字节都不从请求体里读，且执行前重算环境
  指纹（`repair_plan_stale`）。没有计划 → `dependency_install_not_allowed`。
- **pip exit 0 不等于修好了**：验证三层——import 那个包 / import matplotlib /
  **真起一次 worker 跑通 build**（`deprepair.worker_self_test`，argv 走
  `execspec.worker_argv` 那一份，不另拼）。
- **安装期间那个环境上不许有 worker**：`pool.mutating_environment()` 先把该
  解释器上的会话全停、并让 `pool.get()` 拒起新会话（`environment_mutating`）。
  锁的粒度是**一个环境**，不是全局。装完 `pool.invalidate()` 点名作废——
  磁盘上多个包不会让已经起来的解释器看见它。
- **用户环境上的安装只进不退**：本轮禁止任何自动 `pip uninstall`。取消之后
  对用户 `.venv` **不假装完整 rollback**，如实说「可能已发生部分修改」；
  受管环境标 incomplete、下次重建。
- **隐私**：安装日志两道脱敏（pip 特有的 index 地址与 URL 凭据归
  `deprepair._sanitize`，路径与密钥走 `diagnostics.redact_text` 那一份）；
  诊断只记 `custom_package_index: true/false`，**绝不记地址**。本轮**没有加
  遥测事件**（EVENTS 扩容要升 CONSENT_VERSION 并让所有人重新同意，理由见
  ADR 0019 §十二）。
- 看护：`tests/test_dependency_repair.py`（十五条负向反证）+
  `tests/test_dependency_repair_e2e.py`（真建 venv、真跑 pip、真起 worker、
  真出图；不联网靠手工 wheel + `PIP_FIND_LINKS`/`PIP_NO_INDEX`）+ web 的
  `DependencyRepairCard.test.tsx`。

## 两条执行入口：safe worker 与 native bridge（ADR 0014 / 0020）

**Figure 到手之后的编辑语义只有一份**（总纲原则 1）。两条入口的分工：

| | safe worker（`worker.py`） | native bridge（`bridge_runner.py`） |
|---|---|---|
| 解释器 | Tavotto 挑（`pool` 五级优先） | **用户 invocation 里那一个**，绝不静默替换 |
| cwd | 沙盒（写入边界） | **用户的原样**（继承） |
| argv | `[脚本自身]` | **用户的原样** |
| env | bundled 时 `child_env()` 清洗 | **原样继承**；只额外注入 `TAVOTTO_BRIDGE_TOKEN`，且子进程一起来就摘掉 |
| savefig | 吞掉（不写盘）+ 捕获 | **透传**（照常写文件）+ 捕获 |
| 写/删守卫 | 有 | **无**（脚本拥有用户的全部权限——文案必须与此一致，绝不声称沙盒） |
| stdout | 重定向到 stderr | **原样是用户的** |
| 控制通道 | stdin/stdout 行协议 | 127.0.0.1 loopback + 一次性 token |
| 编辑语义 | `figsession.LiveFigureSession` | **同一个** |
| 协议信封 | `wireproto`（worker v1） | **同一个**（只多一个 `continue`） |

改动纪律：

- **`figsession` / `wireproto` 是两条入口共用的。** 改它们等于同时改两条
  入口——先跑 `tests/test_worker_roundtrip.py` 与 `tests/bridge/` 两套。
- **native 里绝不能出现裸的兄弟模块 import。** engine 目录在 bridge 里是
  **临时**上 `sys.path` 的（装完就收回），用户项目里完全可能有同名的
  `manifest.py` / `overrides.py` / `config.py`。延后执行的 import 一律走
  `overrides._sibling(...)` 那条按包前缀解析的路（结构性守卫：
  `tests/bridge/test_bridge_namespace.py::test_no_bare_sibling_import_survives_in_overrides`）。
- **`bridge_runner` / `bridgeboot` 启动阶段不许 import matplotlib**，
  钩子挂在 `sys.meta_path` 的后置 import 回调上。
- **native 侧不许起后台线程**：Figure 归主线程，`LiveFigureSession` 有线程
  身份断言兜底（源码判据在 `test_bridge_thread_model.py`）。
- **spike 不是产品**：`python -m tavotto.engine.bridge_spike` 没有稳定契约、
  没有接进 `tavotto` CLI，别在文档 / 官网 / release notes 里提它。
  **产品入口是 `tavotto run`**（下一节）。

## `tavotto run` 的控制面（ADR 0021，Beta）

进程关系是**倒过来的**——用户的 Python 是 **CLI 的子进程**，sidecar 只是
通过一条认证 relay 连上去：

```text
用户终端 → tavotto run CLI ─┬─ 用户的 Python（Bridge Runner）
                            └─ Tavotto 桌面 sidecar
```

| 模块 | 职责 |
|---|---|
| `runcodes.py` | **稳定错误码 + 中英文案的唯一出处**；`RunError`；退出码闭集 |
| `runspec.py` | 严格 invocation 解析（`--` 强制）、解释器体检、cwd vs project_root、status file |
| `runcli.py` | `tavotto run` 本身：拥有 stdio / env / cwd / 子进程，按顺序编排 |
| `nativehandoff.py` | 一次性交接凭据（0700 目录 / 0600 文件 / 墓碑 / 过期 / realpath 判据） |
| `nativerelay.py` | 两侧认证 + **纯字节**转发。**不许 import 任何引擎语义** |
| `nativesession.py` | sidecar 侧注册表 + **单 reader** 传输 + 状态闭集 + live route |
| `nativeperm.py` | "记住这个项目和这个 Python"（绑定 项目 × 解释器 × schema） |
| `envlease.py` | **环境占用的唯一一张表**：safe worker / native 会话 / pip 安装三方共用 |
| `enginesession.py` | **"谁来渲染"的唯一判据**（按 `execution_profile` 路由） |

改动纪律（每一条都有用例，改之前先看它们）：

- **CLI 必须继续拥有用户的 Python。** 让 sidecar 去 spawn 会同时失掉
  stdin / stdout / cwd / env / Ctrl+C 五样（ADR 0021 §1）。
- **确认之前一行用户代码都不许跑。** 顺序是产品语义的一部分
  （`test_not_a_single_line_runs_before_the_user_confirms`）。
- **Tavotto 的话只写 stderr。** stdout 是用户程序的——所以也没有 `--json`。
- **`creationflags` 必须显式声明是哪一类**：GUI 拥有的隐藏子进程用
  `CREATE_NO_WINDOW`，CLI 拥有的控制台子进程用 `INHERIT_CONSOLE`
  （`test_windows_regressions` 按闭集判）。
- **屏障释放必经 `bridge_runner.release_barrier()`**：保存 patch → 恢复成
  脚本原样。下一个屏障 `rebase()` 重新采基准 + 重放。任何绕过它的释放路径
  都会让**故障路径上的语义比正常路径更宽松**（ADR 0021 §8.1）。
- **不许再写第二处 `pool.get()` 分支**：`app.py` 里所有"谁来渲染"都经
  `enginesession.resolve()`（结构性守卫
  `test_native_api::test_the_resolver_is_the_only_place_that_branches`）。
- **native 会话绝不进池**：LRU 淘汰会杀掉用户正在跑的脚本。
- **环境占用只有 `envlease` 一张表**：加第二张就保证了它们迟早不一致。
- **连接过的 socket 一律 `shutdown(SHUT_RDWR)` 再 `close()`。** Linux 上
  `close(fd)` **不唤醒**另一个线程里阻塞着的 `recv(fd)`——那个系统调用还持着
  底层的 file description，于是**套接字不拆、FIN 不发**，对端永远等不到 EOF；
  macOS 会让阻塞中的 `recv` 带 `EBADF` 返回，**所以这类缺陷本机恒绿、CI 恒红**。
  产品上的形状：用户按了 Ctrl+C，脚本收到了也退出了，但 runner 停在"脚本
  结束"那个屏障上等控制通道说话——通道没关、屏障不放、终端再也回不来。
  判据要两条：一条量**不变式本身**（替身 socket 记 `shutdown` / `close` 的
  调用顺序，任何平台都红），一条量行为（对端看不看得到 EOF，只有 Linux 红）。
  只留后者等于把判据的有效性押在 CI 的平台组合上。
- **native 面板"出自哪一档"只有一个出处**：`enginesession.profile_of()`。
  `/api/runtime/status` 的 `execution_profile` 与渲染路由读的是同一份，
  另立一份迟早在某个边角上分叉，而分叉的那一侧会在界面上显示成"能编辑"。

## 布局层（R18）

- **布局版本**：`/api/versions/<docId>` 系列，快照存 `layouts/_versions/`，
  自动检查点去重 + 滚动清理（保手动裁自动）；恢复=前端 commit（可撤销），
  与「写回原始文件」的 baked 历史完全无关。
- **论文样式**：`/api/styles`（`layouts/_styles.json`）；前端按角色映射成
  override / 标注属性一次 commit 应用，绝不写回源文件。
- **项目包**：`POST /api/package` 打 zip（layout+素材+脚本+sha1 清单）；
  `POST /api/package/open` 检视（缺失/sha1 漂移），素材永不自动写入图库。
- **导出**：请求可带 `proof` 对象 → 随成图写 `_proof.json`。
- **项目文件统一收纳在项目内的 `tavottofile/`（2026-08-17 定版）**：命名画布
  布局直接放 `tavottofile/`，导出默认 `tavottofile/export/`（settings.export_dir
  可覆盖；建不出来退回数据目录，测试读响应里的 export_dir 而不是猜路径），
  布局版本历史 `tavottofile/versions/`。旧位置（项目 `canvases/`、项目同级
  `<项目名>-exports/`、数据目录 layouts/ 与 layouts/_versions/）只读兼容、
  合并列出，重名以 tavottofile 为准；**素材扫描的 EXCLUDE_DIRS 必须含
  tavottofile**，否则导出成图会混进素材面板。autosave / styles 等
  跨项目或内部机制仍留在数据目录。
- 前端文档模型的对应字段（lockedGids / layoutGroups 等）见 `web/AGENTS.md`。

## 项目系统（后端侧）

- **项目（多开，2026-08-16 起）**：`app.py` 无默认路径；`PROJECTS: dict[id, ProjectCtx]`
  同时端着多个图库，`DEFAULT_PROJECT` 只是「不带 pj 的请求落到哪」。未打开项目时
  API 回 409 `code=no_project`，前端渲染 ProjectPicker。用户级配置在
  `engine/config.py`（macOS `~/Library/Application Support/Tavotto/config.json`，
  测试用 `TAVOTTO_CONFIG_DIR` 重定向——conftest 已全局隔离）。
  每项目设置（导出/备份目录、`allow_write_back` 只读）经
  `PATCH /api/project/settings`；写回类端点先过 `_write_back_forbidden()`。
- **每标签页一个项目**：请求靠 `pj` 认领（`_request_ctx()`）——**查询参数与
  请求头两条路都必须认**：fetch 统一带头，但 `<img src>` 与 EventSource 加不了头，
  只做一条会让一半 API 串到别的项目上。指名一个不存在的 pj 一律 409，
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
- 前端侧（sessionStorage 的 pj、schema 3、画布会话、自动保存、剪贴板、撤销
  防线等）见 `web/AGENTS.md`。

## 会话认证（ADR 0008，勿破坏）

**会话认证在 `src/tavotto/security.py`，桌面与浏览器模式共用一道边界**
（2026-08-21，1.0 审计的 P0 修复）：一次性 nonce →
`POST /api/session/bootstrap` → HttpOnly + SameSite=Strict cookie，
Host 只认 `127.0.0.1:<port>`、带 Origin 必须同源，`/`、`/assets/*`、
`/api/version`、bootstrap/relaunch 之外全部 401 兜底。浏览器模式的 nonce
在落地 URL 的 fragment（`#dnonce=`），另写 0600 的本机凭据文件
（`engine/session_client.py`，**纯标准库**——Flask 父进程与 handoff 都
import 它）：本机 CLI/冒烟凭 `X-Tavotto-Auth` 头直连，二次启动/交接凭
`/api/session/relaunch` 换新 nonce（实例复用 = 安全的 token 交接）。
**旁路只有三个**：pytest 的 test_client（无状态天然旁路）、
`--insecure-no-auth` / `TAVOTTO_INSECURE_NO_AUTH=1`（vite dev proxy、
e2e、手工 curl；启动时打印警告）。看护 `tests/test_browser_auth.py` +
smoke_app 的「未认证必须 401」硬断言——**别再让任何新端点绕过 guard**。

## 编码 Agent 桥

- `POST /api/ai/run` → spawn 本机的编码 Agent CLI（`codex exec` / `claude -p`），
  cwd=figures 目录；修改前快照到 `cache/ai_snapshots/`，结束后 diff 经 SSE
  `ai.done` 推送；revert 恢复快照。脚本被改后 mtime watcher 自动作废渲染会话。
- **「支持哪些 Agent」的唯一权威是 `engine/ai_agents.py` 的 `AGENT_REGISTRY`**
  （ADR 0015，改动前先读）。候选探测、启动验证、无副作用就绪检查、命令构造、
  流式输出分类、一键安装包名，全部由各自的 `AgentDefinition` 适配器给出。
  `ai_bridge.py` 只剩会话编排（快照 / SSE / diff / revert / cancel / history），
  **不许再写 `if agent == "codex"` 这种分支**——`tests/test_ai_agents.py` 用一个
  Fake Adapter 反证「加第三个 Agent 不用改通用层」，写回分支那条会红。
  依赖方向单向：`ai_agents ← ai_providers ← ai_bridge`；第三方接口的注入结果
  由 ai_bridge 算好、经 `RunContext` 交给适配器拼装（`ai_agents` 不 import 另外两个）。
  生产注册表**只放真的能跑起来的** Codex 与 Claude Code：架构支持第三个 ≠
  假装已经支持，界面上不出现「即将推出」的占位行。
- `GET /api/ai/capabilities` 实测本机每个已注册 Agent（安装/版本/就绪/模型/
  推理强度），返回**动态 `agents[]`**（旧的 `providers` / `settings` / `active`
  三个硬编码对象已整体移除，见 ADR 0015）。`argv` 不公开——前端没有消费者。
  通用设置走 `PATCH /api/ai/agents/<id>`（`enabled` / `path_override`），
  安装走 `POST|GET /api/ai/agents/<id>/install`；自定义路径**不允许硬编码私人
  路径**（pytest 看护）。run 可带 model/effort。
- **CLI 子进程一律用 `ai_agents.spawn_env()` 的增强 PATH**（探测与运行同一份）：
  桌面壳从 Finder / 开始菜单启动时继承 GUI 的最小 PATH，npm shim 的
  `#!/usr/bin/env node` 解析不到 node（`env: node: No such file or directory`）
  ——把 CLI 所在目录 + 常见安装目录补到 PATH 末尾即可，不改用户已有排序。
- **CLI 探测（Windows 尤其）**：`search_locations()` 在 PATH 之外把 npm 全局、
  `%LOCALAPPDATA%\Microsoft\WindowsApps`（**商店版 codex 的执行别名——真身在
  受 ACL 保护的 WindowsApps 包体里，只能走这个入口**）、WinGet/scoop/choco/
  bun/volta 全翻一遍；macOS 上 ChatGPT 应用内置的 codex 由 **codex 适配器自己的**
  `extra_search_locations()` 追加（排在常规位置之后）。每个候选带一个 `source`
  标签，**只是诊断信息、不参与「能不能用」的判断**——判据只有「`--version`
  真的起得来」这一条。找不到时 capabilities 的 `diagnostics.searched` 告诉用户
  找过哪儿，第一个坏候选记在 `diagnostics.broken_path`（界面据此把「安装不可用」
  与「未安装」分开说）。npm 装出来的 `codex.cmd` 外壳经 `resolve_shim()` 解析成
  真正的 exe/node 脚本——经 cmd.exe 中转会吃掉提示词里的 `%`、`&`、`^`、`<`、
  `>`、`|`（中文提示里写个「透明度调到 50%」就够出事）。路径拼接一律用字符串，
  不用 pathlib（`os.name` 一变 Path 就分派到另一半实现，连跨平台测这段都做不到）。
- **就绪检查（readiness）与探测是两件事**：探测回答「有没有一个能启动的可执行
  文件」，就绪检查回答「它现在能不能干活」。**只允许跑官方 CLI 明确提供的本地
  状态命令**（`codex login status` / `claude auth status`），不发模型请求、不建
  会话、不产生费用、不改登录状态，`stdin=DEVNULL` + 10s 硬超时；不支持 / 超时 /
  看不懂 → `unknown`，映射为「已安装」。**绝不因为「配置目录存在」就宣布已登录，
  也绝不为了让那一行变绿偷偷发一个真实 Prompt。** `claude auth status` 的 JSON
  里带着邮箱 / 组织名 / 订阅档位——**只取 `loggedIn`**，其余一个字节都不进
  capabilities、日志或诊断包。
- **状态模型六档**（ready / installed / needs_auth / broken / not_installed /
  disabled）+ 前端本地的 `detecting`。`usable = enabled && 能启动 && state ∉
  {broken, not_installed, needs_auth}`。`enabled` 是三态：没表过态跟着「装没装」
  走，**明确关过就一直关着**。判据在后端（`require_usable`），`/api/ai/run`
  自己判一次——只靠前端隐藏不够，那个端点可以被直接调；**它的兜底判据就是
  `usable` 那一个字段**，在那里重列一遍条件就是第二份定义，分叉的表现是
  「界面把它藏了、API 还放它进来」。
- **接了第三方接口时，CLI 自己的登录态不参与判定**：注入凭据的全部意义就是让
  CLI 不必用官方登录跑起来，拿它的登录态回答「能不能派活」是**把判据的主语搞错**
  ——表现是「配好 DeepSeek 的用户发现 Codex 从选择器里整个消失」。判据取
  `ai_providers.spawn_overrides()` 是否**真的**产出了参数/环境变量，不是「配置里
  有没有一条记录」（codex 侧 base_url 为空时它什么都不注入，那时登录态仍算数）。
- **`path_override` 来自请求体、最终会被 spawn**：非空且无 NUL → `realpath`
  归一化（`..` 与符号链接在判断**之前**解掉）→ 存在的普通文件且可执行 →
  **文件名必须指向该 Agent**。最后一条挡的是「把 Tavotto 指向 /bin/sh」那一整类。
- **遥测的 agent 白名单取自 `telemetry.EVENTS` 的枚举，不是注册表**：拿「在不在
  注册表里」当白名单，加第三个 Agent 之后恒真，而 capture() 只收表里那几个值
  ——那个 Agent 的调用会被静默丢弃。
- **第三方 API 接入（`engine/ai_providers.py`）**：按**协议族**分支（anthropic 走
  `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` 环境变量，openai 走
  `-c model_provider=tavotto` + `[model_providers.tavotto]` 临时覆盖 +
  `TAVOTTO_CODEX_API_KEY`），谁支持接第三方接口由适配器的 `endpoint_family` 说了算
  ——**不再自己列一份 agent 名单**。
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
- **用户配置按 agent id 分键**：`ai.agents.<id>.{path_override, enabled}`。
  旧的 `ai.codex_path` / `ai.claude_path` 由 `config._migrate_ai_agents()`
  一次性迁移并**立刻删掉旧键**（两份权威并存 = 一边改路径另一边不知道）；
  规则写成正则而不是列举两个名字。不认识的 agent id 原样留着。
- 会话历史在 `engine/ai_history.py`（SQLite `cache/ai_history.sqlite3`，
  按 project 列过滤）；启动时 running→interrupted + purge(180d，pinned 豁免)；
  历史 API：list（分页/搜索/筛选）/delete/pin。前端默认只显示人类可读目标，
  脚本名收在「技术详情」。

## 文字：行内上下标与大小写

- **画布标注**用行内标记 `^{…}` / `_{…}`（`web/src/lib/richText.ts` ↔
  `src/tavotto/richtext.py` **严格同源**：`SCRIPT_SIZE/SUP_RISE/SUB_DROP`
  三个常量与 parse 规则改一边必须同步另一边，pytest 用真实 PDF 的字形
  字号与基线做几何级看护）。只有 `^{`/`_{` 才触发，正文里孤立的 `^`/`_`
  原样显示；`\^`、`\_`、`\\` 是转义。序列化**按需转义**——无脑加反斜杠会让
  用户点一次「大小写」正文里就凭空多出 `\`。
- **图内元素文字**走 matplotlib mathtext（`cm$^{-1}$`），不是上面那套标记；
  大小写转换要 `protectMath`（`$…$` 里是 `\alpha` 这类命令，改大小写就废）。
- 大小写是**一次性动作**，直接改文本内容（可撤销），不新增字段、导出零改动。

## 出版规范 profile 与预检

- **规则的唯一权威文件是 `src/tavotto/profiles/publication.json`**（随 wheel 分发）。
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
  proof report。整套换掉用 `TAVOTTO_PROFILES_FILE`。
- 导出目录规则收在 `engine/config.project_export_dir(project, fallback)` —— Flask 与
  MCP server 都调它（`fallback` 是参数不是常量：app 的 `EXPORT_DIR` 会被测试 monkeypatch）。

## 匿名用量统计（2026-08-20，默认关闭）

事件契约与指标定义在 `docs/analytics/`（`telemetry-events.md` / `yc-metrics.md` /
`yc-dashboard.json`），隐私承诺在 `docs/privacy.md`，代理部署在
`services/telemetry_proxy/README.md`。改动前先读。

- **`engine/telemetry.py` 纯标准库**（Flask 父进程 import，边界同 updater）。
  **不许引入 posthog / requests / httpx / 任何分析 SDK**——为一个「失败无所谓」
  的可选功能，让主进程多出一条起不来的可能性，这笔账划不来。worker 子进程
  对遥测**一无所知**，一行都别加进去。
- **同意是三档**（`unset` / `enabled` / `disabled`）。「没设置」不是同意：
  unset 时一个字节不发、**连 install_id 都不生成**。`ever_enabled` 让「关掉
  再打开」不再重发 telemetry_enabled——那不是一个新用户。改采集范围要同时
  升 `CONSENT_VERSION` 并退回 unset 重新问：当初同意的不是这一版。
- **`TAVOTTO_NO_TELEMETRY=1` 是硬开关**，压过任何已保存的同意，并且**不弹**
  首启询问。它与 `TAVOTTO_NO_UPDATE_CHECK` 是**两个独立开关**，谁都不代管
  对方（合成一个 = 想关用量统计就得连安全更新提醒一起关）。conftest 把它钉成
  1、四条 workflow 的顶层 env 也钉成 1、smoke/bench 脚本各自注入——**CI 绝不
  产生真实的产品事件**，一台每天跑几十次的机器足以让「有多少人在用」失真。
- **distinct_id 是本机随机 UUIDv4**，不从任何机器信息推导（没有 MAC、
  machine GUID、主机名、用户名）。它是**假名不是身份**，所以指标文档里一律
  写 "opted-in anonymous install"，绝不写 user。诊断包按键名 + **按值**两道
  抹掉它（`_PSEUDONYM_KEYS` + `_redact_text`）；开关状态不抹，那对排障有用。
- **白名单是结构性防线，不是自觉**：`EVENTS` 表里没有任何一个属性接受
  dict / list，值只能是 bool / 有界 int / 短枚举 / 受控版本串。文件名、路径、
  脚本、提示词、图内文字**在结构上就发不出去**。客户端与代理各有一份表，
  靠 `test_client_and_proxy_contracts_match` 逐条对拍（与 patchspec ↔ Rust
  同一套纪律）——**刻意不做 schema 编译器**，两张显式的表加一条对拍用例
  比一套机制好读也更难错。
- **`capture()` 永不抛、永不阻塞**：有界内存队列（满了丢）+ 一条懒起的守护
  线程 + 2–4 秒超时。**没有落盘队列**——能把用户几周前的行为攒起来择机上传的
  队列，与「本地优先」是冲突的，还必然在磁盘上留一份行为记录。断网 = 丢事件，
  这是自觉的取舍。
- **成功边界埋点，不是点击埋点**：`export_completed` 在 `/api/export` 文件
  全部写完之后、正常响应之前（服务端）；`ai_assistant_invoked` 在
  `engine_ai.run()` 真的回了 session 之后（服务端）；pip/pipx 的
  `update_completed` 在升级命令成功之后。前端记的是「点了」，点了还会失败。
- **编辑埋点只有一个调用点**：`documentStore.pushHistory`（commit 与 endTxn
  的共同漏斗）。一次拖动 = 一条事务 = 一条历史 = **一个事件**，不是 120 次
  pointermove；预览平面不 commit，因此天然不产生事件。`edit_kind` 由**历史
  标签的 key**（开发者写死的稳定标识）经闭表映射，查不到落 `other`——
  标签**文案**绝不能用，它被翻译过、还插值了用户的文件名与属性名。
- **前端只发服务端推断不出来的那几条**，经 `/api/telemetry/event`，
  校验走**同一份** `engine/telemetry.validate`。`web/src/lib/telemetry.ts` 只缓存
  「现在发不发」+ 转发 + 分类，同意态与 install_id 一律不进前端
  （`public_settings()` 是唯一出口，**不含 install_id**）。
- **内嵌 Codex 画布不发遥测**：widget 打包了同一份前端代码，但没人调
  `setTelemetryEnabled`，`captureTelemetry` 是 no-op。它没有 sidecar 会话、
  也没有自己的同意上下文，继承一个只会让人意外——这是决定，不是疏漏。
- **代理 `services/telemetry_proxy/` 不进 wheel/sdist**（pyproject 的 exclude），
  也不给 Tavotto 加任何运行时依赖。应用里**没有也不该有** PostHog 密钥或地址
  （`test_no_posthog_key_or_direct_host_anywhere_in_the_client` 看护）：开源桌面
  应用里嵌的东西一律是公开的。提供商特有的 JSON 只在 `posthog.py` 一个文件里
  （与 `pdfbackend/` 同一条边界思路）。
- **发行量与用户是两回事**：`scripts/collect_distribution_metrics.py` 从 CI 发
  `github_release_asset_snapshot` / `pypi_daily_downloads` / `github_repo_snapshot`，
  distinct_id 是常量 `distribution_metrics`，**绝不混进用户队列**。GitHub 的
  `download_count` 是**累计计数器**，发的是快照、区间量靠做差；资产身份是
  `asset_id` 不是文件名（资产会被删掉重传）。**更新包与签名不算安装量**
  （`Tavotto.app.tar.gz` / `*-setup.nsis.zip` / `latest.json` 全是更新器自己拉的，
  算进去会让「装过的人」随老用户升级不断膨胀）。分类规则从真实发布工作流推出，
  不靠「.exe 就是安装包」的直觉。**桌面遥测丢事件必须无声，采集器丢数据必须
  有人看见**——所以采集器失败就让 workflow 红。
- **部署顺序**：先发代理（它拒绝不认识的事件与 schema_version）→ 验 PostHog
  收得到 → 配采集器 → 再发客户端。反过来的话新事件会被静默 400，而且全绿。
- 验证：`tests/test_telemetry.py`、`tests/test_telemetry_api.py`、
  `tests/test_export_endpoint.py` 末节、`tests/test_telemetry_proxy.py`、
  `tests/test_distribution_metrics.py`、`web` 的 `lib/telemetry.test.ts` /
  `store/telemetryStore.test.ts` / `components/TelemetryConsentDialog.test.tsx` /
  `components/SettingsTelemetry.test.tsx`。

## 诊断与排障

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
    `TAVOTTO_REPLAY_NONCE` salt env 绕开 spec 哈希复用，用完 `discard()`——
    它返回时**进程已被 wait 回收、句柄已关、exact base 已删、`_oneshot_bases`
    已注销**（注销排在删除之后：重试期间目录仍算 active）；删不掉也注销并留日志，
    好让 `prune_engine_cache()` 还有机会回收。写回的泄漏用例只对**本次
    `one_shot()` 建出来的 base** 负责，不许再用 `ENGINE_CACHE.glob("_replay-*")`
    断言全局为空——ENGINE_CACHE 全进程共用，那样会把别的测试文件的残留算到
    自己头上（run 32937999297 就是这么误报的）。
    同一次写回只 build 一次（override + PDF + PNG 共用）。
    热会话最后应用的正是这组 patches 时（`worker.last_patch_hash`），把两份
    manifest 逐元素比 bbox/anchor（容差 0.5% figure 分数）与 size_mm（0.01mm），
    有分歧回 409 `replay_divergence` + 分歧清单。几何过了还要过**像素门**
    （ADR 0009，issue #81）：两侧各出一张 `render_png` 探针图逐像素比——
    颜色 / 线型 / 字体 / 透明度这类几何不变的纯属性分歧只有像素量得到
    （PR #49 的 facecolor 恢复顺序 bug 报了 0 处分歧）。比较器是
    `pdfbackend.compare_png`（判据结构与 `scripts/ci/pixelcompare.py` 同构但
    **逐 RGBA 通道比**——等亮度换色与纯 alpha 差异灰度量不到；灰度等值图上
    两份一致，对拍用例钉住，Flask 边界内不许 import 科学栈），阈值
    `app.REPLAY_PIXEL_TOL`；分歧作为 `field: "pixels"` 进同一份清单。
    **热态不是这组 patches 就不比**（历史恢复、跨面板同步都是），响应据实回
    `replay: "fresh_only"`——假报一次，用户学到的就是「这个提示可以无视」；
    比过且过了才在 `verification` 里报 `pixels: "ok"`。manifest 经 JSON 落盘，
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
  * 「写回可携带画布标注」的前端换算规则见 `web/AGENTS.md`；后端入口是
    `pdfbackend.annotate_asset`（导出合成同一组 `_draw_*` 矢量），只有 PNG 的
    素材回 `annotations_need_pdf`。
  * 看护：`tests/test_write_back.py`（假 worker，全部分支）+
    `tests/test_worker_roundtrip.py` 末节（真 matplotlib + Flask 全链路，
    含 workerd 路径的一次性会话不泄漏）+ `web` 的 `WriteBackDialog.test.tsx`。

## 外部交接（`tavotto open` 与桌面唤起）

完整版在 `docs/adr/0005-external-handoff-and-codex-plugin.md`，改动前先读。
插件侧（codex-plugin/ 的镜像定位器、update_check、技能纪律）见
`codex-plugin/AGENTS.md`。

- **发现链的唯一权威是 `engine/locate.py`**（纯标准库）：`TAVOTTO_CLI` → PATH →
  安装清单 `install.json` → 已知安装位置 → HKCU（只当补充）→ 当前解释器。
  **只装了桌面版也必须能被发现**——这是 2026-08-18 修的那个 bug：装出来的
  `Tavotto.exe` 与 sidecar 都是 GUI 子系统可执行文件，没有真终端时
  `sys.stdout is None`、输出被 `entry.py` 改道进 app.log，**调用方拿到的是空
  stdout**。所以 `packaging/tavotto.spec` 从同一个 Analysis 多出一个
  `console=True` 的 `tavotto-cli`（共用 `_internal/`，只多 ~1.5 MB）。
  **别把 GUI exe 当 CLI 调**，哪怕它接受同样的参数。
  安装清单落在**用户配置目录**（安装目录可能只读、卸载会被删）：安装器装完跑
  `tavotto-cli doctor --json --write-manifest`（让 CLI 自己写，NSIS 不拼 JSON），
  应用每次启动 `locate.refresh_manifest()` 刷一遍（**只补充不抹掉**：pip 装的
  那份是非冻结进程、只去惯例位置找壳，无条件写下去会把桌面版记的非惯例路径
  抹成空，一次 `tavotto --figures …` 就够），卸载器在**删文件之前**移除。
  读的一方要核实里面的路径还在——清单是缓存不是真相。**任何单一机制都不是
  唯一依据**（清单可能没写成、注册表可能被策略锁住），也**不动用户 PATH**。
  `sidecar/Tavotto` 这一段的出处只有 `tauri.conf.json` 的 `bundle.resources`，
  Rust 壳 / locate / NSIS 三处同源。协议与错误码全文在 `docs/handoff-protocol.md`。
- **子命令在 `engine/cli.py`（`open` / `doctor`）**，三个入口都先问它一句：
  `tavotto/cli_entry.py`（pip/pipx 的 console script 与 `python -m tavotto`）、
  `packaging/entry.py`（冻结产物）、`app.main()`（兼容旧调用方式）。
  分派**必须在 import Flask 之前**：一次交接用不上任何 HTTP 端点，却要付
  整个 Flask + PyMuPDF 的冷启动；更要紧的是 `doctor` 本该是「装坏了怎么查」
  的工具，界面依赖 import 失败时它自己也得能跑
  （`test_subcommands_run_without_flask_or_pymupdf` 看护）。
- **`HandoffError` 一律带稳定 `code`**（`registry_write_failed` /
  `path_not_found` / `launch_failed` …），`--json` 失败也输出一行 JSON。
  文案随时可改，code 不行——调用方按它分诊。裸抛的那条由
  `test_every_handoff_error_carries_a_code` 挡住。
- **入口是 `tavotto open <产物|脚本|目录>`**（`engine/handoff.py`，纯标准库）：
  解析目标 → 登记 stem → 唤起界面。子命令在 argparse **之前**分派——主入口是纯
  flag 形态（`tavotto --figures …`），改成 subparsers 会把既有命令行整个换掉。
  项目 = 含 `tavotto_registry.json` 的那一层（向上找 ≤3 层，**有上限**：静默把上层目录
  当图库会把一整棵源码树当素材扫）。注册表合并复用 `discover.merge`，
  **不另写裁决**；读不懂就报错，绝不重写用户手写的注册表。
- **桌面契约是 argv `--open <目录> [--stem <stem> | --pick-script <脚本>]`**：
  生产者唯一 `handoff.desktop_argv()`，消费者唯一
  `src-tauri/src/main.rs::parse_open_args()`，两侧各有单测，改一边必须同步
  另一边；macOS 的 `open -na … --args` 之后**复用 desktop_argv 的切片**，
  不再手拼第二份。`--pick-script` 是多 Figure 交接的选择信息（脚本相对
  路径，与 `--stem` 互斥）——壳只透传，Figure 选择器在前端。
  首启：项目 → sidecar 的 `--figures`，stem → 落地 URL 的 `?open=`、
  pick → `?pick=`（browser-new 由 `--open-pick` 带给 `app.main`）；
  已开着窗口：单实例转发 argv → emit `tavotto:open`。两条路汇进前端同一个
  `lib/openRequest.ts`（浏览器模式共用同一套查询参数，定位逻辑只有一份）。
- **`tavotto open script.py` 自动 safe probe（2026-08-26，Session 6）**：
  显式给出 `.py` = 运行意图（总纲原则 5）。`handoff.resolve_script_route`
  的顺序：现有注册表/静态发现的每张图都已有路由（磁盘原件或 runtime
  cache，判据各自唯一：`figcapture.find_original_artifact` /
  `runtimeasset.load_metadata`）→ 复用；否则 probe——本机实例在跑就
  **委托**（`POST /api/registry/probe`，同一个 `_PROBES` 并发闸，409 →
  `probe_in_progress`），否则本进程 `probe_and_register` + 物化 cache
  （只复制热 worker 的预览，绝不二次执行），返回前 `pool.invalidate`
  关净 worker（**不留 orphan**；交接目标进程读注册表 + cache，零重跑，
  看护 `tests/test_open_script_route.py` 的 execution-count 用例）。
  单图直达 stem；多图 `--stem` 显式选或把 `pick` 交给界面选择器，
  `--no-launch` 下必须显式选（`multiple_figures_found`）。稳定 code 表
  在 `docs/handoff-protocol.md`（missing_dependency 映射成
  `native_run_required`，原始 code 在 extra）。`--no-probe` 关掉探测。
- **macOS 唤起走 `open -na <bundle> --args …`，不再直接 exec 包内二进制**
  （2026-08-20 实测修复）：GUI 进程会继承调用方的执行上下文，从受限环境
  （沙箱 shell、无 Aqua 会话）直接 exec 会在 AppKit `RegisterApplication`
  处 SIGABRT——**转发 argv 的第二个实例也一样崩**（NSApplication 初始化先于
  单实例检查），所以旧注释「open 送不到、只能直接 exec」只说对了不带 `-n`
  的那半：`-n` 起的新实例照样把 argv 交给单实例插件转发。`open` 把 spawn
  委托给 launchd，App 落在用户 GUI 会话里。Windows / 裸二进制覆盖仍直接
  spawn。
- **桌面模式的 `ok: true` 是等出来的**（`_launch_desktop_via_open` /
  `_launch_desktop_via_spawn`，带限期轮询、可注入时钟，**不是 sleep**）：
  进程存在且活过稳定窗（或单实例转发完成）才算成功；起来就死回
  `launch_failed` + `exit_code`/`signal`/`log_path`/`retryable`（HandoffError
  的 `extra`，`--json` 逐键并入输出），限期内没出现回 `launch_timeout`。
  sidecar 日志路径由 `handoff.sidecar_log_path()` 按 `brand.DESKTOP_BUNDLE_ID`
  推导（与 tauri 的 app_log_dir 同源）。看护 `tests/test_desktop_launch.py`。
- **前端交接三条纪律**（`applyOpenRequest`）：① 同项目**绝不**调
  `projectStore.open`（那条路 switchDocument 成空白文档，用户排的版当场没）；
  ② 必须重扫素材（交接的图刚写到磁盘，实例手里那份 panels 是旧的）；
  ③ 找不到就说找不到，绝不退而求其次选别的面板。重复交接同一张只选中，不叠第二份。

## 浏览器 playground（引擎侧）

`engine/browser.py` 平铺 import `manifest/overrides/pathgeom/patchspec` 与
`figcapture`（**语义与捕获策略都只有一份实现**，与 worker.py 同一条 sys.path
纪律，不许出现 browser_manifest.py 这类分叉）。engine.zip 的模块白名单在
`scripts/build_browser_playground.py` 的 `ENGINE_FILES`：**加一个 flat import
就得同步加进去**。完整的 playground 纪律（Pyodide、完整性校验、案例库、
预热）在 `web/AGENTS.md` 与 `docs/adr/0007-browser-playground.md` /
`docs/adr/0011-playground-examples-first.md`。
