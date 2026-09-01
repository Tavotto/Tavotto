# ADR 0030：统一检查服务与真实问题定位 —— 一条问题必须指得出对象、字段和去那儿的路

状态：**Accepted**
日期：2026-08-31
相关：[0029 Style / Spec / Export 三层](0029-style-spec-profiles.md)（阈值只从这一层来）、
[0028 原图输出规格](0028-original-output-spec.md)（同一条纪律：一句面向用户的话只能有一个定义）、
[0027 面板接入就绪度](0027-panel-readiness-fact-model.md)（**另一类事实**，刻意不混进问题清单）、
[0017 显示回退 ≠ 几何权威](0017-display-fallback-vs-geometry-authority.md)（定位只读状态，不写文档）、
[0006 Codex MCP 与出版规范](0006-codex-mcp-app-and-publication-profile.md)（`eff <= floor` 那条边的出处），
本轨道文档 [`docs/implementation/product-ux-reliability/`](../implementation/product-ux-reliability/STATUS.md)。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 谁回答「这份项目有什么问题」 | **一条链，四个模块**：`preflight`（求值）→ `lib/validation.ts`（接成可定位问题）→ `store/validationStore.ts`（编排）→ 界面。导出面板**只消费摘要**，不跑第二遍求值器 |
| 问题的身份 | `issueId` = 指纹 = 规则 + 画布 + 对象 + 元素 + 属性。**不含当前值**——值变了仍是同一条问题 |
| 缺的那一维 | `ObjectRef` 补上 `documentId` / `canvasId`（R-12）。多画布项目里「另一张画布上的那个对象」从此说得出、跳得到 |
| 聚合项 vs 逐条命中 | 求值器的聚合项回答「过没过」，逐条命中（`PreflightOccurrence`）回答「谁没过」。**面板列的是后者**——聚合项的 `detail` 属于最糟的那一次，拿它描述别的对象会说出假数字 |
| 跨语言合同 | 仍然是**聚合投影**（golden vectors 逐条比）。逐条命中是 TS 侧的展开层，不进合同；看护用例盯着两者一致 |
| 三类规则 | Document/Object（实时可见）、Export-context（选了格式与 PPI 才判）、Readiness（**不进这个清单**，面板底部给一条链接） |
| 「还没查」 | **独立一档**。`total === 0` 单独看不足以说「检查通过」；`ready` / `failed` 与计数一起进摘要 |
| 检查失败 | **不清空**上一次的结果，另说一句「这一次没查成」；导出对话框据此要求用户显式确认 |
| 定位 | 跨模块**只有 `lib/issueFocus.focusObject()`**：切画布 → 切工作流模式 → 选中 → 视口 → 高亮 → Inspector → 属性字段；失败回闭集原因，绝不静默不动 |
| 属性字段的落点 | `data-prop`（稳定机器标识）。**不是 aria-label**——那是本地化文案 |
| 界面上的身份 | 普通界面**不出现 gid / 对象 id / 文件路径**；主语取 manifest 的 `label`（过 `engineLabel()`），精确名词只在每行收起的「技术详情」里 |
| `safe_auto` 的门槛 | 目标值唯一、**修完真的能过**、不动科研数据。算不出计划就降回 `none`——按了没反应的按钮比没有按钮更坏 |
| 修复的落地 | 经 `documentStore.commit`：一个修复一个事务、一批一个批事务、⌘Z 一次撤回、dirty / autosave 照常。**批量只在当前画布** |
| 检查本身 | **不改文档**、不发后端、不执行用户脚本 |

---

## 1. 背景：问题看得见，但去不了

改造前，「这张图有没有问题」只有一条路能问：**打开导出对话框**。那一屏的
`PreflightBlock` 自己调 `runPreflight()`，把结果折成一行摘要，展开之后每行
末尾挂着 `axes_0.lines_1` 这样的内部标识，点一下调 `revealObjects(ids)`。

四个具体的洞：

1. **入口只有一个，而且在流程末端。** 用户排完版、点了导出，才第一次听说
   字号偏小。此时改动成本最高。
2. **`PreflightIssue` 没有画布维度**（风险登记表的 R-12）。多画布项目里第二
   张画布上的问题**根本不会被列出来**（对话框只查激活画布），列出来了也跳
   不过去——`revealObjects` 只在当前画布里找 id。
3. **聚合项没法定位到"是谁"。** 一条 `font-too-small` 底下挂着三个 gid，
   文案说的是最糟那个的数字。点「定位」会把三个对象一起选中，而属性页显示
   的是多选摘要——用户仍然不知道该改哪个。
4. **导出对话框里有第二套判据。** 「导出 DPI」那一格写着 `bad={dpi < minDpi}`
   ——一个直接写在组件里的比较。规范里的 `raster-dpi` 与它各判各的，
   而 MCP 那条入口（`bridge.export_raster_issues()`）判的又是第三份。

## 2. 裁决

### 2.1 求值与导航分开

`lib/preflight.ts` 保持原样：它是**规则求值器**，与 `engine/preflight.py`
靠 `tests/golden/preflight_vectors.json` 对齐，回答「过没过」。

