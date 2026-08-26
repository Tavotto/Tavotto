# ADR 0016：诊断 V2 —— 前端状态快照与交互轨迹

日期：2026-08-26 · 状态：已接受 · 关联：ADR 0008 / 0010 / 0013 · 触发：issue #131

## 背景

### 现在的诊断包能看到什么

`engine/diagnostics.py` 出的一键诊断包（`GET /api/diagnostics/bundle`）里有
三个文件：`report.json`（版本 / 系统与编码 / 安装方式 / 数据目录 / 渲染解释器 +
matplotlib / 内置 runtime 实测 import / 编码 Agent 探测 / 遥测开关 / 项目概况 /
最近错误行）、`app.log`（尾部 400 行）、`config.json`（密钥已抹）。

它把「环境类」故障从「来回问十次」压到「用户点一下发过来」：装的哪个 Python、
matplotlib 在不在、内置 runtime 是不是被杀毒软件挖走了一个 `.pyd`、worker
起不起得来、项目目录可不可写、端口是多少。这一类问题它答得很好，**这一层
一个字都不改**。

### 看不到什么

issue #131 是这样报上来的：「多选图内文本元素点击左对齐后，画面已经调整的
布局丢失、错乱，撤回也回不到正确位置」，复现步骤「未知」，**并且附了一个
诊断包**。那个包里没有一个字节能回答下面任何一个问题：

* 用户点左对齐的**那一刻**，选中的是哪几个 gid？
* 对齐用的 bbox 是从**哪一版渲染**量出来的？
* 那一版和文档当时的 override 组合是**同一个变体**吗？
* 这次对齐写了几条 patch？进的是哪个事务？
* 撤销的时候 past/future 各有多少条，document 哈希变了没有？
* 那一刻有没有一个 preview session 还挂着？

`report.json` 是一张**环境的静态照片**。#131 这一类是**前端状态机的时序
问题**——竞态、事务混合、几何权威过期、preview 残留。静态照片对它零信息量。

### 这一类问题为什么特别难猜

`renderStore` 按「文件 + 变体」分键（`renderKeyOf(panel) = fileId + overrides`），
而 `panelRender()` 在「自己那份变体还没画出来」时**退回该文件最近画好的那份**
（`latest` 表）——这条退路是对的，没有它每敲一个字画布都会闪回磁盘原图。
代价是：**属性页读到的 manifest 可能来自另一个变体**。

于是同一个面板身上同时存在三个变体身份：

| 名字 | 含义 | 出处 |
| --- | --- | --- |
| document variant | 文档说这个面板现在是什么 | `renderKeyOf(panel)` |
| display variant | 画布上此刻挂着的那一版 SVG | `activeRenderKey(state, panel)` |
| authority variant | **量 bbox/anchor 的那份 manifest 来自哪一版** | `manifestSourceKey(state, panel)` |

三者不一致在 render pending 期间是**合法**的常态。真正危险的只有一种组合：
**在 authority ≠ document 的时刻执行几何写入**——量的是 A 的几何，写进的是
B 的文档。对齐正是这样一个操作：它把「别的元素现在在哪」当成输入，算出一批
`pos_frac` / `position` 写回去。用 A 的坐标去摆 B 的版面，结果就是
#131 描述的「布局丢失、错乱」，而且**它写进了历史**，所以撤销回到的是
「错乱之前」而不是「用户以为的上一步」。

## 决定

把诊断体系从

    Environment Snapshot + backend logs

升级为

    Environment Snapshot + Frontend State Snapshot + Interaction Trace + Invariant Violations

并且**同一套判据既用于诊断也用于运行时护栏**：`geometry_authority_mismatch`
不只是事后记一笔，它当场**阻止这次几何写入**。

### 1. 诊断包结构（bundle schema 2）

