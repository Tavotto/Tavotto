# 统一检查与问题定位（2026-08-31，ADR 0030）

> 原文出自 `web/AGENTS.md`「统一检查与问题定位（2026-08-31，ADR 0030）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

完整版在 `docs/adr/0030-validation-and-problem-navigation.md`，改动前先读。
**「这份项目有什么问题」只有一条链**：

```text
preflight.runSpec()      规则求值（两份求值器，golden vectors 对齐）
  → lib/validation.ts    接成可定位问题：画布维度、逐条命中、指纹、fixKind
  → store/validationStore.ts  编排：防抖 250ms + 代次、按画布增量、失败不清空
  → components/left/ProblemPanel.tsx  左侧「问题」抽屉（常驻入口 + 角标）
```

* **导出对话框不再跑第二遍求值器**：它消费 `getValidationSummary(scope, extra)`
  与 `rawIssuesFor(canvasId)`（样式检查报告要的聚合投影，**同一次求值的另一份
  投影**）。摘要的组装只有 `lib/validation.summaryFor()` 一份——**按导出目标
  取范围**（`objectId`：按原图导出只算那张图，页面级问题不算；按画布算整张
  画布），报告那份用 `rawIssuesForObject()` 裁同一刀（审计 T33）。**裁完
  `message` / `detail` 要按留下来的那些命中重挑一次**（尺子与 `Sink` 完全一样：
  带排名的取最糟那次，不带排名的第一次说了算）——它们原本属于**全画布**最糟
  那一次，而 `buildProofPayload()` 序列化的正是这两个字段（不是 occurrences）；
  不重挑的话，按原图导出的样式检查报告会把别的面板的测量值记到选中的那张图
  头上（目标自己 7 pt，报告里写成 4 pt）。
  它**不列第二套清单**（ADR 0031 §四），只有一个例外：**阻断项逐条列出**
  （最多 5 条，无筛选无修复，每条一个「定位」入口，紧挨着知情确认框——用户
  在点头之前得看见自己在为什么点头）；其余等级只给数量 + 「查看问题」，
  完整清单、筛选与修复都在左侧问题面板。「定位」与「查看问题」关掉对话框时
  把用户填的东西留在组件里（`parked`），再点导出原样回来。
* **主对话框栈**（`uiStore.dialogStack`，审计 T35）：导出 → 设置 → 论文样式
  是一条前进 / 返回的小流程。任何时刻只显示栈顶；下面的用 `Dialog` 的
  `covered` 藏起来**但不卸载**（状态、滚动、触发按钮都在，Radix 把焦点还给
  那颗按钮）。从导出深链进设置**不先关导出**；设置「应用到当前图」带着选中的
  样式 id 打开样式对话框（`stylesPresetId`），也不关设置。
* **`ready` / `failed` 不许压扁成「没问题」**：`total === 0` 单独看不足以说
  「检查通过」。打开导出对话框时**当场同步跑一遍**，就是为了不让那 250ms 防抖
  窗口里说出一句假话。
* **逐条命中**（`PreflightOccurrence`）是 TS 侧的展开层，**不进跨语言合同**：
  golden vectors 比的仍是聚合投影。看护用例盯着两者一致（命中的 objectId /
  gid 并起来必须与聚合项逐字相等）。每条命中带着自己那次的 `worse`（量化排名，
  没法比大小的规则不带）——上一条的「裁完重挑」靠它，两处不许各排一套序。
  命中对象**只在 `Sink.record` 里造一次**（新增与顶掉旧条目共用同一个字面量）：
  各写一份的话，下一个新增的字段只会被加进其中一条。
* **定位只有 `lib/issueFocus.focusObject()` 一处**：切画布 → 切工作流模式 →
  选中 → 视口 → 高亮 → Inspector → 属性字段，失败回**闭集原因**
  （`canvas_missing` / `object_deleted` / `not_editable` / `document_not_loaded`），
  绝不静默不动。属性字段的落点是 `data-prop`（稳定机器标识），**不是
  aria-label**——那是本地化文案，换语言就选不中。
* **普通界面不出现 gid / 对象 id**：措辞唯一实现 `lib/validationText.ts`，
  主语取 manifest 的 `label`（过 `engineLabel()`），精确名词只在每行收起的
  「技术详情」里。
* **`safe_auto` 的三条判据**：目标值唯一、**修完真的能过**（绝对下限不含等号，
  所以"提到正好 8 pt"不算修好）、不动科研数据（色图 / 裁剪 / 重排一律不自动）。
  落地经 `store/issueFixActions.ts` → `documentStore.commit`，一个修复一个事务、
  一批一个批事务；**批量只在当前画布**（撤销栈按画布换入换出）。
* **修复按「修的是谁」分两路，一类对象只有一个计划器（ADR 0080）**：
  **面板内部**（`lib/issueFix.fixRoute() === 'engine'`）的计划、真实渲染与裁决全在
  后端 `/api/engine/specfix`（`engine/specfix.py`）——前端**不算面板的计划**，只把
  此刻的全量列表 + `panelScale()` + 这份规范 + 点名的 `(规则, gid)` 发过去，后端
  回 `ok: true` 才把它回的**整张列表**一次 commit；不通过 / 出错 / 等待期间文档
  被改过（override 列表或 `loadSeq` 对不上）一律**文档零改动**，原因走闭集
  `FixFailureReason`。**画布层**（标注字号、页宽）仍在 `lib/issueFix.planFix()`。
  可修规则集 `ENGINE_FIX_RULES` ↔ `specfix.FIXABLE_RULES` 是严格同源对。
  「全部处理」的集合唯一出处 `batchable()`（**不含建议档**；组头的「全部修复」
  是点名那一组，带 `includeSuggestions`）；计数与执行是同一个集合。修复在跑时
  `uiStore.fixing` 把所有修复入口置灰（同一时刻只跑一轮，第二轮回 `busy`）。
* **就绪度不混进问题清单**：面板底部只放一条通往接入状态的链接。
* **面板的呈现层在 `lib/problemList.ts`（2026-09-06，审计 T09）**，纯函数，
  不跑第二遍求值器：① 范围「当前图 / 整个文档」——当前图 = 快速编辑的
  `activePanelId` → 图内编辑的 `elementPanelId` → 选中的面板，`uiStore.problemScope`
  为 `null` 时有当前图就看它；抽屉标题的计数与面板同一个范围（`useProblemScope`），
  **轨道角标仍是全文档数**（它是入口）。② 按 ruleCode 聚合，组头说标题 + 等级 +
  受影响对象数，行里只说「谁、现在多少、要多少」；叶子行仍带
  `data-issue-row[data-issue-rule][data-issue-object]`。③ 逐项游标
  `uiStore.problemCursor`：定位后清单**留在原地**（`enterElementEdit(id, { leftTab:
  'keep' })`，元素树不顶掉左栏），当前行 `aria-current` + 左侧竖条 + 「当前」，
  底部上一项 / 下一项；那条修好消失后「下一项」指向**顶上来的那条**，不跳回开头。
* 看护：`lib/validation.test.ts` / `lib/validationText.test.ts` /
  `lib/issueFocus.test.ts` / `lib/issueFix.test.ts` / `lib/problemList.test.ts` /
  `store/validationStore.test.ts` / `components/left/problemPanel.test.tsx`；
  Python 侧 `tests/test_preflight.py` 的跨语言同源一条。
