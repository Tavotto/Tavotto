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
- **首开的那一次确认（U03，ADR 0057 §三）**：渲染以 `workdir_confirmation_required` 回来时
  它不是错误块，是缺一个决定——`renderStore` 把 `EngineError.confirmation` 交给
  `envStore.requestWorkdirConfirmation`，`WorkdirConfirmDialog` 渲染三档（项目根 / 脚本目录 /
  继续沙盒）与各档找得到的文件；**推荐项只在后端 `recommended` 有值时预选，歧义时不预选**
  （机器不裁决，界面只翻译）；「运行」= `setWorkdirMode(mode, { confirmed: true })`（一次 PATCH，
  不再弹第二层确认框，成功后 `retryEnvironmentFailures` 把这批面板重排）；「稍后」只关框，载荷
  留在 `PanelRender.confirmation`，错误块的 `WorkdirChooseButton` 能再打开；同一时刻只开一份；
  换项目 `resetProject` 清掉。设置里 `WorkdirRow` 是同一份决定的三档 `Segmented`（切到两个真实目录
  各自确认一次，切回沙盒不确认；老服务端只报两档时第三档不摆）。**三档与选项的文案键写成字面量**
  （`OPTION_LABEL` / `MODE_LABEL` 表），模板拼出来的键死键门禁看不见。MCP 那一面是同一份决定：
  `tavotto_open_figure(workdir=…)`。
- **跑前的那一次授权（U04，ADR 0061 §六）**：渲染以 `dependency_preparation_required` 回来时同样不是
  错误块，是缺一次授权——`renderStore` 把 `EngineError.dependencyPreparation`（整份联合计划 + 可选目标）
  交给 `depRepairStore.requestPreparation`（动态 import，避免 store 环），`DependencyPrepareDialog` 列出
  要装的包（项目声明的完整形态，不翻译）、认不出的 import、只作约束的条数与两档目标（Tavotto 隔离环境
  默认；项目 venv 只在它就是此刻选中的解释器时出现，文案说清会改用户环境）；「准备并继续」= `prepare(target)`
  先绑定计划（`POST /api/engine/dependencies/plan`）再只发 `plan_id`（`/prepare`），进度经同一条 SSE
  `engine.dependency`（`flow: 'joint'`）按 state 换文案、装完 `retryEnvironmentFailures` 重排并关框——
  **只认自己发起的那条**（`depRepairStore.onProgress` 按 `plan_id` 与本地的 `plan` / `jointPlan` / `progress` 比对：
  这条 SSE 不带项目判别、广播给每个订阅者，别的标签页 / 项目的计划装完不能收掉这里的框、不能把这里的渲染重排，
  Codex #470 P2）；
  blocked 的计划把 `joint.blocked` 的理由摆出来、不装；「不准备，直接运行」= `POST /api/engine/dependencies/skip`
  （这道门一直问到有答案——授权或明确跳过），载荷留在 `PanelRender.dependencyPreparation`，错误块的
  `DependencyPrepareButton` 能再打开；同一时刻只开一份；换了项目的旧载荷不弹。**目标与状态的文案键写成
  字面量**（`TARGET_LABEL` / `STATE_TEXT` / `BLOCKED_TEXT` 表）。MCP 那一面是同一份决定：
  `tavotto_open_figure(prepare_dependencies=…)`。
  **用户自己的环境（ADR 0079）**：载荷的 `user_environments` 里装齐的排在安装目标前面、同一组单选，
  后端排第一的预选；「改用这个环境」= `depRepairStore.adoptUserEnvironment(id, script)`（只交 id，不认路径），
  成功关框 + `retryEnvironmentFailures`；没装齐的收在 `ui/Details` 里只说还缺什么（不可选）；一个都没装齐
  才说「没有装齐的环境」（老后端没有这个字段时不说）。环境名只有一份实现 `lib/userEnvironmentText.ts`
  （来源 → 文案键的字面量表）。后端在门里**已经自动改用**时不弹框，SSE `engine.environment_adopted`
  进 `envStore.adoptedEnvironment`，通知轨（与「刚为编辑加入本文档」同一档）说一句并给「改回」=
  `revertAdoptedEnvironment()`（`setProjectPython(null)` → 后端 `remember_default`，所有在用的面板
  `markStale`，不只是失败的）。
- **元素树的行只在自己显示的东西变了时重画（2026-09-24，松手卡顿剖析）**：
  `ElementTree` 的 `ElementRow` / `ClusterRow` 是 `React.memo`，props 全是这一行显示与
  交互实际用到的值（`gid` / `label` / `role` / 行名 `name` / `canHide` / `readonly` /
  `hidden` / `locked` / `depth` / `selected` / `tabbable` / `expanded`、面板 id）加上树级
  稳定回调（`useCallback`，行调用时带上自己的 key / gid / 当前展开态）。**不收整个
  `panel`**（每次 commit 都是新引用，拖一下松手 58 行全重画）、**也不收 `el`**（新图到达时
  manifest 每个元素都是新对象）；隐藏的判据照旧是「该 gid 有 `visible=false` 的 override
  或 `isElementHidden(el)`」，在树里按 gid 查表算好再传。比较用 React 默认的逐 prop 浅比较、
  不写自定义比较函数：行里要用到 el 上的新东西就只能先加成一个 prop，「漏比一个字段 =
  改了不刷新」在结构上不会发生。行内文案跟着语言走，每行各自 `useTranslation`。看护：
  `components/left/elementTreeRerender.test.tsx`（A1 无关提交零重画 / A2 新 manifest 零重画、
  单行 label 或行名变只重画那一行 / A3 selected·tabbable·hidden·locked·expanded 各自只重画
  那一行并显示新状态；观测点是 `<li>` 上 React 记的 props 对象，读不到直接抛）。
