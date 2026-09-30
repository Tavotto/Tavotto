# ADR 0106：脚本要读的数据找不到时，请用户指认一次，按项目记住

日期：2026-09-28 · 状态：**Accepted**（§裁决 A–D 四项由用户 2026-09-28 拍板）
相关：[0047 在脚本目录里运行](0047-safe-profile-project-workdir.md)、
[0057 首开的一次确认](0057-first-open-environment-and-workdir.md)（本 ADR 兑现它「不做的事」里留给 X01 的
「任意绝对路径的重新定位」中**用户显式指认**的那一片，以及登记用例 FO08 的 `guided` 合同）、
[0070 执行回执与数据绑定](0070-execution-receipt-and-manifest-identities.md)、
[0084 探路调用是首开证据](0084-probe-calls-are-first-open-evidence.md)、
[0094 写回脚本](0094-script-writeback.md)、[0099 脚本 input() 在界面上作答](0099-script-input-bridge.md)

## 问题

用户问（2026-09-28）：只把 `.py` 拖进 Tavotto 时，脚本引用的外部数据能不能找到？

数据还在原处时，现有机制基本够用：拖放打开的是脚本所在目录（ADR 0092，不复制脚本）；沙盒 cwd 下的
相对读由只读回退改指到脚本目录（`figcapture`，四个打开入口）；`glob` / `exists` / 只在项目根的数据由首开
确认把用户带到「在脚本目录 / 项目根运行」（ADR 0057 / 0084）。

数据**不在**原处时，产品只剩一段报错：

| 形状 | 今天 |
| --- | --- |
| 脚本被单独复制出来，`pd.read_csv("data/x.csv")` 相对路径落空 | 首开证据「一处都找不到」→ 不问（ADR 0084 §二）；运行后 `FileNotFoundError` 被 `worker.ensure_built` 一律归 `script_error`：「脚本执行失败: [Errno 2] No such file or directory: 'data/x.csv'」+ traceback |
| 脚本写死 `/Users/a/proj/data/x.csv`，数据被挪走 / 换了电脑 | 同上；而且规则明文「沙盒**之外**的绝对路径一个都不碰」（`figcapture` 模块头） |
| 脚本先 `exists()` / `glob` 判空再自己 `exit("找不到数据")` | `no_figures_captured`，界面只有脚本自己打印的那句 |

这对会写脚本、不熟文件系统的科研用户是死路：他要读懂 traceback、判断是路径问题、再去改脚本——而
**脚本永远不改**是产品承诺。登记用例 FO08（「项目移动与外部数据失联」）要的产品结果正是 `guided`：
「准确说明失联并允许重新定位；不复用旧绑定假报成功」，至今 `planned` 未兑现。

1.0 收敛纪律：这是兼容性缺口（同一脚本 `python fig.py` 在原机器能跑、换位置后 Tavotto 里只能报错），
且 FO08 属统一实施包 U03 的登记范围，不是新扩能力。

## 裁决

一句话：**认出「脚本要读的输入不存在」→ 弹窗请用户指认那个文件或它所在的文件夹 → 按项目记一张只读的
改指表 → 重跑一次。**不改脚本、不搜同名、不放松写入边界。

### 一、把「输入不存在」认出来：新错误码 `missing_input`

主语：**safe worker 进程**里、**这一次 build** 期间、**脚本（含它调用的库）发起的只读打开**。

1. **记下落空的读**：`figcapture.install_input_remap` 在四个打开入口（`builtins.open` / `io.open` /
   3.10 的 `Path.open` / numpy `DataSource.open`）外面再包一层——装在输入观察器**外**、只读回退**内**
   （回退先找脚本目录，找不到才轮到它）。**原路径打开抛 `FileNotFoundError` 之后**，只读模式的那次把
   **脚本写的那一串**（cwd 之内的绝对路径换回相对那段——`Image.open` 这类先 realpath 的库）与当时的 cwd
   记进 `InputMisses`（去重、有上限）。成功的打开零额外开销；写 / 追加 / 读写模式不记。
