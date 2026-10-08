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
  `markStale`，不只是失败的）。**ADR 0114 §六（2026-10-06）起默认的检测模式下这条 SSE 又会发**（准备 / 运行时
  自动检测采用了能跑的那一个）；确认模式（`TAVOTTO_ENV_ADOPTION=confirm`）下不发：候选里没检查过的（`checked === false`，`ok` / `satisfies` 为 null，
  含 `project_venv` 来源）单列成「还没检查」的单选，选中后主按钮是「检查并使用」，仍是同一个 `adoptUserEnvironment`
  ——后端现场体检、装齐才采用，不冒充装齐也不冒充没装齐；「没有装齐的环境」那句只在没有未检查的候选时才说。
- **环境建议（ADR 0114）**：`project.recommendation` / `project.consent` 是后端 `envadvice.recommend()` 的原样投影，
  `envStore` 只保存与转发（`checkEnvironment` = 明确的检查动作、`adoptCandidate` = 在建议上点「使用」，带候选 id 与看到建议那一刻
  的环境代 `expected_generation`），**不自写「能不能跑」的判据**；`EngineEnvironmentCard` 的 `EnvironmentAdviceRow` 只在
  `decision.needs_decision` 时问一句 + 一个主按钮，全局解释器压着（`locked_by`）时只说原因不给按钮。看护
  `store/envAdvice.test.ts`、`components/EngineEnvironmentCard.test.tsx`、`e2e/environment-advice.spec.ts`。
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
  移除不取消收藏）。行与当前卡片的右键开出与「…」**同一份**清单（`PointMenu`，一份 JSX 两个入口）；
  其中「在 Finder 中打开」只在桌面壳摆出（`lib/desktop.canRevealInFileManager`，文案按
  `fileManagerKind` 分三档），失败把完整路径说出口。收藏里是用户的项目路径：诊断包的条数化与路径记号两处都要带上它。
  看护：`components/left/workspaceList.test.tsx`、`store/projectSwitchSerial.test.ts`、`tests/test_projects.py` 的 pinned 六条、
  `tests/test_diagnostics_bundle.py::test_pinned_projects_are_redacted_like_recent_ones`。
- **左栏「样式」面板（2026-09-24）**：`components/left/StylePanel.tsx`，排在「问题」前面。
  **当前图**与问题面板同一个判据（`useCurrentFigure`）；面板**不判规范、也不显示问题**（用户 2026-09-26：
  行尾的等级记号去掉，字号被阻断这类情况样式页不提示，问题只在左侧图标栏带计数角标的「问题」面板里看）。数字是**页面上
  的 pt**（× `panelScale`，写入 ÷ 回去，与样式应用同一个换算）；图内元素经 `useTextStyleAdapter`、画布标注经
  `useCanvasTypography` 写（Inspector 同一条路，一次改动一次 commit）；多个值是「多个值」不压扁。底部是**画布跟随
  样式**（ADR 0081，唯一编排 `store/styleBinding.ts`；在途计数与欠账唯一持有者是叶子 `store/styleWork.ts`，原图准入直接读同一份）：选一套 = 绑定并立刻对齐整张画布（一次 commit）；已绑定时各格
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
  绑定时存成样式里的 boolean），线条 = 数据线宽 / 边框线宽 / 刻度方向 / 刻度长度 / 刻度线宽。行是两列固定网格：
  标签列 `4rem`、控件列（字号 / 线宽 / 方向这类「值」格同一个宽 `VALUE_W`，从左缘起排）；没有状态列，控件行里只有
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

## 导入即扫描（T02，`store/projectScanStore.ts` / `lib/projectScanText.ts`；呈现在 T13b 的引导卡）

后端规则全文在 `docs/rules/backend/registry-discovery-and-probe.md`「导入即扫描」。前端只做三件事：取回快照、翻译、
决定引导卡要不要露出来。**T13b 起没有顶部检查条**（用户硬性要求：页面不下移）——扫描结果只由画布右下的引导卡呈现
（见下一节「准备引导卡」）。

- **接在统一认领与启动恢复上**：`projectStore.adoptOpenedProject`（打开 / 切换 / 教程换画布都走它）认领完成后、
  `init()` 发现项目还开着时，各调一次 `projectScanStore.start()`（后端单飞 + 新鲜复用，重复认领便宜）。**不 await、不阻塞**
  认领——静态素材此刻已经可以排版；教程副本不扫。不要只在某个 picker 按钮上接。准备会话跑出结果后（`onCompleted`）
  再 `start({force:true, reason:'refresh'})` 一次（零执行），快照随结果更新。
- **事实在后端**：phase / outcome / 目标选择 / 每项检查的状态都读快照，前端没有第二份「有没有脚本 / 能不能运行」的判据。
  句子只在 `lib/projectScanText.ts`（按 outcome / 账本 code / role 查，不从计数里推结论）。运行中**没有百分比**。
  环境线索不再显示（T13b：用哪一套由程序在准备时决定，ADR 0114 §六）。
