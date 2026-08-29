# SESSION_HANDOFF — 跨 Session 交接

> **整段重写，不加行。** 半新半旧的状态块会继承「刚被动过」的可信度，
> 而下一个 Session 没有办法分辨哪几行是这次更新的。

---

## 最近一次：Session 09（2026-08-29）

### 目标

把「打开一张图 → 改 → 按原图规格导出」做成**默认路径**，并给「按原图导出」
一个说得出口的定义。

本阶段**不做导出面板、不做输出门禁**（Prompt 12），**不动 Style/Spec 分层**
（Prompt 10），也**不在前端重新判一次「这张图能不能编辑」**（那是 07/08 的
事实模型）。

### 开始前实测到的两件事（不是假设）

1. **图内编辑确实必须先把面板放进画布。** `uiStore.elementPanelId` 存的是
   **画布对象 id**；`usePruneSelection` 在对象离开 `doc.objects` 时清掉它。
   没有对象就没有图内编辑——这不是 UI 层的限制，是 overrides 挂在
   `CanvasObject` 上的直接后果。
2. **导出尺寸没有出处。** `/api/export` 只按 `page_w_mm/page_h_mm` + 每个对象
   的 x/y/w/h 合成，没有"原图尺寸"这条路；而 `/api/panels` 的
   `native_*_mm` 在位图那一档是**猜**的：`ppi = 600 if png else 300`，
   依据只有旁边那句注释。

### 实际完成

**1. `web/src/store/workspace.ts` —— 两条工作流的唯一出口。**

```text
mode: 'fast_edit' | 'layout'      activePanelId: string | null
openFastEdit(figureId)      打开一张图 → 快速编辑工作区
addFigureToLayout(figureId) 已在文档里就聚焦，不重复创建
returnToLayout()            回排版
focusLayoutPanel(panelId)   切画布 + 选中 + 滚进视野（11 / 12 复用）
findFigurePanel(figureId)   「文档里有没有这张图」的唯一判据
```

**关键决定（T-43）：不建第二个容器。** 一张图在文档里只有一个面板对象，
快速编辑是它的**另一种看法**。三个要求因此不需要各写一份实现：

* 「添加到画布不复制失联对象」——根本没有复制这一步；
* 「进图内编辑再返回位置尺寸不变」——快速编辑**一个字都不写 x/y/w/h**；
* 「重复添加不叠对象」——找得到就聚焦。

**代价如实记着**：打开一张图会真的在当前画布上放一个面板（一次可撤销的文档
修改）。用户从没想过画布，但画布里多了一个对象——那个对象就是他接下来编辑的
载体，让它凭空存在于"文档之外"才是幻觉。

**2. 快速编辑这一屏**（`CanvasStage` + 新的 `canvas/FastEditBar.tsx`）：
不铺纸面、不画网格/参考线/标尺、只画那一个对象，取景框（fit / 双击适应）
换成那张图的包围盒；画布标签行与顶栏的标注工具整组收起（它们画的是画布对象，
这一屏上看不见）；浮动条给三样东西——图名、原图规格、两个出口
（添加到画布 / 画布排版）。没有源脚本时多一条说明，词取自
`lib/readinessText.ts`，动作是就绪度中心的 `focusPanel()`。

**3. 打开的入口全部换成快速编辑**：素材卡双击 / Enter（主动作从「加入画布」
换成「打开」）、运行时图卡片（跑过的那一档）、`tavotto open <stem>` 的交接。
交接不再自己拼「有就选中、没有就 addPanel」——它调 `findFigurePanel` +
`openFastEdit`，只把结果翻译成 `selected` / `placed`。

**4. `web/src/lib/originalSpec.ts` —— 原图规格的唯一服务**（ADR 0028）。
规格不确定时**界面必须说出来**（浮动条上一个短标记 + 一句 `title`）：
`assumed`（位图没写密度）/ `stale`（源文件不在了，这是上次已知的）/
`fallback`（一个来源都没有，显示「尺寸未知」而**不是**那个占位数）。
优先级 ① 渲染回来的 manifest `size_mm` → ② 文档里的 `nativeW/nativeH`
→ ③ `/api/panels` 的 `original_spec` → ④ 明确 fallback。①在②之前是因为
**图幅不是派生字段**；②在③之前是因为**源文件消失之后它还在**。
画布上的缩放 / 裁剪 / 旋转 / 翻转 / 透明度只进 `spec.ignored`。

**5. `src/tavotto/engine/originalspec.py` —— 事实层。** 位图密度**先量后猜**：
纯标准库解析 PNG `pHYs`、JPEG JFIF 密度、以及 JFIF 只给长宽比时 Exif 的
`XResolution`/`YResolution`；读不到才落回 `ASSUMED_DPI`（**取值与改造前逐位
相同**）并报 `dpi_source: "assumed"`。`/api/panels` 的 `native_*_mm` 改成这份
spec 的**投影**，不是第二次计算。

**别改回 MuPDF 的 `Pixmap.xres`**：实测（PyMuPDF 1.28.2）它对「没有 pHYs」
与「写着 96 dpi」一律回 `96`——两个不同的答案被压成同一个值，而"不知道"正是
这里最需要说出来的那一档。`test_ninety_six_dpi_is_metadata_not_the_absence_of_it`
就钉着这条。

### 关键 API（后面几个 Prompt 直接用）

```ts
// web/src/store/workspace.ts
useWorkspaceStore              // mode / activePanelId
openFastEdit(figureId)         // 'editing' | 'layout_only' | 'missing'
addFigureToLayout(figureId)    // 'added' | 'focused' | 'missing'
returnToLayout()
focusLayoutPanel(panelId)      // boolean —— 11 的问题定位、12 的导出报告直接调
findFigurePanel(figureId)      // { panel, canvasId } | null
restoreWorkspace(documentId, objects)   // 恢复前先验对象还在不在

// web/src/lib/originalSpec.ts   ← Prompt 12 的导出面板从这里取尺寸
getOriginalOutputSpec(figureId): OriginalOutputSpec | null   // 不认识 → null
resolveOriginalSpec(inputs)    // 纯函数核心，判据都打在它上面
ignoredTransforms(panel)       // 画布上设了、原图导出不套用的那几项
FALLBACK_MM                    // 占位尺寸；走到它的 spec 必带 fallback: true
```

```python
# src/tavotto/engine/originalspec.py
asset_spec(path, kind, probe) -> dict     # 文件自己说了什么
raster_dpi(path) -> (x, y) | None         # 只回文件写下的密度，没写就 None
ASSUMED_DPI / ASSUMED_DPI_DEFAULT         # 明确 fallback（值与改造前相同）
```

`/api/panels` 每项新增 `original_spec`；`PanelObject` 新增可选 `pxH`；
`pdfbackend.probe_asset()` 的 raster 结果新增 `alpha`。

### 迁移