2. **build 失败时分类**（`figcapture.missing_input_of`）：异常链（`__cause__` / `__context__`）里有
   `FileNotFoundError` 或 errno 为 ENOENT 的 `OSError`，**并且**对得上一次落空的只读打开——`exc.filename`
   就是（或按 realpath 等于）某条记账；异常没带文件名（numpy 的 `"d.txt not found."`、h5py 的 C 层消息）
   时用最近一条。对得上就报 **`missing_input`**（`retryable=False`，traceback 原样，`extra.missing_input =
   {requested, absolute, cwd, misses}`）；带了文件名却不是一次只读打开落空的（写进不存在的目录）或根本
   没有记账，一律仍是 `script_error`，一字不变——判不出就别判。
3. **弹窗载荷**（`inputremap.payload_for`，pool 两条控制面与试运行各接一处，`pool._offer_missing_input`）：
   `{script, requested, absolute, via, others}`。`others` 是脚本里写着、此刻哪儿都找不到的路径
   （`inputremap.static_missing`）：`databinding` 认得的相对字面量 / 探路目标里在脚本目录与项目根**都**
   `missing` 的（项目外的 `outside` 不看），加上**以常量出现的绝对路径**里不存在的（只 `os.path.exists`，
   不读、不列目录）；已经被改指表救回来的不列。「脚本跑完没出图」（`no_figures_captured*`、试运行的
   `script_no_figure`）只挂静态那部分，`requested` 为 null——覆盖「先 `exists()` 判空再自己 exit」的形状。
   每一条带 `via`：`open`（改指救得回来）/ `probe`（exists / listdir / stat / import_file，同一串也被探路
   问过的也算这一档）/ `glob`。

### 二、弹窗：说清缺什么，请用户指认

画布渲染（`renderStore`）与素材库「运行并发现图」（`scriptRunStore`，用户拖进脚本后走的第一条路）拿到载荷
都交给同一个 `MissingInputDialog`（与首开的运行目录确认框同一个家：`envStore`，同一时刻只开一份，换了项目
的旧载荷不弹）；MCP 把载荷原样放进 `structuredContent`，`recovery` 请用户回 Tavotto 窗口里指认。

> **找不到数据文件**
> 脚本要读的 values.txt 不在原来的位置了，请指给 Tavotto 看它现在在哪。
> 【稍后】【找到这个文件…】　　› 详情

- **一句话 + 一个主按钮，其余折叠**（用户 2026-09-29：「像这样一个卡片太冗杂了，坚决不能出现，一定要让用户
  一句话就能够读懂」）。默认只有标题、那一句话（**只有文件名**，不出完整路径）、主按钮与「稍后」；完整路径、
  「相对路径 / 写死的完整路径」的解释、「一并修好」的其余路径、「只影响读取」的说明、改选文件夹（「选择所在
  文件夹…」——选中那个文件就推得出规则，整批换了文件夹时才用得上）全收进默认折叠的「详情」。浏览器模式的
  粘贴框与主按钮「使用这个路径」同样在外面。探路救不回的条目：一句话换成「请把它放回脚本写的位置」，不给
  主按钮。
- 以 worker 说出来的那串为主；「没出图」时先挑 `via = open` 的那条。其余进「详情」（「按同样规律能找到的会
  一并修好」）——**只列真进了读取调用的常量**（`inputremap.READ_FUNCS` / `read_*` 的路径实参整条是常量、
  只赋值一次的名字、`Path(<常量>)` 的读、拼路径时打头的那一段（目录常量）；写 / 追加 / 读写模式的打开
  不算）与探路调用问的；坐标轴标签、
  存图 / 写出的目标、`.py`、当输出目录用的路径都不在任何读取调用里，不靠按字符串长相事后去猜。