- **要不要露出来也在那一处**（`scanCard`）：`discover` / `choose`（有待准备的绘图脚本，卡片）、`scanning`（慢于 `SLOW_SCAN_MS`，
  只出角标）、`stuck`（没看全 / 失败 / 取消，角标，点开一句话 +「重新检查」）、`quiet`（静态项目、脚本都已连接、空项目——只在用户
  从命令面板「显示项目检查结果」时出现，`forced`）、`null`（什么都不露）。
- **几个不同的动作**：「稍后」/「—」缩成角标、×收起（都只改呈现，`uiStore.guideCard`）；「取消检查」只打取消扫描的端点；
  切项目 `clear()` 换代并停轮询（后端那笔账在项目关闭时才收）。没有一个会碰执行 / 安装 / worker。
- **迟到响应**：请求序号只认最后发出的、发请求那一刻的项目 id、`clear()` 换代、同一 `scan_id` 内 `observation_seq` 不许倒退；
  POST 回包是「此刻谁在跑」的最新事实，不比序号。后端 404（`project_scan_not_started`）= 丢掉旧快照等下一次 `start()`。
- 看护：`store/projectScanStore.test.ts`、`lib/projectScanText.test.ts`、`store/projectSwitchScan.test.ts`、
  `components/preparationEntries.test.tsx`「引导卡：扫描发现绘图脚本」；真浏览器 + 真后端：`e2e/project-scan.spec.ts`（只有脚本的
  项目自动弹卡、页面不下移、脚本零执行且没建会话、每个项目只自动弹一次；静态项目无卡；目录读不动出角标；教程独占）。

## 准备引导卡（T09 / T13b，ADR 0116：`store/projectPreparationStore.ts` / `lib/preparationText.ts` / `components/PreparationCard.tsx`）

后端会话合同全文在 `docs/rules/backend/preparation-and-receipts.md`「准备会话」。前端只做四件事：保存报告投影、冻结参数快照、订阅 / 补拉、
翻译成一句话 + 一个主按钮。T13b 把 T09 的准备面板（贴在检查条下、挤占布局）换成画布工作面板右下的**一张浮动卡**（设计稿 v2）：
不占布局、页面不下移，宽 400，浮动工具条上方。

- **三种呈现，一个开关**：`uiStore.guideCard`（`card` / `pill` / `closed`，不进持久化）。「稍后」「—」「放到后台」缩成角标；
  角标或卡上的 × 才收起。缩成角标时只有**脚本发问**才自动展开（跑完、出错只改角标）；收起时等作答的 input 由原对话框接着问
  （同一问只有一个展示面，`scriptInputStore.claimPresentation('prep-card')`）。**进入编辑之后卡片自动收起、不留角标**。
- **每个项目只自动弹一次**（`lib/guideCardSeen.ts`，本机 localStorage 按后端 `project_id` 记）：扫描给出 `discover` / `choose`
  时弹；**弹出时不建会话**——建会话 = 检查 = 检测候选（会起解释器），扫描阶段零执行；用户点「开始准备」才 `open()`。
- **入口**：卡上「开始准备」、素材库脚本行 ▶、接入中心逐行「试运行并连接 / 重新试运行」（T09b：接入中心先让开）都只
  **打开**会话（`projectPreparationStore.open(scriptTarget(script))`，同时把卡展开）：后端只做只读检查，一个试运行请求都不发；
  参数草稿在这一刻取拷贝（`scriptTarget`），之后再改草稿不动这份会话——草稿与会话冻结的不同（`draftDiffers`）时卡片说「参数已填好」、
  主按钮「继续」（按新参数重新检查）。本地开关 `lib/preparationFlag.ts`（`localStorage['tavotto.preparationPanel']`，默认开，`'off'` 时
  「开始准备」/ ▶ / 接入中心回到**同一台**旧状态机 `scriptRunStore.run`；接入中心委派它、读它的状态显示那一行；参数草稿照样经
  `probeWithDraft` 带上，保留一版）。渲染路上的门与完整 PNG 准入**不走**会话：它们是编辑已知图的执行器（合同 §A），对话框是
  `pool._new_worker` 两道门的薄展示适配器，答完续上的是被挡住的那次渲染 / 准入（ADR 0116 §二）。同一份依赖需求两个展示面的下游效果只有
  一种：授权框 / 修复卡装完或明确跳过 → 空闲会话 `recheckIdle`；会话里的依赖作业装完 → `renderStore.retryEnvironmentFailures()`。
- **按钮只来自报告**：`lib/preparationText.prepView` 按 phase / outcome / requirement kind 查标题（一句话、不带句号），主按钮只来自
  报告里后端生成的 `actions` 与 `requirements`——没有 `run` 动作就没有「运行」，有 `prepare_dependencies` 才有「安装」（回显
  `impact_digest`）。环境不让用户选（ADR 0114 §六）：「用的是哪一套」只在详情一行人话（报告 `environment.kind`），界面不出现
  环境 / 解释器 / venv 字样；`environment_choice` 只在确认模式出现。**必填参数没填齐时任何卡都不说「可以运行」**：参数 schema 是报告里
  `script_arguments` 待办的载荷，草稿缺几个必填项由 `lib/scriptArgsForm.missingRequired` 读出来（`ctx.missingArgs`），这时换成参数卡
  （只摆必填项；其余参数、原样 token、粘贴命令在折叠里）、主按钮置灰。参数块在可以运行 / 出错 / 没出图 / 画好的卡上以折叠摆着，按同一个
  React key 留在原位——改参数时输入框不重建、焦点不丢。一句话 + 至多一个主按钮，其余在默认不展开的「详情」；看护
  `PreparationCard.test.tsx` 的「每一种状态 × 每一种语种」（`visibleSentenceCount` / `visiblePrimaryButtons`）。