**没有迁移，磁盘格式一个字节没升版。** 两处新增都是**可选**字段：
`PanelObject.pxH`（老文档没有它 = 那一维未知，**不许补默认值**）、
`PanelInfo.original_spec`（老后端不发 = 那个后端没有这份事实，解析退到文档里
那份）。新增的本机存储是 `localStorage['tavotto.workspace.<documentId>']`，
读不回来就当"上次在画布排版"。

**一处行为变化要留意**：写了物理密度、而那个密度不等于我们旧假定的位图
（例如 72 dpi 的照片、300 dpi 的 PNG），`native_*_mm` 会变成按文件说的算。
改造前那些值本来就是错的——但用户**已经摆在版上的面板一个都不动**
（`nativeW/nativeH` 存在文档里，`panelSourceSync` 明确不碰图幅）。

### 修改的文件

```text
新增  src/tavotto/engine/originalspec.py         原图规格事实层（+16 条用例）
新增  tests/test_original_spec.py               （含两侧 dpi_source 闭集的同源看护）
新增  web/src/store/workspace.ts                 两条工作流（+19 条用例）
新增  web/src/store/workspace.test.ts
新增  web/src/lib/originalSpec.ts                原图规格唯一服务（+25 条用例）
新增  web/src/lib/originalSpec.test.ts
新增  web/src/canvas/FastEditBar.tsx             快速编辑浮动条
新增  web/src/canvas/fastEditStage.test.tsx      这一屏的可见差别（5 条）
新增  docs/adr/0028-original-output-spec.md
改动  src/tavotto/app.py                         scan_panels 走 originalspec
改动  src/tavotto/pdfbackend/pymupdf_backend.py  raster probe +alpha
改动  web/src/canvas/CanvasStage.tsx             快速编辑取景 / 只画一个对象
改动  web/src/canvas/PageSheet.tsx               +data-page-sheet 测试落点
改动  web/src/components/TopBar.tsx              模式标签 + 标注工具收进 MarkTools
改动  web/src/components/left/AssetBrowser.tsx   主动作「加入画布」→「打开」
改动  web/src/lib/openRequest.ts                 交接落到快速编辑
改动  web/src/App.tsx                            挂持久化订阅 + 快速编辑藏画布标签
改动  web/src/hooks/usePruneSelection.ts         对象消失也退出快速编辑
改动  web/src/store/projectStore.ts              换项目清工作区
改动  web/src/store/workspace.ts                 returnToLayout 的焦点救援
改动  web/src/store/actions.ts / lib/clipboard.ts / lib/migrate.ts /
      store/panelSourceSync.ts                   pxH 与 pxW 成对
改动  web/src/lib/api.ts                         +AssetOriginalSpec / original_spec
改动  web/src/types/document.ts                  +PanelObject.pxH
改动  web/src/i18n/locales/*                     +assets.open* / +fastEdit.*，
                                                 删掉死掉的 assets.addAria
改动  web/src/i18n/overflow.test.tsx             +6 条字数预算
改动  web/src/components/left/AssetBrowser.runtime.test.tsx  主动作改名跟着改
改动  AGENTS.md                                  +一行严格同源对（dpi_source 闭集）
改动  src/tavotto/AGENTS.md / web/AGENTS.md      长期规则的家
重建  codex-plugin/mcp/widget/canvas.html        指纹 e8a2c128a5200354
重建  web/dist-playground/                       指纹 73719cc4290353e6（不进 git）
```

### 测试命令与真实结果

```sh
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest tests/test_original_spec.py
cd web && pnpm test && pnpm build && pnpm i18n:check && pnpm lint
# 单跑一个前端用例文件要自己补环境变量（package.json 的 test 脚本里有）
NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/store/workspace.test.ts
# 改了 web/src 之后两个受管产物都要重建
python scripts/build_mcp_widget.py && python scripts/build_browser_playground.py
ruff check . && ruff format --check .
```

（实跑数字见 `STATUS.md` 的「Session 09 之后」表。）

**变异反证 26 条全部被打红**；第一轮活下来 2 条，成因与处置见
`TEST_MATRIX.md`。两条都不是"判据写错了"，而是**判据没被执行到它该看的那个
点上**：一条是 T-36 的形状（两条判据说同一件事），合并之后露出了一个从来没被
量过的维度（"上次停在画布排版"）；另一条是三条界面用例里没有一条让素材从清单
里消失过，于是「上次已知」那个标记在界面上从来没被量到。

### 这一轮踩到的坑

**1. `Pixmap.xres` 看不见"没写"这一维。** 第一版打算直接用 MuPDF 报的
`xres`——实测之后发现没有 `pHYs` 的 PNG 和写着 96 dpi 的 PNG 它一律回 96。
**尺子量不了那一维时，判据是恒等成立的**：两条用例会给出同一个答案，而它们
问的是两件不同的事。处置是自己按格式解析（纯标准库）。

**2. pHYs 是每米整数像素**，300 dpi 落盘再读回来是 299.9994。第一版用例
`assert dpi == 300.0` 当场红——红的不是实现，是**编码损失**。量化误差上界
`0.0254/2 = 0.0127`，所以"离最近整数不到 0.02 就还原"是有根据的，不是
四舍五入的方便。

**3. 一条用例自己把 JFIF 段写坏了。** 测试里改 JFIF 密度时把 units 写到了
版本字节上（`JFIF\0` 之后是 2 字节版本再是 units），于是"读不到密度"这条绿得
毫无意义，"读得到"那条红。**自己捏的输入形状会产生假红**——先确认构造是对的
再怀疑实现。

**4. 前端 mock 回 `undefined` 把崩溃甩到被测代码外面。**
`AssetBrowser.runtime.test.tsx` 把 `@/store/actions` 整个打了桩，
`addRuntimePanel` 回 `undefined`；主动作改成"打开"之后，工作区要拿它的 `id`
——报错栈指向 `workspace.ts`，看起来像产品坏了。处置是让桩回一个真的对象，
不是给产品代码加一句 `?.`（那句话没有任何用例能打红它）。

**5. `npx vitest` 直接跑仍然会漏 `NODE_OPTIONS=--no-experimental-webstorage`。**
连续第三轮踩到，记在这里。

### 尚存限制

1. **导出还没有"按原图"这条路。** 本轮只给规格与合同，`/api/export` 仍然只
   会按画布合成——**在快速编辑里点顶栏「导出」，出来的仍然是整张画布**。
   Prompt 12 接（它要做的第一件事就是让导出面板读 `getOriginalOutputSpec()`
   并给出「按原图 / 按画布」两条路）。这一条是本轮已知的**表里不一**：
   工作区说的是一张图，导出给的是一张版。
2. **快速编辑里画不了画布标注。** 标注工具整组收起了——它们画的是画布对象，
   这一屏上看不见。图内标注（override）不受影响。真需要"在图上加个箭头"的
   用户，路径是"添加到画布 → 排版模式"。
