# #874 纹理颜色（hatchcolor）可编辑：调研报告

> 调研，不含功能实现。基线 `origin/main 711b44cb8`，分支 `research/hatchcolor-874`。
> 实测环境：matplotlib 3.10.0 / 3.10.9 / 3.11.0 / 3.11.1 / 3.11.2（`--only-binary` 临时 venv，没动主 .venv），
> Tavotto 真 worker（`pool.one_shot` → `override` → `preview_png`）。
> 脚本（不进产品）：`scripts/dev/hatchcolor_matrix_probe.py`、`hatchcolor_triggers_probe.py`、`hatchcolor_alpha_undo_repro.py`。
> 判据按「Measure, don't trust the comment」：下面每个数字都是跑出来的，不是读文档读出来的。

## 0. 结论（先说）

1. **main 上的现存缺陷确认存在**：只改 alpha（或单柱 / 形状的 `fill`）再撤销，在 3.11.x 上凡是
   「脚本在 `rc_context` / `style.context` 里把 `hatch.color` 设成非环境值再建 patch」的图，热态 ≠ 全新重放
   （像素不同，manifest 相同，warnings 为空）。3.11.0 / 3.11.1 / 3.11.2 三个版本一致。≤3.10 上 alpha 无此问题，
   但 `fill` 撤销另有一条同族缺陷（见 §2）。
2. **根因是同一个**：3.11 起 `Patch` / `Collection` 的 `set_alpha` 与 `set_fill`（Patch）会把「未设」模式
   （`_original_hatchcolor is None`）的花纹色按**当前** `rcParams['hatch.color']` 重新解析，而创建时用的是
   `rc_context` 里的值。Codex 在 #860 报的 `'edge'` 哨兵、自带 alpha 的 RGBA 都是这同一条的不同取值，**不是两个独立缺陷**。
3. **有一个很小的修法能把三条一起收掉**：在任何会重解析的 setter 动手之前，把「未设」模式**钉成创建时已解析的值**
   （一次 `p.set_hatchcolor(p._hatch_color)`，Collection 用 `_hatchcolors`）。已用 matplotlib 层原型实测：3.11.0/1/2
   的 64 格矩阵从 8 格 DIVERGE 变 0，`'edge'`（环境 red）与半透明 RGBA 往返都回到创建值。
4. **建议：先修现存缺陷（独立 PR，高风险档但很小），再做纹理颜色编辑**，且纹理颜色字段**只在有
   `set_hatchcolor` 的版本（3.11+）上发**。先修的好处：钉住之后，hatchcolor 的 getter 就是普通的
   `_original_hatchcolor`，不需要再造一个自定义快照类。

## 1. 实测表（问 1）

### 1.1 matplotlib 源码事实（实读 3.10.9 / 3.11.2，非凭文档）

| | 3.10 | 3.11 |
|---|---|---|
| 公开 API | 无 `set_hatchcolor` / `get_hatchcolor` | 有（`Patch` 与 `Collection` 都有） |
| rc `hatch.color` 合法值 | 颜色；**`'edge'` 报 ValueError**（脚本里 `rc_context({"hatch.color":"edge"})` 直接炸） | 颜色或 `'edge'`；**默认值是 `'edge'`**（3.10 默认 `'black'`） |
| 存储 | 只有 `_hatch_color`（RGBA）；创建时读 rc，之后**被 `_set_edgecolor` 顺手覆盖**（有边色时 = 边色） | `_original_hatchcolor`（模式：`None` / 颜色 / `'edge'`）+ `_hatch_color`（解析值：`'edge'` 或 RGBA） |
| `set_alpha(a)` | 重算面 / 边；花纹色靠边色连带 | 重算面 / 边 / **花纹色**：`_set_hatchcolor(self._original_hatchcolor)`；`None` 经 `mpl._val_or_rc` 读**此刻**的 rc |
| `set_fill(b)`（Patch） | 重算边色 → 无边色且 `not fill` 时 `_hatch_color = patch.edgecolor` | 同样重算 `_set_hatchcolor(self._original_hatchcolor)` |
| `set_edgecolor` | 顺手写 `_hatch_color`（有边色时） | **不碰**花纹色 |
| `to_rgba(color, alpha)` | `_alpha` 非 None 时**覆盖**颜色自带 alpha；为 None 才保留自带 alpha（两版相同） |