- 桌面用系统选择器（`pickAnyFile` / `pickDirectory`，同一条 `dialog:allow-open` 权限）；浏览器模式拿不到
  本机路径，让用户粘贴，按 `chosen_kind: "auto"` 发（后端按是不是文件夹分派）。
- 文件选择器由用户操作，Tavotto **不按同名搜索、不预选候选**（ADR 0057 / FO08）。
- 成功后关框、画布上 `missing_input` 的面板与素材库里带载荷失败的脚本各自重跑一次；失败（`input_remap_*`）
  留在框里说原因。「稍后」只关框，错误块 / 脚本行里的「指认数据位置…」能再打开。

### 三、改指表：从一次指认推出规则，只读，按项目记住

`POST /api/engine/input-remap {requested, chosen, chosen_kind}` → `inputremap.derive` 按**路径段**求最长
公共后缀推出一条规则，推完自检（`remap_target([规则], requested)` 必须落到存在的文件）：

| 脚本要的 | 用户指认 | 推出的规则 |
| --- | --- | --- |
| `data/run1/x.csv`（相对） | 文件 `/Volumes/B/proj/data/run1/x.csv` | `prefix` `""` → `/Volumes/B/proj`（所有相对路径到这里找） |
| `/Users/a/proj/data/x.csv` | 文件 `/Volumes/B/proj/data/x.csv` | `prefix` `/Users/a/proj` → `/Volumes/B/proj` |
| `/Users/a/proj/x.csv` | 文件 `/Volumes/B/y.csv`（改了名） | `file`：只改指这一个文件 |
| `data/x.csv` | 文件夹 D | 从最长后缀试起，第一个 `D/<后缀>` 存在的定规则；都没有 → `input_remap_not_found_in_dir` |

- **只在原路径打不开之后查表**：原路径存在永远读原件（数据回来了，规则自动不起作用）；只读模式、目标是文件
  才改道，否则原样抛出原来那个异常。写 / 改 / 删 / 重命名一个字节都不经过这里。命中顺序：`file` 整串相等
  优先，其次 `prefix` 里路径段最长的；相对只配相对、绝对只配绝对；盘符按小写比，反斜杠当分隔符。
- **判据只有一份**：`figcapture.remap_target`——worker 改道、父进程推规则后自检、筛 `others` 用的是同一个函数。
- **存在哪**：本机项目设置 `config.project_settings(<项目>)["input_remap"]`（§用户拍板 C），唯一出处
  `inputremap.rules_for`；同 kind 同 `from` 重新指认是替换；`DELETE` 删一条；`GET /api/engine/environment`
  的 `project.input_remap` 列出来（设置 →「环境诊断」里可删，目标不存在的标出来）。改了就 `shutdown_all(root)`。
- **进执行描述，但不进稳定那一档**：`ExecutionSpec.input_remap`（规则是本机路径——与 `cwd` / `interpreter`
  同类，**不进** `stable_payload()`）。三条 spawn 路径（Python 池 / `_spawn_spec` / `one_shot`）都从
  `rules_for` 取——写回的重放与热态读同一份数据。`worker_argv` 只在非空时多 `--input-remap <json>`，
  没有规则的 argv 逐字节不变。改道来的读照样经 `InputObserver` 记（项目内的进数据身份，ADR 0070）。
- **native（`tavotto run`）不改指**（构造时拒绝）：那是用户自己的 `python fig.py`（ADR 0020）。浏览器
  playground 不适用（单文件，`missing_file` 维持现状）。

### 四、C++ 读取器与探路调用：诚实说做不到的部分

ovito `import_file`、h5py / netCDF 的原生打开、`exists` / `glob` / `listdir` 不经四个打开入口，
**改指表救不回它们**（与 ADR 0047「不扩回退到 `exists` / `glob`」同一个理由）。主条目 `via` 不是 `open`
时，对话框不给选择器按钮，如实写「脚本先用 exists / glob / listdir 检查数据在不在，Tavotto 没法替这类检查换
位置，请把数据放回脚本写的位置」。画布错误块里「在脚本目录里运行」的既有出口（ADR 0047）照旧在。

