# ADR 0094：把图内调整写回原脚本——用户显式选择的导出动作：先备份、写前逐行看清、一键复原

日期：2026-09-26 · 状态：**Proposed**（设计 + 可行性 spike；十一个取舍已由用户 2026-09-26 拍板，见文末「已决定」；实施时转 Accepted）
相关：[0083 override 的目标身份](0083-override-target-identity.md)（选择器的身份规则照搬它）、
[0049 写回像素门](0049-write-back-pixel-verification.md) 与 `docs/rules/backend/writeback-transaction.md`（verify 用同一把尺、同一条纪律）、
[0080 按规范修图的事务](0080-spec-fix-transaction.md)（「改完真的过了才提交，不过一个字不改」的先例）、
[0081 画布跟随样式](0081-canvas-follows-style.md)（样式写的也是 override，写回后与样式绑定的关系见 §三.5）、
[0047 在脚本目录里运行](0047-safe-profile-project-workdir.md)（verify 沿用项目的 cwd 模式）、
[0008 会话认证](0008-unified-local-session-auth.md)、[0023 文档落盘权威](0023-document-persistence-authority.md)（落盘顺序）、
`docs/rules/backend/ai-agent-bridge.md`（「修改前快照、只回滚这一版、之后又变过就 409」的先例）。
Spike 证据：`docs/spikes/0094-script-writeback/`（README 有复现步骤与全部数字的出处）。

## 问题

用户原话：「开始设计一个能够允许将当前调整完的 override 写回到原始 py 脚本的功能，有很多用户需要，但是这个就需要我们能够帮助用户顺利恢复原脚本，也就是要先给用户备份，能够让用户顺利复原原脚本，并且写回原脚本时有明显提示才可以。」

今天 Tavotto 里的一切图内编辑都以 override 形式存在项目文档里，脚本一个字不改。用户要的是：把调好的样子**变成脚本自己的代码**——脱离 Tavotto、在终端里 `python fig.py` 也能画出同一张图（投稿用的「可复现运行包」、交给合作者、放进 git）。

三条硬要求：写回前有备份、能顺利复原、写回时有明显提示。

## 零、与「永远不改用户脚本」的关系

**原则不变。** 「永远不改用户脚本」说的是：Tavotto 为了**兼容**、为了**让图画出来**、在**用户没有要求**的时候，绝不动脚本——兼容性问题一律从产品侧解决（0047 的 cwd、`install_unused_import_placeholders`、`paper_style` 捷径都是这条原则的产物）。本 ADR 不放松其中任何一条。

写回脚本是**另一类动作**：一个用户**亲手发起、看过逐行 diff、显式确认**的**导出**——输出目标恰好是用户自己的脚本文件。它与编码 Agent 桥（用户让 Agent 改脚本、修改前快照、可 revert）同一类：改动的意图来自用户，Tavotto 只是执行者，并且必须能完整撤回。

由此划出的边界（每条都是实现里的判据，不是文案）：

- 只有一个入口会写脚本：桌面 / 本地浏览器界面里用户点「写回脚本…」→ 预览 → 勾选确认 → 「修改脚本」。
  **没有任何自动路径**：样式跟随（0081）、规范修图（0080）、AI 刷新、MCP / Codex 插件、`tavotto run`、
  启动时迁移，一律不得调用提交端点（§七.4 的结构性看护）。
- 写之前必有备份（两处，§六），写完必有一键恢复。
- 写进去的是一段**带明显标记、整段删除即恢复原样**的代码（§三），不是散落在全文的修改。

## 一、现状：override 存在哪、怎样作用于 artist

**存在哪。** 画布文档（`tavottofile/*.json`，schema 3，落盘只经 `atomicio`）里每个面板有一份 `overrides`
列表，每条 `{gid, prop, value, identity?}`；布局版本在 `tavottofile/versions/`；「写回原图文件」的基线在
`<data_dir>/…/baked_overrides/<项目id>.json`。前端永远发**完整**列表（全量列表语义：缺了的键自动还原）。

**怎样作用。** 一次性或热 worker 在沙盒里跑脚本；`Figure.savefig` 被 `worker._patched_savefig` 拦截——
不落盘，只按 `figcapture.savefig_stem(fname)` 把 Figure 登记进会话（`paper_style.save` 另有一条捷径直接登记，
§五.3 会讲它为什么要改）。脚本**跑完**之后 `manifest.instrument` 给元素编 gid：`axes_i` 取
`axestraversal.ordered_axes` 的顺序（`fig.axes`，再逐层 `child_axes`，再寄生轴），子元素 `lines_j` / `texts_j` /
`images_j` / `collections_j` 是 `ax.lines[j]` 等列表下标，还有 `title`、`xlabel`、`legend`、`legend.texts_j`、
`xticks`、`colorbar`……；有显式 label 的元素另带目标身份（0083）。然后 `overrides.apply` 按 `_apply_rank`
七档顺序应用整份列表，经 `HANDLERS` 表的 getter / setter（记下 originals 以便还原），广播组（图例整体字号 →
每条图例文字）先于窄条目。

**有哪些类型。** `HANDLERS` 当前 19 类 artist、249 个 (类, prop) 键（spike 里实时枚举；下表的分档是按
setter 实现静态估计，逐条是否等价要以 §五 的 verify 为准）：

