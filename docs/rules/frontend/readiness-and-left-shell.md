# 接入状态与左侧外壳（2026-08-29，Prompt 08）

> 原文出自 `web/AGENTS.md`「接入状态与左侧外壳（2026-08-29，Prompt 08）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

「这张图能不能编辑」的**事实**只有后端 `engine/readiness.py` 一个出处
（六个 status + 十个 reason code 的闭集，ADR 0027）。前端只翻译不判断——
界面里**一个 `!!script` 的状态分支都没有**。

- **句子与「待连接」只有一份实现**：`lib/readinessText.ts` 的 `statusLabel()`
  （读 `status`）、`reasonText()`（读 **`reason_code`**，不读 `status`），
  以及 `PENDING_STATUSES` / `pendingCount(summary)` / `allEditable(summary)`
  ——横幅与接入中心顶部说的是同一个数，各展开写一遍的话，多一个状态时总有
  一处会漏掉。`allEditable` 那一档两处表现不同但**判据同一个**：横幅整条不说话
  （`bannerReport` 回 null），接入中心把四个计数换成一句「N 张图都可以编辑」
  （审计 T10：正常项目不该长得像故障排查页）。这一档里**可编辑的图不再逐张
  重复同一句解释**（那句话对每一张一模一样），第一层也只留「添加到画布」——
  「重新试运行」是排障动作，与改绑一起收在技术详情里。四个出口共用它：
  素材卡角标、素材说明条、接入中心每一行、属性栏那条提示。按状态查句子会让
  只读项目里的用户一直等一个永远不来的结果（`auto_linkable` 有四个 code，
  一个是"马上就好"、三个是"不做点什么永远不会好"）。
- **持有者只有 `store/projectReadinessStore.ts`**：并发纪律与 `assetStore`
  逐条相同（请求序号挡旧响应、发请求那一刻的 pj 挡串项目、同批合并、
  `force` 另起一次、失败保留上一次成功那份）；**fingerprint 没变时连报告
  对象的引用都不换**。刷新挂在 `liveSync.refreshAssetsAndSync()` 一处，
  与素材清单同一批事件、同一个 `force` 语义。
- **开关只有 `uiStore.registryOpen`**（`RegistryDialog` 的文件名与导出名保留）。
  就绪度 store 只管 `focusId`；`focusPanel(fileId)` 是 17/18 复用的入口。
  关闭后的焦点归位归 `ui/Dialog`，**别再记第二份**。
- **「没测量」三档不许压扁**：`conflicts` 的 `null`、`project.registry_valid`
  的 `null`、`PanelInfo.capability` 的 `undefined`。第三档的界面表现是
  **什么都不显示**——补成 `layout_only` 就是替后端撒谎。
- **界面不执行动作**：试运行走 `/api/registry/probe`（只由用户点出来，点之前
  先说「Tavotto 将运行这个脚本」）、手工关联走 `PUT /api/registry`（键是
  **`ReadinessPanel.stem`**，不是文件名）、重扫走 `/api/registry/scan`；
  每次成功之后只调一次统一刷新，不手拼状态。冲突**一个候选都不预选**。
- **`role="option"` 里不许再嵌可 Tab 的控件**：状态角标是 `<span>`，
  「查看接入状态」那个真按钮住在 listbox 外面的说明条里。
- **侧栏的「偏好」与「此刻开着没开」是两件事**（`uiStore` 的模块级
  `prefOpen`）：互斥断点的自动让位、窄屏开机的裁剪只改后者，**绝不写回
  本机偏好**。写反了的表现是"把窗口拖窄一次，常驻左栏就再也回不来了"，
  而用户从没关过它。判据只求值一次（`autoShowProperties` 的 `assetsYield`），
  写状态与写偏好共用它。
- 看护：`store/projectReadinessStore.test.ts`、`components/RegistryDialog.test.tsx`、
  `components/ProjectReadinessBanner.test.tsx`、
  `components/left/AssetBrowser.readiness.test.tsx`、
  `canvas/panelReadinessEntry.test.tsx`、`components/inspector/panelCapabilityNote.test.tsx`、
  `canvas/drawerViewportResize.test.tsx`、`store/uiStore.test.ts` 的两个左栏
  describe；e2e `a11y.spec.ts` 的接入状态两条 + `golden-paths.spec.ts`。
