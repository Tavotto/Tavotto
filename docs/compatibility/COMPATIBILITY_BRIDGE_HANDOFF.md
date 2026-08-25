# Compatibility Bridge Session Handoff

> 每个 Session 结束前必须更新本文件。

## 当前状态

- 日期：2026-08-25
- 当前 branch：`compat/bridge-session01-audit`（worktree
  `.claude/worktrees/compat-bridge-session01`）
- 基于 commit：`2b1ea06`（origin/main）
- 本 Session Prompt：`02_SESSION_01_AUDIT_AND_ADRS.md`（外部实施包）
- 目标 PR：PR 1（本 Session 是其审计/ADR 前置，不含实现）
- 当前工作树状态：clean（仅新增文档，产品代码零改动）

## 本轮唯一目标

完成事实审计、Pylustrator clean-room 研究、基线记录与两份 ADR 草案。
**不实现任何产品功能。**

## 已完成

- [x] 八条入口的真实调用链审计（`compatibility-bridge-audit.md` §一）
- [x] 十条关键假设逐条实测（§三；ad-hoc 反证脚本输出见下）
- [x] Pylustrator clean-room 研究（`pylustrator-study.md`，commit
  `b0341ee9`，GPL-3.0，不 vendoring / 不引 Qt / 不复制 change tracker）
- [x] Master Plan 落库（`COMPATIBILITY_BRIDGE_MASTER_PLAN.md`）
- [x] ADR 0013 Runtime Figure Assets（Proposed）
- [x] ADR 0014 Safe/Native Execution Profiles（Proposed）
- [x] 基线测试全部跑过并记录（见下）

## 未完成

- [ ]（无——本轮范围内全部完成；两份 ADR 的"待定稿事项"留给 Session 4/7）

## 本轮关键决策

### 决策 1：show-only 缺口定性为"产品入口层"，不是引擎层

- 决策：PR 1 不改捕获/manifest/override 引擎，只补入口与素材模型。
- 证据：CompatBench `shape_pyplot_show_only` 九级漏斗引擎阶段全绿；
  ad-hoc 实测 `probe_and_register` 对任意项目内 .py 成功并按 pyplot 兜底
  stem 登记；被挡住的是 UI candidates 过滤（`analyze_script` 对无存图
  调用脚本返回 None）与四条产品路由。
- 未采用方案：给 `analyze_script` 放宽"无存图调用也算候选"。
- 未采用原因：那只是让候选列表变长，没有解决"无磁盘产物 → 素材库不可见"
  的素材模型缺口；两个根因要分开修（Session 3 vs Session 4）。

### 决策 2：Runtime asset id 形态 `runtime:<script>#<stem>`

- 决策：id 只由 (项目, 脚本相对路径, stem) 决定（ADR 0013 §2）。
- 证据：stem 的跨会话稳定性已由 figcapture 的编号规则保证（模块头有完整
  论证与看护用例）；前缀与现有 fileId（相对路径）字面不冲突。
- 未采用：内容哈希 / 会话 id 参与构成——重跑脚本或重开会话后 override
  会挂错身份。

### 决策 3：native 捕获走注入驱动层，不要求用户改脚本

- 决策：ADR 0014 §3；capture 语义仍是 figcapture 唯一那份。
- 证据：Pylustrator 的兼容优势全部来自"跑在用户自己的 invocation 里"，
  但它要求 `import pylustrator`——Tavotto 不能提这个要求。
- 未采用：pickle Figure 回主进程（ADR 0014 §6 三条理由）。

## 架构与数据契约

### 新增/修改类型

```text
（本轮零实现。方向见 ADR 0013/0014：ExecutionSpec、
CapturedFigureDescriptor、RuntimeFigureAsset、fileKind: 'runtime'）
```

### API/协议形状

```json
{}
```

worker 协议零改动（`stems.<s>.source` 与 `dropped_figures` 已存在，
RuntimeFigureAsset 直接消费）。

### 稳定错误码

```text
（无新增。审计记录的相关既有码：script_not_in_project /
missing_dependency / worker_timeout / script_error /
stem_not_parameterizable / no_figure / registry_invalid …）
```

### Schema/version 变化

无（ADR 0013 §3 提议"可选字段 + 新枚举值、版本不升"，未实施）。

## 修改文件

| 文件 | 修改原因 | 是否有测试 |
|---|---|---|
| docs/compatibility/COMPATIBILITY_BRIDGE_MASTER_PLAN.md | 新增：总纲落库 | 文档 |
| docs/compatibility/compatibility-bridge-audit.md | 新增：事实审计 | 文档（判断均附实测） |
| docs/compatibility/pylustrator-study.md | 新增：clean-room 研究 | 文档 |
| docs/compatibility/COMPATIBILITY_BRIDGE_HANDOFF.md | 新增：本文件 | 文档 |
| docs/adr/0013-runtime-figure-assets.md | 新增：ADR 草案 | 文档 |
| docs/adr/0014-safe-native-execution-profiles.md | 新增：ADR 草案 | 文档 |

## 实际运行的测试

```bash
# 全部在 worktree 内、PYTHONPATH=src 指向 worktree、主仓 .venv 解释器
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_discover.py \
    tests/test_projects.py tests/test_handoff.py
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_compat_manifest.py \
    tests/test_compat_runner.py
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_worker_roundtrip.py
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_compat_capture_parity.py
python scripts/ci/compat_matrix.py --case shape_pyplot_show_only
python scripts/ci/compat_matrix.py --smoke
cd web && pnpm test
```