| 档 | 含义 | 键数 | 例子 |
| --- | --- | --- | --- |
| T1 | 有一个公开 matplotlib setter 与之对应 | 181（73%） | 颜色、线宽、线型、字号、字重、透明度、可见、文字内容、面色、网格、边框、刻度线 |
| T2 | 公开 API 能表达，但要换算或连带 | 32（13%） | 文字 / 轴标签拖动（`pos_frac`）、图例拖动（`loc_frac`）、图例预设位置、子图位置、图幅、柱 / 误差棒 / 茎叶组 |
| T3 | Tavotto 自己的模型重建，第一轮认为公开 API 表达不出等价物 | 36（14%） | 图例整体字号与间距（原生 `legend(fontsize=)` 语义要重建图例盒）、条目顺序 / 绑定、刻度定位模型、单条刻度文字、色条方向 / extend、3D 轴箭头、渐变色 |

第二轮 spike 逐个复核了这 36 个键并实测两条路线：只剩 6 个真的要靠私有 API；标题拖动（`pos_frac` 键本身在 T2，第一轮是按元素形态拒的）也用公开 API 救了回来。见 §十四。

**图内与版面。** 以上是**图内**（改的是脚本画出来的那张 Figure）。画布上的摆放、缩放、裁剪、画布标注 / 箭头 /
文字、面板标号属于**版面**，本来就不属于任何一个脚本，不写回（导出画布照旧由画布合成）。

**不要与既有的「写回原始文件」混淆。** 既有功能（`/api/update_source`，`writeback-transaction.md`）改的是
**图文件**（PDF / PNG）：一次性 worker 全量重放后覆盖原图，脚本不动。本 ADR 改的是**脚本**。界面与文档里两者要用
不同的名字：「更新原图文件」与「写回脚本」。

## 二、写回形态：四个候选

spike 在 8 份脚本（仓库示例 3 份共 4 张图、playground 示例 3 份、用户真实脚本的副本 2 份）上挑了 5 类常见
override（字号、颜色、线宽、图例位置、文字位置），共 **82 条**，逐条量了两种形态（数字出处见 spike README）。

**A. savefig 钩子块（推荐）。** 在脚本顶部 import 段之后插入**一段**带标记的代码：每张图（按 savefig 的 stem）
一个调整函数，外加一个包住 `Figure.savefig` 的钩子——脚本调用 savefig 的**那一刻**，先把对应的调整作用在这张图上，
再照常保存。只用 matplotlib 公开 API 与标准库，写回后的脚本不 import Tavotto。

**A′. 在每个 savefig 调用前插一段。** 否决：示例图库里 savefig 在另一个文件里（`paper_style.save(fig, stem)`），
改它等于改第二个文件；用户脚本 `for` 循环里一行 savefig 出多张图、savefig 之后紧接 `plt.close(fig)`（用户脚本甲）
都要按调用点猜 stem。A 把「哪张图」交给与 Tavotto 同一条 stem 规则，这些形状全都不用猜。

**B. 就地改 AST 字面量**（libcst 保格式，`fontsize=8` → `9`）。实测 82 条里：

| 分类 | 条数 | 例子 |
| --- | --- | --- |
| A 唯一字面量：调用只执行一次、参数是常量——B 能就地改 | 18（22%） | `ax.legend(loc='lower right')` |
| B 共享字面量：常量但那一行执行了多次——改了会连带别的对象 | 2 | 用户脚本乙的 `linewidth=3.0` 在循环里画了 3 条；用户脚本甲的 `lw=…` 在两个子图的循环里 |
| C 表达式：变量 / 下标 / f-string | 27 | `color=PALETTE[1]`、`color=<模块常量>`、`color=color`、所有拖动位置 |
| D 不在调用里：值来自 rcParams / 默认 | 35 | 大多数标题、轴标签、刻度、单条图例文字的字号 |

B 只有 22% 能干净表达；D 类要「新增参数」，而新增在 `tight_layout()` / `fig.canvas.draw()` **之前**会改变布局输入
（用户脚本甲先 `canvas.draw()` 再按子图的最终位置摆色条；用户脚本乙 `tight_layout()` 后 `bbox_inches='tight'`），
与 Tavotto「脚本跑完之后再应用」的热态不再等价。B 唯一胜过 A 的地方是原生语义参数：用户脚本甲的图例
`legend(fontsize=…)` 是 A 类——第二轮实测（§14.2）钩子块的公开 API 重建恰好在这张图上失败、B 成功。**结论：B 不做默认；可作为以后「精修」的补充，
只处理 A 类字面量。**

**C. 旁挂文件 `<脚本>_tavotto.py`（或 `.mplstyle` / rcParams）+ 原脚本一行 import。** 机制与 A 相同，只是块放在另一
个文件里。好处是原脚本只多一行；代价是可复现运行包必须带上第二个文件（漏带就静默少了调整），sibling import 依赖
`sys.path[0]`（Jupyter `%run`、被别处 import 等形态会找不到）。`.mplstyle` / rcParams 只能表达全局默认值，逐元素
的 override 一条都表达不了；它更适合将来「把绑定的样式（0081）写成 rcParams 段」，不在本 ADR 范围。