3. **打开一张图会在当前画布上放一个面板**（见上面的代价）。多画布项目里它落在
   **激活画布**上，不由用户挑。
4. **`original_spec` 只覆盖 `/api/panels` 的素材。** runtime 素材（ADR 0013）
   走描述符里的 `size_mm`，没有像素网格与密度——它没有磁盘原件，那两维本来
   就不存在。
5. **`FALLBACK_MM` 是个占位常数**（80 × 60 mm），与 Prompt 10 的规范层没有
   耦合。真到了要按规范给默认尺寸的那一步，那是 10 的事。
6. **窄屏下快速编辑浮动条没有实测过**。它是一条 flex 行，jsdom 量不出溢出；
   英文字数预算已经进了 `overflow.test.tsx`，但真实断行要等 e2e（issue #30 的
   POSIX 腿仍然缺）。
7. 04–08 的其余遗留原样开着（R-05 五处手写原子写、R-07 autosave 位置、
   `/api/layouts/<name>` 无 schema 校验、axe 那两条从没真跑过）。

### 工作树状态

- worktree：`/Volumes/Projects/Tavotto/.claude/worktrees/product-ux-v2`
- 分支：`feat/product-ux-reliability-v2`，从 `origin/main` 的 `ef9ac02` 开出
- **PR #201** 带的仍然是 Session 01–04；**05–09 的提交还没有推**——节奏由用户
  定（一推就触发一轮 Codex 评审）
- author 用 `88193520+erwanjun@users.noreply.github.com`（与 `main` 上每一个
  提交一致）。本机 `~/.gitconfig` 是别的邮箱，提交时用
  `git -c user.email=… commit`，**别改共享的 `.git/config`**

---

## 下一阶段入口（Prompt 10：Style / Spec 分层）

**从这里开始读**：`UX_CONTRACTS.md` 的「4. Style / Spec / Validation / Export
分层合同」与新增的「6b. 原图规格合同」、`ARCHITECTURE.md` 的 §6。

**Session 09 留给它的可复用入口**：

| 东西 | 位置 | 性质 |
| --- | --- | --- |
| 这张图有多大 | `lib/originalSpec.getOriginalOutputSpec(figureId)` | **唯一服务**；12 的导出面板、10 的"规范说尺寸该是多少"都从它取现值 |
| 画布上设了但原图导出忽略的变换 | 同文件 `spec.ignored` | 界面照此说明；别在导出面板里重新算一遍 |
| 定位到画布上的某个面板 | `store/workspace.focusLayoutPanel(panelId)` | 11 的问题面板直接调 |
| 打开一张图 / 加入画布 | `openFastEdit` / `addFigureToLayout` | 18 的 QuickEdit、21 的 onboarding 直接调 |

**文档 / profile 上可以绑的字段**（Prompt 10 需要的那几个）：
`CanvasData.profile: DocumentProfile { id, journal? }` 已经在文档里（每张画布
各自一份，规则本身一条都不进文档）；最小字号的两个数仍在
`src/tavotto/profiles/publication.json`（`absolute_min_font_size_pt: 8.0` 与
`legend_policy.min_font_size_pt: 8.5`，R-11），**统一为 8 pt 要在 profile
文件里改，不在两个求值器里改**。

**绝不要做的事**（07 的六条、08 的三条原样成立，09 再加四条）：

10. **不许给快速编辑建第二个容器**（隐藏画布、文档顶层 `figures[]`、
    per-figure override 表都算）。两个容器 = 两份 `overrides` = 一条迟早会漏
    的同步规则，而漏掉的表现是"我在那边改的东西这边没有"。
11. **不许在快速编辑里写 x/y/w/h。** 「返回后布局不变」靠的是没动过；
    改成"回来时恢复一下"的那一刻，旋转 / 成组 / 布局组重排里就会有一条路径漏掉。
12. **不许在导出那一刻现算尺寸。** 来源优先级写在 ADR 0028 里，实现只有
    `lib/originalSpec.ts` 一份；要加一档就改 ADR。
13. **不许把 `dpi_source` 压成两档。** `assumed`（文件没写）与 `derived`
    （反算出来的）不是同一件事，`unknown`（这一维没测量）更不是。

**必须保留的不变式**（改动前先确认还成立）：

1. `loadSeq` / `derivedSeq` 把「载入」「用户编辑」「派生同步」分成三档。
2. `dirty` 同时盯 `doc` 与 `canvases`；收到 409 后基线**故意不推进**。
3. 落盘一律走 `engine/atomicio`（ADR 0023）；保存状态只经 `setSaveState()` /
   `setDocNotice()` 改（ADR 0024）。
4. **刷新的编排只有 `refresh_project_index()` 一份**（ADR 0025）；**发现只有
   `project_watch` 一份**（ADR 0026）；**前端的消费只有 `liveSync` 一份**；
   **能力事实只有 `readiness` 一份**（ADR 0027）；**原图规格的决策只有
   `lib/originalSpec.ts` 一份**（ADR 0028）。
5. **无差异 = 零事件、零写盘、零 worker 失效、零缓存失效**（后端）；
   **无差异 = 零 `set()`、零 dirty、零提示**（前端）。
6. 「哪些文件算素材」只有 `iter_assets()` 一处；脚本遍历只有
   `discover.iter_all_scripts()` / `iter_scripts()` 两个视图；「谁认领了这个
   stem」只有 `discover.claims_of()` 一处；「状态说成什么话」只有
   `lib/readinessText.ts` 一处；**「文档里有没有这张图」只有
   `findFigurePanel()` 一处**。
7. **就绪度不执行用户脚本、不 probe、不写盘、不改注册表、不发 SSE**；
   界面也不执行。
8. **派生数据刷新不得把文档标脏（对用户而言），也不得进普通撤销历史。**
   侧栏折叠、横幅关闭、聚焦目标、**工作区模式**同样不进文档、不进 undo。
9. **素材不在清单里 ≠ 脚本关系失效**（T-28）；**也 ≠ 这张图没有规格**
   （原图规格退到文档里那份并标 `stale`）。
10. `reason` 是闭集，表外的值归成 `manual`；客户端字符串不透传。
11. **「没测量」不许压扁**：`conflicts` 的 `null`、`registry_valid` 的 `null`、
    `capability` 的 `undefined`、**`dpi` 的 `null` 与 `dpi_source` 的四档**。

---

## 历史：Session 08（2026-08-29）

### 目标

把 Session 07 算好的那份事实**变成普通科研用户看得懂的产品体验**，并顺手把
左侧工作区外壳整理成稳定的常驻结构。

本阶段**不改 watcher、不增强解析器、不实现多选栏与 onboarding**，也**不在
前端重新判一次状态**。

### 实际完成