结果：

```text
112 passed / 113 passed / 62 passed + 6 skipped（无 workerd 产物，环境项）
/ 27 passed
compat --case：引擎九级全过，分类 partial_support（与基线一致）
compat --smoke：24 case 全阶段通过，21 full / 3 partial，product_bug 0
web：82 文件 913 项全过
基线漂移提示（环境事实，非缺陷）：本机 target=current，matplotlib 3.11.1
等与 bundled 锁文件版本不同，报告头如实标注。
```

## 负向反证

本轮无产品修复，无"拿掉修复应变红"的对象。做的是**假设反证**（ad-hoc
脚本，show-only 脚本 `myplot.py` 于临时项目，数据/配置目录隔离）：

```json
{"A_analyze_script": null, "A_discover_scripts": [],
 "B_resolve_target": {"stem": null},
 "C_ensure_registered": {"status": "created", "added_scripts": [],
                          "dynamic_names": [], "parameterizable": null},
 "D_probe": {"entry": "__main__", "stems": ["myplot"], "error": null,
              "registered": true, "tried": ["main", "render", "__main__"]},
 "E_registry_for_stem": {"script": "myplot.py", "entry": "__main__"},
 "E_reused_built": true,
 "F_disk_artifacts": []}
```

| 待反证假设 | 判据 | 结果 |
|---|---|---|
| "probe 只能跑已登记脚本" | D | **证伪**：任意项目内 .py 可 probe |
| "show-only 会进 dynamic_names 报告" | C | **证伪**：零痕迹 |
| "probe 后要重新 build 才能渲染" | E | **证伪**：热会话复用，built=True |
| "文档 schema 能表达无产物 Figure" | 类型定义 | **证伪**：仅 fileId+pdf/raster |

## 真机/产品证据

- OS：macOS（arm64，本机开发环境）——本轮为审计，无产物级验收项。
- Tavotto SHA：2b1ea06
- Python：主仓 .venv（无 matplotlib）+ pool 自动探测到的本机解释器
  （CompatBench target=current）
- 证据路径：会话 scratchpad `base*.log` / `compat_*.log`（临时）；结论
  全部转录于上与审计文档。
- 结果：产品行为零改变（`git status` 仅新增 docs）。

## 已知失败与限制

| 问题 | Stage/Route | 严重度 | 是否本轮 | 后续 |
|---|---|---|---|---|
| show-only 脚本四条产品路由不可达 | product_entry × desktop/cli/mcp | 高 | 否（既有，基线已记 partial_support） | Session 3–6 |
| probe 登记但无磁盘产物 → 素材库不可见 | asset_model × desktop | 高 | 否 | Session 4–5（ADR 0013） |
| `tavotto open script.py` 静态失败只开项目、不引导 probe | product_entry × cli_open | 中 | 否 | Session 6 |
| `dropped_figures` 只进 worker.log，UI 不可见 | capture × desktop | 低 | 否 | Session 4 顺带（ADR 0013 §8） |
| workerd 用例本机 skip（未 cargo build） | 环境 | — | — | 需要时本机构建 |
| figcapture.py 模块 docstring 有一段重复文字（L101-109） | 文档 | 低 | 否（本轮不碰产品文件） | Session 2 顺手删 |

## 不得被下一 Session 破坏的约束

- 只有一套 Figure 编辑语义；figcapture 是捕获策略唯一实现（worker/browser
  平铺 import，改它要重建 playground 与 MCP widget 两个受管产物）。
- Python 池与 workerd 双控制面行为一致；pool.py 的 Python 实现是参考实现，
  引入 ExecutionSpec 时**两条 spawn 路径（`EngineWorker.__init__` 与
  `_spawn_spec`）必须同步、行为零变化**（有 golden/等价矩阵盖着）。
- 浏览器与桌面的**记录在案差异**（figure 总数上限、相对路径回退、entry
  超集）不得在统一 DTO 时被抹平。
- 项目打开阶段只扫描不执行；probe 的路径校验（项目内 .py）不得放松。
- 1.0 收敛纪律：每轮只动本轮根因；新增不变式测试须手工反证一次。

## 下一 Session 唯一目标

> 引入统一 ExecutionSpec 与 CapturedFigureDescriptor，不改 UI，
> 不实现 native run。

## 下一 Session 首先阅读

```text
AGENTS.md
CLAUDE.md（含 src/tavotto/AGENTS.md）
docs/compatibility/COMPATIBILITY_BRIDGE_MASTER_PLAN.md
docs/compatibility/COMPATIBILITY_BRIDGE_HANDOFF.md（本文件）
docs/compatibility/compatibility-bridge-audit.md §一/§六
docs/adr/0013-runtime-figure-assets.md
docs/adr/0014-safe-native-execution-profiles.md §0
src/tavotto/engine/pool.py（EngineWorker.__init__ 与 _spawn_spec 两处）
src/tavotto/engine/figcapture.py / worker.py / browser.py
```

## 建议启动命令

```bash
git status --short
git log -8 --oneline
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_worker_roundtrip.py \
    tests/test_compat_capture_parity.py
python scripts/ci/compat_matrix.py --smoke
```
