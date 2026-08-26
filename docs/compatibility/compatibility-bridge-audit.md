# Compatibility Bridge 事实审计（Session 1，2026-08-25）

> 审计基线：`main` @ `2b1ea06`（worktree 分支 `compat/bridge-session01-audit`）。
> 本文只记录**由代码与实测证明的现状**，不含实现。总纲在
> `COMPATIBILITY_BRIDGE_MASTER_PLAN.md`，交接在
> `COMPATIBILITY_BRIDGE_HANDOFF.md`。所有"实测"均给出命令，可复现。

---

## 一、当前入口的真实调用链

十个维度：项目根 / 解释器 / entry / stem / 何时执行 / 何时捕获 /
素材从哪来 / 是否要求磁盘产物 / worker 会话复用 / 错误怎样到达用户。

### 共同底座（所有入口最终汇入）

```text
pool.get(script, figures_dir, entry)            # 池键 = (norm(项目), 脚本)
  → select_worker_python()                      # 解释器唯一出处 _prioritized_candidates()
    env override → 用户配置 → 内置 runtime → 自身 → 系统探测
  → spawn worker.py --script --figures-dir --out-dir --sandbox --entry
      worker.build():
        cwd = 沙盒；sys.path = [图库根, 脚本目录]
        Path.unlink / Path.write_text 守卫（挡真实图库写删）
        figcapture.install_relative_read_fallback（只读回退）
        拦截 Figure.savefig（吞掉写盘，按 stem 捕获）+ paper_style.save
        sys.argv = [脚本自身]
        entry == "__main__" → runpy.run_path；否则 import + getattr(module, entry)()
        figcapture.collect_pyplot_figures（show-only 兜底，上限 8，丢弃有报告）
      → 每个 stem：FigState → instrument → manifest → 预览 SVG
```

之后 manifest / override / undo / replay / export 全部具备（引擎语义只有
这一份；workerd 控制面等价，`TAVOTTO_WORKERD=0` 回 Python 池）。

### 1. 桌面打开目录（ProjectPicker / 壳 argv `--open <dir>`）

- 项目根：用户选的目录本身（`open_project`）；**首次打开若无注册表，自动
  静态起草**（`app.py:1017-1024` → `discover.build_draft` + `write_config`）。
- 解释器 / entry / stem：打开阶段不执行任何脚本（只扫描——总纲原则 5 已成立）。
- 素材：`scan_panels()`（`app.py:434`）——**只列磁盘上真实存在的 PDF/图片**，
  按 `registry.for_stem(p.stem)` 标注可参数化（⚡）。
- 要求磁盘产物：**是**。没有产物的脚本在素材库彻底不可见。
- 执行/捕获：首次对某面板发 `/api/engine/render` 时 lazy build。
- 错误：worker 错误带稳定 code（`missing_dependency` / `worker_timeout` /
  `script_error`…）经 `_worker_error_payload` 到前端，SSE `render.failed`。

### 2. 桌面打开一张磁盘图（素材库 → 画布 → 双击）

- `PanelObject.fileId`（图库相对路径）是身份；`/api/engine/render` 里
  `_engine_worker()` = `safe_resolve(rel_id)`（**必须是项目内真实文件**，
  404 否则）+ `registry.for_stem(path.stem)`（None → 404，前端显示
  "不可参数化"）。
- entry/脚本：全部来自注册表；stem = 文件名主干。
- 会话复用：池按 (项目, 脚本) 复用；同脚本多 stem 共用一条会话。

### 3. 桌面 RegistryDialog probe（设置 → 脚本注册表 → 试运行）

- 后端 `/api/registry/probe`（`app.py:1449`）：**接受项目内任意 .py**
  （唯一校验：`.py` + `is_file` + 路径在项目内）→
  `probe.probe_and_register()`。