### 1.2 `set_alpha(0.5)` → `set_alpha(脚本 alpha)` 往返（`hatchcolor_matrix_probe.py`）

条件：创建后环境 `hatch.color` 回到默认（3.11 = `'edge'`，3.10 = `'black'`）。花纹色单位：RGBA 或哨兵。
`base` = 创建后；`α=0.5` = 改 alpha 后；`undo` = 撤销（set_alpha 回脚本值）后；末列 = undo 是否等于 base。
脚本 alpha `a0` 取 `None` / `0.8`；边色取未设 / `green`（边色不影响 3.11 的结论，下表合并）。

**3.11.2（0.0 / 1 / 2 三版逐格一致）**

| 创建时 rc `hatch.color` | 脚本 hatchcolor 模式 | base（a0=None / a0=0.8） | α=0.5 | undo | undo==base |
|---|---|---|---|---|---|
| 默认（`'edge'`） | 未设 | `edge` / `edge` | `edge` | `edge` | 是 |
| 默认 | 显式 `'blue'` | (0,0,1,1) / (0,0,1,0.8) | (0,0,1,0.5) | 回到 base | 是 |
| 默认 | `'edge'` | `edge` | `edge` | `edge` | 是 |
| 默认 | RGBA (0,0,1,0.5) | (0,0,1,0.5) / (0,0,1,0.8) | (0,0,1,0.5) | 回到 base | 是 |
| **`rc_context` red** | **未设** | (1,0,0,1) / (1,0,0,0.8) | **`edge`** | **`edge`** | **否** |
| `rc_context` red | 显式 / `'edge'` / RGBA | 同「默认」行 | 同 | 同 | 是 |
| **`rc_context` RGBA (1,0,0,0.25)** | **未设** | (1,0,0,0.25) / (1,0,0,0.8) | **`edge`** | **`edge`** | **否** |
| `rc_context` RGBA | 显式 / `'edge'` / RGBA | 同「默认」行 | 同 | 同 | 是 |
| `rc_context` `'edge'`，环境非 edge（需另设，见 1.4） | 未设 | `edge` | red | red | **否**（Codex r…303 的形状） |

要点：
- **只有「未设」模式 × 创建时 rc 与撤销时环境 rc 不同**才分叉；显式色（含自带 alpha 的 RGBA、`'edge'`）在
  `set_alpha` 往返里都是对的，因为它们的 `_original_hatchcolor` 就是它们自己。
- 显式 RGBA 自带 alpha 在 `a0 != None` 时被 `a0` 覆盖（(0,0,1,0.5) → (0,0,1,0.8)），这是 matplotlib 自己的语义，
  往返后回到同一个值，不是缺陷。
- `rc_context` 里 `(1,0,0,0.25)` 的内在 alpha 0.25 只有在**创建时 `_alpha is None`** 才活在 `_hatch_color` 里；
  创建时 `alpha=0.8` 则 0.25 已无从恢复（artist 上不留痕）。钉住方案对这种情形给出的行为是：往返回到创建时的
  (r,g,b,0.8)，与全新重放一致（见 §3.4）。

**3.10.0 / 3.10.9**：64 格里 `set_alpha` 往返**全部 OK**（`'edge'` 作 rc 值直接 ValueError，不适用）。花纹色就是边色
或创建时 rc，`set_alpha` 不重读 rc。

### 1.3 哪些 setter 会触发重解析（`hatchcolor_triggers_probe.py`，rc_context red 创建，环境默认）

| 对象 | 3.11.0 / 1 / 2 | 3.10.0 / 3.10.9 |
|---|---|---|
| Patch / bar / hist 柱 | **`set_alpha`、`set_fill` → `edge`**；`set_edgecolor` / `set_facecolor` / `set_hatch` / `set_linewidth` 不动（仍 red） | `set_alpha` 不动；**`set_fill(False)` → 黑（边色），`set_fill(True)` 回不去**（实测见 §2）；`set_edgecolor('k')` → 黑（连带） |
| Collection（`fill_between` / `scatter`） | **`set_alpha` → `edge`**（`_hatchcolors` 重解析；没有 `set_fill`）；其余不动 | `set_alpha` 不动 |
| `set_color(c)` | 连花纹色一起设成 c（显式） | 连边色 → 花纹色 |

即 Tavotto 现有可编辑 prop 里会触发的是：`alpha`（patch / bar / bar_series 经 ALIAS_GROUPS 广播 / collection）与
`fill`（patch / bar）。其余样式 prop 不触发。