**§用户拍板 B（经确认改写脚本里那一处路径常量）不在本次实现里**：ADR 0094 的写回脚本仍是 Proposed，
仓库里没有可复用的备份 / 复原事务。它另开一个 PR，届时在这个对话框里给 `via ≠ open` 的条目加次级按钮。

### 五、改指表的代次：依赖映射的在飞工作一律按代次落地

改指表是会话之外的一份输入：它一变，所有**按旧表跑、还没落地**的工作都成了旧数据的产物。逐条补
（shutdown 会话、前端标 stale、runtime 指纹……）挡不住在飞的那一次，Codex 在 #716 上连着报了四轮同族
问题（在飞渲染、在飞试运行被物化成新鲜、两个窗口并发改表吞规则）。收口成一个机制：

- **唯一的代次**：`inputremap` 按项目维护一个整数（`generation()`），每次**成功的**增 / 换 / 删 +1。起点是
  进程启动时刻的毫秒数：代次只增不减、**跨重启也单调**（前端按「≤ 已见就忽略」去重，重启归零会骗过它）。
  「同一处」按 `remap_parts` 规范化后的 `from` 认（`data` / `./data` / 反斜杠写法），最新的那条胜出。
- **按项目的一把互斥锁**（`inputremap.project_mutex`，可重入；用户 2026-09-30 拍板）：下面这些全在同一把锁里——
  改指表的读 / 改 / 写与换代；`state()` / `snapshot()` 的「表 + 代次」（一次持锁一起取出）；注册表文件的整段读改写
  （`discover.register` 自己拿锁）与登记标记（`record_registration`）；以及所有落地提交：写回、导出发布、登记、
  物化写缓存。落地从**核对代次开始一直持到提交完成**，改指等在途的提交落完才改表、换代。
  - **为什么是一把锁、不是读写锁**：读写锁那一轮之后，每轮评审仍能找出新的并发窗口（读者之间注册表读改写互相
    覆盖、读表与读代次之间插进一次改表……），逐个补不收敛。一把互斥锁一次消掉整类竞态。
  - **代价**：同一项目的写回、导出发布、登记彼此串行。它们只串行落地那一小段——长活（跑脚本、渲染、导出的渲染）
    都在锁外，桌面上基本感觉不到。
  - **锁序**：池锁（`pool._lock`）→ 项目锁。池在自己的锁里起会话会调 `snapshot()`；所以**持项目锁时绝不取池锁**：
    锁里不取会话、不起会话、不跑脚本（物化先在锁外取会话，再进锁核对会话的代次）。配置锁是叶子。
- **落地前在锁里核对、持锁到提交完**（`landing()`）：代次对不上就丢弃，报 `input_remap_changed`（409，
  **可重试**）。导出作业把**开始时**的代次一路带到发布那一步（`exportjob.run(commit_guard=)`），在提交点之前
  进锁再核一次、持到最后一个文件发布完。
- **能停的顺手停**：改表之后收掉项目的会话，并对在跑的试运行置取消 + 硬杀。停只是尽早——正确性只靠代次。
- **前端作废按代次幂等**：统一入口 `envStore.onInputRemapChanged(generation, reason)`——`generation` ≤ 这个
  窗口已见的代次就忽略，同一代不论从哪条路来只执行一次（→ `restaleProjectRenders`：`invalidateInflight` +
  面板 stale + runtime 判定重查 / 清单重取 + 试运行结果作废，删除之外再重跑因「找不到数据」失败的脚本，并重取
  设置里的规则列表）。三条路径都调它：① 改指 / 删指接口的响应带回新代次（`input_remap.generation`），
  **发起的窗口收到响应就本地作废**，不依赖事件；② 后端经项目事件流（SSE `/api/events`）广播
  `input_remap_changed{pj, generation, reason}`，同项目的其它窗口据此作废；③ 事件流重连、页面恢复可见时
  拉一次环境（里面带当前代次，`noteInputRemapGeneration`），落后就补一次作废——事件丢了也不漏。刚开项目
  （已见代次为空）拉到的只记作起点。