- 但 **UI 只对两类脚本给"试运行"按钮**（`RegistryDialog.tsx`）：
  `/api/registry` 报告里的 candidates（= `discover.analyze_script` 解得出
  的脚本，**要求至少有一处存图调用**，`discover.py:665-666`）与已登记脚本。
  没有 savefig 的脚本连 candidates 都进不去 → **没有任何 UI 入口能对它
  probe**（后端能力被 UI 挡住）。
- probe 内部：每换一个 entry 候选（静态推断 → main → render → `__main__`）
  先 `pool.invalidate` 再 `pool.get`；**成功后不 invalidate——build 好的
  热会话留在池里，随后的渲染直接复用**（实测 `built=True`，见 §三-E）。
- probe 产出的 stem 是**真实产出**（含 pyplot 兜底 stem），写进
  `tavotto_registry.json`。但若无磁盘产物，登记完素材库仍然看不见（§二-6）。

### 4. `tavotto open <artifact>`（产物路径）

- `resolve_target`：项目根 = 向上 ≤3 层找注册表（新旧两个文件名都认），
  找不到 = 图所在目录；stem = 文件名主干。
- `ensure_registered`：缺登记则 `discover.merge`（静态，现有条目永远优先）；
  解不出 → `parameterizable: false`，**提示用户去 UI 里试运行**，自己不 probe。
- 唤起：桌面 App（`--open <dir> --stem <s>` argv 契约 / 单实例转发）→
  已跑实例（HTTP + relaunch nonce）→ 新起浏览器模式。`ok` 是等出来的。
- 错误：`HandoffError` 稳定 code，`--json` 时失败也是一行 JSON。

### 5. `tavotto open <script.py>`

- `resolve_target`：`_script_stems` = **纯静态**（`discover.analyze_script`）；
  多产物优先取磁盘上存在的那个。
- **静态解不出 → `Target(project, stem=None)`**：只打开项目、不带面板、
  不 probe（实测见 §三-B）。show-only 脚本更糟：`analyze_script` 返回 None，
  它连 `dynamic_names` 报告都进不去（实测 `dynamic_names: []`，状态
  `created` 但 `added_scripts: []`——注册表里没有它的任何痕迹）。
- **不存在"自动 safe probe"路径**（PR 1 的目标之一，当前缺）。

### 6. 浏览器 Playground（网站 /try 上传脚本）

- `engine/browser.py`：源码进 Worker 虚拟 FS → `runpy.run_path` →
  savefig 拦截 + **同一份 figcapture** pyplot 兜底 → FigState/manifest/
  override/export 全同源。
- 不需要注册表、不需要磁盘产物、不需要 entry（一律按 `python figure.py`
  语义跑）——**三个入口里唯一对 show-only 脚本可达的**。
- 差异（记录在案）：单文件、无相对路径回退（报 `missing_file` 是对的）、
  figure **总数**上限 8（桌面只限 pyplot 兜底部分）。

### 7. Codex / MCP 打开 Figure（`tavotto_open_figure`）

- 路径授权走 RootAuthority；项目/登记复用 `handoff.ensure_registered`
  （**静态**）；`bridge._pick_stem`：显式 stem 必须已登记
  （否则 `stem_not_parameterizable`），不点名时要求"已登记 **且** 产物在
  磁盘上"（否则 `no_figure` / `stem_required`）。
- 渲染走同一个 `engine.pool`。**没有 probe 入口**——show-only /
  动态命名脚本在 MCP 侧同样不可达。

### 8. CompatBench（`scripts/ci/compat_matrix.py`）

- 漏斗：discover → execute → capture → open → semantic → edit → replay →
  export → fidelity。`discovery: requires_probe` 的 case **直接调
  `engine_probe.probe_and_register()`**（`compat_matrix.py:165`），随后
  直接拿 pool worker 走各阶段。
- **量的是引擎，不是产品入口**：桌面素材库、RegistryDialog、
  `tavotto open`、MCP 这四条产品路由都没有被覆盖（browser 有对拍
  `_browser_verdict`，是唯一贴近产品路由的一段）。基线里
  `shape_pyplot_show_only` 的 reason 已如实记为 partial_support。