### 1.4 `'edge'` 哨兵的真形状

3.11 默认 rc 已经是 `'edge'`，所以「创建时 `'edge'`」必须配一个**非 edge 的环境**才能分叉
（脚本顶部 `plt.rcParams["hatch.color"]="red"`，局部 `rc_context({"hatch.color":"edge"})` 建柱）。实测（matplotlib 层）：
未钉 → `set_alpha(0.5)`/`set_alpha(None)` 之后 `(1,0,0,1)`；钉住 → 仍 `edge`。Tavotto 真 worker 复现见 §2 的 `rc_edge` 行。

## 2. main 上的现存缺陷（问 2）

复现：`TAVOTTO_WORKER_PYTHON=<venv>/bin/python PYTHONPATH=$PWD/src python scripts/dev/hatchcolor_alpha_undo_repro.py`。
脚本在 `rc_context({"hatch.color": ...})` 里建柱 / 形状（带 `hatch="//"`）；热 worker：应用 `[alpha 0.5]` → 再提交 `[]`；
对照：另起一个**只见 `[]`** 的全新 worker。比像素 sha1。（`hot(on)==fresh(on)` 在所有行都为 True：编辑那一刻热态与重放一致，
**分叉只出现在撤销之后**。）

3.11.2 摘录（0/1 版一致；36 行中 18 行 DIVERGE）：

| 创建 ctx | 目标 | prop | hot(undone) | fresh([]) | 结果 |
|---|---|---|---|---|---|
| rc_context red | 单柱 `bar_0`（无边色） | alpha | 20288372b9 | 2c5921991b | **DIVERGE** |
| rc_context red | 单柱 `bar_0` | fill | 20288372b9 | 2c5921991b | **DIVERGE** |
| rc_context red | 独立 `Rectangle` | alpha / fill | 0a1cab5e6b | b4e9b24296 | **DIVERGE** |
| rc_context red | 柱系列 / 有显式边色的柱 | alpha | ff4c2d470e | 1c508c1e1c | **DIVERGE** |
| 环境 red + rc_context `'edge'` | 柱 / 形状 | alpha | 与脚本原样不同 | | **DIVERGE** |
| rc_context 半透明 RGBA | 柱 / 形状 | alpha | | | **DIVERGE** |
| 无 rc_context（对照） | 全部 | alpha / fill | | | OK |

3.10.0 / 3.10.9：**alpha 全 OK；`fill` 撤销分叉 4 行**（`rc_context` red 与半透明 RGBA × 单柱 / 形状，边色未设）。
机理：`set_fill(False)` 把 `_hatch_color` 写成 `patch.edgecolor`（黑），`set_fill(True)` 因边色 `'none'` 不再写
`_hatch_color`，于是留在黑色。`_PatchEdge.hatch` 的快照只在 `edgecolor` prop 上起作用，`fill` 不经过它。

**结论：是现存 bug，不依赖纹理颜色编辑。**
- 触发条件（合取）：matplotlib ≥3.11（alpha）或任意版本（fill，仅 ≤3.10 形态不同）× 脚本在 `rc_context` / `style.context`
  （或之后被还原的 rc）里把 `hatch.color` 设成和**脚本跑完后环境值**不同的值 × 那个 patch 带花纹（或之后被用户加花纹）
  × 用户改了 alpha / fill 又撤销（或改完直接撤销到默认）。
- 现状：#860 合入前后行为相同（#860 只加了 `hatch` / `hatch_linewidth`，不改 alpha 语义）。`tests/` 里没有任何一张图用 `rc_context`
  （grep `rc_context|hatch.color` 在 `tests/`、`src/tavotto` 为空），序列 harness 的种子永远不会撞到它。
- 影响面：热态画面不等于「脚本 + 当前列表」应有的样子（撤销后花纹色停在 `edge`/环境色），但**编辑那一刻**热态与重放一致，
  所以不是静默写坏文件：写回的 verify 在热态最后应用的恰是这组 patches 时会跑「热 vs 全新重放」像素门（ADR 0049），
  分叉会被拦成 409 `replay_divergence`——代价是**一次正当的编辑序列被拦下来**，与 `_get_axes_lim` 注释里 `_AUTOSCALE` 那一类同形
  （此条按文档机制推断，未在本次调研里跑写回端点）。
