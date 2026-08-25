# Compatibility Bridge Session Handoff

> 每个 Session 结束前必须更新本文件。

## 当前状态

- 日期：2026-08-25
- 当前 branch：`compat/bridge-session02-execution-spec`（worktree
  `.claude/worktrees/compat-bridge-session01`，stacked 在
  `compat/bridge-session01-audit` 之上；两支均未推送——按审计 §七，
  Session 2–6 合成一个 PR 1 再走 push → PR → merge）
- 基于 commit：Session 1 落库提交 `032d487`（其基线为 origin/main `2b1ea06`）
- 本 Session Prompt：`03_SESSION_02_EXECUTION_AND_CAPTURE_MODEL.md`（外部实施包）
- 目标 PR：PR 1（本 Session 交付其数据模型层）
- 当前工作树状态：clean（提交见 git log）

## 本轮唯一目标

建立统一的执行描述（ExecutionSpec）与捕获 Figure 描述
（CapturedFigureDescriptor），让 worker、probe、browser、handoff 共享同一份
语义。**不改普通用户 UI，不实现 Runtime Figure Asset，不实现 `tavotto run`。**

## 已完成

- [x] `engine/execspec.py`：ExecutionSpec（frozen dataclass，safe/native 两档
  字段齐备）；`safe_spec()` 唯一权威构造函数；`worker_argv()` 唯一 argv
  出处；`to_payload()` / `spec_from_payload()` JSON 往返；`stable_payload()`
  跨机器稳定子集
- [x] `engine/figcapture.py`：CapturedFigureDescriptor + `build_descriptor()`
  工厂（writeback 能力只能派生）、`runtime_asset_id()`（ADR 0013 §2 形态）、
  `source_fingerprint()`（stale hint，诚实边界写明）、`size_mm_of()`、
  `find_original_artifact()`、`ARTIFACT_EXTS`（产物扩展名唯一出处）；
  顺手删除模块 docstring 的重复段（Session 1 遗留项）
- [x] worker：build 时装配描述符缓存，**v1** build 响应新增 `descriptors`
  （加字段不升版；legacy 信封零改动，有用例钉住）
- [x] browser：记录捕获来源（savefig/pyplot），load 响应新增 `descriptors`
  （同一份 figcapture 工厂装配；`figures` 键原样保留）
- [x] pool：`EngineWorker.__init__` 与 `_spawn_spec()` 两条 spawn 路径改为
  execspec 消费者（argv 逐字节不变）；两类 worker 都带 `.spec` 属性
- [x] probe：`probe()` / `probe_and_register()` 返回 `descriptors` 列表
  （成功路径热会话仍留池复用）
- [x] handoff：`_first_on_disk` 改调 `figcapture.find_original_artifact`
  （产物判据单源化）；`discover.OUT_EXTS` / `handoff.OUT_EXTS` 变为
  `figcapture.ARTIFACT_EXTS` 的镜像别名
- [x] 两个受管产物重建且 `--check` 一致（playground 指纹 `0082db964264f917`，
  MCP widget 指纹 `9f3efaf95c826477`）。注意：canvas.html 逐字节未变
  （widget 只打包前端，不含引擎 Python）；`web/dist-playground` 在
  gitignore 内（engine.zip 含新 figcapture/browser），**PR 1 合并后要
  re-sync 网站仓库**（`pnpm sync-playground`）
- [x] ADR 0013（§2/§5 落地记录）与 ADR 0014（§0 落地记录）更新
- [x] 测试：`tests/test_execspec.py` 37 项新增；
  `tests/test_compat_capture_parity.py` +10 项（描述符对拍 / probe 集成 /
  legacy 兼容）；三条负向反证完成（见下）

## 未完成

- [ ]（无——本轮范围内全部完成）

## 本轮关键决策

### 决策 1：entry 不进 asset id（沿 ADR 0013 决策 2，偏离 Prompt 的"建议"）

- 决策：`runtime_asset_id(script, stem)` 只由脚本相对路径 + stem 决定；
  Prompt "建议以 script + entry + stem + schema version 生成" 未采纳
  entry 与 schema version 两项。
- 理由：注册表里一个脚本只有一个 entry，(script, stem) 已唯一；entry 进 id
  的话，用户改脚本换入口重新探测后同一张图变新身份，历史 override 全成孤儿
  （正是 FigS3 / fallback-stem 两次事故要防的形态）。schema version 进
  **fingerprint** 而不进 id：id 要的是跨版本稳定，指纹要的才是失配提示。