---

## 二、按层的失败模型（对照总纲第一节，逐层核对现状）

| # | 层 | 现状判定 | 证据 |
|---|---|---|---|
| 1 | 文件发现 | **部分缺**：`iter_scripts` 递归 4 层、剪 PRUNE_DIRS，但 `SKIP_PREFIXES`（`_`/`test_`/`setup`/`conftest`/`paper_style`）与 4 层上限会漏真实脚本；且发现结果只喂静态分析，"列出所有 .py 供用户挑"的入口不存在 | `discover.py:584-612` |
| 2 | 静态分析 | 覆盖面很广（抽象求值 + 跨函数传播），但**结构性盲区**：无存图调用的脚本返回 None（show-only 隐形）；stem 来自运行期数据的进 `dynamic_names`（有报告） | `discover.py:640-672`；实测 §三-A |
| 3 | 产品入口 | **主要缺口**：probe 后端能跑任意项目内 .py，但 UI 只对"静态解得出的候选 + 已登记"给按钮；`tavotto open script.py` 不 probe；MCP 不 probe | §一-3/5/7 |
| 4 | 运行环境 | safe 模式已尽力（解释器五级优先、argv 替换、相对路径只读回退、missing_dependency 分诊）；**native 语义不存在**（用户的 venv/cwd/argv/env 组合无法原样复现——只能全局换解释器） | `pool._prioritized_candidates`；ADR 0014 待定 |
| 5 | Figure 捕获 | **引擎已解决**：savefig / paper_style / show-only / 多 Figure 都捕获（figcapture 唯一实现，worker+browser 共用；上限 8 有报告）。`source: savefig|pyplot` 已随 build 响应带出 | `figcapture.py`；`test_compat_capture_parity.py` 27 项全绿 |
| 6 | 素材模型 | **结构性缺**：素材 = 磁盘文件（`scan_panels`），文档面板 = `fileId + fileKind: 'pdf'|'raster'`（`web/src/types/document.ts:73`）。"没有原始产物的运行时 Figure"在 schema 上无法表达 | §一-1/2 |
| 7 | 语义识别 | 已有 artist family 能力层 + census 诊断；与本计划正交（长期增强 Session 10） | `docs/architecture/matplotlib-artist-capability-map.md` |
| 8 | 重放与导出 | 引擎侧完备（四路等价矩阵、写回事务、状态中立 export）；**对 runtime figure 的"保存/重开/重放"因 6 不存在而未定义** | `tests/test_equivalence_matrix.py` |

---

## 三、关键假设的实测验证

反证脚本：scratchpad `audit_probe_check.py`（临时项目 + show-only 脚本
`myplot.py`，只 import 引擎模块，隔离 `TAVOTTO_DATA_DIR`/`CONFIG_DIR`，
`TAVOTTO_WORKERD=0`）。输出全文见交接文档。

| # | 假设 | 结果 |
|---|---|---|
| A | `analyze_script(show-only)` | **None**；`discover()` 的 scripts 为空 → 不进 candidates、不进 dynamic_names |
| B | `resolve_target("myplot.py")` | `stem: None`（只开项目） |
| C | `ensure_registered` | `status: created`，`added_scripts: []`，`dynamic_names: []`——注册表里零痕迹 |
| D | `probe_and_register(任意 .py)` | **成功**：tried `[main, render, __main__]`，entry=`__main__`，stems=`["myplot"]`（pyplot 兜底 stem），registered=true |
| E | probe 后 `pool.get` | 同会话复用，`built=True`（probe 成功路径不 invalidate） |
| F | 磁盘产物 | 无（沙盒吞掉 savefig；show-only 本来就不存盘）→ `scan_panels` 形态的入口看不见 |
| G | 捕获同源性 | `Figure.savefig` 拦截只有 worker.py / browser.py 两处，都经 figcapture；无第三份 |
| H | 文档 schema | `PanelObject` 只有 `fileId/fileKind`，无 runtime figure 表达 |