| 维度 | A 钩子块 | B 改字面量 | C 旁挂文件 |
| --- | --- | --- | --- |
| spike 5 类 override 覆盖 | 第一轮 69/82 通过验证（84%），13 条按表不写，0 条被验证拒；第二轮开路线 1、2(a) 后 81/82（§十四） | 18/82 可干净表达（22%） | 同 A |
| 多图 / 循环 / 函数封装 / 辅助模块里 savefig / 子图网格 | 按 stem 认图，全部成立（8/8 组合写回像素逐字节一致） | 循环与封装处连带改别的对象 | 同 A |
| 可读性、之后还能手改吗 | 一段独立代码，每条调整一行公开 API，有注释说明改的是哪个元素；用户可以删掉任一行 | 最自然（就是用户自己的代码） | 两个文件 |
| 二次写回 | 整段重新生成、替换旧段（幂等，spike 逐字节验过） | 要追踪上次改了哪些字面量 | 同 A |
| 与 Tavotto 重放的关系 | 作用时刻就是 Tavotto 登记 Figure 的时刻（savefig），与热态「脚本跑完再应用」等价（除非脚本 savefig 之后还改图，verify 会拦下） | 作用时刻提前，布局可能不同 | 同 A |
| 恢复 | 删掉整段 = 逐字节回到写回前（spike 100%） | 要逆向每处改动 | 删一行 import + 删文件 |

**推荐 A。** C 可以作为设置里的可选形态以后再议（需要拍板 Q1）。

## 三、推荐形态：钩子块长什么样

```python
# >>> Tavotto 调整 >>>
# 由 Tavotto 0.18.0 于 2026-10-02 写入。整段删除即恢复原样；原脚本备份：tavottofile/script-backups/…
# 本段只在 savefig 的那一刻修改对应的图，其余代码一行未动。
def _tavotto_adjust():
    import matplotlib.figure as _mfig
    ...                                    # 运行期辅助：按 Tavotto 的顺序找子图、按 label 找曲线……
    def _tavotto_0_0(fig):  # axes_0.lines_0.color
        _with_legend(_line(_ax(fig, 0), 0, 'Catalyst'), lambda _a: _a.set_color('#d6278f'))
    def _tavotto_0_1(fig):  # axes_0.xlabel.fontsize
        _AxisLabel(_ax(fig, 0), 'x').set_fontsize(10.5)
    _install({'Fig1_kinetics': [('axes_0.lines_0.color', _tavotto_0_0), …]})
_tavotto_adjust()
del _tavotto_adjust
# <<< Tavotto 调整 <<<
```

1. **位置**：模块 docstring、`from __future__` 与开头连续的 import 段之后；没有就放文件头。前后各两个空行，删除时
   连同这四个换行一起去掉——所以「删掉整段」逐字节回到写回前。
2. **钩子**：`_install` 包住**当时的** `Figure.savefig`（在 Tavotto 的 worker 里就是它的拦截函数，在终端里就是
   matplotlib 原版），同一张图同一个 stem 只调整一次（`png` + `pdf` 各存一次也只调一次）；重复安装是空操作。stem
   的求法与 `figcapture.savefig_stem` 逐字相同（新同源对，§十）。
3. **每一步都兜住**：一步失败（找不到对象、matplotlib 版本太旧没有某个 API）只 `warnings.warn` 一条
   `TavottoAdjustmentWarning` 并跳过，**绝不打断脚本本身**——spike 里生成代码的一个 bug 曾经让整个脚本跑挂，这条就是
   从那里来的。
4. **应用顺序**按 `_apply_rank`（与引擎同一个出处），不是列表序。
5. **块自带来源**：结束标记之前写一行机读的 `_TAVOTTO_SOURCE = {...}`（写进来的 patch 全文、Tavotto 版本、块正文的
   sha256）。二次写回时 Tavotto 读回它，与这次新增的编辑按 (gid, prop) last-wins 合并后**整段重新生成**；块正文
   哈希对不上（用户手改过块）就不重新生成，提示用户选择「保留手改、只追加」还是「按 Tavotto 重新生成」。
6. **写回之后 override 清零**：写进脚本的那几条在**同一次提交**里从文档里去掉（剩下写不了的仍是 override），热
   worker 作废重建。不清零的话每次重放都在「已经含调整的脚本」上再应用一遍；多数 prop 是绝对值、看起来没事，但
   目标身份、`value_original`（0081 §十三「脚本改了就让位」）会读到错的基线。这一步不进撤销历史——撤销写回脚本
   的方式是「恢复原脚本」（§六），它会把这些 override 放回去。
7. **与样式绑定（0081）**：样式写的 override 写进脚本之后，脚本的值就等于样式值，`effectiveChanges` 为空、零
   commit；之后改样式照常只写 override。

## 四、选择器：gid → 脚本里可执行、跨运行稳定的定位

| gid | 生成的定位 | 稳定性 |
| --- | --- | --- |
| `axes_i` | `_ax(fig, i)`：块里带一份与 `axestraversal.ordered_axes` 逐字等价的遍历（`fig.axes` → 逐层 `child_axes` → 寄生轴） | 与 Tavotto 编号同源；新同源对，§十 |
| `axes_i.lines_j` 等（有显式 label） | 按 label 找**唯一**一条（与 0083 的目标身份同一条规则）；0 条或多条 → 这一步告警跳过 | 用户之后重排 / 插入 / 删除曲线：spike 实测重排后仍落在原曲线上，删掉后告警、脚本照常跑完 |
| 同上（无 label） | 按下标 `ax.lines[j]` | **已知边界**（与 0083 一致）：spike 实测在前面插一条新线后，调整落到了新线上。确认界面对这类条目标注「按位置定位」 |
| `title` / `xlabel` / `legend` / `legend.texts_j` / `xticks` | `ax.title`、`ax.xaxis.label`（Text 不认得自己的子图，块里带着子图一起传）、`ax.get_legend()`、`get_texts()[j]`、`ax.tick_params(axis=…)` | 结构性成员，位置即身份 |
| 其余（色条代理、柱组、误差棒组、3D、表格……） | v1 不写 | 进报告，保留为 override |

