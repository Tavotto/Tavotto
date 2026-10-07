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
- **持有者只有 `store/projectReadinessStore.ts`**：并发纪律取 `assetStore` 的这几条
  （请求序号挡旧响应、发请求那一刻的 pj 挡串项目、同批合并、`force` 另起一次、
  失败保留上一次成功那份；`assetStore` 的全文在 `asset-library.md`，它另有项目代际）；**fingerprint 没变时连报告
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
  `envStore.requestWorkdirConfirmation`，`WorkdirConfirmDialog`（md，三档是可选的 `Card`，推荐项是 ok 的 `Badge`，写出文件的后果是
  一行警示 `Notice` + 「详情」气泡，主按钮「在这里运行」，2026-10-07 设计审计 §10.2）渲染三档（项目根 / 脚本目录 /
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
  `DependencyPrepareButton` 能再打开；同一时刻只开一份；换了项目的旧载荷不弹。素材库试运行撞上同一道门时经
  `scriptRunStore.handOffProbeGate` 交同一份载荷、准备成功后重跑那一行（全文在 `asset-library.md`）。**目标与状态的文案键写成
  字面量**（`TARGET_LABEL` / `STATE_TEXT` / `BLOCKED_TEXT` 表）。MCP 那一面是同一份决定：
  `tavotto_open_figure(prepare_dependencies=…)`。
  **完整 PNG 的编辑准入**同样交出工作目录 / 依赖准备 / 缺数据的结构化载荷；准入发生在加入渲染态之前，
  不把未采用的 PNG 标成待重建。作答经 `retryEnvironmentFailures`（数据改指经 `restaleProjectRenders`）接回显式编辑意图，
  重新校验所选源字节才提交 FRAME / guard 与进入编辑。改指只交接它自己造成的那次渲染作废；等待期间先发生的
  换项目 / 渲染代际、选择或工作区变化、文档或历史变化仍不接回。真实 `pointAtData` 响应与同代 SSE 只恢复一次。
  看护 `store/artifactEntry.test.ts`「selected PNG environment gates」、`e2e/guarded-png-activation.spec.ts`。
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
- **元素树与图层树是 ARIA 树（2026-10-07 设计审计 §10.3）**：`role="tree"`，每行 `aria-level` / `aria-posinset` /
  `aria-setsize`；每行一份 `ui/RowMenu`（⋯ / 右键 / ⇧F10，行有焦点时 ⋯ 进 Tab 顺序；开菜单不改选区）。元素行的 ⌫ / Delete
  与画布一样只隐藏、不反向显示，所以菜单只在「隐藏」上标 ⌫，「恢复显示」不标（Codex #832）。图层行的键位：
  Enter = 只选这一个（主操作，此前是改名）· F2 改名 · ⌥↑↓ 改层级 · Esc 清选区；拖放落点是 `dropLineClass`。元素树的
  「只看这一支」是搜索行里的一枚 chip（`data-element-isolate`，× 退出），不再是一条横幅；`ElementTree` 有 `chrome`
  （`drawer` 缺省 / `bare`：别处借用时搜索行自己留边，playground 侧栏用它）。
- **元素树的父级只有 `roles/hierarchy.structuralParent` 一份（ADR 0102）**：先认 manifest 的显式
  `parent_gid`（指向的元素或组确实在），再按 gid 路径回退；组（`Manifest.groups`）挂在整张图下。
  组是**真实节点**（可选中、进面包屑，选中 = 成员一起平移 / 缩放），抽屉是**视图容器**（不可选中、
  不进面包屑、不参与几何）；组下不加抽屉。抽屉按元素**是什么**分（`clusterOf(el)`：色条轴与
  图例同进「图例与色条」），与它挂在谁下面无关。面包屑走 `ancestorsOf` 的真实祖先链，色条轴
  那一级不单列。选中组时属性区是组页（`inspector/GroupPage.tsx` 的 `PanelElementPage` 分流）。
  手动色条缺显式宿主时保持顶层；属性页消费 `owner_status=undeclared` 显示「归属未确定」与
  保留 cax 的可执行 `ax` 关联方式，不由颜色关系推父级（#792）。
  看护：`components/left/elementTreeGroups.test.tsx`、`inspector/groupInspector.test.tsx`、
  `roles/hierarchy.test.ts`、`inspector/colorScalePanels.test.tsx`、`e2e/shared-colorbar-group.spec.ts`。