**1. `web/src/store/projectReadinessStore.ts` —— 就绪度的前端唯一持有者。**
职责只有三样：把报告取回来、记住「用户已经看过哪一版」、记住「接入中心此刻
聚焦在哪张图」。并发治理逐条照抄 `assetStore` 的纪律（请求序号挡旧响应、
发请求那一刻的 `pj` 挡串项目、同批合并、`force` 另起一次、失败保留上一次成功
那份）。**fingerprint 没变时连报告对象的引用都不换**——换了引用，订阅它的每个
组件都会白重渲染一轮。

**2. 顶部摘要横幅 `ProjectReadinessBanner`**，与 `UpdateBanner` /
`DocumentBanner` 同形（同高度、同挂载点、不阻塞画布、不自动弹框）：

```text
已找到 18 张图：8 张可编辑，5 张待连接，5 张仅排版。   [查看接入状态] [关闭]
```

关闭按 **项目 id + 报告 fingerprint** 记在本机（`tavotto.readinessDismissed`，
只留最近 20 个项目，坏 blob 安全恢复，**不记项目绝对路径**）。事实一变就再说
一次——它不是「永久别再提」。

**3. `RegistryDialog` 重构成「项目接入状态」**（文件名与导出名保留，T-38）。
信息架构从「一份脚本清单」翻成「一张图一行」：

```text
总计 18 · 可编辑 8 · 待连接 5 · 仅排版 5          [重新扫描]
需要处理（5）
  Fig3.pdf                                        [有冲突]
  不止一个脚本说自己生成这张图，需要你指定用哪一个。
  [用 old_version.py] [用 z_newer.py]  ▸ 技术详情
可编辑（8） / 仅排版（5）
▸ 全部脚本（12）        ← 高级段：每个 .py + 试运行 + 手工填图名
```

每个状态的下一步、以及**绝不做的事**：

| 状态 | 动作 | 绝不 |
| --- | --- | --- |
| `editable` | 添加到画布 / 重新试运行 / **技术详情里改绑**（候选不含它现在连着的那个） | — |
| `auto_linkable` | 自动连接（= 重新扫描） | — |
| `needs_probe` | 试运行并连接（点之前先说「Tavotto 将运行这个脚本」） | 不自动跑 |
| `conflict` | 候选**逐个列出**，点哪个写哪个 | **不猜**，一个都不预选 |
| `source_missing` | 重新扫描 / 选择新脚本 | 不说成"文件损坏" |
| `layout_only` | 选择源脚本 / 继续当普通素材 | **不画成错误** |

聚焦（`focusPanel(id)`）：打开 → 滚到那一行 → 焦点落上去 → 短暂静态高亮 →
**当场清掉聚焦标记**。关闭后的焦点归位**不在这里**——`ui/Dialog` 已经做了
（`onOpenAutoFocus` 记、`onCloseAutoFocus` 还，带节点被换掉的兜底）；再记一份
就是同一条保证有两个实现，删掉任意一个都不会有用例红（T-36 的形状）。

**4. 素材卡与画布上的四个出口**，全部读同一份 `PanelInfo.capability`：

* 卡片左下角一个**非交互** `<span>` 角标（`editable` 不加，那里已经有 `{}`）；
  状态进 `aria-label`；完整解释在 `title` 与说明条里；
* 选中卡片后，**listbox 外面**一条说明条（文件名 · 状态 / 一句原因 /
  「查看接入状态」按钮）——`role="option"` 里不许再嵌可 Tab 的控件；
* 画布单选没有编辑入口的图时，ContextBar 上多一个「为什么不能编辑？」；
* 属性栏 panel 段顶部一条非阻塞说明。

**5. 常驻左侧工作区外壳。** 轨道与抽屉的骨架早就在（默认展开、可折叠、可钉住、
可拖宽、三档断点），本轮做了三件事：轨道底部加**项目接入状态**入口、在
`ITEMS` 旁标注 Prompt 11 的「问题」入口位置（**不放占位按钮**），以及——

**修掉一个真实缺陷（T-40）**：`persist()` 原来照抄当前状态，而**响应式让位也
写在同一个 `leftOpen` 上**。把窗口拖窄一次（左栏自动让位），之后任何一次
persist 都会把 `leftOpen: false` 当成偏好写进本机；回到大屏、重启之后常驻左栏
再也回不来，**而用户从没关过它**。现在偏好单独记一份，只有用户自己的动作与
产品规则写它，响应式让位一律不写。

**6. 后端一处很小的改动**：就绪度报告的每个 panel 多一个 `stem`（T-37）。
关联动作写进去的键是 stem 不是那张图，而 `sub/Fig.v2.pdf` 的 stem 是 `Fig.v2`
——前端自己切就是第二份判据。`CAPABILITY_FIELDS` 一个字没改。

### 关键 API（后面几个 Prompt 直接用）

```ts
// web/src/store/projectReadinessStore.ts
useProjectReadinessStore   // report / loading / error / focusId / dismissed
  .load({ force })         // 合并；force 另起一次
  .focusPanel(fileId)      // 打开「项目接入状态」并滚到这张图  ← 17/18 复用这个
  .openCenter() / .closeCenter() / .dismissBanner() / .clear()
bannerReport(state)        // 横幅该不该出现（纯函数，五个条件）

// web/src/lib/readinessText.ts   ← 状态、句子与「待连接」的唯一一份实现
statusLabel(status)        // 可编辑 / 待连接 / 需试运行 / 有冲突 / 源脚本丢失 / 仅排版
reasonText(capability)     // 按 reason_code 查，**不按 status**
PENDING_STATUSES           // 「待连接」是哪几个状态（集合，不是显示顺序）
pendingCount(summary)      // 由上面那个集合现算——横幅与接入中心同一个加法

// web/src/lib/api.ts
ReadinessPanel.stem        // 新增：关联动作的键
ReadinessSummary           // 从内联类型抽出来的具名类型
```

**开关仍然是 `uiStore.registryOpen`**（T-38）——就绪度 store 里没有同义布尔值。

### 迁移

**没有迁移，磁盘格式一个字节没动。** 唯一新增的本机存储是
`localStorage['tavotto.readinessDismissed']`（项目 id → fingerprint），
读不回来就当"谁都没关过"。`tavotto.ui` 的键集没变——`leftOpen`/`rightOpen`
写进去的值从「当前状态」改成了「用户的偏好」，老 blob 原样能读。

### 修改的文件