失败处置分两层，**都不猜**：生成时找不到对应写法 → 不写这一条、在预览里列出原因、它继续作为 override 存在；运行时
定位失败 → 这一步告警跳过。没有 savefig 的图（只 `plt.show()`、Tavotto 用 pyplot 兜底起的 stem）没有「那一刻」可
钩，v1 不写（Q11）。

## 五、验证：写回之后的脚本必须重跑、与热态逐像素一致，否则一个字不改

纪律与写回事务相同：**prepare → verify → commit，任一环不过 409，原脚本零改动**。

1. **prepare**：热 worker 的 `script_sha1` 与磁盘一致（否则 `script_changed`）；热 worker 最后应用的正是这份 patch 列表
   （`last_patch_hash`，否则先按列表重渲染一次再来，不做 `fresh_only` 式的「没比也放行」——这里没有「没比」这一档）；
   路径与写入条件检查（§八）；生成新字节（§九）。
2. **verify**：一次性 worker（`pool.one_shot`，项目自己的 cwd 模式与沙盒）跑**新脚本**，只带「写不进脚本、留作
   override」的那几条 patch；拿到的 manifest 与热态过 `app._compare_manifests`（几何）+ 像素门
   （`pdfbackend.compare_png` 逐 RGBA，`REPLAY_PIXEL_TOL`），worker warnings（含块发出的 `TavottoAdjustmentWarning`）
   一条即阻断。新脚本**不写进用户目录**：一次性 worker 增加「源码取自 staging、`__file__` / `sys.path[0]` / 模块名
   仍是原路径」的装载方式——verify 期间用户目录零写入（spike 为了省事写了同目录的临时副本，产品不这样做）。
3. **两条 spike 里量到、verify 必须挡住的失败形态**：
   - **看似等价的翻译其实不等价**：图例整体字号若按「逐条改字号」翻译，图例盒不随之重排——pg_kinetics 与用户脚本甲两处
     都被几何门（图例 bbox 偏 0.014–0.025 figure 分数）与像素门（0.84%–1.95% 像素变化）拒掉。等价的写法是
     按原生 `legend(fontsize=)` 语义重建图例（§14.2 路线 (a)）；这一条也证明 verify 不是摆设。
   - **部分写回打乱了广播与窄条目的先后**：图例整体字号（留作 override）在重放时排在「单条图例文字字号」之前；
     单条那条若写进脚本，就在 savefig 时先改、再被重放时的广播盖掉。生成器的规则：留作 override 的广播条目，其
     子孙同 prop 的窄条目一起留下。修之前 fig1 的组合写回被拒，修之后通过。
4. **与 `paper_style` 捷径的冲突（前置修正）**：worker 为 `paper_style.save` 装的捷径直接登记 Figure、**不经过
   `Figure.savefig`**，于是钩子块在 Tavotto 里永远不触发（终端里却会触发）——spike 在未修正的 worker 上跑三个
   `paper_style` 用例，25/25 条全部被 verify 拒掉（零改动，安全但无用）。修正：捷径改为调用 `fig.savefig(f"{stem}.pdf")`
   （照样被拦截、不落盘、来源仍是 savefig）。spike 用一份打了这一行补丁的源码副本复现，之后 0 条被拒。这是实施计划
   的第一个 PR。
5. **代价**：每次写回多跑一遍脚本（与更新原图文件相同；spike 里单次运行 0.6–3.3 s，heavy 脚本分钟级）。不许为省时间
   跳过。
6. **v1 不做自动二分**：组合写回不过时，响应列出几何分歧的 gid 与像素指标，整次 409；预览里提供「逐条诊断」（用户点了
   才跑，N 条 = N 次重跑），把不过的条目移回 override 再试（Q5）。

## 六、备份与恢复

**放哪：两处都放（推荐，Q2）。**

- **项目内** `tavottofile/script-backups/<脚本相对路径的 slug>/<月日_时分秒>/`：用户看得见、随项目走（拷走整个项目
  备份也在）、不用 Tavotto 也找得到；`tavottofile/` 已在 `discover.PRUNE_DIRS` / `project_refresh.EXCLUDE_DIRS`
  里，不会被当成素材或触发重扫；路径只从 `project_layout_dir()` 这一处取。
- **数据目录镜像** `<data_dir>/script_backups/<项目id>/<slug>/<时间戳>/`：用户删了 `tavottofile/`、`git clean -fdx`、
  整个项目被同步盘冲掉时仍在。符合「运行时可写数据走 `data_dir()`」。
- 任何一处写不进去（权限、盘满）→ 整次 409 `script_backup_failed`，原脚本未动（与 `_backup_targets` 同一顺序：
  两处备份全部写完并 fsync，才碰原件）。

**备份内容**：`original.py`（原字节，一个都不改）+ `meta.json`：脚本路径（相对项目 + 当时的绝对路径）、写回前后的
sha256 / 大小 / mtime_ns / 权限位、编码 / 换行 / BOM、Tavotto 与 matplotlib 版本、时间、stem、写进去的 patch 全文（含
identity）、留作 override 的 patch、写回前文档里这张图的整份 override 列表、verify 摘要、git 状态（§六末）。

**版本链**：每次写回一条。**第一条（写回前脚本里没有 Tavotto 块）标 `pristine`，永不自动清理**；其余保留最近 20 条
（与 AI 快照同一个数）。清理只动非 pristine 的。

**恢复入口**：写回结果区的「恢复原脚本」；面板菜单「脚本调整历史…」（列出每个版本、看 diff、恢复到任一版本）。
恢复本身是同一套事务：先把**此刻**的脚本备份成一条「恢复前」、再原子替换；默认同时把当时写进脚本的那几条放回
override，图看起来与写回前一样（Q9）。