- **左栏「项目」抽屉（2026-09-24；轨名 2026-10-07 起与短名同为「项目」）**：切项目的列表**只有一份**，住在
  `components/left/WorkspaceList.tsx`（当前 · 收藏 · 最近，最近不截断）。**行只有一种**：`ProjectPickerRow.ProjectRow`，
  抽屉用 `density="drawer"`（44px）、Project Picker「全部项目」用 `density="page"`（52px + 文件夹记号），两处同一份菜单
  （`ui/RowMenu`：⋯ / 右键 / ⇧F10）、同一套键位（一列一个 Tab 停靠点 `left/rovingList`，↑↓ / Home / End 走行，Enter 打开，
  收藏行 ⌥↑ / ⌥↓ 与菜单的上移 / 下移、拖动同一个按路径的 move，拖动时画 `dropLineClass` 落点线）。当前项目是顶上一张
  `Card appearance="subtle"`，**不再在收藏区重复**（此前被收藏时一屏画两次选中）；卡不可聚焦，它的 ⋯ 常驻 Tab 顺序
  （`RowMenu tabbable`）。目录已不在的行「打开」是 `aria-disabled`（不是 `disabled`）：仍是漫游列表的一站，⋯（移除 / 收藏）
  够得着，点了不打开；切换中 / 正在打开照旧 `disabled`（Codex #832）。页脚是左栏统一的页脚语法
  （`border-t px-1.5 py-1`、抽屉底、28px ghost 钮）。Project Picker 不在工作台的 TooltipProvider 里，「全部项目」自己包一层。顶栏项目名
  （`ProjectSwitcher`）只做 `railClick('workspace')`，不再自己弹菜单——两处各列一遍
  就是两套判据。同名区分 / 筛选 / 失效分组共用 `lib/recentProjects`；「打开文件夹 /
  新建项目」与 Project Picker 共用 `useProjectEntry`（桌面走系统选择器）。收藏的事实
  在后端 `config.pinned_projects`，改动**一次一个按路径描述的操作**（`POST
  /api/projects/pinned`：add / remove / move，move 用相对 `delta` 或 `to_path`），由
  `config.edit_pinned` 在锁里对照最新那份执行——**不发整张列表、不按下标**：整张替换会
  让两个标签页互相盖掉，按下标排队的挪动会移错项（Codex #550）。上移 / 下移（⌥↑ / ⌥↓）跳过藏起来的
  当前项目、与可见的邻居换位，邻居在收藏队列**轮到执行时**按最新列表才找（`movePinned(path, { step, skip })`），
  不在按键那一刻定——不然连按两下拿同一个旧邻居，第二下挪回原处（Codex #832）。前端每个标签页一条
  收藏队列、一条切项目队列（`projectStore.serialQueue`），切换期间所有「打开」入口置灰；
  `init` / `refreshRecent` 回来时若收藏修订号已变就不写 `pinned`。界面以回包为准、失败
  不动列表；PUT/POST 前先解析 pj，失效时 409 且配置不变。与最近列表互相独立（从最近
  移除不取消收藏）。行与当前卡片的右键 / ⇧F10 开出与「…」**同一份**清单（`ui/RowMenu`）；
  其中「在 Finder 中打开」只在桌面壳摆出（`lib/desktop.canRevealInFileManager`，文案按
  `fileManagerKind` 分三档），失败把完整路径说出口。收藏里是用户的项目路径：诊断包的条数化与路径记号两处都要带上它。
  看护：`components/left/workspaceList.test.tsx`、`store/projectSwitchSerial.test.ts`、`tests/test_projects.py` 的 pinned 六条、
  `tests/test_diagnostics_bundle.py::test_pinned_projects_are_redacted_like_recent_ones`。
