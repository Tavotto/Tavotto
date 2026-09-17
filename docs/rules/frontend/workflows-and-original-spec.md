# 两条工作流与原图规格（2026-08-29，Prompt 09；ADR 0028）

> 原文出自 `web/AGENTS.md`「两条工作流与原图规格（2026-08-29，Prompt 09；ADR 0028）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

```text
快速编辑：打开一张图 → 修改 → 按原图规格导出
画布排版：加入多张图 → 排列 → 按画布规格导出
```

**一个文档，不是两套应用。** 没有第二个 documentStore、第二个 override
writer、第二份对象模型：一张图在文档里只有**一个**面板对象，两种模式看的是
同一个对象。快速编辑 = 把它单独摆出来（页面纸 / 网格 / 参考线 / 别的对象
全部让开），画布排版 = 它在页面上的落位。

- **模式是工作区状态**：`store/workspace.ts` 的 `mode` / `activePanelId`，
  不进文档、不进撤销、不置 dirty，按 documentId 存本机一档
  （`tavotto.workspace.<id>`，与 `tavotto.tabs.<id>` 同一条纪律）。
  不变式：`mode === 'fast_edit'` ⟺ `activePanelId !== null`。
  **`activePanelId` 是对象 id 不是素材 id**——同一张素材可以有两个实例，
  用素材 id 的话工作区会在两个实例之间随机跳。
- **快速编辑一个字都不写 x/y/w/h**。「从画布进图内编辑再返回，布局不变」
  不是靠"回来时恢复一下"，而是靠**根本没动过**——恢复式的实现总有一条路径
  会漏掉（旋转、成组、布局组重排），而漏掉的表现是用户的版被悄悄改了。
- **视口是另一回事，它必须被还原**（2026-09-06，审计 T01）。进快速编辑一定要
  动视口（那一屏按图自己的图幅框住），所以 `enterFastEdit` 记下当时的排版视口
  （**记在 store 的 action 里**——问题面板的定位也走它，不是只有 `openFastEdit`），
  `returnToLayout` 还原。记录带**画布 id**：`openFastEdit` 会为了找图切画布，
  换了画布之后那一片属于上一张画布。`returnToLayout` 在本来就是排版态时
  **一个字都不动视口**（onboarding 的前置动作会那样调）。看护：
  `store/workspace.test.ts` 的「切模式不动用户的视口」六条 + `e2e/nav-audit.spec.ts`
  （量之前先把视口挪开——不挪的话判据恒等成立，实测放走过变异）。
- **四个稳定动作是唯一出口**（11 定位 / 12 导出 / 18 QuickEdit / 21 onboarding
  复用它们，别在界面里重新拼一遍"找对象 / 没有就添加 / 切画布 / 选中"）：
  `openFastEdit(figureId)` / `addFigureToLayout(figureId)` / `returnToLayout()`
  / `focusLayoutPanel(panelId)`。「文档里有没有这张图」的判据也只有一处：
  `findFigurePanel()`。
- **「添加到画布」不复制对象**：已经在文档里就只是聚焦它（`focused`），
  重复点不会叠出第二个面板，overrides 一直在同一个对象 id 上。
- **能不能进图内编辑用既有判据**（`panel.script`，与 `ObjectView` 双击、
  `enterElementEdit` 同一个）；**说什么话**归 `lib/readinessText.ts`。
  两者不是一件事，别在 workspace 里另起一份状态判断。
- **对象消失就退出快速编辑**，判据复用 `usePruneSelection` 里那个 `usable`
  （删除 / 隐藏 / 切画布同一条）。第二份判据迟早与图内编辑态分叉。
- **原图规格只有 `lib/originalSpec.ts` 一份服务**（ADR 0028）。优先级
  ① 渲染回来的 manifest `size_mm` → ② 文档里的 `nativeW/nativeH` →
  ③ `/api/panels` 的 `original_spec` → ④ 明确 fallback（必带 `fallback: true`）。
  ① 在 ② 之前正是因为**图幅不是派生字段**（见 `docs/rules/frontend/project-document-and-autosave.md` 的派生字段表）。
  画布上的缩放 / 裁剪 / 旋转 / 翻转 / 透明度只进 `spec.ignored`，**绝不套进
  原图导出**——跟着缩的话字号会一起缩。`getOriginalOutputSpec()` 对不认识的
  id 回 `null`，不发明一张不存在的图。
- 看护：`store/workspace.test.ts`、`lib/originalSpec.test.ts`、
  `canvas/fastEditStage.test.tsx`；后端事实层 `tests/test_original_spec.py`。