- **脚本会弹窗**（`gui_dialog` 待办，后端静态识别，不阻塞）：`ready_to_run` 且报告带它时，标题换成一句「脚本会弹窗选文件 / 询问，
  这里弹不出来，请把文件路径 / 答案写进脚本」（`state = gui_dialog`），主按钮仍是报告里的 `run`，文案「仍然运行」；怎么改（路径写进脚本、
  数据放项目里用相对路径）与哪几行、哪个调用在默认收起的详情里。报告没有 `run` 动作就没有按钮；叠栈里没有「让助手改脚本」的现成入口，
  不为它另造。必填参数没填齐时仍让位给参数卡。看护 `PreparationCard.test.tsx`「脚本会弹窗」。
- **运行目录在卡里选**：推荐项（后端 `recommended`）预选，「换一个」才展开其余；歧义时全部摆出、不预选、选中前按钮置灰。确认 =
  `envStore.setWorkdirMode(mode, {confirmed:true})`（与原对话框同一次 PATCH），会话随 `envStore` 订阅只读地 `recheck`，**不运行**；
  按钮说「用项目根目录」之类，不说「运行」（原对话框同改为「用这个目录」）。缺数据仍交 `envStore.requestMissingInput`；
  确认模式下的环境候选用 `envStore.adoptCandidate`。作答后**绝不认领 `run`**。
- **三种关停**：收起 / 角标 = `uiStore.guideCard`（订阅照旧）；切项目 = `clear()` 换代、停轮询、零请求（后端一样不取消），卡片收起；
  停止 = 会话的 `cancel` 动作（按 owner，当场）——运行 / 安装中它是文字按钮，主按钮是「放到后台」。
- **连接不是执行**：取报告带看门狗（`REQUEST_TIMEOUT_MS`），超时 / 断网只把 `connection` 标 `lost`、退避补拉，phase 原样、不标失败、
  动作失败不重发；SSE `preparation.session` 与事件流重连只触发补拉；404 `unknown_or_restarted` → 重建只读检查（`restarted`），不运行。
  迟到响应：项目代际 + 发请求那一刻的 pj + 同一会话修订 / 观察序号只许前进 + 重建后不被旧会话 id 的回包换回去。
- **结果分层**：`facts.execution_finished` / `facts.figure_captured` 读后端（详情里分开说）；`first_edit_ready` 是渲染态观察。
  「进入编辑」用报告的 `captured` 走 `openFastEdit`（清单还没有这张图时先 `addRuntimePanelToCanvas` 描述符），多张图开
  `ProbeResultsDialog` 逐张加。跑完没图：「跑完了，没有出图」+「知道了」，原因在详情。失败：「运行出错了」+「再试一次」，详情第一行是
  **错误原文**（`result.error.params.error`，其次 `message`；traceback 只取最后一帧），下面是 `TaskDiagnostic`（`preparation` / `dependency`）。
  无参数运行替换掉的旧图名（`unlinked_stems`，T09b）在「画好了」卡的详情里说清怎么恢复。
- **重新提问的原因分开说**（ADR 0099 §十）：`ScriptInputForm.suggestionText` 按 `recheck` 选句子——`legacy_answer`（这台电脑上没有当时的记录）
  不说成「输出变了」；`config_changed` 说参数变了；其余说输出变了。
- 看护：`store/projectPreparationStore.test.ts`、`components/PreparationCard.test.tsx`、`components/preparationEntries.test.tsx`、
  `components/ScriptInputDialog.test.tsx`、`store/projectReadinessStore.test.ts`（A → B → A）、`components/EngineEnvironmentCard.test.tsx`；
  真浏览器 + 真后端：`e2e/preparation-card.spec.ts`（只有脚本与数据的项目：页面不下移、卡自动弹出且不建会话 → 选目录 → 改参数 → 答 input →
  进入编辑后卡片收起，执行恰好一次；收起卡片换展示面、放到后台、HTTP 断开、应用重启、明确停止）、`e2e/first-run-qualification.spec.ts`
  （必填参数未填时「继续」置灰、进入编辑后取景与元素树可点）、`e2e/registry-center-preparation.spec.ts`（接入中心 → 同一张卡、点下去不执行、
  无参数重跑的可恢复提示；开关关闭时委派旧状态机、零会话请求）、`RegistryDialog.test.tsx`。旧路径的 e2e（`asset-library` /
  `dependency-one-click` / `missing-input*` / `script-input`）在开关关闭下跑。

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