```
tavotto-diagnostics-YYYYMMDD-HHMMSS.zip
├── report.json              环境快照（**一个字段都没动**）
├── app.log                  后端日志尾部（**没动**）
├── config.json              用户配置，密钥已抹（**没动**）
├── frontend-state.json      前端状态快照（新增，schema 1）
├── interaction-trace.jsonl  最近的结构化交互事件，一行一条（新增，schema 1）
├── manifest.json            诊断包**自身**的元数据（新增，schema 2）
└── README.txt               采集内容说明（双语，重写）
```

`manifest.json` 的存在理由：以后诊断格式再升级时，读包的人**不该靠 Tavotto
版本号去猜 schema**。它自报 `schema_version` / 三个子 schema 版本 /
带没带前端数据 / 事件条数 / 有没有被截断。

**兼容不是可选项**：老三件的文件名、位置、内容语义一律不变，
`GET /api/diagnostics/bundle` 继续可用（出的包 `contains_frontend_state:
false`）。任何只认老三件的脚本、任何 0.11.0 时代发出去的包，读法都不变。

### 2. `web/src/diagnostics/` 是前端诊断的唯一模块

诊断逻辑**不许散落**在 renderStore / documentStore / 组件里。新模块：

```
web/src/diagnostics/
    types.ts        事件的可辨识联合 + 快照类型 + 三个 schema 版本常量
    hash.ts         diagnosticHash 与带前缀的类别化 hash
    sanitize.ts     字段级 allowlist 序列化器（**唯一的出口**）
    store.ts        定长环形缓冲 + recordDiagnosticEvent
    snapshot.ts     buildFrontendDiagnosticSnapshot()
    authority.ts    几何权威不变式（诊断 + 护栏同一份）
    wiring.ts       对业务 store 的**只读订阅**（选择变化、面板变体采样）
    index.ts        对外只暴露这些
```

三条纪律：

* **只观察，不当真源**。诊断 store 不参与任何业务判断，业务代码永远不从
  它读状态。
* **自己出错不许伤到编辑**。`recordDiagnosticEvent` 整体包在 try/catch 里，
  任何异常吞掉；导出失败只影响导出。
* **不是 source of truth**。快照是**读**业务 store 得来的，不是诊断自己
  维护的一份影子状态——影子状态会漂移，而漂移的诊断比没有诊断更坏。

### 3. 事件是可辨识联合，不是 `console.log`

```ts
type DiagnosticEvent =
  | AlignRequestEvent | AlignBlockedEvent | AlignCommitEvent | AlignNoopEvent
  | RenderRequestEvent | RenderSuccessEvent | ...
```

每种事件**只允许自己 schema 里的字段**。这是隐私的**类型层防线**：

```ts
recordDiagnosticEvent({ type: 'align.commit', text: element.label })
//                                            ^^^^ TS 直接报错
```

有人哪天顺手想把图内文字带进事件，编译期就红，不用等到代码评审。

### 4. 序列化是 allowlist，不是 denylist

**禁止** `JSON.stringify(store.getState())` 再删敏感字段这条路。Tavotto 后续
每加一个功能，都可能往 store 里塞进论文标题、annotation 文本或文件路径；
denylist 的失效方式是**静默**的。

`sanitize.ts` 给每种事件一张字段表，每个字段一个 kind（`int` / `num` /
`bool` / `enum` / `hash` / `gid` / `geom` / `count`）。**只有表里有、且值过得了
kind 检查的字段才进诊断包**，其余整条丢掉——包括嵌套对象里的。未知事件类型
整条丢掉。

`gid` 这一 kind 额外钉死字符集 `^[A-Za-z0-9_.:-]{1,64}$`：gid 现在是结构性的
（`axes_0.title` / `axes_0.xticklabels_3`），但**判据不能建立在「现在恰好是」
上面**——哪天 gid 规则变了、混进用户文字，这条正则当场把它换成 hash。

### 5. hash 系统：不知道内容也能判断「是不是同一个状态」