**外部改过脚本**（磁盘 sha256 ≠ 写回后记下的那个）：绝不静默覆盖。给两个选项——「只移除 Tavotto 调整段（保留你之后的
其它修改）」（默认；spike 实测移除后逐字节回到写回前，只要块外没动）与「整份恢复到写回前（你之后的修改会丢；当前
版本先另存一份）」；块本身也被改过则只给第二项并说明。

**git**：脚本目录在 git 仓库里时（只读探测 `git rev-parse`，带超时；没有 git 就跳过），确认界面说明「此脚本受 git 管理
（有 / 无未提交的修改），也可以用 git 恢复」，并记进 meta；**照样做自己的备份**，不自动 commit / stash。

## 七、明显提示

### 7.1 写回前：确认界面

点「写回脚本…」先生成并 verify（显示进度，可取消），然后才出确认界面——用户看到的一定是**已经验证过**的改动：

- 醒目的警告条：「这会修改你的脚本文件」，下面是完整绝对路径（含卷名，如外置硬盘）。
- **逐行 diff**：插入 / 改动的每一行（语法高亮，行号），后端生成、前端只渲染，不在前端二次拼接。
- 「将写入 N 条 / 无法写入 M 条」，逐条列原因；无法写入的继续作为 Tavotto 调整存在。按位置定位的条目单独标出。
- 两处备份路径；git 状态；**同一目录下有校验清单**（`CHECKSUMS*`、`SHA256SUMS`、`*.sha256` 里列着这个脚本——用户脚本甲所在的
  可复现运行包就有）时提示「写回后校验值会变」。
- 其它画布 / 布局版本也引用这张图时列出来：脚本改了，它们的基线跟着变。
- verify 结果：「已在隔离环境重跑并比对：几何与像素一致」。
- 必须勾选「我已查看以上改动，知道这会修改我的脚本」，按钮文字是「修改脚本」、不是默认焦点、回车不触发（Q3）。

### 7.2 写回后：结果

成功（附备份位置、「恢复原脚本」、「在编辑器中打开」「在 Finder 中显示」）、部分（写入几条、哪些仍是调整）、失败（409 的
原因，并明说「脚本没有被修改」）。提交时再核一次磁盘 sha256：确认界面停留期间脚本被改过 → `script_changed`，重新预览。

### 7.3 三个入口

| 入口 | v1 | 理由 |
| --- | --- | --- |
| 桌面壳 | 开放 | 主入口 |
| 本地浏览器界面（`run.sh` / `tavotto open`） | 开放 | 同一套界面与会话认证 |
| 浏览器 playground（`/try`，Pyodide） | 不出现 | 没有用户文件 |
| Codex 插件 / MCP / 编码 Agent 桥 | **不开放**（Q4） | Agent 不能替用户改脚本；MCP 结果里最多一句「可在 Tavotto 里写回脚本」 |

### 7.4 结构性看护（「Agent 不能替用户改脚本」写成判据）

提交端点只收**预览端点发给同一个浏览器会话**的一次性确认令牌（绑定 diff 的 sha256 与脚本写回前的 sha256，单次、短时效），
令牌不出现在任何 MCP 结果里；AST 门禁：`codex-plugin/`、`engine/ai_*`、`specfix`、样式与刷新代码里不许出现提交端点的调用。

## 八、安全边界

- **不放松任何现有边界**：新端点全部走 ADR 0008 的 guard（旁路仍只有那三个）；worker 沙盒、`Path.unlink` 守卫、0047
  的 cwd 模式原样；**写脚本的只有 Flask 父进程**，worker 从不写。
- **写哪个文件由服务端决定**：目标脚本取自注册表里这个 stem 对应的脚本（`app.safe_resolve` / `projectenv.contained_path`），
  请求里不收路径——路径穿越无从谈起。
- **符号链接**：脚本本身或它在项目内的任何父目录是符号链接 → v1 拒绝（`script_is_symlink`）：写穿过去改的是项目外
  的文件，替换则会把链接换成普通文件。**硬链接**（`st_nlink > 1`）同样拒绝：`os.replace` 会断开另一个名字。
- **只读卷 / 没有写权限**：预览阶段就检查（文件与目录的写权限、`statvfs` 的只读位），按钮置灰并说明原因。
- **外置卷**（如 exFAT 的移动硬盘）：没有 POSIX 权限位、会生成 `._` 旁文件；同目录 rename 仍是原子的。macOS 上 fsync
  用 `F_FULLFSYNC`。写到一半被拔掉：临时文件名带固定前缀，下次打开项目清理。
- **原子写**：同目录临时文件 `.<名字>.tavotto-writing` → 写入 → fsync → 复制权限位（能复制属主就复制）→ `os.replace` →
  fsync 目录（失败只记日志，与写回事务同一处置）。Windows 上文件被编辑器独占 → `file_locked` 409（既有处置）。
- **不支持的会话**：native（`tavotto run`，[0021](0021-tavotto-run-product-contract.md) §9.4 恒禁写回）、`runtime:` 资产、同一脚本有进行中的 AI 会话
  （`script_busy`）——入口不出现或 409。

## 九、编码与格式

- 编码按 PEP 263 取（`tokenize.detect_encoding`：BOM / 编码声明 / 默认 UTF-8）；解不开就拒绝，不猜。
- 块用文件自己的编码写；写不进去（如 latin-1 声明的文件放不下中文注释）→ 自动换成纯 ASCII 版的块（英文注释、字符串用
  `ascii()` 转义）。