- **前端**：后端代次是唯一权威。渲染回包 `input_remap_changed` 当 stale 重排、试运行重跑一次，不报失败；
  `renderStore.invalidateInflight()`（与换项目同一个代际）只是收到事件时提前 abort 在途请求，不是第二套判据。
- **绝对路径保留绝对身份**：worker 里落在 cwd 里的绝对路径（`cwd_mode=project` / `project_root` 时常见），只有
  **能证明**是相对路径被库规范化出来的（PIL 这类先 realpath 再打开）才按相对那一段查表、记账——证据是脚本源码
  （`figcapture.path_literals`：有相对常量的路径段是它的前缀，且没有绝对常量认领它）。证不出就保留绝对身份：
  宽泛的相对规则不接管脚本显式写的绝对路径，弹窗也不会据此推出一条 `from=""` 的全项目规则。
- **静态载荷里的「已被规则救回」只算真读取**：`static_missing` 只压掉 `via=open` 且规则解析得了的条目；
  exists / glob / listdir 不查改指表，一条宽泛规则碰巧解析得了也照列，按「改指救不回」说（否则脚本照样退出、
  用户只剩一句「没出图」）。
- **持久化的派生物**：进程内的代次只在本进程有意义，能跨重启对账的是**改指表指纹**（`inputremap.fingerprint`）。
  runtime 物化 cache 与试运行登记各记一份：cache 在 metadata 里（`possibly_stale` 判据）；试运行登记记在
  **本机项目设置**（`input_remap_registered: {脚本: 指纹}`）——注册表 `tavotto_registry.json` 随项目走、
  不放本机派生物。登记的 stems 可能由数据决定（改指之前是 `group_A`、之后只有 `group_B`）：指纹对不上时，
  下一次这个脚本的会话**按新表 build 之后**（渲染成功或 `unknown_stem` 都一样）按这次 build 的真实产出
  重新登记（`app._resync_registration`，锁内核对代次，不多跑一次脚本），`registry.changed` 让所有窗口看到。
  选它而不是改表时立刻重跑全部试运行：不替用户执行没在用的脚本，也不在改表那一刻串行跑 N 个脚本。
  **没有指纹标记的已登记脚本**（这个机制之前登记的、静态发现的）按保守对账处理：项目里有任何改指规则就算
  过期，下一次 build 时重新登记；没有规则的维持原样（它只可能是在「无表」下登记的）。

以映射为输入的工作点与派生物（全仓枚举；新增一处先接到这个机制上）：