- **左栏「样式」面板（2026-09-24）**：`components/left/StylePanel.tsx`，排在「问题」前面。
  **当前图**与问题面板同一个判据（`useCurrentFigure`）；面板**不判规范、也不显示问题**（用户 2026-09-26：
  行尾的等级记号去掉，字号被阻断这类情况样式页不提示，问题只在左侧图标栏带计数角标的「问题」面板里看）。数字是**页面上
  的 pt**（× `panelScale`，写入 ÷ 回去，与样式应用同一个换算）；图内元素经 `useTextStyleAdapter`、画布标注经
  `useCanvasTypography` 写（Inspector 同一条路，一次改动一次 commit）；多个值是「多个值」不压扁。滚动区最后一节是可折叠的
  **画布跟随样式**（2026-10-07 设计审计 §10.3：此前是钉在底部的约 150px 页脚；节头收起时仍说此刻跟着哪一套 / 有几处不一致，
  开合按本机记）（ADR 0081，唯一编排 `store/styleBinding.ts`；在途计数与欠账唯一持有者是叶子 `store/styleWork.ts`，原图准入直接读同一份）：选一套 = 绑定并立刻对齐整张画布（一次 commit）；已绑定时各格
  的改动改的是**这套样式本身**（先存库、存成功再一次 commit 对齐改了的那一项，内置样式先复制一份再改绑）；「不跟随
  样式」只解绑；「恢复原样」清整张画布样式管得到的 override 并解绑。写入前一律过 `effectiveChanges`，已合样式的图零
  commit。设置 › 样式页的「用于当前画布」调同一个 `bindCanvasStyle`；样式对话框只编辑、不应用。
  **脚本重跑后脚本赢**（用户 2026-09-25 裁决，ADR 0081 §十三）：样式只在绑定 / 改值 / 库更新 / 新图第一次拿到 manifest
  时自动写；重跑后的不一致（脚本改了的值、新 gid）由面板显示「N 处与样式不一致」+「对齐」（`styleMismatchPlan` /
  `alignCanvasToStyle`，一次 commit，用户手改的不算不动，为 0 时整行不出现）。样式写的每条 override 登记在
  `style.owned`（连同写入时脚本的原生值），精确 manifest 的 `value_original` 与基线不等时让位（`yieldToScript`，一次
  commit「脚本改动优先于样式」）；登记只在 override 仍是那个值时算数（`lib/styleOwned.ownedLive`），用户经
  `updateObject` / 混排对齐 / 一键修复写过的那一条当场注销；老文档、老引擎不让位；恢复原样连样式写的孤儿（gid 已不在 manifest 里）一起清，解绑只清孤儿；恢复原样挑「样式管得到的」要求此刻 manifest 确实暴露这条属性（用户的孤儿不删）；按 (gid, prop) 取 override 一律走 `effectiveOverride`（重复条目 last-wins，#587），`styleOverrideLookup.test.ts` 按 TS AST 结构性看护（不用源码正则）。
  **能改哪些、怎么排**（2026-09-26 用户反馈）：文字各行 = 字体 + 字号 + 粗体 / 斜体（`FIGURE_TEXT_ROWS.faceRole`；
  图例的在 `legend_text` 上；刻度文字的引擎字段没有 `weight` / `style`，不摆开关；画布标注写 `bold` / `italic`，
  绑定时存成样式里的 boolean），线条 = 数据线宽 / 边框线宽 / 刻度方向 / 刻度长度 / 刻度线宽。行是两列固定网格，
  与属性栏同一副（2026-10-07 设计审计 §9.2 / §10.3）：标签列 `var(--insp-label)`（属性栏那一期定义之前退回同一个
  `clamp(88px, 28%, 112px)`）、列间 8、控件列（字号 / 线宽 / 方向这类「值」格同一个宽 `VALUE_W`，从左缘起排）；没有状态列，控件行里只有
  控件（`data-style-cell` / `data-style-face`），框只有 `fieldBox` 一副。绑定时的写入排队存库（`editBoundStyle` 返回 Promise），每一格记住最后一次排进去、
  还没落定的值（`usePendingWrites`），显示与「在当前值上做」的动作（粗 / 斜体开关、↑↓ 步进）都按它算，落定后放掉
  （Codex #662 P2：否则连点两下加粗会排进两次 `bold`）。文字颜色、线条 / 边框颜色没进面板：取色是连续手势，绑定时每一下都要存一次库，得先有「一轮取色 = 一次
  存库」的收口。
  **设置 › 样式页与面板同一张行表**（2026-09-28）：`settings/StyleProfileFields.tsx` 的行从 `FIGURE_TEXT_ROWS` /
  `FIGURE_LINE_ROWS` 派生（另加「其余文字」= `element.text`），面板加一行、设置跟着多一行，不另抄一份角色 × 属性。
  编辑的是样式本身，所以每格多一档「未设置」（占位写「未设置」不写「多个值」），且**每一格都能单独回到它**：数字框清空后
  回车 / 失焦删掉这个键、不写 0（`NumberField.onClear`），字体下拉顶上一项「未设置」，粗 / 斜体的三态循环；行尾 ×
  是「整行清除」（名字写明整行）；删空的角色不留空壳。控件认不出的值（字号 `large`、字体是一串候选、画布标注的
  粗斜体不是 boolean、线宽 / 刻度长宽不是数、刻度方向不是字符串；规范页的数字字段同样）照原值显示（`profilePath.rawValueText`）、
  不当成没设，不点就原样保存，也能单独清；只读摘要同口径。粗 / 斜体是
  看得出的三态：未设置（面板「多个值」同一副第三态视觉、`aria-pressed="mixed"`，名字换成「未设置」）→ 开 → 显式关
  （写 `normal` / false，应用时一律去掉，≠ 未设置的「保留原样」）→ 回到未设置，悬停三句各说应用时会怎样；从图里提取的非规范值（`semibold` / `600` / `oblique`）
  按 `lib/typography.weightIsBold`（≥ 600）/ `styleIsItalic`（非 normal，与引擎 #704 同一口径）显示成开 / 关、悬停说原值，
  不点就原样保存，点了按显示的那一态往下走；字体选项 = 通用三族 + **每一张**已渲染的图里引擎报过的首选项与本机族（逐张走 `withMachineFamilies`，按键排序）
  + 样式里已写着的名字；`options_unavailable` 跟着并，口径「任一运行时画得出就不算不可用」（样式里写着、没有哪张图
  请求过的名字：**每一张**图的运行时都报了完整本机表时不在任何一份里就标，混着老引擎、可用性未知时不标），标记与 warning 用属性页
  同一副（`FontMissingTag` / `FontMissingHint`），名字不换；内容没变时选项对象引用不变（选项与标记都比）；
  改动以函数交出（字体下拉按数据 memo，回调不许捏着旧草稿）。只读那份每行一句摘要（字体 · 字号 · 粗斜体）。
  示例图（`lib/styleSample.ts`）每一笔只读它自己那一维（同一张行表），没设是示例默认、不从别的角色回落——应用时
  `element.text` 只落在 `text` 角色上、边框线宽不改刻度线宽，示例跟着变就是预告不会发生的事。
  字重 / 字形照原值画（`light` → 200、`600`、`oblique`，与 `planStyle` 交给引擎的同一个值），归一只给开关与字宽估算。
  字号超出示例预算时几何里**所有**长度（字号、线宽、边框、刻度长度 / 线宽）乘同一个系数（`fitSampleGeometry`），
  版面与画框再按缩后的几何现算（`sampleLayout`），比例与应用后一致。
  看护：`components/left/stylePanel.test.tsx`、`lib/stylePresets.test.ts`、`store/styleBinding.test.ts`、`lib/migrate.style.test.ts`、
  `components/settings/profilesSettings.test.tsx`、`lib/styleSample.test.ts`。