`diagnosticHash(value)` —— 非加密、同步、无依赖（FNV-1a 变体的双 32 位折叠，
输出 12 hex）。类别化封装给出带前缀的身份：

```
doc:81af27cc9d10   panel:411b9e10a2c3   file:039e2d11...   var:22ab481c...
prev:...（preview session）    ver:...（layout version）
```

**加一层每会话随机 salt**。要求里只写了「同一次会话中稳定」，salt 完全满足，
并且额外买到两件事：① 12 hex 的非加密 hash 对**已知候选**是可暴力的，
salt 让「拿一本常见路径字典去撞用户的 file hash」这条路失效；② 两份不同
会话的诊断包无法靠 hash 相互关联，跨包画像不成立。

**fileId 可能带路径，绝不许原样进 trace**——只进 hash。这不是「记得别写」，
是 sanitize 层的 `hash` kind 强制的：值不匹配 `^[a-z_]+:[0-9a-f]{8,16}$`
就整条丢掉。

document hash **只在真正的状态边界算**（commit / undo / redo / 版本恢复），
而且算的是**规范化摘要**（对象 id/type/几何/override 的 gid+prop+值折叠），
不是把整个文档 JSON.stringify 一遍。拖动途中一次都不算。

### 6. 几何权威不变式：诊断 + 护栏同一份判据

```ts
verifyGeometryAuthority({ operation, panelId, authority })
```

`authority` 是**测量那一刻**的 `manifestSourceKey`，由调用方在算 bbox 的同一
个渲染周期里捕获后传进来——**不能在写入时再推导一次**。理由是「判据的主语」：
问题是「我量的那份几何来自哪一版」，不是「此刻 manifest 来自哪一版」；两者
之间隔着用户从看到界面到按下按钮的那段时间，重新推导会让 TOCTOU 窗口内的
不一致刚好检查不出来。

`authority !== renderKeyOf(现取的 panel)` 时：

1. **阻断这次写入**（文档不动、历史不动）；
2. 记 `invariant.violation{kind: 'geometry_authority_mismatch'}` +
   `align.blocked{reason: 'authority_stale'}`；
3. 走**现有**同步机制补一次定稿渲染（`flushRender`）并给一句状态提示；
4. 开发构建额外 `console.error`，**生产不崩**。

**阻断范围刻意收窄，避免大量误报**：

| 操作 | 判据 | 处置 |
| --- | --- | --- |
| 对齐 / 分布 / 等宽等高 / 成组缩放 | 输入是**别的元素的 manifest bbox**，用户看不见这些数字 | **阻断** |
| 拖动元素 / 拖动子图 / 拖端点 | 输入是用户的指针 + 画布上**看得见**的那版 SVG，基准优先取文档里已有的 override | **只记录，不阻断** |

区别是原则性的：对齐是「拿一组用户没看到的坐标去摆版面」，几何过期就是
静默的错误结果；拖动是「用户对着自己眼前的像素拖」，操作与所见自洽，中途
弹一个拒绝反而是伤害。`document_display_variant_diverged` 同理，只作为
info 事件，**render pending 期间它是合法状态，不是 bug**。

### 7. 纯内存、定长、用户主动导出才落盘

环形缓冲 **240 条**（区间 150–300 的中位），满了丢最旧的。事件在**写入那一刻
就已经脱敏**——缓冲区里物理上不存在未脱敏的数据，不是「导出时再洗一遍」。

**不写磁盘、不自动上传、不进 telemetry**。Diagnostics 与 telemetry 是两件事：
telemetry 是同意后自动发的匿名事件（白名单在 `engine/telemetry.py`），
diagnostics 只在用户点「导出诊断包」时才出现在一个 zip 里，然后由用户自己
决定发不发。本轮**不加**任何自动上传、崩溃回传或云端 replay。

浏览器崩掉时纯内存 trace 会丢——这是**自觉的取舍**：为了不丢那一次，代价是
「软件一直在往磁盘记我的编辑」，那个代价换不回来。