其余由既有测试证明：worker 捕获矩阵与素材真实性
（`test_compat_capture_parity.py`，27 passed——其中
`TestNoFakeWriteBackTarget::test_panel_scan_only_lists_real_files` 正是
假设 4 的看护）；build 后 manifest/override/replay/export
（`test_worker_roundtrip.py`，62 passed 6 skipped，skip 全部是本机没有
workerd 产物）。

## 四、基线测试记录（2026-08-25，本机 macOS arm64）

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_discover.py \
    tests/test_projects.py tests/test_handoff.py        # 112 passed
PYTHONPATH=src .venv/bin/python -m pytest -q \
    tests/test_compat_manifest.py tests/test_compat_runner.py   # 113 passed
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_worker_roundtrip.py
    # 62 passed, 6 skipped（无 workerd 产物，环境缺失非缺陷）
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_compat_capture_parity.py
    # 27 passed
python scripts/ci/compat_matrix.py --case shape_pyplot_show_only
    # 九级漏斗引擎阶段全过；分类 partial_support（产品入口不可达，与基线一致）
python scripts/ci/compat_matrix.py --smoke
    # 24 cases 全阶段通过；21 full / 3 partial；product_bug 0；门禁 nightly 通过
cd web && pnpm test    # 82 文件 913 项全过（含 RegistryDialog）
```

已有失败/偏差分类：

- **环境缺失**：workerd 6 skip（本机未 cargo build）；CompatBench 本机
  target=`current`（基线为 `bundled`，matplotlib 3.11.1/numpy 2.4.3 等与
  锁文件有版本漂移，报告头如实提示）——均非缺陷。
- **当前 main 已有缺陷**：无新发现。基线 149 case：118 full / 18 partial /
  7 unsupported_by_design / 6 environment_dependency / **0 product_bug**。
- **审计误判**：无——所有假设均按实测修订（例如"probe 只作用于已登记脚本"
  是错的：后端接受任意项目内 .py，被挡住的是 UI）。

---

## 五、"引擎支持但产品入口不可达"的具体 case

1. **show-only 脚本**（`plt.plot(...); plt.show()`，AI 最常见输出）：
   引擎九级全过（CompatBench 实测），但桌面素材库（无产物不可见）、
   RegistryDialog（无存图调用不进候选）、`tavotto open`（静态不 probe）、
   MCP（`stem_not_parameterizable`）四条产品路由全不可达。仅浏览器
   playground 可达。基线 `shape_pyplot_show_only` 的 reason 已完整记录。
2. **动态命名脚本已 probe 登记、但从未在真实目录跑过**（产物不在磁盘）：
   注册表有条目、素材库无面板——登记成功却"什么都没发生"。
3. **无入口函数且不 savefig 的多 Figure 脚本**：同 1，且桌面对超过 8 张的
   兜底有截断（stderr 有报告，产品 UI 看不到这句话——它只写进 worker.log）。
4. **`tavotto open script.py` 指向静态解不出的脚本**：打开的是项目根
   （可能只有这一个孤儿脚本的目录），没有报错、没有引导 probe 的动作，
   人类可读输出里才有一句"用试运行探测登记"。

## 六、可复用 / 必须新增 / 不应重写

**可复用（一行不用改就能撑起 PR 1 的）**

- `figcapture`：捕获策略唯一实现（含 `SOURCE_SAVEFIG|PYPLOT` 来源标记）；
- `probe.probe_and_register`：entry 候选轮换 + 真实产出登记 + 失败取第一个
  候选的错误；
- `pool`：解释器五级优先、会话复用（probe→渲染热接力已经成立）、超时/
  重建纪律、`one_shot` 干净重放、workerd 双控制面；
- worker build 响应里的 `stems.<s>.source` 与 `dropped_figures` 字段——
  RuntimeFigureAsset 需要的"有没有原始产物"信号已经在协议里；
- `handoff` 的目标解析 / 登记 / 唤起编排与稳定错误码体系；
- CompatBench 漏斗、基线纪律与 `_browser_verdict` 对拍模式（产品路由维
  只需在其上扩列，不需要重做）。

**必须新增（PR 1）**

- 统一 ExecutionSpec / CapturedFigureDescriptor（Session 2）：目前
  spawn 规格在 `EngineWorker.__init__` 与 `_spawn_spec` 两处拼 argv，
  entry/cwd/argv 语义散在 worker 参数里；捕获描述散在 build 响应里；
- "列出项目内全部 .py 供用户主动试运行"的发现维（Session 3：
  `iter_scripts` 放宽为候选枚举 + UI 任选脚本 probe）；
- RuntimeFigureAsset（Session 4，ADR 0013）：稳定 asset id、cache
  materialization、文档 schema 表达、无产物时禁 artifact writeback；
- 素材库入口"脚本 → 运行并发现图"（Session 5）；
- `tavotto open script.py` 静态失败时的 safe probe（Session 6）；
- CompatBench 产品路由维（desktop_project / cli_open / safe_probe /
  browser_playground / native_run）（Session 6）。

**不应重写**

- manifest / overrides / patchspec / pathgeom（语义四模块，唯一实现）；
- 写回事务（prepare→verify→commit + 像素门）；
- 前端渲染态（renderStore 按 fileId+overrides 分键）——RuntimeFigureAsset
  要以"新的 fileId 形态"接入而不是另起一套渲染态；
- 会话认证边界（新端点一律过 guard）；
- workerd 协议（加字段不升版；RuntimeFigure 不需要协议改动——stems 的
  `source` 已经带出）。

## 七、PR 边界

- **PR 1（Session 2–6）**：safe 导入产品入口。ExecutionSpec/
  CapturedFigureDescriptor → 任意脚本 probe → RuntimeFigureAsset →
  素材库入口 → `tavotto open script.py` safe probe → 产品路由 CompatBench。
- **PR 2（Session 7–9）**：native 执行。ADR 0014 定稿 → Python invocation
  parser → `tavotto run`（原解释器/cwd/argv/env）→ 桌面交接 → 真机 E2E
  与安全审计。
- Session 是上下文边界，PR 是根因边界；不拆成 9 个 PR。

## 八、真实风险排名（高→低）

1. **RuntimeFigureAsset 的稳定 asset id 与文档 schema**（数据损坏级）：
   id 一旦依赖会话/路径/时序，保存重开后 override 挂错对象——正是
   fallback-stem 事故（figcapture 模块头）在素材层的重演。ADR 0013 必须
   先定 id 规则再写代码。
2. **"素材 = 磁盘文件"假设散布在前后端**（`scan_panels`、`safe_resolve`、
   `assetStore`、`renderStore` 键、写回对话框、导出合成、包检视）：漏改
   一个消费点就是"面板显示了另一个面板的图"级别的错位——修判据必须
   sweep 全部消费点。
3. **native 模式的安全边界**（PR 2）：脚本以用户权限跑真实目录；确认流程、
   MCP 授权（模型给的路径不是授权）、与 safe 文案的诚实区分，一步走错就是
   安全事故。ADR 0014 先行。
4. **probe 的执行成本与预期管理**：任意脚本可试运行 = 用户会对分钟级脚本
   点按钮；超时/取消/进度（SSE）不做好，表现是"点了没反应"。
5. **CompatBench 产品路由维的假绿**：路由检查若只验"函数能调"而不验
   "用户可达"，就是又一层空门禁；每条路由维加用例时必须做反证（拿掉
   入口，路由维必须红）。
6. **浏览器/桌面刻意差异被"统一"错**：figure 总数上限、相对路径回退、
   entry 超集是记录在案的差异，Session 2 统一 DTO 时不得顺手抹平。