- **左栏「工作区」抽屉（2026-09-24）**：切项目的列表**只有一份**，住在
  `components/left/WorkspaceList.tsx`（当前 · 收藏 · 最近，最近不截断）。顶栏项目名
  （`ProjectSwitcher`）只做 `railClick('workspace')`，不再自己弹菜单——两处各列一遍
  就是两套判据。同名区分 / 筛选 / 失效分组共用 `lib/recentProjects`；「打开文件夹 /
  新建项目」与 Project Picker 共用 `useProjectEntry`（桌面走系统选择器）。收藏的事实
  在后端 `config.pinned_projects`，改动**一次一个按路径描述的操作**（`POST
  /api/projects/pinned`：add / remove / move，move 用相对 `delta` 或 `to_path`），由
  `config.edit_pinned` 在锁里对照最新那份执行——**不发整张列表、不按下标**：整张替换会
  让两个标签页互相盖掉，按下标排队的挪动会移错项（Codex #550）。前端每个标签页一条
  收藏队列、一条切项目队列（`projectStore.serialQueue`），切换期间所有「打开」入口置灰；
  `init` / `refreshRecent` 回来时若收藏修订号已变就不写 `pinned`。界面以回包为准、失败
  不动列表；PUT/POST 前先解析 pj，失效时 409 且配置不变。与最近列表互相独立（从最近
  移除不取消收藏）。收藏里是用户的项目路径：诊断包的条数化与路径记号两处都要带上它。
  看护：`components/left/workspaceList.test.tsx`、`store/projectSwitchSerial.test.ts`、`tests/test_projects.py` 的 pinned 六条、
  `tests/test_diagnostics_bundle.py::test_pinned_projects_are_redacted_like_recent_ones`。
- **左栏「样式」面板（2026-09-24）**：`components/left/StylePanel.tsx`，排在「问题」前面、两者互相
  跳转。**当前图**与问题面板同一个判据（`useCurrentFigure`）；面板**不判规范**——「这一格不合规」只按
  对象 · gid · `propertyPath` 认回问题清单里已有的那一条（`lib/stylePanelModel.cellIssues`），点行尾记号走
  `issueFocus.openProblemAt`（定位仍是 `focusObject`，再把问题面板的范围 / 筛选 / 游标摆好）。数字是**页面上
  的 pt**（× `panelScale`，写入 ÷ 回去，与样式应用同一个换算）；图内元素经 `useTextStyleAdapter`、画布标注经
  `useCanvasTypography` 写（Inspector 同一条路，一次改动一次 commit）；多个值是「多个值」不压扁。底部是**画布跟随
  样式**（ADR 0081，唯一实现 `store/styleBinding.ts`）：选一套 = 绑定并立刻对齐整张画布（一次 commit）；已绑定时各格
  的改动改的是**这套样式本身**（先存库、存成功再一次 commit 对齐改了的那一项，内置样式先复制一份再改绑）；「不跟随
  样式」只解绑；「恢复原样」清整张画布样式管得到的 override 并解绑。写入前一律过 `effectiveChanges`，已合样式的图零
  commit。设置 › 样式页的「用于当前画布」调同一个 `bindCanvasStyle`；样式对话框只编辑、不应用。
  **脚本重跑后脚本赢**（用户 2026-09-25 裁决，ADR 0081 §十三）：样式只在绑定 / 改值 / 库更新 / 新图第一次拿到 manifest
  时自动写；重跑后的不一致（脚本改了的值、新 gid）由面板显示「N 处与样式不一致」+「对齐」（`styleMismatchPlan` /
  `alignCanvasToStyle`，一次 commit，用户手改的不算不动，为 0 时整行不出现）。样式写的每条 override 登记在
  `style.owned`（连同写入时脚本的原生值），精确 manifest 的 `value_original` 与基线不等时让位（`yieldToScript`，一次
  commit「脚本改动优先于样式」）；登记只在 override 仍是那个值时算数（`lib/styleOwned.ownedLive`），用户经
  `updateObject` / 混排对齐 / 一键修复写过的那一条当场注销；老文档、老引擎不让位；恢复原样连样式写的孤儿（gid 已不在 manifest 里）一起清，解绑只清孤儿。
  看护：`components/left/stylePanel.test.tsx`、`lib/stylePresets.test.ts`、`store/styleBinding.test.ts`、`lib/migrate.style.test.ts`。
- 看护：`store/projectReadinessStore.test.ts`、`components/RegistryDialog.test.tsx`、
  `components/WorkdirConfirmDialog.test.tsx`、`components/WorkdirRow.test.tsx`、
  `components/DependencyPrepareDialog.test.tsx`、`components/notificationRail.test.tsx`（「已改用你的环境」）、
  `components/ProjectReadinessBanner.test.tsx`、
  `components/left/AssetBrowser.readiness.test.tsx`、
  `canvas/panelReadinessEntry.test.tsx`、`components/inspector/panelCapabilityNote.test.tsx`、
  `canvas/drawerViewportResize.test.tsx`、`store/uiStore.test.ts` 的两个左栏
  describe；e2e `a11y.spec.ts` 的接入状态两条 + `golden-paths.spec.ts`。