- **块外一个字节不动**：换行风格只作用于插入的那一段（按文件的主导换行），混合换行的文件原样保留；BOM 原样；缩进按文件
  第一处缩进（空格或制表符）。
- spike 实测矩阵（UTF-8 LF、CRLF、BOM+CRLF、GBK 声明含中文注释、制表符缩进、latin-1 声明）：6/6 编码与换行不变、删块逐字节
  还原、插两次 == 插一次，且在不 import Tavotto 的纯 matplotlib 进程里调整生效；另对 9 个源文件（全部用例脚本、`paper_style.py` 与用户脚本乙的原版）加测「前三行 CRLF、其余
  LF」的混合换行，删块同样逐字节还原。
- 对应仓库里的两条教训：「编码要钉两侧」——读与写都按同一个探测结果，诊断预览是 UTF-8 JSON，但文件字节永远在服务端由原编码
  产生，前端从不回传文件内容；「跨语言内建函数不同源」——生成器与插入 / 删除只有 Python 一份，前端不做第二份实现。

## 十、分阶段实施（PR 切分）

1. **worker 前置修正**：`paper_style.save` 捷径改经 `Figure.savefig`；worker 把 `TavottoAdjustmentWarning` 收进 warnings；
   一次性 worker 的 staging 源码装载（§五.2）。每条带真 worker 用例；不改任何用户可见行为。
2. **生成器 `engine/scriptadjust.py`（纯标准库）**：(role, prop) → 写法的表（含 §十四 的路线 1 `_title_to` 与路线 2(a)
   `_legend_refont`）、选择器、块模板、插入 / 删除 / 编码。
   **枚举而不是白名单**：用例遍历 `HANDLERS` 的每个键，要么在写法表里，要么在 `NOT_WRITABLE` 里带原因——新增 prop 忘了表态
   就红。新增两对严格同源：块里的 axes 遍历 ↔ `axestraversal.ordered_axes`、块里的 stem ↔ `figcapture.savefig_stem`（各配
   golden 向量），写进 `docs/rules/repo/same-origin-pairs.md`。每个 T1 / T2 键用 `tests/support/overridesample.py` 的采样值在
   真 matplotlib 上跑一遍「生成代码 == `overrides.apply`」（几何 + 像素），不过的移进 `NOT_WRITABLE`。
3. **事务与备份库**：`engine/scriptbackup.py` + 端点 `POST /api/script_writeback/preview`（生成 + verify，回 diff、报告、
   令牌）、`/commit`、`/restore`、`GET /history`。落盘经 `atomicio` 的同一套 fsync 原语。
4. **前端**：确认界面、结果区、历史与恢复；i18n；e2e（真浏览器截一张确认界面）。
5. **规则与发行**：`docs/rules/backend/script-writeback.md` + `src/tavotto/AGENTS.md` 速查行 + 本 ADR 转 Accepted +
   发行说明。

## 十一、测试策略（含反证）

- 事务分支（假 worker，照 `tests/test_write_back.py`）：每个失败出口 409 且原脚本逐字节不变、没有残留临时文件、
  备份目录按规则删 / 留。
- 真链路：仓库示例 + 五种脚本形状（循环出多张图、辅助模块里 savefig、`plt.close` 紧随 savefig、子图网格 + 插图、只有
  `plt.show`）。
- 字节矩阵：spike 的六种编码 / 换行 / 缩进，加混合换行、无结尾换行、只有代码没有 import、`from __future__`。
- 恢复：pristine 永不清理；外部改过 → 两个选项；恢复前先备份；override 放回。
- 安全：符号链接、硬链接、只读目录、项目外路径、未认证 401、令牌跨会话 / 重放 / diff 不符一律拒；AST 门禁
  （§七.4）。
- **每条新用例提交前手工反证一次**（根 AGENTS「反证先验落点」）：拿掉 verify 调用 → 「图例整体字号按逐条翻译」的用例必须红；
  撤掉 `paper_style` 修正 → 钩子触发用例红；块里 axes 遍历只取 `fig.axes` → 插图用例红；去掉逐步兜底 → 运行时定位失败用例
  从「告警」变「脚本崩」而红；提交后不清零已写入的 override → `value_original` 用例红；插入时统一换行 → 混合换行用例红；
  拿掉广播连带规则 → 图例组合写回用例红。

## 十二、风险

1. **给 `Figure.savefig` 打补丁**：别的库或用户自己也可能包它。块包的是「当时的」那个，按装载顺序叠加；重复安装是空操作。
2. **脚本在 savefig 之后还改图**：Tavotto 的基线是「脚本跑完」，钩子作用在 savefig 那一刻，两者不同——verify 会拒，这类图写
   不了，报告里说明。
3. **只证明了 Tavotto 里等价**：verify 跑的是 Tavotto 的 worker；终端里的差别只剩 savefig 真的落盘（spike 在纯 matplotlib
   进程里量到调整生效，但没有逐像素比终端产物）。以后可加「在我的环境里再跑一次」。
4. **用户用更旧的 matplotlib 跑**：块只用 3.8 起就有的公开 API（`Legend.set_loc`、`legend_handles`），与 worker extra 的下界
   `matplotlib>=3.8` 一致；更旧的版本上那一步告警跳过。
5. **无 label 对象按位置**：与 0083 同一条边界，确认界面标注。
6. **可复现运行包的校验清单**会变（§7.1 提示）。
7. **同步盘**（iCloud / Dropbox）在替换瞬间可能生成冲突副本——备份在，结果区照实说。
8. **heavy 脚本**：每次写回多跑一遍。

