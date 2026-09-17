# 设置外壳与包管理（2026-09-02，ADR 0038）

> 原文出自 `web/AGENTS.md`「设置外壳与包管理（2026-09-02，ADR 0038）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

完整版在 `docs/adr/0038-settings-shell-agents-packages.md`，改动前先读。

* **外壳尺寸是合同**：`SettingsDialog` 固定 `SHELL_WIDTH = 760` / `SHELL_HEIGHT = 600px`
  （`ui/Dialog` 的 `height`），内容区 `[data-settings-content]` 独立滚、切页滚回顶部；<640px 导航变
  顶部一条。**新分区再长也不许让外框撑高。** 十一个分区在 `SECTIONS`；旧 id 走 `resolveSection()`
  的别名表（`profiles → spec` 等），深链的调用方**不要**再写旧 id。
* **深链带返回**：`setSettingsOpen(true, section, { returnTo: 'export' })`；`settingsReturnTo` 是闭集
  （`'export' | null`），每次打开重置。要加新的返回目标先扩闭集。
* **编码 Agent 一级列表只有名称 · 版本号 · 状态**：版本号经 `agentVersionLabel` 只取数字，抽不出
  就不渲染（真机上 shim 的报错行带完整路径）；路径 / 命令 / 检测来源只在 `AgentDetailView`，
  用 `settings/CopyButton` 给复制。**一级页面上不许出现路径、内部包名、解释段、卡片外框。**
* **编码 Agent 的 e2e 锚点是稳定 `data-*`，不是小标题上那句话**（2026-09-07，#299 posix-e2e
  真红）：审计 T44 把两个小节按用户目标改了名（「在 A 中使用 B / 在 B 中使用 A」→「配置改图
  助手 / 连接外部工具」），`e2e/coding-agents.spec.ts` 里两条认文案的用例当场找不到元素。
  换成新文案只是把同一个赌注再下一次；**更糟的是那两条 `toHaveCount(0)` 的反向断言**
  ——文案一改它们恒真，连红都不会红一下，直接从「守着」变成假绿。清单（都归
  `e2e/coding-agents.spec.ts` 用，改属性要同步它）：
  `data-agent-section="in-app" | "external"`（`CodingAgentsSection`，两个小节）、
  `data-agent-codex-integration`（② 里那一行）、
  `data-agent-rescan`（列表头与详情概览各一颗「重新检测」）、
  `data-agent-last-checked`（那颗按钮旁边的时间戳）、
  `data-agent-open="<agent id>"`（`AgentList` 覆盖整行的进详情按钮）、
  `data-agent-detail="<agent id>"`（`AgentDetailView` 根节点 = 「此刻在详情页」）、
  `data-agent-back`（返回列表）、`data-agent-field="state | version | executable | source |
  checked-at | readiness"`（概览与诊断里的「标签 / 值」行）、
  `data-agent-fold="custom-executable" | "diagnostics"`（两个 `<details>`）、
  `data-agent-custom-exe`（「使用自定义可执行文件」——它只被一条 `toBeHidden()` 用到，
  所以那条断言先 `toHaveCount(1)` 再判隐藏：`toBeHidden()` 对不存在的元素同样通过）。
  「一级页面不许有输入框」这条现在量的是 `[data-endpoint-step]`（端点编辑器整个不在这一层，
  锚点在 `EndpointDialog`）与 `[data-agent-field]`（概览字段只在详情里），不是「某两句话没出现」。
  分区导航认 `[data-section="ai"]`（分区 id 是持久化格式的一部分），不认导航项的文案。
* **左栏那两颗循环外的按钮补进了既有的 `data-rail` 约定**（2026-09-07，同一族收尾）：
  `LeftRail` 的 `ITEMS.map` 里每颗都带 `data-rail={id}`，注释也写着「aria-label 是本地化
  文案，不能当选择器」；但「接入状态」与「设置」是循环外单独写的两颗，**漏了这个属性**，
  于是三个 spec 只能退回按中文文案找它们（同一个赌注在三处各下了一次）。现在是
  `data-rail="readiness" | "settings"`，id 与 rail 文案键的末段对齐。
  连带两处：覆盖式抽屉的遮罩认 `[data-scrim]`（`App.tsx`，判「左抽屉此刻盖住了下面的
  东西」，原先按「收起侧栏」这句话），设置对话框按**里面装着 `[data-settings-shell]`**
  与帮助气泡消歧，不按对话框的名字。`e2e/` 里已经没有按可见文案定位的入口了
  （`i18n.spec.ts` 里那张 `settings: '设置'` 是**语言切换的期望值表**，不是定位，别动它）。