- 额外的保真问题（同一根因的「编辑那一刻」一面）：对这类 patch 只要碰一次 alpha，花纹色就从脚本里的 red 变成 `edge`，
  热态与重放虽然一致，但**与用户脚本写的颜色不一致**——用户拖一下透明度，斜线换色了。
- 实际频率判断：`rc_context` 里专门设 `hatch.color` 的科研脚本不多，但 3.11 把默认值改成 `'edge'` 之后，
  任何在上下文里设了 `hatch.color` 的脚本都会命中，而且 Codex 能连报三轮说明这条路径是现实可构造的。
  没有数据支撑「多常见」，按 correctness 缺陷处理，不按热点处理。

## 3. 快照方案设计（问 3）

### 3.1 原则

- 症结是「原样是个**模式**，不是值」（同族：`_AUTOSCALE` / `_NO_BBOX` / `_PatchEdge` / `_PatchFace`），而「解析」这一步读了
  **会变的环境**（rcParams）。判据的主语：谁的？该 patch 的花纹色；哪个时刻？**创建那一刻**，不是 apply 那一刻；哪个维度？模式 + 解析值 + 内在 alpha 三件。
- 原则：让 setter 的结果只依赖 **「创建时快照 + 当前 alpha + 用户值」**，不依赖此刻的 rcParams。热态与重放都从同一个创建时快照
  出发，`HOT(P) == REPLAY(P)` 就是构造出来的，而不是靠测试碰出来的。

### 3.2 推荐形态：「钉住」而不是新造快照类

`_pin_hatch(artist)`（幂等；3.11+ 且 `_original_hatchcolor is None` 才动手）：

```python
# Patch
if getattr(p, "_original_hatchcolor", 0) is None:
    p.set_hatchcolor(p._hatch_color)        # 'edge' 或创建时已解析的 RGBA；to_rgba 在同一 alpha 下幂等
# Collection
if getattr(c, "_original_hatchcolor", 0) is None:
    c.set_hatchcolor(c._hatchcolors)        # 'edge' 或 RGBA 数组
```

调用点（**动手之前**，保证 `_hatch_color` 还是创建时的解析值）：
1. 所有会触发重解析的 setter 的入口：`_CAP_ALPHA` 的 setter（Patch / bar / bar_series 逐柱 / Collection）、
   `fill` 的 setter（patch / bar）；
2. 新增 `hatchcolor` prop 的 getter 与 setter。

为什么是「首次触碰前」而不是 instrument 时全量钉：全量钉要遍历全部 patch（大直方图上几千个），并且在 native 图上改了用户对象的
模式；首次触碰钉只碰被编辑的 artist，且只在 alpha / fill / hatchcolor 被真正用到时发生。代价：热 worker 里这些 artist 的
`_original_hatchcolor` 会永久从 `None` 变成显式值，但**画面与「未设」逐位相同**，而且唯一会再次重解析它的就是上面那几个 setter。

三件事怎么被保住：

| 要保的 | 怎么保 | 实测 |
|---|---|---|
| 解析模式（`'edge'` 哨兵） | `set_hatchcolor('edge')` → 之后 `set_alpha` 仍走 `'edge'` 分支 | 环境 red 下往返仍 `edge`（triggers 探针 `EDGE-SENTINEL`） |
| 解析值（rc 里的 red） | 钉成显式 RGBA，不再读 rc | 64 格矩阵：3.11.0/1/2 各 0 格 DIVERGE（未钉 8 格） |
| 内在 alpha（rc 里的 0.25） | 创建时 `_alpha is None` 则 `_hatch_color` 本身带 0.25；钉成 RGBA 后 `set_alpha(None)` 恢复自带 alpha | `(1,0,0,.25)` → α0.5 → None → `(1,0,0,.25)`（bar / fill_between / scatter 均 OK） |

局限（要写进规则，不要假装覆盖）：创建时 `_alpha` 非 None 的 patch，rc 的内在 alpha 在 artist 上已不可恢复，钉住保存的是
`to_rgba(rgb, 创建时 alpha)`；之后撤销回到的就是这个值（与全新重放一致，因为重放也是从同一个创建时状态出发）。唯一与「如果脚本当初没写
alpha」不同的情形是用户把 alpha override 设成 `None`，UI 目前不发这个值。

### 3.3 `hatchcolor` 作为新 prop