## 十三、1.0 建议

这是**扩大产品能力**并新增一个「写用户文件」的写入面，不属于 1.0 收敛纪律的四类例外（correctness / safety / compatibility /
release blocker）。**建议不进 1.0 GA，排在 1.0 之后的第一个 minor**；在那之前可以先合本 ADR（Proposed）与 PR 1 之外的任何
东西都不动。若用户决定进 1.0，最小可发范围是 PR 1–4 且只开 T1 + 文字 / 图例拖动（spike 已验证的那几类），T2 其余与二次写回
合并编辑放到之后。

## 十四、覆盖率路线（2026-09-26 第二轮 spike）

用户追问「为什么不能 100%」。这一轮把第一轮写不了的两类（标题拖动 7 条、图例整体字号 6 条）各实测了能想到的路，
并把 36 个 T3 键逐个对着 setter 复核。数字出处：`docs/spikes/0094-script-writeback/evidence/results_routes.json`
（路线 1 与 2(a)）、`results_legend_ast.json`（路线 2(b)）；第一轮原样留在 `results_round1.json`。
spike 仍只在 scratch 里跑，数据目录、配置目录、`HOME` 三者都指向 scratch。

### 14.1 路线 1：标题拖动用公开的 `set_title(..., y=...)`（成立，T3 → T2）

matplotlib 3.8.4（读 wheel 源码）与 3.11.2（读已装源码）的 `Axes.set_title` 是同一段：`y` 为 None 时取
`rcParams['axes.titley']`，仍为 None 才置 1.0；**否则 `self._autotitlepos = False`**。`_update_title_position`
开头见到 `_autotitlepos` 为 False 就直接返回。所以显式传 y 就是关自动定位的公开入口。

代价是 `set_title` 会把字号 / 字重 / 对齐重置成 rcParams，并把标题间距（pad）重置成 `axes.titlepad`。块里的
`_title_to` 因此把此刻标题的 `FontProperties`、颜色、对齐、旋转、透明度作为参数原样带回去，最后按 Tavotto 同一个
算法（当前 transform 的逆）把标题落到 figure 分数上——pad 被重置不影响终点，因为终点是在新 transform 上反算的。

实测 7/7 通过 verify，含一份在脚本里显式设了字号、字重与标题间距的标题。已知限制：`_autotitlepos`
是整个子图的开关，左 / 右标题会跟着停止自动定位（spike 用例里都没有左 / 右标题，未实测）；`title_left` /
`title_right` 的拖动仍不写。

### 14.2 路线 2：图例整体字号与间距

| 路 | 做法 | 6 条实测 | 失败形态 |
| --- | --- | --- | --- |
| (a) 钩子块里用公开 API 重建 | 条目取 `ax.get_legend_handles_labels()` 里按文字唯一匹配到的源对象；位置、锚点、列数、边框、各项间距、标题从 **manifest 的整份图例参数**烘进 `ax.legend(..., fontsize=新值)`；`borderaxespad` / `markerscale` / `numpoints` 等 manifest 没有的读旧图例的同名属性；文字的颜色 / 字体从旧文字逐条搬 | **5/6** | 用户脚本甲把改过的代理 handle 与一个自定义色带代理**显式**传给 `legend()`：matplotlib 没有公开接口读回原来传进去的 handle（`legend_handles` 是它派生出来的示意对象，自定义 handler 对不上），块里这一步告警跳过 → verify 拒 |
| (b) 源码层精修（方案 B 作补充） | 脚本里恰好一处 `.legend(...)`、不在循环里、没有 `prop=` / `**kwargs` 时，就地改 `fontsize=` 的值，没有就新增这个参数 | **5/6** | 用户脚本乙的图例用锚点放在子图**外面**，`tight_layout()` 会把它算进边距：字号在 `legend()` 那一刻就变大，布局随之变化（子图框移动），与 Tavotto「脚本跑完再改」的热态不等价 → 几何门 + 像素门拒（3.9% 像素） |
| (c) 维持不写 | 留作 override | 0/6 写入 | 在 Tavotto 里照样生效，终端产物里没有 |

(a) 与 (b) 恰好互补：(a) 败在「显式代理 handle」，(b) 败在「图例参与布局」，两份失败用例互不重叠，**先 (a)、verify 不过再试 (b)** 时 6/6
都能写进脚本。第一版 (a) 自己也踩了一个坑：用 `Text.update_from` 从旧文字搬样子，它连 transform 一起抄，新文字全落到
旧图例的坐标上——5 条全部被几何门拒，改成只搬字体 / 颜色 / 透明度后通过。又一次说明「看起来等价」要靠 verify 来判。

采用建议：v1 做 (a)，并在生成时就按 manifest 的图例条目绑定（0034）判断「条目是不是按 label 自动收集的」，不是就直接进报告、
不等 verify 才发现。(b) 是另一类改动（改的是用户自己的一行代码，不在标记块里）：「删掉整段即恢复」对它不成立，恢复只能靠备份。
所以 (b) 放到之后，作为确认界面里单独勾选的「精修」，不进 v1。间距类（`borderpad`、`labelspacing`、`handlelength`、
`handletextpad`、`columnspacing`、`ncol`）与字号走同一条 `legend(...)` 参数路，机制相同，**未逐条实测**。

### 14.3 T3 复核：36 个键的去向