* **同一族在英文侧与「只有 Windows 跑」的用例里各还有一处**（2026-09-07，#299 windows 腿）：
  审计 T34 把写回确认按钮从「Write back」改成「Write back to the original files」，
  `e2e/error-recovery-en.spec.ts` 的 `/^Write back$/` 当场匹配不到、等满 180 秒。锚点换成
  `data-write-back="open" | "confirm"`（`UpdateSourceButton`）。那条用例**只在 Windows 腿上真跑**
  （posix 是 skip），所以锚点另在两条天天跑的 jsdom 用例里钉住正向存在性
  （`WriteBackDialog.test.tsx` / `settingsCopy.test.tsx`）——只被一条隔天跑的用例引用的锚点，
  被删掉了没人会知道。
* **条件分支里的定位是假绿最好的藏身处**（2026-09-07 复核，同一族）：`e2e/ux-consistency.spec.ts`
  流程 C 按 `getByRole('radiogroup', { name: '执行改动的命令行工具' })` 与
  `getByRole('slider', …)` 取控件，审计 T37 把执行器换成了一个 `Select`、把推理强度收进了
  折叠区，两个定位都匹配到 0 个元素——而它们写在 `if (…)` 里，**一条都没红，全部静默跳过**，
  用例名字里的「模型与推理强度、键盘可调、偏好保持」一个字都没在验，CI 一路绿。
  现在锚点是 `data-ai-agent-model="select" | "static"`、`data-ai-effort="disclosure"`、
  `data-ai-open-settings`，机器上装没装 Agent 的分支保留（CI runner 上可能一个都没有），
  但**锚点的存在性由 `aiModelPicker.test.tsx` 的正向用例负责**，e2e 那边的 `if` 才是安全的。
* **包管理只操作当前项目的 Tavotto 受管环境**：`store/packageStore.ts` 的 `plan(op, spec)` →
  `run(jobId)` 两步，**`run` 只在 `PackagesSettings` 里被调**——教程 / readiness / watcher 只能
  深链到包管理页，不许替用户点 run。错误文案走 `DependencyRepairCard.repairCodeMessage`
  （`errors:engine.repairError.*`，与缺包修复同一张表）。「没有回滚」那句话常驻，别删。
* **查找是两层，只有第二层出网**（ADR 0038 的 2026-09-07 修订）：打字只在本地过滤两份
  清单（PEP 503 归一后的子串），**界面上没有任何随输入自动触发的请求**——在设置页里
  打字不许悄悄变成对外发送，前后端各有一条用例钉着它。出网只发生在用户点「在 PyPI
  查找」那一下（`packageStore.runLookup` → `lookupPackage()`）。输入框只有**一个**
  （既是安装规范也是搜索词），**回车仍然是安装**；切版本约束的判据只有
  `packageStore.searchTerm()` 一处，组件把原串原样交给 store，别在组件里再切一次。
  结果卡上的安装走**既有**的 `plan → run`，不复制第二条路径；选「最新版」交给 pip 的
  是**裸包名**（安装 argv 带 `--only-binary=:all:`，钉死一个只有 sdist 的版本会当场
  失败），选了具体版本才钉 `==`。两次查找重叠时按序号只落最后一次。首屏只留短句
  「查找会访问 PyPI 或你配置的软件源」，「打字不出网」这条**为什么**折进技术详情
  ——首屏最多一段长文，名额已经归「装坏了可以重建」。看护
  `settings/PackagesSearch.test.tsx`（29 条）。
* **`packageStore` 也有项目代际**（本轮评审 P2）：`clear()` 换代 + 把清单 / 查找结果 /
  上一次的错误整份丢掉，`resetForNewProject()` 调它。查找结果里的 `installed` 与
  `source` 说的是**发请求那个项目**的受管环境，开在另一个项目的包页面上就是假的，而
  那一页的安装按钮作用在当前项目上。**代际与 `lookupSeq` 是两条轴，不许合并**：序号答
  「同一个项目里哪一次最新」，代际答「这个响应属于哪个项目」——只有序号的话，A 那次
  查找在 B 里仍然是最新的一次，照样落地。`clear()` 只换代、**不动 `lookupSeq`**：两条
  保证各由一条判据负责，做两遍的话拆掉其中一遍会照样全绿。**作业不清、但按所属项目分格**
  （issue #309）：作业改的是旧项目的环境、还在后端跑，`job_id` 是唯一的把手，与导出作业 /
  native 会话同一条纪律；「B 的页面上不该出现 A 的进度条与取消按钮」靠作业自带项目字段
  解决——`run()` 记下起它那一刻的 `currentProjectId()`，进度落进 `jobs[所属项目]`，界面只读
  `progressFor(currentProjectId())`，`cancel` / `poll` 只问当前项目的作业，终态副作用
  （刷清单 / 环境 / 渲染重排 / 错误文案）只在**此刻开着的就是作业所属项目**时派发。
  `engine.package` 的 SSE 事件**不带 pj**，`handleServerEvent` 顶上那道按项目的闸对它不起作用，
  所以这一层非做不可。看护 `store/projectSwitchPackages.test.ts`（12 条：查找 6 + 作业 6，
  含「作业属于当前项目时终态照常派发」的对照——没有它，「B 上 0 次」会因为从没人触发而恒绿）。