新增 `lib/validation.ts` 是**导航层**，回答「谁没过、点一下去哪」：

```ts
ValidationIssue {
  issueId        // = fingerprint
  ruleCode       // 稳定；文案可翻译
  severity
  context        // document | export
  objectRef      // { documentId, canvasId, objectId, gid }
  subject        // 界面拿它说人话（kind / elementLabel / elementRole / objectName）
  propertyPath   // 'fontsize' / 'sizePt' / 'page.w' / 'export.dpi'
  message        // 描述符，不是翻好的字符串
  technicalDetails
  fixKind        // none | safe_auto | user_choice
}
```

分开的理由是**两者的变更节奏完全不同**：规则要跟规范走、要跨语言对齐；
导航要跟界面走、要认得工作流模式与 Inspector 的结构。合在一起的话，
任何一次界面改动都要动跨语言合同。

### 2.2 逐条命中：一行一个真实对象

求值器的 `Sink` 会把同一条规则的多次命中聚合成一项。这在「过没过」这个
问题上是对的，在「谁没过」上是错的：

```text
聚合项  font-below-absolute-floor  gids=[xticks, yticks, title]  detail={effective_pt: 6}
```

这条项的文案说 6.00pt，而 `yticks` 是 7、`title` 是 7.5。把它摊成三行显示，
其中两行的数字就是假的——而**判据修对了不等于它说的话对**，一旦判据可信，
人更会相信那句错的。

所以 `Sink` 额外记一份**逐条命中**（`PreflightOccurrence`：objectId / gid /
prop / 它自己那次的 message 与 detail），去重的尺子与聚合项完全一样（带
`worse` 的取最糟那次，不带的第一次说了算）。

**它不进跨语言合同**：golden vectors 比的仍是聚合投影，Python 侧一个字没改
（那边的消费方是 MCP 的聚合清单）。看护用例盯着两者一致——命中的 objectId /
gid 并起来必须与聚合项逐字相等，任何一侧漏掉都会当场红。

### 2.3 指纹不含当前值

```text
fingerprint = ruleCode | canvasId | objectId | gid | propertyPath
```

**刻意不含当前值。** 7.5pt 改成 7.6pt 仍然是同一条问题；指纹跟着值走的话，
每敲一个数字整行都会被 React 当成新节点重建——焦点掉、动效重播、展开状态丢。

### 2.4 三类规则不混在一起

| 类 | 什么时候能判 | 谁产生 |
| --- | --- | --- |
| Document / Object | 编辑时实时可见 | `preflight.runSpec()` |
| Export context | 选了格式与 PPI 之后 | `validation.exportContextRaw()` |
| Readiness | 项目接入事实 | `engine/readiness.py`（ADR 0027）**不进清单** |

导出上下文那条与 MCP 那条是**同一条规则**：同一个 rule code（`raster-dpi`）、
同一个 message key（`exportRasterDpi`）、同一张 severity 表。另起一个 code
的话，期刊覆盖里把 `raster-dpi` 调成 warn 对其中一条路就不生效——同一份规范
在两条入口上说不同的话。登记成严格同源对，看护
`test_the_export_context_rule_is_one_rule_on_both_sides`。

主语是**这次导出请求**（`objectId` / `gid` 都是 null），所以它与面板那条
`raster-dpi`（主语是某张位图素材）指纹不同、不会互相顶掉，也不会重复报。

**Readiness 刻意不产生 issue。**「这张图还没连上脚本」与「这张图字号偏小」
的下一步完全不同：前者要去接入状态里连接源脚本，后者要改一个属性。混在同一
个清单里，用户既分不清轻重，也找不到各自的出口。面板底部只放一条链接。

### 2.5 「还没查」是独立一档

`summarizeIssues(issues, { ready, failed })` **不给默认值**。默认成
`ready: true` 的话，"还没查"会静默变成"查过了、没问题"——那是这套服务能犯的
最坏的错，因为它长得和好消息一模一样。

配套的两个决定：

* 导出对话框打开时**当场同步跑一遍**（纯计算，没有请求），于是那 250ms 防抖
  窗口里不会说出一句假话；
* 检查失败时**保留上一次的结果**（它当时是真话），另说一句「这一次没查成」，
  并把它算进「需要用户点头」的条件里。

### 2.6 定位只有一处

```text
issue → 画布 → 工作流模式 → 对象 → 视口 → 选中 → Inspector → 属性字段
```

`focusObject(ref, propertyPath)` 是**跨模块唯一的一个**（问题面板、导出摘要、
将来的就绪度与搜索都调它）。分支只有两条：

* `gid` 非空 = 目标在图**里面** → 进快速编辑（ADR 0028 的那一屏就是为看一张图
  设计的）+ 图内元素编辑 + 选中那个 gid；
* 否则 = 画布对象或页面 → 回排版模式（越界、重叠、页边距只在版面上说得清）。

**失败必须有反馈**，而且是闭集原因，不是一个 `false`：`canvas_missing` /
`object_deleted` / `not_editable` / `document_not_loaded` 各自对应不同的下一步。