- **只在真有 `set_hatchcolor` 的 artist 上发字段**（与 `hatch_linewidth` 同一条「按真实 setter 判，不按版本号」）。3.10 及以前不发：
  那里花纹色是边色的副产品（`_set_edgecolor` 每次覆盖），任何自制的「独立花纹色」都会被下一次 `set_alpha` /
  `set_edgecolor` 冲掉，要做就得 Tavotto 自己再包一层重算，成本高、对 3.11+ 的发行 runtime 没有收益。
- getter：`pin` 后返回 `_original_hatchcolor`（显式 RGBA / `'edge'`；`originals` 里存这个）。setter 接受：
  `'#rrggbb'` / `'edge'`；还原时喂回 `originals` 里的值，走同一个 `set_hatchcolor`。不需要 `_PatchHatch` 之类新类，也就没有新哨兵、
  没有「只活在 originals 里、不过 JSON」的额外纪律。
- manifest 的 `value`：用 `get_hatchcolor()` 的解析结果转 hex（`'edge'` 时 manifest 要给 UI「跟随边框」这个选项的当前状态；
  建议 `value` 给 hex，另给 `options`/`follows_edge: bool`，具体字段形状留给实现 PR 与前端注册表一起定）。
- 与 alpha、fill 的顺序无关：每个 setter 都以「`_original_hatchcolor`（已钉）+ 当前 `_alpha`」重算，两种应用顺序得到同一结果。
- 不进 `ALIAS_GROUPS` 以外的新机制；bar_series 照 `hatch` 的做法加进 `for _bprop in (...)` 那串（广播 + 单柱叠加撤销才回得到脚本原样）。

### 3.4 ≤3.10 的 `fill`（独立小修）

`fill` 撤销分叉（§2）：给 `fill` 的 setter 加「`set_fill` 前记 `_hatch_color`，`set_fill(True)` 之后若 `_original_edgecolor is None`
就写回」（matplotlib 层已验证：写回创建时的 `_hatch_color` 后恢复为 `(1,0,0,1)`）。仅 ≤3.10 分支，3.11 的 `fill` 已由 §3.2 的钉住覆盖。
这条与 alpha 无关，可以和主修一起做，也可以单独成 PR；验证用 §5 的新夹具图。

### 3.5 写回到脚本的形式

**脚本永不改**。纹理颜色编辑走现有覆盖层：patch 列表里的一条 `{gid, prop:"hatchcolor", value, identity}`，随布局版本落盘，
重开 / 写回 verify 都是「脚本 + 这串 patch」全量重放（`docs/rules/backend/override-application-and-replay.md`、`writeback-transaction.md`）。
不需要新的写回通道；`patchspec` 不枚举 prop 名。native 图的屏障 / `unrestored` 机制按现有 prop 同样适用。

### 3.6 要改的模块（实现 PR 的清单，本次不改）

后端
1. `src/tavotto/engine/overrides.py`：`_pin_hatch`；`_CAP_ALPHA`、`fill` setter、Collection 的 alpha 包一层；
   `_PATCH_CAPS` 加 `"hatchcolor"`；`bar_series` 那串 `_bprop`；`_COLLECTION_CAPS` 是否加 `hatchcolor`（建议第一版不加，见 §4）；
   ≤3.10 `fill` 修。
2. `src/tavotto/engine/manifest.py`：`_hatch_style_fields` 旁加 `hatchcolor` 字段（按 `hasattr(pt, "set_hatchcolor")` 发）；
   `value_original` 读法（`_field_values` 口径）；`_bar_fields` / `_bar_series_fields` / `_patch_fields` 三处。
3. `src/tavotto/engine/legendmodel.py`：图例示意块如果引用 hatch 则同步（grep 命中，实现时核）。
4. `tests/support/overridesample.py`：`hatchcolor` 的前置（`("hatch","/")`）与采样值，进序列 harness。

前端（严格同源对）
5. `web/src/components/inspector/presentation/registry.ts`、`roleProfiles.ts`；`web/src/i18n/locales/{zh-CN,en-US}/inspector.json` +
   `resources.d.ts`；`seriesPanels.test.tsx`。字段用颜色选择器 + 「跟随边框」，按 `docs/rules/frontend/inspector-presentation-registry.md`，
   并遵守「卡片一句话读懂」（默认折叠，不并列术语）。
6. 文档：`docs/architecture/matplotlib-artist-capability-map.md`、`docs/rules/backend/axes-and-artist-families.md`（删掉「纹理颜色不在此列」那一段）、
   `override-application-and-replay.md`（加一条「重解析读环境 → 首次触碰前钉住」）。