* **设置里的说明先改控件，改不动才加帮助**（2026-09-06 审计「说明文字专项补查」）：优先级是
  命名 → 单位 → 对象关系 → 状态 → 条件展开。`SettingRow` 的 `description` 是标签底下的一行短
  说明（改的是什么、影响哪里），`status` 只在那个状态**真的成立**时出现（「当前窗口只能固定
  一侧」），`help` 小问号只留给真有歧义的少数几处——**常规字段不默认挂问号**，常规页现在一个
  都没有（`settingsDisclosure.test.tsx` 按 `[data-help-tip]` 数）。界面上不写像素断点、不写实现
  词（figure / twinx / JSON 格式）、不承诺兑现不了的事（「证明图没变过」）；会影响这次选择的
  限制、错误原因、写回范围与备份后果一律就近显示，不许只藏进悬停提示。设置页与它深链过去的
  对话框（导出偏好 ↔ 导出对话框）**读同一批 key**，不写同义词。
  这条规则**对审计自己新加的文字同样成立**：T41 给只读配置补的那句「内置配置只读……想改先
  复制一份」（37 字）和分区说明（43 字）在样式页叠成了两段散文，把 `e2e/ux-consistency.spec.ts`
  的「一个分区最多一段长解释」顶红（2026-09-07，#299）。**解法是按第一条走、不是把阈值抬到 2**
  ——阈值一抬，新加的文案就单方面推翻了一条已经生效、审计自己在别处反复引用的约束。
  现在它是**状态 + 动作**：一枚常驻徽标（`profiles.readOnlyBuiltinBadge` / `readOnlyBadge`）
  加旁边一颗「复制一份再修改」（接的还是原来那个 `duplicate`，原先摆在所有字段下面、要滚很远）。
  信息一个字没丢，锚点 `data-profile-readonly`。
* **诊断页不显示 `cli_*` 检查**（Agent 页已有），渲染环境卡只在技术详情里一张，内置包清单归包管理页。
  「复制诊断」的文本来自 `fetchDiagnosticsSummary()`（后端同一份采集），前端不另拼。
* **更新页只说得出「上一次检查的回答」**（2026-09-06 审计 T48）：界面上没有无条件的「已是最新
  版本」——`LastCheckVerdict` 按**真实存在的时间戳**二选一（没查过 → 「无法判断」，查过 →
  「{时间} 检查时没有发现新版本」），判据认 `data-update-verdict`，不认那两句散文。桌面通道的
  时间戳 `desktopCheckedAtMs` **只在检查成功时**写。下载进度只显示壳真给的数，拿不到就走不确定
  态、绝不编百分比。升级失败要看得出是失败（`applyFailed` + danger）且重试入口留着；pip 那条
  失败走 500、原因在响应体的 `log` 里而 `error` 是空的，得自己取出来。五态覆盖在
  `components/settings/updateStates.test.tsx` + `store/updateStore.test.ts`。
* **同意是三档，控件也得是三档**（审计 T49）：`unset` / `enabled` / `disabled` 在界面上必须可辨，
  用 `Segmented` 的 `value=null` 表达「尚未选择」——**可写的只有开 / 关两档**（回不到 unset）。
  「同意的是上一版采集范围」（`needs_reconsent`）单独一句话，不许画成「已开启」。
  「会发送哪些数据」是闭集 `lib/telemetryDisclosure.ts`，逐条对应后端 `EVENTS`（严格同源对，见根
  `AGENTS.md`）——**别再写成一段会过期的散文**，上一版就是这么漂掉九条事件的。
  看护：`components/SettingsTelemetry.test.tsx` + `tests/test_telemetry_disclosure.py`。
* 看护：`SettingsDialog.test.tsx` / `settings/PackagesSettings.test.tsx` /
  `settings/DiagnosticsSettings.test.tsx` / `settings/agentState.test.ts` / `e2e/settings-shell.spec.ts`
  （外框逐像素、溢出、窄窗口、英文、方向键、axe——**量之前先等 `getAnimations().finished`**）。