| 工作点 / 派生物 | 开始时的代次 | 落地点（锁内核对） | 持锁区间（项目互斥锁） | 对不上 | 别的窗口怎么得知 |
|---|---|---|---|---|---|
| 画布渲染 `/api/engine/render` | 会话 `remap_generation` | 回包之前 | 只核对（回包不写盘；晚到的由前端按代次作废） | 409，前端标 stale 重排 | `input_remap_changed` → 面板 stale |
| runtime 物化 cache `_materialize_runtime`（渲染 / 试运行两处） | 产出那次 build 的代次 + 取到的会话的代次；metadata 记指纹 | 写 cache 之前 | 核对 → 每个描述符的物化写完（取会话在锁外） | 丢弃，不写；指纹不符判 `possibly_stale` | 事件 → runtime 判定重查、清单重取 |
| 试运行登记 `probe.probe_and_register` | `probe()` 用的会话 | 登记之前 | 核对 → 注册表写完、重载、记指纹 | `registered: false` + 码，前端重跑 | 事件 → 试运行结果作废；登记变了发 `registry.changed` |
| 注册表里试运行登记的 stems（持久化）`_resync_registration` | 本机项目设置里的指纹（无标记 + 有规则 = 过期） | 下一次按新表 build 后 | 核对 → 重新登记、记指纹 | 按真实产出重新登记；build 跑完一张没出（`no_figures_captured*`）也是权威结果，整条摘掉 | `registry.changed` |
| 写回原图 `_write_source_files` | 热态会话与全量重放会话各一 | 两者都等于此刻才进 commit | 核对 → `_backup_targets` → 整个 replace 循环（含失败回滚） | 409，staging 清掉，原件不动 | 事件 → 面板 stale（写回前先重画） |
| 导出面板 `_serialize_figure_with_worker` | 会话 | 导出文件交出去之前 | 只核对（交给作业的是临时文件） | 409 / 作业失败 | 事件 → 面板 stale |
| 导出作业的发布 `exportjob.run(commit_guard=)`（同步 / 后台两条） | 作业开始时 `generation(项目)` | 提交点之前 | 核对 → 提交点 → 最后一个文件发布完 | 作业 `input_remap_changed`（可重试），一个文件都不发布 | 事件 → 面板 stale |
| 准备接口 / 预热（U01） | 会话 | 复用前（池） | 不持（`_remap_current` 只读代次） | 重建会话 | —— |
| 改指 / 删指 `add_rule` / `remove_rule` | —— | —— | 等在途提交落完 → 读表、写表、换代 | —— | 响应带新代次 + 事件 |
| 读状态 `state()` / 起会话 `snapshot()` | —— | —— | 表与代次一起取 | —— | —— |
| 注册表读改写 `discover.register`（所有调用方） | —— | —— | 读文件 → 合并 → 写回 | —— | `registry.changed` |
| 前端缓存（渲染 / runtime 判定 / 试运行结果） | —— | —— | —— | —— | `onInputRemapChanged(代次)`：接口响应 / 事件 / 重连补拉，按代次去重 |
| `tavotto open` 的本地试运行（`handoff._local_probe`） | —— | —— | —— | 不适用：只在没有在跑的实例时于 CLI 进程里跑，改表只发生在服务进程 | —— |
| 弹窗载荷 / `static_missing` / `native_miss` | —— | 只读、当场算 | —— | 不适用 | —— |

## 用户拍板（2026-09-28）

四项均按建议裁决：**A 放行**、**B 提供改写脚本（次级、经确认）**、**C 存本机项目设置**、**D 运行失败后才问**。
下文保留各项的理由与取舍。

**A. 绝对路径改指：放行。** 现行规则：「沙盒之外的绝对路径一个都不碰——那是用户指名的位置，就近找一个能用的
在那里是越权」。本 ADR 的改指不是「就近找」，是用户在弹窗里亲手指认、确认过规则、按项目记住、可删的。
落地：`figcapture` 模块头与 `docs/rules/backend/figure-capture-and-execution.md` 那句收窄为「沙盒之外的绝对
路径**回退**一个都不碰」，并指向本 ADR——回退仍然不猜；改道只来自用户亲手指认的改指表。

**B. C++ 读取器 / 探路调用的出路包含「改写脚本」。** ADR 0094 已有写回脚本的事务通道。**建议**：
只作为弹窗里的次级按钮，展示逐行 diff、用户确认后才写，且只替换那一个字符串常量（拼出来的路径不改）；
默认路径仍是「不改脚本」。若否，这类脚本只给「把数据放回原处」的说明。

**C. 改指表存本机项目设置。** 规则里全是本机绝对路径，换台机器就失效。**建议**存本机项目设置
（`config.project_settings(<项目>)["input_remap"]`，与 `workdir` 同处），不进项目里的 `tavottofile/`
（不随项目包/同步走、不把本机路径带给合作者）。