```text
新增  web/src/store/projectReadinessStore.ts    就绪度前端持有者（+ 22 条用例）
新增  web/src/lib/readinessText.ts              状态标签 / 一句话原因（唯一一份）
新增  web/src/components/ProjectReadinessBanner.tsx  顶部摘要（+ 9 条用例）
新增  web/src/canvas/drawerViewportResize.test.tsx   抽屉开合 → 画布视口（5 条）
新增  web/src/canvas/panelReadinessEntry.test.tsx    「为什么不能编辑？」（7 条）
新增  web/src/components/RegistryDialog.test.tsx     接入中心（25 条）
新增  web/src/components/inspector/panelCapabilityNote.test.tsx（5 条）
新增  web/src/components/left/AssetBrowser.readiness.test.tsx（11 条）
改写  web/src/components/RegistryDialog.tsx     脚本清单 → 一张图一行
改动  web/src/components/left/AssetBrowser.tsx  角标 + listbox 外的说明条
改动  web/src/components/left/LeftRail.tsx      项目接入状态入口 + 11 的位置注记
改动  web/src/components/inspector/PanelSection.tsx  非阻塞说明
改动  web/src/canvas/ContextBar.tsx             「为什么不能编辑？」
改动  web/src/store/uiStore.ts                  偏好与实际开合分开（T-40）
改动  web/src/store/liveSync.ts                 就绪度刷新挂在统一入口（T-39）
改动  web/src/store/projectStore.ts             换项目清就绪度 + 重取
改动  web/src/App.tsx                           挂横幅 + 启动取一次
改动  web/src/lib/api.ts                        +ReadinessPanel.stem、+ReadinessSummary、
                                              writeRegistryEntry 的 entry 改成可省
改动  web/AGENTS.md                           +「接入状态与左侧外壳」一节（长期规则的家）
改动  web/src/i18n/locales/*                    +readiness.*，删掉 33 个死掉的 registry.*
改动  web/src/i18n/overflow.test.tsx            +9 条字数预算
改动  web/src/store/uiStore.test.ts             +9 条左栏外壳用例
改动  web/e2e/golden-paths.spec.ts              跟着改名（菜单项 / 对话框名 / 按钮）
改动  web/e2e/a11y.spec.ts                    +2 条 axe 用例（**本轮没真跑过**，见尚存限制）
改动  web/src/i18n/locales/*/errors.json      三条指向「脚本注册表」的后端错误文案跟着改
改动  src/tavotto/engine/readiness.py           panels 多一个 stem
改动  tests/test_project_readiness.py           +2 条、shape 用例的键集 +1
重建  codex-plugin/mcp/widget/canvas.html       改了 web/src 就要重建（指纹 ebea0b57749239f2）
重建  web/dist-playground/                      同上（指纹 4dd2877615f06445，不进 git）
```

### 测试命令与真实结果

```sh
# 后端全量（worktree 里必须带 PYTHONPATH，否则 import 到主工作区）
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest
# 只跑本阶段动过的那份
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest tests/test_project_readiness.py
# 前端（先 cd web）
pnpm test && pnpm build && pnpm i18n:check && pnpm lint
# 单跑一个前端用例文件时**必须自己补环境变量**（package.json 的 test 脚本里有）
NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/store/projectReadinessStore.test.ts
# 改了 web/src 之后两个受管产物都要重建
python scripts/build_mcp_widget.py && python scripts/build_browser_playground.py
```

后端全量 **exit 0 —— 3200 passed / 34 skipped / 2 deselected**，10 分 27 秒
（Session 07 的 3199 + 本轮新增的 1 条 = 3200，数字对得上）。
前端 **131 files / 1557 passed**，`build` / `i18n:check` / `lint` 三条 exit 0。

**Session 06 那条偶发红本轮又是绿的**
（`tests/native/test_run_cli_integration.py::test_ctrl_c_reaches_the_script_and_leaves_no_orphan`）。
**三次绿仍不构成"它被修好了"**：`tavotto run` 那条线本轮一个字节没改。
它仍留在 `STATUS.md` 的遗留表里。
**变异反证 33 条全部被打红**（第一轮活下来 5 条，四种成因与处置见 `TEST_MATRIX.md`——
其中一条查出来是**杀不死的冗余**，处置是删掉那句防御，不是造输入去覆盖它）。

### 这一轮踩到的坑

**1. 一条测试**捏了一个**后端给不出来的输入形状**（两个不同项目、同一个
fingerprint），于是红的不是缺陷、是幻觉。`project_id` 就在被哈希的那份 body
里，两个项目不可能撞指纹；换项目那条路又必然先 `clear()`。**处置是改测试，
不是给代码加一句 `project_id` 比较**——那句话没有任何用例能打红它，正是
T-36 说的「冗余的保证杀不死」。

**2. 三条变异第一轮活了下来**，没有一条是"判据写错了"，全都是**判据没被执行
到自己该看的那个点上**：一条的 fixture 让断言恒真（`mockResolvedValue(report())`
只求值一次，两次响应是同一个对象）；一条缺一维（没有任何用例选中过一张
`capability` 缺席的卡片）；一条是 fixture 里两个出处给了同一个值，于是屏蔽掉
第一个出处，第二个照样返回它。第三条是 T-36 的形状长在了 fixture 里。

**3. 一条判据的尺子看不见它要量的那一维**：「改绑候选里不含当前脚本」原本
打在 Radix Select 的触发器文本上，而选项住在弹层里、触发器上只有 placeholder
——无论实现怎么改它都恒真。处置是把算选项那段抽成纯函数
（`sourceOptions()`），判据直接打在它上面。

**4. `npx vitest` 直接跑会漏掉 `NODE_OPTIONS=--no-experimental-webstorage`**，
表现是 `localStorage` 是 `undefined`、报错看起来像被测代码坏了。这条在
STATUS.md 里记过一次，本轮又踩了一次——单跑文件时记得带上。

### 尚存限制

1. **runtime figure 素材（ADR 0013）在接入状态里一个字不说。** 它们不在
   `/api/panels` 的 id 空间里，拿不到 `capability`；四个出口都以「拿得到
   capability」为前提，所以自然沉默。runtime 卡片有它自己那套角标。
2. **「重新扫描」只有项目级一个入口**（对话框顶部）。Prompt 08 的原文把它
   列进了 `editable` 与 `source_missing` 两个状态的行内动作；`source_missing`
   那一行给了，`editable` 没给——18 行里每行都挂一个项目级动作是噪音。
3. **冲突那一行只给两个声称者，不给「从全部脚本里挑一个」的下拉。** 正确答案
   是第三个脚本时，出路在高级段的「全部脚本 → 手工填图名」。这么排是因为
   两个候选按钮就是绝大多数情况下的答案，再摆一个下拉会把"选哪个"这件事
   稀释掉。真遇到用户抱怨再加。
4. **接入中心没有虚拟滚动**：报告里有多少张图就渲染多少行（每行一个
   `<details>` + 若干按钮）。**本轮没有实测过大项目**——用例里最多 6 行，
   真实上限不知道。几百张图的项目要不要分页或虚拟化，等有人拿真项目量过
   再定。
5. **横幅关闭记录不随项目走**（存本机 `localStorage`，按项目 id 索引）。
   换一台电脑要重新关一次——刻意的：它是 UI 会话偏好，不该写进用户项目。