| 去向 | 键数 | 键 | 依据 |
| --- | --- | --- | --- |
| 本来就是公开 setter（第一轮分错档） | 4 | 图例 `title` / `title_fontsize` / `frame_linewidth` / `frame_rounded` | setter 就是 `set_title`、`get_title().set_fontsize`、`get_frame().set_linewidth / set_boxstyle` |
| 路线 2（图例重建） | 9 | 图例 `fontsize`（实测）、`borderpad`、`labelspacing`、`handlelength`、`handletextpad`、`columnspacing`、`ncol`、`entry_order`（重排传入的 handle）、单条的 `binding`（换传入的 handle） | 都是 `legend(...)` 构建期参数或 handle 的选择 |
| 单条图例示意线 | 5 | `handle_color` / `handle_linestyle` / `handle_linewidth` / `handle_marker` / `handle_markersize` | `legend_handles[k].set_*` 是公开 API；未实测 |
| 路线 3（公开 locator / formatter） | 11 | 刻度 `major_mode` / `major_step` / `major_values` / `format` / `minor_*` 共 10 个，单条刻度文字 1 个 | Tavotto 自己就是用 `set_major_locator(MultipleLocator / FixedLocator)`、`set_major_formatter` 实现的，块里可以照写；未实测 |
| 块里复刻 Tavotto 的算法 | 1 | 图像 `gradient_color` | 公开的 `get_array` / `set_data` 加一段 numpy 分解，可写但要把算法抄进块；未实测 |
| **只能靠私有 API（Q10：永不写）** | **6** | 色条 `orientation` / `extend`（就地改 `cb._inside` 等再 `_draw_all()`）；3D 轴箭头 `axis_arrows` / `arrow_color` / `arrow_head` / `arrow_width`（Tavotto 自建的 artist 子类，依赖私有投影接口） | 这 6 个永远留作 override，在 Tavotto 里照样生效 |

### 14.4 新的预估覆盖率，以及为什么仍不是 100%

- **spike 的 82 条**：第一轮 69（84%）→ 开路线 1 与 2(a) 后 **81/82（98.8%）**，剩下 1 条就是 14.2 里的显式代理图例；
  加上 2(b) 兜底为 82/82。组合写回 7/8 组与热态 PNG 逐字节相同；不过的那组正是用户脚本甲——只有那条图例被拒、整组 409，
  符合 Q5 的「逐条诊断」流程。
- **249 个键的上限**：只有 6 个（2.4%）在原则上写不了；其余 243 个（97.6%）有公开 API 路子，其中实测过的仍只是 spike 的
  那几类（T1 的 181 个与路线 3 的 11 个都要按 §十一 的枚举用例逐条过 verify 才算数）。

仍到不了 100% 的原因分三层，前两层与键无关、是**脚本形状**决定的：

1. **原则上不写（Q10）**：上表 6 个私有 API 键。
2. **这张图的结构让公开 API 无从下手**：显式代理 handle 的图例（14.2 (a)）、图例参与布局又没有安全的源码改法（14.2 (b)）、
   脚本在 savefig 之后还改图（§十二.2）、只有 `plt.show()` 没有 savefig（Q11）、带布局引擎（constrained / tight 布局
   引擎）的图上拖过的文字——Tavotto 用包住引擎 `execute` 的办法把它们钉住，公开 API 里没有等价物，本轮 spike 的 8 份脚本里
   没有这种图，**未实测**。
3. **定位不唯一**：无 label 的对象按位置定位（Q7），写进脚本之后用户再改结构可能落错——这是写得进去但不保证以后还对，不计入
   覆盖率。

所有写不进脚本的调整都**留作 override、在 Tavotto 里照样生效**，确认界面逐条列出。100% 只在 Tavotto 里成立；终端里
`python fig.py` 的产物只含写进去的那部分。

## 已决定（用户 2026-09-26）

Q1–Q11 全部按「决定」一栏执行（用户原样采纳了当时的建议）。

| # | 问题 | 选项 | 决定 |
| --- | --- | --- | --- |
| Q1 | 默认写回形态 | A 钩子块 / B 改字面量 / C 旁挂文件 | **A**；C 以后作为可选形态再议；B 只在将来做「精修」补充 |
| Q2 | 备份放哪 | 两处 / 只 `tavottofile/` / 只数据目录 | **两处** |
| Q3 | 确认强度 | 勾选 + 按钮 / 还要输入文件名 | **勾选 + 非默认按钮**；输入文件名对高频用户太重 |
| Q4 | MCP / Codex / Agent 入口 | 不开放 / 只读预览 / 预览 + 界面确认 | **v1 不开放**；以后最多只读预览 |
| Q5 | 部分写回 | 能写的写、其余留作 override / 全有或全无 | **部分写回**，界面逐条列出；组合不过时整次 409 + 「逐条诊断」 |
| Q6 | 写回后 override | 同一次提交清零已写入的 / 保留 | **清零**，撤销走「恢复原脚本」 |
| Q7 | 无 label 对象的守卫 | 按位置（同 0083）/ 文字类另加内容守卫 | **按位置**并标注；与 0083 保持一条规则 |
| Q8 | 进不进 1.0 | 进 / 1.0 后第一个 minor | **1.0 后第一个 minor** |
| Q9 | 恢复原脚本时 | 把写进去的调整放回 override / 只恢复脚本 | **放回**（图与写回前一样），可取消勾选 |
| Q10 | 写不进去的调整能否用私有 API 换覆盖率 | 允许 / 不允许 | **不允许**：块要在用户以后的 matplotlib 上长期可跑；§14.3 那 6 个键永远留作 override |
| Q11 | 只有 `plt.show()`、没有 savefig 的脚本 | v1 不支持 / 也钩 `pyplot.show` | **v1 不支持**，预览里说明原因 |