- 看护：`test_execspec.py::TestAssetId`（含 entry 刻意不进 id 的显式用例）。

### 决策 2：fingerprint = 内容哈希 + spec 稳定字段 + matplotlib 版本 + schema 版本

- 决策：`source_fingerprint()` 吃 `script_bytes` 与 stable 字段（签名里
  **没有**任何绝对路径参数），输出 `sha256:…`。
- 诚实边界：只是 stale hint——CSV / 本地模块 / 环境变量 / 数据库不在内，
  文档与 docstring 都写明，UI 文案将来不得声称覆盖一切。
- ADR 0013 §5 的"fingerprint = 脚本 sha1"草案已按落地口径更新（写回防线的
  `pool.script_sha1` 另属阻断机制，两者不合并）。

### 决策 3：spec.env 只存增量；两条控制面的 env 机制照旧

- 决策：`ExecutionSpec.env` 是序列化形态的**注入增量**（bundled runtime 时
  即 `runtime.child_env(base={})`），`None` = 原样继承。EngineWorker 的
  Popen 仍用全量 `child_env()`（含摘除敌意变量），workerd 仍只传增量——
  与重构前逐字节一致。
- 未采纳：把 env 的落地方式也收进 spec——那会改变两条控制面的现网行为
  （EngineWorker 有敌意变量摘除、workerd 没有，是**既有**差异），本轮
  纪律是"重构不改语义"。

### 决策 4：descriptors 只进 v1 响应；browser 的 `figures` 键保留

- v1 加字段不升版（ADR 0003 §1）；legacy 信封一字不改（用例
  `test_the_legacy_envelope_is_untouched` 钉住）。workerd 对响应字段是
  透传的（`session.rs::request` 只滤信封字段），两条控制面都带 descriptors。
- browser 旧的 `figures`（stem/size_mm/preview）继续存在：前端 playground
  还在消费它，Session 2 不改 UI。

### 决策 5：original_artifact 判据 = 项目根 + stem + ARTIFACT_EXTS 顺序

- 与 handoff `_first_on_disk` 历史行为完全一致（现已同源）；只对
  `capture_source == "savefig"` 的 stem 查，pyplot 捕获**结构上**拿不到
  产物（工厂对 "pyplot + artifact" 直接抛）。磁盘上碰巧同名的文件不会让
  show-only 的图变成"可写回"（有用例 + 反证 #3）。
- 已知边界：paper_style 方言存到子目录的产物（如 `figures/Fig1.pdf`）不在
  项目根一层，本判据查不到 → `can_writeback_artifact` 保守为 False。这与
  现网写回面板（`scan_panels` 递归扫描）口径不同，属**保守方向**的差异
  （少报能力，不会误报）；Session 4 做 RuntimeFigureAsset 时若要放宽，
  必须与 `scan_panels` 的消费点一起裁决（审计风险 #2）。

## 架构与数据契约

### 新增/修改类型

```text
engine/execspec.py
  ExecutionSpec(profile, interpreter, target_kind, target, entry, argv,
                cwd, env, project_root, passthrough_savefig)
  safe_spec() / worker_argv() / spec_from_payload() / stable_payload()
  SPEC_VERSION = 1

engine/figcapture.py
  CapturedFigureDescriptor(asset_id, script, entry, stem, capture_source,
      execution_profile, original_artifact, size_mm, source_fingerprint,
      can_writeback_artifact, can_writeback_source)
  build_descriptor() / descriptor_from_payload() / runtime_asset_id()
  source_fingerprint() / size_mm_of() / find_original_artifact()
  PROFILE_SAFE / PROFILE_NATIVE / ARTIFACT_EXTS / DESCRIPTOR_VERSION = 1
```

### API/协议形状

```jsonc
// worker v1 build 响应（新增键；stems 原样）：
{ "stems": {"show_only": {"size_mm": [162.56, 121.92], "source": "pyplot"}},
  "descriptors": [{
     "asset_id": "runtime:show_only.py#show_only",
     "script": "show_only.py", "entry": "__main__", "stem": "show_only",
     "capture_source": "pyplot", "execution_profile": "safe",
     "original_artifact": null, "size_mm": [162.56, 121.92],
     "source_fingerprint": "sha256:…",
     "can_writeback_artifact": false, "can_writeback_source": false }] }
// browser load 响应：同形 descriptors 数组（figures 键不变）
// probe.probe / probe_and_register：结果 dict 新增 "descriptors": [...]
```