6. **axe 那一层本轮没有真跑过。** `e2e/a11y.spec.ts` 新增了两条（接入状态
   对话框的 axe + focus trap + Escape 归位；素材卡角标的 nested-interactive），
   `playwright test --list` 收得到它们，但 Playwright 要真实后端与浏览器，
   本机沙箱里起不来。单测只做了**结构性**断言（`role="option"` 内零 `<button>`、
   零可 Tab 控件、方向键导航不回归）——那不等于 axe 跑过。**23 之前必须真跑
   一次**，这一条记在 `STATUS.md` 的遗留表里。
7. 04/05/06/07 的其余遗留原样开着（R-05 五处手写原子写、R-07 autosave 位置、
   `/api/layouts/<name>` 无 schema 校验、项目打开仍走自己的静态草稿逻辑、
   「编辑历史」入口位置）。

### 工作树状态

- worktree：`/Volumes/Projects/Tavotto/.claude/worktrees/product-ux-v2`
- 分支：`feat/product-ux-reliability-v2`，从 `origin/main` 的 `ef9ac02` 开出
- **PR #201** 已开，带的是 Session 01–04。**05 / 06 / 07 / 08 的提交还没有推**
  ——用户定的节奏是「每个 Session 一个独立提交，攒够几个再一次推」，
  推上去会立刻触发一轮 Codex 评审，所以由用户决定什么时候推
- author 用 `88193520+erwanjun@users.noreply.github.com`（与 `main` 上每一个
  提交一致）。本机 `~/.gitconfig` 是 `1259959884@qq.com`，两者不一致会让
  cla-check 在同一个仓库里数出两个贡献者；提交时用
  `git -c user.email=… commit`，**别改共享的 `.git/config`**
  （linked worktree 默认共享它，一条命令污染所有会话）
- `web/node_modules` 已在 worktree 内真装

---

## 历史：Session 07（2026-08-29）

### 目标

把「这张图能不能进图内编辑」变成**一句可以直接显示的事实**，主语固定为
`/api/panels` 的那一个素材。

本阶段**只做后端事实模型与 API**——不做界面（08）、不增强解析器、不跑用户
脚本、不 probe。前端只加了类型与一个 fetch 函数，一个组件都没动。

### 为什么需要它（真实的起始状态，不是 prompt 假设的那个）

「这张图能不能编辑」在 07 之前由三处各答一次，而**三处的主语都不一样**：

| 出处 | 主语 | 它能回答的 |
| --- | --- | --- |
| `/api/panels` 给不给 `script` | **素材** | 注册表映射了没有 |
| `/api/registry` 的 `candidates` | **stem** | 静态扫描认领了没有 |
| `probe.script_inventory()` 的 `reason` | **脚本** | 这个 .py 处于什么状态 |

同一张图于是在素材面板里「不可编辑」、在注册表对话框里「有候选脚本」、在
脚本清单里「可试运行」——三句话都对，合起来却没有一句回答了用户的问题。
决策写在 `DECISIONS.md` 的 T-31。

### 实际完成

**1. 新模块 `src/tavotto/engine/readiness.py`（纯标准库，Flask 父进程 import）。**
六个互斥状态 + 稳定 reason code，判定表如下（分支从上往下，每张图只落一个）：

| # | 条件 | status | reason_code |
| ---: | --- | --- | --- |
| 1 | 注册表映射了这个 stem，脚本文件**在** | `editable` | `registered_source` |
| 2 | 注册表映射了，脚本文件**不在** | `source_missing` | `registered_script_missing` |
| 3 | 这一轮静态扫描**没跑成** | `layout_only` | `source_scan_unavailable` |
| 4 | **多个**脚本认领同一个 stem | `conflict` | `multiple_source_candidates` |
| 5 | **恰好一个**脚本认领 | `auto_linkable` | 见下 |
| 6 | 项目里有产图但输出名要跑才知道的脚本 | `needs_probe` | `runtime_output_unknown` |
| 7 | 其余 | `layout_only` | `no_source_candidate` |

第 5 行的 reason 说的是**卡在哪一步**，优先级从「刷多少次都没用」往「下一次
刷新就好了」排：

```text
registry_invalid  >  project_read_only  >  registry_write_failed  >  static_unique_candidate
```

**注册表优先于静态报告**（第 1 行在第 4 行之上，T-34）：注册表文件就是人工
裁决的落处（`src/tavotto/AGENTS.md`：「归属有歧义的 stem，裁决结果记在各图库
自己的注册表文件里，**勿改**」）。静态冲突照旧出现在项目级 `conflicts` 里，
带上 `resolved_by`。

**2. `GET /api/project/readiness`。** 下面这份是**真的跑出来的**（一个含
`sub/fig_a.py`→`FigA`、两个脚本抢 `Dup`、一个动态输出名脚本的项目；只有
`generated_at` 换成了固定值，其余逐字照抄，fingerprint 也是真的）：

```json
{
  "project_id": "3f9c1a2b7d04",
  "fingerprint": "70b70db1f41d093425be7c0349362c76",
  "generated_at": 1756468800.42,
  "summary": {
    "total": 3, "editable": 1, "auto_linkable": 0, "needs_probe": 1,
    "conflict": 1, "source_missing": 0, "layout_only": 0
  },
  "panels": [
    { "id": "Dup.pdf", "status": "conflict",
      "reason_code": "multiple_source_candidates", "script": null,
      "candidates": ["old_version.py", "z_newer.py"],
      "can_probe": true, "can_manual_link": true,
      "details": { "candidate_scope": "panel" } },
    { "id": "FigA.pdf", "status": "editable",
      "reason_code": "registered_source", "script": "sub/fig_a.py",
      "candidates": [], "can_probe": false, "can_manual_link": true,
      "details": { "entry": "main", "cost": "light" } },
    { "id": "Mystery.pdf", "status": "needs_probe",
      "reason_code": "runtime_output_unknown", "script": null,
      "candidates": ["dyn.py"], "can_probe": true, "can_manual_link": true,
      "details": { "candidate_scope": "project" } }
  ],
  "conflicts": [
    { "stem": "Dup", "candidates": ["old_version.py", "z_newer.py"],
      "resolved_by": null }
  ],
  "project": { "writable": true, "registry_valid": true,
               "scan_ok": true, "can_rescan": true },
  "issues": []
}
```

注意 `Dup.pdf` 那一条：`z_newer.py` 的名字更像"新版本"，`old_version.py` 的
名字更像"旧的"——**机器一个都不选**（`tests/…::test_two_scripts_claiming_one_stem_is_a_conflict_and_is_never_auto_resolved`
连 mtime 更新的那一个也不许赢）。

三个字段的取值是**三档不是两档**，08 不要把它们压扁：

* `conflicts`：`null` = 这一轮没跑静态扫描；`[]` = 扫过了、没有冲突；
* `project.registry_valid`：`null` = 项目里根本没有注册表文件（还没起草过）；
  `false` = 有、但读不回来；