**D. 运行失败后才问，不在运行前问。** 静态证据里「哪里都找不到」的路径，可以在第一次运行前就弹同一个窗。
**建议不在运行前问**：脚本可能自己先生成那个文件、或数据是可选的（`if exists(...)`），运行前问会误报；
改为运行失败后一个弹窗带上全部 `static_missing`，一次问完。

## 不做的事

* 不按文件名在磁盘上搜索、不猜候选（FO08 的「旁边有同名不同值」）。
* 不把改指扩到 `exists` / `stat` / `glob` / `listdir`，不包 C++ 读取器。
* 不改写入边界：沙盒 cwd、`Path.unlink` / `write_text` 守卫、savefig 不落盘原样。
* 不拷贝、不软链接用户数据到任何地方。
* 不对 `os.path.join` / f-string 拼出来的路径做静态判断（运行时仍经 §一.1 的记账被认出）。
* native 与浏览器 playground 不在范围内。

## 代价

- worker 四个打开入口多一次查表与一次记账（只在「确实不存在」的分支上，命中路径零额外开销）。
- `ExecutionSpec` 多一个 stable 字段：改指表一变，会话重建、准备计划作废——这是对的（数据变了）。
- 一个新错误码与一个对话框；三类入口（桌面 / MCP / HTTP）各接一处结构化载荷，同源对表加一行。

## 看护

- `tests/test_missing_input.py`：匹配规则（相对根 / 前缀按路径段 / 最长前缀 / `file` 优先 / Windows 写法 /
  坏规则丢弃）；推规则（同尾 / 改名 / 文件夹 / 拒绝）；项目级存取；进程内改道（原件优先、写不改道、落空记账、
  numpy 无文件名、写模式的 FileNotFoundError 不认领）；argv 与 stable payload；真 worker——相对 / 绝对缺失 →
  `missing_input` → 指认后输出等于那份数据的真值、项目里放同名诱饵不影响（FO08 的反例）、原件回来读原件、
  脚本目录模式同样生效、「先 exists 再退出」只给静态列表、普通 `ValueError` 仍是 `script_error`、试运行两条路、
  三条 spawn 路径同源；HTTP 增 / 查 / 删与稳定码；§五 的代次——并发改表两条都保留且各换一代、`landing()`
  拒旧代次、试运行途中改表不登记、旧代次的物化被丢弃、池不复用旧代次的会话；项目锁——写回替换中途、导出发布
  中途的改指都等提交落完（barrier 钉住中途）、作业开始后改指则一个文件都不发布、两个脚本同时重新登记两边都在、读状态时改表读到的表与代次配套、
  同一线程可重入；无标记的旧登记在有规则时重新登记；改指后 0 张图摘掉旧登记；探路项不被规则压掉；脚本写的绝对
  路径保留绝对身份。每条落地时做过变异反证。
- `web/src/store/inputRemapRestale.test.ts`：发起窗口按接口响应本地作废（没有事件也不漏）、同一代两次只作废
  一次、重连补拉落后就补一次、刚开项目只记起点。
- `tests/test_mcp_server.py`：载荷进 `structuredContent`、`recovery` 说得出下一步。
- `tests/test_error_codes.py` / `tests/test_script_probe.py`：新码两种语言都有文案、占位符对得上。
- `web/src/components/MissingInputDialog.test.tsx`：主条目与其余列表、文件 / 文件夹各发一次且重排、取消不发、
  失败留框、探路不给按钮、浏览器粘贴按 `auto`、「稍后」后能再开、换项目清掉、英文无中文。
- `web/e2e/missing-input.spec.ts`（功能登记 `assets.missing-input-relink`）：真后端真 worker，运行并发现图 →
  弹框 → 粘贴数据文件夹 → 自动重跑出图（图名由数据决定）。
- FO08 的机制由上面的真 worker 用例覆盖；登记表的升级随 HTTP 场景另做。