协议版本不升（加字段，ADR 0003 §1）；legacy 信封零改动。

### 稳定错误码

```text
无新增（模型层坏数据一律 ValueError 在边界上抛，不进协议错误码表）。
```

### Schema/version 变化

- 用户文档 schema：**零改动**（descriptors 尚未持久化——那是 Session 4 的
  RuntimeFigureAsset）。
- 新增两个模型自己的版本号：`execspec.SPEC_VERSION = 1`、
  `figcapture.DESCRIPTOR_VERSION = 1`（后者参与 fingerprint）。

## 修改文件

| 文件 | 修改原因 | 是否有测试 |
|---|---|---|
| src/tavotto/engine/execspec.py | 新增：ExecutionSpec 唯一模型 | test_execspec.py |
| src/tavotto/engine/figcapture.py | 描述符唯一实现 + ARTIFACT_EXTS + 删重复 docstring | test_execspec.py + parity |
| src/tavotto/engine/worker.py | v1 build 带 descriptors | parity + test_worker_protocol |
| src/tavotto/engine/browser.py | 来源记账 + load 带 descriptors | parity + test_browser_session |
| src/tavotto/engine/pool.py | 两条 spawn 路径走 execspec | test_workerd_pool + test_execspec |
| src/tavotto/engine/probe.py | 透传 descriptors | parity::TestProbeReturnsDescriptors |
| src/tavotto/engine/handoff.py | `_first_on_disk` 单源化 | test_handoff（行为不变） |
| src/tavotto/engine/discover.py | OUT_EXTS 镜像别名 | test_execspec（is 断言） |
| （web/dist-playground：重建但 gitignore；canvas.html：重建后逐字节未变） | 受管产物 | 各自 --check |
| docs/adr/0013 / 0014 | 落地记录 | 文档 |
| tests/test_execspec.py | 新增模型契约 | — |
| tests/test_compat_capture_parity.py | +10 描述符/probe/legacy 用例 | — |

## 实际运行的测试

```bash
# worktree 内、PYTHONPATH=src、主仓 .venv 解释器
PYTHONPATH=src .venv/bin/python -m pytest tests/test_execspec.py          # 37 passed
PYTHONPATH=src .venv/bin/python -m pytest tests/test_compat_capture_parity.py  # 37 passed（原 27 + 新 10）
PYTHONPATH=src .venv/bin/python -m pytest tests/test_workerd_pool.py \
    tests/test_worker_protocol.py                                        # 39 passed
PYTHONPATH=src .venv/bin/python -m pytest tests/test_worker_roundtrip.py \
    tests/test_browser_session.py    # 119-37=82 passed + 6 skipped（无 workerd 产物）
PYTHONPATH=src .venv/bin/python -m pytest -x -q                          # 全量（见下）
python scripts/build_browser_playground.py && … --check                  # 指纹一致
python scripts/build_mcp_widget.py && … --check                          # 指纹一致
.venv/bin/python scripts/ci/compat_matrix.py --smoke   # 24 case 全过，product_bug 0
cd web && pnpm test                                                      # 82 文件 913 项全过
```

坑（本轮踩过）：compat_matrix 必须用带 flask 的解释器跑（`.venv/bin/python`）。
本机裸 `python3`（Homebrew）没有 flask，症状是 **replay 阶段整列
`No module named 'flask'` 且被分类成 product_bug**——像大面积回归，其实是
`stage_replay` 里 `from tavotto.app import _compare_manifests` 挂了。

## 负向反证（本轮三条，全部先红后还原）

| # | 变异 | 判据测试 | 结果 |
|---|---|---|---|
| 1 | `runtime_asset_id` 混入 `os.getcwd()`（绝对路径） | `test_asset_id_and_fingerprint_are_stable_across_project_paths` | **红**（还原后绿） |
| 2 | browser `_descriptors` 绕开工厂手拼 dict（id 形态 `browser:…`） | `test_show_only_descriptors_are_identical_on_both_sides` + multi 对拍 | **红 ×2**（还原后绿） |
| 3 | worker 把 pyplot 兜底错标成 `SOURCE_SAVEFIG` | `test_a_coincidental_file_does_not_make_a_pyplot_figure_writable` | **红**（capture_source 断言当场抓住；配合盘上同名文件，能力会翻 True） |

另：模型层的结构性反证由签名保证——`runtime_asset_id` /
`source_fingerprint` 的参数表里**没有**任何绝对路径参数（inspect 断言钉住），
`build_descriptor` 没有 `can_writeback_*` 参数（同样有 inspect 断言）。