* `details.candidate_scope`：`"panel"` = 这张图的候选；`"project"` = 项目里
  这几个脚本的产物静态解不出来，跑一个才知道是不是它。

**3. `/api/panels` 每项多一个 `capability`**（六个字段，`CAPABILITY_FIELDS`）。
它是**同一次 `compute()` 的投影**，`/api/panels` 不自己再判一遍——两处各算
一遍的话，「素材面板说可编辑、就绪度面板说要试运行」只是时间问题。

**老字段一个没动。** `script` 的语义仍然是「注册表声明了映射」，`editable`
时照旧有值；`auto_linkable` / `conflict` 有候选，但候选**不塞进 `script`**
（塞了的话旧前端会当场给它画上 ⚡）。`source_missing` 仍带 `script` ——那是
注册表里真实记着的那一条，不是伪造；要分辨「脚本还在」与「指着的文件没了」
就看 `capability.status`。

**4. fingerprint = 报告自身的内容哈希**（T-32）：规范化 JSON（**键排序**）
的 SHA-256 前 32 位，输入是 body 去掉 `generated_at` 与 `fingerprint`。
于是要求里那四条自动成立，不用逐条去防——时间戳不在 body 里所以进不来；
素材 / 脚本的 mtime 没有进报告所以变了它不动；绝对路径本来就一个都不在；
键序由 `sort_keys` 排掉。

**5. 项目级缓存**（挂在 `RefreshState.readiness`，随项目消亡）：两层，键都是
**输入的内容签名**——贵的那层是 `discover.discover()`（逐脚本 `ast.parse`），
外层是整份报告。**扫描失败的那一份不进缓存**（缓存一次失败等于让一次瞬时
错误把就绪度永久钉死）。**进出都深拷贝**（缓存里那份是唯一权威）。

**6. 三处很小的既有代码改动**（都在同一条链路上，不是顺手重构）：

* `discover.claims_of()` —— 从 `discover()` 里抽出的纯函数（「stem 被谁认领」
  的唯一判据）。`discover()` 的输出**一字未变**，它现在是这个函数的第一个
  消费者，就绪度是第二个；
* `RefreshState.registry_write_failed` —— 静态合并**写**注册表失败时置位、
  成功时清零。对外的 `scan_failed` code **没改**（老 `/api/registry/scan` 的
  契约），区分只留在状态里给就绪度用；
* `RefreshState.readiness` —— 缓存槽位。刷新在**确认事实真的动了之后**把它
  清成 `None`（`project_refresh` 不 import `readiness`，否则依赖成环）。
  这是签名之外的**第二道**判据：签名盖不住「同尺寸 + 同一个 mtime_ns 刻度里
  的就地改写」，而那正是刷新自己写注册表时的形状。

### 关键 API（Prompt 08 直接用）

```python
# src/tavotto/engine/readiness.py
compute(ctx) -> dict            # 报告本体（**不含** generated_at）；ctx 只要 path/id/registry
capability_map(ctx) -> dict     # 素材 id → capability 子集（/api/panels 用的就是它）
invalidate(ctx) -> None         # 丢掉缓存（用例与非刷新路径用）
fingerprint(body) -> str        # 报告 → 内容哈希
STATUSES, REASONS_BY_STATUS, CAPABILITY_FIELDS   # 枚举与判定表的机器可读版本
```

```ts
// web/src/lib/api.ts
fetchReadiness(): Promise<ReadinessReport>
PanelInfo.capability?: PanelCapability
type ReadinessStatus   // 六个状态的闭集
type ReadinessReason   // 十个 reason code 的闭集
```

### 迁移

**没有迁移，磁盘格式一个字节没动。** 就绪度不写盘。唯一的接口变化是
`/api/panels` 每项**多**了一个可选 `capability`——旧前端忽略未知字段。

### 修改的文件

```text
新增  src/tavotto/engine/readiness.py         事实模型（纯诊断，不执行、不写盘）
新增  tests/test_project_readiness.py         53 条
改动  src/tavotto/engine/discover.py          抽出 claims_of()（discover() 输出不变）
改动  src/tavotto/engine/project_refresh.py   +registry_write_failed、+readiness 缓存槽、
                                              _static_merge 记账、有差异才失效缓存
改动  src/tavotto/app.py                      +GET /api/project/readiness；
                                              scan_panels 挂 capability（同源投影）
改动  web/src/lib/api.ts                      +六状态/十 reason 的类型、+fetchReadiness、
                                              PanelInfo.capability
重建  codex-plugin/mcp/widget/canvas.html     改了 web/src 就要重建（指纹 47aee0ca4eee6e47）
```

### 测试命令与真实结果

```sh
# 后端全量（worktree 里必须带 PYTHONPATH，否则 import 到主工作区）
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest
# 只跑本阶段
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest tests/test_project_readiness.py
# 格式（与 CI 那一格逐字相同）
ruff check . && ruff format --check .
# 前端（先 cd web）
pnpm test && pnpm build && pnpm i18n:check && pnpm lint
# 改了 web/src 之后
python scripts/build_mcp_widget.py
```

后端全量 **exit 0 —— 3199 passed / 34 skipped / 2 deselected**，9 分 57 秒
（Session 06 的 3145 passed + 53 新增 + 1 = 3199，数字对得上）。
前端 **124 files / 1456 passed**，`build` / `i18n:check` / `lint` 三条 exit 0。
**变异反证 35 条全部被打红**（第一轮活下来 7 条，两种成因与处置见 `TEST_MATRIX.md`）。

**Session 06 那条红本轮两次全量都绿**
（`tests/native/test_run_cli_integration.py::test_ctrl_c_reaches_the_script_and_leaves_no_orphan`）。
本轮 `tavotto run` 那条线一个字节没改，所以两次绿**不构成"它被修好了"**——
它是偶发的，仍留在 `STATUS.md` 的遗留表里。

### 这一轮踩到的坑

**七条变异第一轮活了下来**，两种成因，都值得下一个 Session 记住：

1. **同一条保证有两个实现，谁也杀不死谁（2 条）。** 排序做了两遍
   （素材清单一次、报告 panels 一次），删掉任意一处都还有另一处兜着。
   这不是"多一层保险"，是**判据量不到自己**。处置：删掉冗余的那一处
   （T-36），顺序的契约只留一份。
2. **用例只跑了「方便的那个时刻」（5 条）。** 只读项目、非法注册表、内存
   注册表、深拷贝出口、结构校验——五条的形状完全一样：用例把状态**摆好之后
   才第一次读**，于是缓存里根本没有旧值可以过期，"缓存键含这一维"就量不到。
   处置：先热一遍缓存，再改条件，再读第二遍。

变异脚本带 `PYTHONDONTWRITEBYTECODE=1`，还原走**备份文件**而不是
`git checkout --`（工作树里有未提交的新文件）。