### 8. 前后端桥：POST 一次，后端再验一次

```
POST /api/diagnostics/bundle
  { "frontend_state": {...}, "interaction_trace": [...] }
  → zip
```

老的 `GET` 原样保留。前端已经脱敏过一遍，后端**再脱敏、再校验一遍**——
不是不信任自己的前端，而是这个端点接受的是**请求体**，而「结构性防线」
的意思就是「就算调用方把整条路径塞进来也走不出这一步」（与
`/api/telemetry/event` 同一套理由）。

硬上限（超出**截断而不是失败**，并在 manifest 里记 `truncated: true`）：

| 项 | 上限 |
| --- | --- |
| 请求体 | 512 KB |
| 事件条数 | 300 |
| 单个字符串字段 | 128 字符 |
| geometry 数组 | 64 条 / 每条 ≤ 8 个数 |
| 递归深度 | 6 |

### 9. 数据分类（唯一一张表）

**默认允许**：Tavotto 版本、OS、runtime 版本、matplotlib 版本、事件类型、
时间戳与时长、各种计数、布尔、**内部操作键**（`align.left` 这种我们自己写死的
标识，不是文案）、技术 gid、数值 bbox / anchor / position、hash 化的 id、
渲染状态、历史长度、**patch 的属性名**（`fontsize` / `pos_frac`）。

**默认禁止**：图内文字正文（title / xlabel / ylabel / legend / annotation）、
用户文件名、项目名、完整路径、用户名、Home 路径、Python 脚本、SVG、PNG/PDF、
数据数组、dataframe、AI prompt、API Key、Token、环境变量值、剪贴板、终端内容。

patch **只记 `{gid, prop}` 或 `{domain, prop}`，永远不记 `value`**——
value 装的就是用户的内容。

路径继续走**既有**的后端脱敏器（`_redact_text` / `_redact_obj`），
新增字段不许绕过它。

## 后果

* 下次 #131 这样的 issue，诊断包里会直接出现「align.request → align.blocked
  reason=authority_stale」或「align.commit 且 authority≠document +
  invariant.violation」，开发者不用猜。
* 危险的几何写入**当场被拦住**，这一类 bug 在被诊断之前先被防住了一半。
* 前端多了一个必须维护的 schema。代价用三件事对冲：类型层的可辨识联合、
  allowlist 序列化器、以及一条端到端隐私回归用例（把醒目的秘密串塞进
  项目里，导出后对 zip 全文搜索，断言 0 次出现）。
* 读诊断包的人要知道：**未知字段一律忽略**。三个 schema 版本号都会独立演进。

## 与 ADR 0017 的关系（2026-08-27 收编）

ADR 0017（issue #131 的战术修复）先落地，它带了一个 100 条的小追踪环
`lib/authorityTrace.ts` 与 `assertGeometryAuthority`。两份守卫并存违反单一
权威原则，因此本轮**收编**：

* `authorityTrace.ts` 删除，`shortHash` → `diagnosticHash`（`PanelView` 的
  `data-display-key` 跟着换），它的隐私用例迁进 `diagnostics/privacy.test.ts`；
* `alignAction.ts` 的 `traceGeometry` 换成本模块的结构化事件（多了三个变体
  身份、选中数、输入/输出几何、patch 数）；
* **权威判据以 ADR 0017 的 `exactPanelRender` 为准**，本 ADR 原先设想的
  「捕获 manifestSourceKey 再传参」整套作废——`exactPanelRender` 更严
  （还要求 `lastPatches` 逐字相等且未被 markStale），而且判据留在写路径上比
  在调用方之间传递更难出错。于是 `authority_variant` 只有两种取值：
  **当前这一版，或 null**。

## 不做什么

云端上传、自动崩溃回传、完整 session replay、把 trace 落盘做 crash recovery、
把诊断事件接进 PostHog/Sentry。这些各自是独立的产品决定，不该搭本轮的车。