- 看护：`store/projectReadinessStore.test.ts`、`components/RegistryDialog.test.tsx`、
  `components/WorkdirConfirmDialog.test.tsx`、`components/WorkdirRow.test.tsx`、
  `components/DependencyPrepareDialog.test.tsx`、`components/notificationRail.test.tsx`（「已改用你的环境」）、
  `components/ProjectReadinessBanner.test.tsx`、
  `components/left/AssetBrowser.readiness.test.tsx`、
  `canvas/panelReadinessEntry.test.tsx`、`components/inspector/panelCapabilityNote.test.tsx`、
  `canvas/drawerViewportResize.test.tsx`、`store/uiStore.test.ts` 的两个左栏
  describe；e2e `a11y.spec.ts` 的接入状态两条 + `golden-paths.spec.ts`。

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 句子只有 `statusLabel()` / `reasonText()`（读 `reason_code`）
- `allEditable` 一档不逐张重复
- 「没测量」三档不压扁
- 界面不执行动作、冲突不预选
- 首开的运行目录确认框只翻译后端的三档载荷、歧义不预选、「运行」一次 PATCH、「稍后」留在错误块
- 跑前的依赖授权框只翻译整份联合计划、先绑定计划再只发 plan_id、blocked 摆理由不装、「不准备直接运行」是明确的 skip
- 装齐的用户环境排在安装目标前、只交 id 采用，后端已自动改用时不弹框、通知轨给「改回」（ADR 0079）
- 侧栏「偏好」与「此刻开着」是两件事、自动让位绝不写回偏好
- 切项目的列表只有工作区抽屉一份、顶栏项目名只开抽屉、收藏按路径发单个操作（不发整张列表）、切项目与改收藏各自串行
- 元素树行 memo、props 只收显示字段（不收 `panel` / `el`）、回调树级稳定
- 样式面板（ADR 0081）不判规范、不显示问题（2026-09-26 起问题只在问题面板里看）、数字是页面 pt（× `panelScale`，写入 ÷ 回去）、多个值不压扁
- **应用样式只有绑定一条路**（`styleBinding`）：库写入一条队列、写文档前比代次、只认精确 manifest、欠账、写入前过 `effectiveChanges`（已合样式零 commit）、future 非空时不自动写、撤销只退画布并标「已脱离」（不推回库）
- 重跑后脚本赢：不自动对齐，面板给「N 处不一致」+「对齐」，样式写的 override 登记在 `style.owned`、脚本改了（`value_original` ≠ 基线）就让位，用户写过的当场注销（ADR 0081 §十三）