属性字段的落点是 `data-prop`，**不是 aria-label**——后者是本地化文案，
换个界面语言就选不中（`lib/focusRescue.ts` 踩过同一个坑）。

定位**一个字都不写文档**：视口、选中、模式、面板开合全是会话状态。

### 2.7 `safe_auto` 的三条门槛

1. **目标值算得出来且唯一。** 要用户在两个同样合理的答案里挑的，是
   `user_choice`（目前只有页宽：单栏还是双栏是用户的排版决定）。
2. **修完真的能过。** 绝对下限那条判据是 `eff <= floor`，**不含等号**——
   所以"把 7.5 提到规范的 8 pt"根本过不了。目标值取**大于下限的最小 0.5 档**，
   并且换算回脚本坐标系（面板缩到 60% 时 `eff = size × scale`，直接写 8.5
   的话读者量到 5.1pt，修复反而制造了一条新的违规）。
3. **不动科研数据。** 字体替换（装没装都不知道）、色图替换（改的是数据语义）、
   裁剪、重排一律不自动做。

目录声明规则的**意图**，`planFix()` 用当前值与当前规范算**这一条**能不能修，
算不出来降回 `none`。两层都要有：只有目录会给出按了没反应的按钮，只有
planFix 则每次渲染都要为每条问题算一遍计划。

落地经 `documentStore.commit`，与用户手改走同一条链路。**批量只在当前画布**：
撤销栈是按画布换入换出的（`switchCanvas`），跨画布的"一个批事务"在这套模型里
不存在，硬拼出来的结果是「撤销要按三次，而且顺序不定」。

### 2.8 界面上不出现内部标识

措辞的唯一实现是 `lib/validationText.ts`（与 `readinessText.ts`、
`profileText.ts` 同一条纪律）。主语取 manifest 里引擎给的 `label`
（「X 轴标题」「图例」），过 `engineLabel()` 换成界面语言；取不到退到角色名，
再取不到才说面板名。**任何一档都不吐 gid。**

gid、对象 id、属性名、量化字段只出现在每行**默认收起**的「技术详情」里。

## 3. 代价与已知限制

1. **逐条命中让 TS 侧的求值器比 Python 侧多一层。** 这是有意的不对称：
   Python 侧的消费方（MCP）要的就是聚合清单。代价是新增规则时要记得
   `sink.add(..., { prop })`——不带 `prop` 只是少了字段定位，不会出错。
2. **不渲染的面板会报「无法核验」。** 渲染只对激活画布上"编辑中 / 有 override /
   脚本领先磁盘"的面板发起（`renderTargets`），所以多画布项目里
   `panel-text-not-verifiable` 会成批出现。它是 `not_verifiable` 这一档、
   有自己的分组，**而且是真话**——但数量上是噪音。改法要么是按需渲染，
   要么是把这一档折叠成每画布一条，留给后面的阶段。
3. **批量修复不跨画布**（见 2.7）。
4. **`user_choice` 目前只有一条规则**（页宽）。这一档不是为它而设——它是
   `applyIssueFix(id, choice?)` 这个签名成立的前提；只有 `none` / `safe_auto`
   两档的话，那个参数就是个永远为 undefined 的摆设。
5. **MCP 内嵌画布保留自己的等级图标表。** 它消费的是 MCP 的聚合载荷
   （`PreflightIssuePayload`），不是 `ValidationIssue`，而且是另一个 bundle
   （`vite.mcp.config.ts` → `canvas.html`，尺寸敏感）。图标一致的看护覆盖
   应用内的两处（问题面板 + 导出摘要）。

## 4. 看护

| 判据 | 用例 |
| --- | --- |
| 规则目录不漏 code | `lib/validation.test.ts`「golden vectors 里出现过的每个 rule code 都在目录里登记了」 |
| 聚合投影不被改写 | 同上「聚合投影原样留着」+「聚合项与逐条命中说的是同一批对象」 |
| 逐条命中说自己的数字 | 同上「每一行带的是**它自己**的当前值」 |
| 画布维度 | 同上「每条问题都说得出自己在哪张画布上」 |
| 指纹稳定且五维参与 | 同上「值变了指纹不变」/「五维各自参与」 |
| 检查不改文档 | 同上「冻起来的文档照样查得动」+ `store/validationStore.test.ts`「跑完之后文档、dirty、撤销栈一个字节没动」 |
| 防抖 / 代次 / 增量 / 失败不清空 | `store/validationStore.test.ts` |
| 定位的八步与四种失败 | `lib/issueFocus.test.ts` |
| 修完真的能过 / 事务 / 跨画布 | `lib/issueFix.test.ts` |
| 界面不出现内部标识 / 空态 / 筛选 / 键盘 / 角标 | `components/left/problemPanel.test.tsx` |
| 措辞与双语 | `lib/validationText.test.ts` |
| 导出上下文跨语言同源 | `tests/test_preflight.py::test_the_export_context_rule_is_one_rule_on_both_sides` |
| 求值器 / 界面不写字号字面量 | `tests/test_profile_store.py::test_no_evaluator_or_ui_hardcodes_a_minimum_font_size`（本轮新增四个消费点） |