### 3.7 风险档与工作量

- 修现存缺陷（§3.2 + §3.4，无 UI）：`risk:high`（触 setter / restore 与重放语义）；约 0.5 天实现 + 1 天夹具与变异。高档要求
  full-ci、变异清单逐条附退出码、一次 Codex + 最多一次定向复核。
- 纹理颜色编辑本体（§3.3 + 前端 + 文案）：`risk:high`（新 prop 进 setter/restore 表）；约 1.5–2 天（含 i18n、presentation 注册表、e2e）。
- 因先后依赖，建议拆两个 PR，修缺陷的先合。

## 4. 测试矩阵（实现 PR 需要）

新夹具图（`tests/support/invariant_figures.py` 或 `test_hatch_editing.py` 同款内联库）：**在 `rc_context` 里建的**图，
覆盖：柱系列 / 单柱 / 独立 `Rectangle` / `fill_between` / `scatter`；创建时 rc ∈ {非 edge 颜色, `'edge'`（环境设成非 edge）, 半透明 RGBA}；
脚本 `alpha` ∈ {无, 0.8}；脚本边色 ∈ {未设, 显式}；脚本 hatchcolor ∈ {未设, 显式色, `'edge'`, RGBA}（3.11）。

| 维度 | 取值 |
|---|---|
| matplotlib | 3.8（旧下限，字段不发）/ 3.10 / 3.11.x（nightly 版本矩阵已跑 `test_hatch_editing.py`） |
| 序列 | alpha→撤；fill→撤；hatchcolor→撤；hatchcolor+alpha 两种顺序；hatchcolor→alpha→撤 hatchcolor→撤 alpha；系列广播 + 单柱叠加撤销 |
| 判据 | 每一步 `HOT(P_k) == REPLAY(P_k)`（manifest + 像素），终态 `HOT(P_n) == FRESH(P_n)`，**撤空后 == 脚本原样的像素** |
| harness | 把 rc_context 图加进 `test_override_sequences.py` 的 `FAMILIES`/种子；`FIXED` 里钉最小复现 |
| 变异（每条用例立刻做） | ① 去掉钉住 → 3.11 上 alpha/fill 往返红；② 钉住放在 setter 之后 → 红；③ Collection 漏钉 → 红；④ 钉住改成全量 instrument 时钉（行为对照，非变异）；⑤ ≤3.10 去掉 fill 写回 → 红。结论用退出码 |
| 写回 | 一条真端点：编辑 alpha + hatchcolor → 写回 → 重开重放像素逐位一致；撤销 alpha 后写回不再 409 |
| 前端 | 注册表字段、i18n 键同源、`pnpm test && pnpm build` |
| Windows | 无新平台分支；PR 带 `full-ci` 跑重腿 |

## 5. 建议（问 4）

**先修第 2 问的现存问题，再做纹理颜色编辑。**
- 现存缺陷是 correctness（热态 ≠ 重放，会让合法编辑序列被写回拦截），修法小而可验证，且钉住之后 hatchcolor 编辑不需要自定义快照类；
  反过来先做编辑功能会继续在这个交叉点上被 Codex 连报 P1（#860 的教训）。
- 纹理颜色编辑本身值得做（分组柱状图的斜线颜色是典型诉求），但第一版范围建议：Patch / bar / bar_series，**3.11+ 才发字段**；
  Collection（`fill_between` / 散点）的 hatchcolor 编辑先不做，只做它的 alpha 钉住（现存缺陷也覆盖到它）。
- 不建议「不做」：问题并不是 3.11 语义不可驾驭，而是我们一直把「未设」当成值；钉住是一次性的结构修法。

## 附录：复现命令

```sh
V=<scratchpad>/hatch-venvs        # m3.10 m3.10.0 m3.11 m3.11.0 m3.11.1，各自 pip install --only-binary=:all: matplotlib==X numpy
MPLBACKEND=Agg $V/m3.11/bin/python scripts/dev/hatchcolor_matrix_probe.py [--pin]
MPLBACKEND=Agg $V/m3.11/bin/python scripts/dev/hatchcolor_triggers_probe.py
TAVOTTO_WORKER_PYTHON=$V/m3.11/bin/python PYTHONPATH=$PWD/src .venv/bin/python scripts/dev/hatchcolor_alpha_undo_repro.py
```