### 尚存限制

1. **就绪度只覆盖磁盘素材**（`/api/panels` 的 id 空间）。runtime figure 素材
   （ADR 0013，`runtime:` 前缀）不在报告里——它们按定义就有脚本，且 id 空间
   不同，混进来会破坏「id 与 `PanelInfo.id` 逐字相同」这条。
2. **`needs_probe` 的候选是项目级的**：静态解不出那些脚本的产物，所以说不出
   「这张图来自其中哪一个」。项目里有一个动态脚本，所有没有专属候选的图都会
   变成 `needs_probe`——`details.candidate_scope: "project"` 就是为了让 08 能
   如实措辞（「跑一个就知道了」，而不是「这张图来自其中之一」）。
3. **`/api/panels` 的 `capability` 可能缺席**：就绪度扫描与素材遍历之间新出现
   的素材这一轮没有它。`undefined` 的意思是「这一轮还不知道」，**不是**
   `layout_only`——08 不要给它补默认值。
4. **签名的分辨率与 watcher 同级**（`(size, mtime_ns)`）：「同尺寸 + 同一个
   mtime_ns 刻度里的就地改写」两边都发现不了。刻意不在就绪度这一侧单独收紧
   ——收紧一侧只会让两个模块对「变了没有」给出不同答案。刷新那一侧的显式
   失效是第二道判据。
5. **项目打开仍走自己的静态草稿逻辑**，没并进统一服务（为了不扫两遍）。
6. 04/05/06 的其余遗留（R-05 五处手写原子写、R-07 autosave 位置、
   `/api/layouts/<name>` 无 schema 校验、没有 SSE 事件名的同源门禁、
   「编辑历史」入口位置）原样开着。

### 工作树状态

- worktree：`/Volumes/Projects/Tavotto/.claude/worktrees/product-ux-v2`
- 分支：`feat/product-ux-reliability-v2`，从 `origin/main` 的 `ef9ac02` 开出
- **PR #201** 已开，带的是 Session 01–04。**05 / 06 / 07 的提交还没有推**
  ——用户定的节奏是「每个 Session 一个独立提交，攒够几个再一次推」，
  推上去会立刻触发一轮 Codex 评审，所以由用户决定什么时候推
- author 用 `88193520+erwanjun@users.noreply.github.com`（与 `main` 上每一个
  提交一致）。本机 `~/.gitconfig` 是 `1259959884@qq.com`，两者不一致会让
  cla-check 在同一个仓库里数出两个贡献者；提交时用
  `git -c user.email=… commit`，**别改共享的 `.git/config`**
  （linked worktree 默认共享它，一条命令污染所有会话）
- `web/node_modules` 已在 worktree 内真装

---

## 下一阶段入口（Prompt 08：Readiness 前端与常驻左栏）

**从这里开始读**：`src/tavotto/engine/readiness.py` 的模块文档（判定表与三档
取值都在里面）、`web/src/lib/api.ts` 的 `ReadinessStatus` / `ReadinessReason` /
`ReadinessReport`、`DECISIONS.md` 的 T-31~T-36。

**Session 07 留给它的**：一份**已经算好**的事实面。

| 东西 | 位置 | 08 可以直接依赖的性质 |
| --- | --- | --- |
| 每张图的能力 | `PanelInfo.capability`（`/api/panels` 每项都带） | 与整份就绪度**同一次计算**，不会互相矛盾 |
| 整份报告 | `GET /api/project/readiness` → `fetchReadiness()` | 带 summary、conflicts、项目级 issues |
| 「变了没有」 | `fingerprint` | 同一份事实下不变；`generated_at` 与无关文件的 mtime 都进不来 |
| 状态与文案的对应 | `REASONS_BY_STATUS`（后端）/ `ReadinessReason`（前端类型） | 闭集，且有用例钉住「不许冒出没备案的组合」 |
| 动作能力 | `can_probe` / `can_manual_link` / `can_rescan` | 只说"界面可以提供"，执行仍归既有端点 |

**绝不要做的事**：

1. **不许在前端重新猜状态。** 按 `script` 有没有值自己判一遍，就是把改造前
   那三个互相矛盾的答案又请回来一个。能力事实只有 `capability.status` /
   `reason_code` 一个出处。
2. **不许另起同义状态。** 六个就是六个；界面上要分得更细的话，回后端加
   reason code（并在 `REASONS_BY_STATUS` 里备案），不要在组件里再分一层。
3. **不许把三档压成两档**（`conflicts` 的 `null`、`registry_valid` 的 `null`、
   `capability` 的 `undefined`）。「没测量」不是「测量结果是零」，把它补成
   默认值，用户会一直等一个永远不来的提示。
4. **不许把 reason code 翻译成的句子存进文档或 history**（存 message key +
   结构化参数——`HistoryEntry.label` 的既有约定）。
5. **不许让就绪度界面去执行动作。** 试运行走 `/api/registry/probe`（用户显式
   触发、可取消、有进度），手工关联走 `PUT /api/registry`，重扫走
   `POST /api/project/refresh` → `refreshProjectNow()`（素材面板的刷新按钮
   已经在用这一条）。
6. **不许在 UI 上暴露实现术语**（stem / registry / AST / manifest）——这正是
   reason code 存在的理由：后端给枚举，前端给人话。

**必须保留的不变式**（改动前先确认还成立）：

1. `loadSeq` / `derivedSeq` 把「载入」「用户编辑」「派生同步」分成三档。
2. `dirty` 同时盯 `doc` 与 `canvases`；收到 409 后基线**故意不推进**。
3. 落盘一律走 `engine/atomicio`（ADR 0023）；保存状态只经 `setSaveState()` /
   `setDocNotice()` 改（ADR 0024）。
4. **刷新的编排只有 `refresh_project_index()` 一份**（ADR 0025）；**发现只有
   `project_watch` 一份**（ADR 0026）；**前端的消费只有 `liveSync` 一份**；
   **能力事实只有 `readiness` 一份**（本轮新增）。
5. **无差异 = 零事件、零写盘、零 worker 失效、零缓存失效**（后端）；
   **无差异 = 零 `set()`、零 dirty、零提示**（前端）。
6. 「哪些文件算素材」只有 `iter_assets()` 一处判据；脚本遍历只有
   `discover.iter_all_scripts()` / `iter_scripts()` 两个视图；「谁认领了这个
   stem」只有 `discover.claims_of()` 一处。
7. **就绪度不执行用户脚本、不 probe、不写盘、不改注册表、不发 SSE**
   （磁盘 CANARY + 桩两层证据钉着）。
8. **派生数据刷新不得把文档标脏（对用户而言），也不得进普通撤销历史。**
9. **素材不在清单里 ≠ 脚本关系失效**（T-28）。
10. `reason` 是闭集，表外的值归成 `manual`；客户端字符串不透传。