## 真机/产品证据

- OS：macOS（arm64，本机开发环境）。产品行为零改变：UI、素材库、
  `tavotto open`、MCP 均未触碰；新增的都是响应里的加字段。
- 受管产物：playground `0082db964264f917`、canvas.html `9f3efaf95c826477`
  （均 `--check` 通过）。
- workerd 腿本机 skip（未 cargo build）；`session.rs::request` 只滤信封
  字段、其余透传，descriptors 在 workerd 控制面同样带出（代码级确认，
  真跑留给 CI 的 workerd 腿）。

## 已知失败与限制

| 问题 | Stage/Route | 严重度 | 是否本轮 | 后续 |
|---|---|---|---|---|
| show-only 四条产品路由不可达 | product_entry × desktop/cli/mcp | 高 | 否（既有） | Session 3–6 |
| probe 登记但无磁盘产物 → 素材库不可见 | asset_model × desktop | 高 | 否 | Session 4–5（ADR 0013） |
| `original_artifact` 只查项目根一层（paper_style 子目录产物报 None，保守方向） | 描述符元数据 | 低 | 是（决策 5，记录在案） | Session 4 与 scan_panels 消费点一起裁决 |
| browser 描述符 entry 恒 `__main__`、original_artifact 恒 None | 记录在案差异 | — | 是（如实体现，不是抹平） | 不修 |
| `dropped_figures` 只进 worker.log，UI 不可见 | capture × desktop | 低 | 否 | Session 4 顺带 |
| workerd 用例本机 skip | 环境 | — | — | CI 覆盖 |

## 不得被下一 Session 破坏的约束

- ExecutionSpec 是唯一运行描述：safe 默认值只出自 `execspec.safe_spec()`，
  worker argv 只出自 `execspec.worker_argv()`——新入口（枚举 .py、批量
  probe）不得再手拼 entry/cwd/argv。
- CapturedFigureDescriptor 是唯一捕获结果语义：worker/browser/probe 之外的
  新消费点（Session 3 的"列出并试运行"）拿的必须是同一份 payload；
  **writeback 能力永远派生，不给任何一层猜的机会**。
- 浏览器与桌面的记录在案差异（figure 总数上限、相对路径回退、entry 超集、
  以及本轮新增的 entry/`original_artifact` 字段值差异）不得被"统一"错。
- legacy 信封形状一字不改；协议加字段不升版。
- figcapture / browser 改动必重建 playground + MCP widget 两个受管产物。
- 项目打开阶段只扫描不执行；probe 的路径校验（项目内 .py）不得放松。
- 1.0 收敛纪律：每轮只动本轮根因；新增不变式测试提交前手工反证一次。

## 下一 Session 唯一目标

> 使用当前项目扫描规则列出所有合理 `.py`，允许任意项目内脚本安全 probe，
> 并保证一次执行与多 Figure 结果正确；暂不实现 Runtime Figure Asset UI。

## 下一 Session 首先阅读

```text
AGENTS.md
CLAUDE.md（含 src/tavotto/AGENTS.md）
docs/compatibility/COMPATIBILITY_BRIDGE_MASTER_PLAN.md
docs/compatibility/COMPATIBILITY_BRIDGE_HANDOFF.md（本文件）
docs/compatibility/compatibility-bridge-audit.md §一/§二（发现层缺口）
src/tavotto/engine/discover.py（iter_scripts / SKIP_PREFIXES / 4 层上限）
src/tavotto/engine/probe.py / execspec.py / figcapture.py（本轮产出）
src/tavotto/app.py（/api/registry 一族端点 + RegistryDialog 的候选过滤）
web/src/…/RegistryDialog.tsx（"试运行"按钮的可见性判据）
```

注意：Session 3 的"列出所有合理 .py"会碰 `discover.iter_scripts` 的
SKIP_PREFIXES 与 4 层上限（审计 §二-1），以及 RegistryDialog 只对
candidates 给按钮的 UI 过滤（审计 §一-3）——**发现维放宽只影响"列给用户
挑"，不得顺手改变自动静态起草的候选口径**（那会改变现有项目打开时的
注册表内容）。

## 建议启动命令

```bash
git status --short
git log -8 --oneline
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_execspec.py \
    tests/test_compat_capture_parity.py tests/test_discover.py
.venv/bin/python scripts/ci/compat_matrix.py --smoke   # 别用裸 python3，见上
```
