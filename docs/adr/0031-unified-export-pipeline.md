# ADR 0031：统一导出管线 —— 「原图」和「画布」是同一个请求的两个 scope

状态：**Accepted**
日期：2026-08-31
相关：[0028 原图输出规格](0028-original-output-spec.md)（`scope=original` 的尺寸从哪来）、
[0029 Style / Spec / Export 三层](0029-style-spec-profiles.md)（Export 是第三层，与 Spec 无耦合）、
[0030 统一检查与问题定位](0030-validation-and-problem-navigation.md)（导出**只消费**摘要）、
[0023 落盘权威](0023-document-persistence-authority.md)（原子写的同一条纪律延伸到导出产物），
本轨道文档 [`docs/implementation/product-ux-reliability/`](../implementation/product-ux-reliability/STATUS.md)。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 「这次导出要什么」谁说了算 | **一个结构**：`ExportRequest`（`engine/exportreq.py` ↔ `lib/exportRequest.ts`）。缺省值只有一处 |
| 原图 vs 画布 | **同一个请求的两个 scope**，不是两条管线。`scope=original` 的载荷里**根本没有** x/y/w/h 与页面尺寸 |
| 被忽略的变换 | 逐项进 `ignored` 并**说给用户听**。忽略而不说等于骗人；说了而不忽略等于套用画布缩放 |
| PPI | **只在有位图格式时是数字**，否则是 `null`。`null` 与 `600` 是两个不同的答案 |
| 多格式 | **一个作业 = 一份快照**。PDF 与 PNG 物理上出自同一页 / 同一个源文件 |
| 落盘 | 临时目录 → 全部产出完成 → `os.replace` 逐个原子放到最终位置。中途断电不留半个 PDF |
| 部分失败 | `partial` 是**独立一档**。成功的照常交付，失败的那一项带自己的 `error.code` |
| 取消 | 作业级取消事件；清临时目录，最终目录一个字节没动过。**关掉对话框不取消它** |
| 覆盖 | 明确枚举 `ask` / `replace` / `rename`，默认 `ask`。撞名时**不渲染、不写盘**，先问 |
| 文件名 | 跨平台校验（按 Windows 的最严口径），**严格同源对** + golden 向量 |
| 文档修订 | 客户端在开始那一刻取的**载荷指纹**，服务端原样回传；客户端拿它与此刻一比 |
| 「留档」 | 更名为**样式检查报告**，进高级选项，格式升到 v3（+ 版本 / 时间 / 产物事实 / scope） |
| 后台线程的项目 | 显式 `bound_project(ctx)`。**不许走"落到默认项目"那条兜底** |

---

## 1. 背景：四份「导出什么」，四套默认值

改造前，「这次导出要什么」在四个地方各说一遍：

* `ExportDialog.tsx` 自己拼一份载荷；
* `app.api_export()` 从 `spec` 里逐个 `get()` 出自己的兜底；
* `codex-plugin` 的 bridge 又一份；
* `/api/package` 再一份。

四份的**默认值并不一样**（dpi 的兜底、stem 的清洗、格式的顺序），于是"同样
一张图、同样的设置"在两条入口下能出来两个不同的文件。

更要紧的是**「按原图导出」这条路根本不存在**。ADR 0028 定义了
`OriginalOutputSpec`，Prompt 09 让快速编辑跑起来了，但导出那一端仍然只有
一条画布合成：用户在快速编辑里改完一张图，点导出，拿到的是**那张图在画布上
的落位**——他把面板缩到过 40 mm，出来的图就是 40 mm 宽，字号跟着缩。

界面这一侧同样欠账。那一屏上摆着：一整行不改页面、只改 dpi 与格式的"预设"；
一格与「设置 → 规范」重复的"期刊宽"；一个把页面 / 栏位 / 字号 / DPI / 矢量
格式 / 位图格式重说一遍的大方格；一句写着 "合成走 PyMuPDF"、"Codex 插件的
tavotto_export" 的说明；一个叫"留档"的开关；三点菜单里一个"打包项目"。
普通用户要在这一屏上回答的问题只有三个——**这个文件叫什么、按哪个尺寸出、
要什么格式**——而那三件事分别排在第五、不存在、和第六位。

## 2. 一个请求，两个 scope

```text
scope: original | canvas
formats: [pdf, png]
filename: "Fig 1"            ← 基名，扩展名由管线补
ppi: 600 | null              ← null = 这次导出里它没有意义
background: white | transparent
overwrite: ask | replace | rename
validation: { policy, acknowledged[] }
include_style_check_report: bool
document_id / document_revision
canvas:   { page_w_mm, page_h_mm, objects[] }     ← scope=canvas
original: { figure_id, overrides[], w_mm, h_mm, px_w, px_h, source_kind, ignored[] }
```

**`original` 段里没有布局字段**。这不是"记得别填"——那几个键不在
`OriginalSource` 这个 dataclass 上，也不在 TS 的类型上。想让画布缩放漏进原图
导出，得先改这个结构，而改结构会当场撞上
`tests/test_export_request.py::test_original_scope_has_no_layout_fields_at_all`
与 `web/src/lib/exportRequest.test.ts` 里那条同名的用例。

`ignored` 是从 `lib/originalSpec.ignoredTransforms()` 原样带过来的清单
（固定顺序 `scale` / `crop` / `rotation` / `flip` / `opacity`），界面把它说成
一句人话：「画布上的缩放不会带进这次导出」。**忽略而不说等于骗人。**

### 两个 scope 的产出规则

| | PDF | PNG |
|---|---|---|
| `canvas` | 合成页真矢量 | **同一页**按 ppi 渲染 |
| `original`，矢量源 | 整页 `insert_pdf` 搬运（不重画，仍是真矢量） | 按 ppi 栅格化 |
| `original`，位图源 | 装进一页 PDF（`vector: false`，**不声称变成了矢量**） | **原样复制，保源像素网格** |

位图源那一格是最容易被悄悄破坏的：顺手按导出 ppi 缩一遍的话，一张 120×80 的
图会变成 750×500 的糊图，而用户点的按钮上写着"原图尺寸"。

## 3. 一个作业 = 一份快照

`prepare(spec) → validate(job) → run(job, produce) → cancel(job_id)`
（`engine/exportjob.py`）。多格式在**同一次 `produce()`** 里出：画布 scope 只
建一次 `Canvas`，PNG 是那一页渲出来的；原图 scope 只解析一次源文件（有
override 的图先由引擎全质量重渲染一次），两个格式共享它。所以 PDF 与 PNG
不可能出自两个不同的语义状态——不是"我们记得要一致"，是它们物理上同源。

`document_revision` 是**客户端**在开始那一刻取的载荷指纹，服务端原样存、原样
回传。服务端看不见前端的编辑，所以不去猜；客户端拿回执里的值与**此刻**重算的
一比，就能说出「导出期间这份文档又被改过」。

指纹取自**将要送去合成的那份载荷**，不是某个自增计数器：改个画布名、折叠个
侧栏、撤销又重做一次，导出结果一模一样，那就不该冒这句话。

## 4. 原子、部分失败、取消

渲染后端把字节写进 `<export_dir>/.tavotto-export-<job>/` 里的临时文件，全部
产出完成之后才 `atomicio.publish_file()` 逐个 `os.replace` 到最终名字上
（fsync 文件 → replace → fsync 目录，与 ADR 0023 同一套序列）。三条性质：

1. **原子**：导出中途断电 / 被杀 / 磁盘满，导出目录里不会出现半个 PDF。
2. **部分失败可见**：`partial` 是独立一档。一次请求要 PDF+PNG 而 PNG 挂了，
   PDF 照常交付，那一行 PNG 带自己的 `error.code`。把它并进 `done` 或
   `failed` 都会说谎，只是方向相反。
3. **取消不留垃圾**：`job.check_cancelled()` 在每个对象、每个格式之间问一次；
   取消时整个临时目录被删掉。上一次进程被 kill 留下的临时目录由
   `sweep_stale_tmp_dirs()` 在下一次导出时顺手扫掉（只删本模块的前缀，
   用户自己放在导出目录里的东西一个不碰）。

**关掉导出对话框不取消作业**——所以作业活在 `store/exportStore.ts` 里，不活在
一个会被卸载的组件里。进度经 SSE `export.progress` 推送，**外加一条轮询**：
SSE 是加速器不是唯一通道，浏览器演练场与断线场合必须照样拿得到终局。两条路
进的是同一个 `applyExportJob()`，晚到的旧快照按 `job_id` + 终局状态挡掉。

### 后台线程必须知道自己在为谁干活

`_request_ctx()` 的兜底是「默认项目」。兜底本身没错（watcher 与启动流程确实
该落到默认项目），错的是让一个**知道自己在为谁干活**的线程去走兜底：同时开着
两个项目时，为项目 B 起的后台导出会去项目 A 解析面板，**作业照样成功，只是
成功地导出了错的那一张图**。所以有了 `app.bound_project(ctx)`——请求上下文里
取好 ctx 带走，后台线程把自己钉在上面。

## 5. 文件名：跨平台，严格同源对

判据按**最严的平台**写（Windows），不按当前运行平台写：项目会被拷到另一台
电脑上，一个在 macOS 上导出成功的 `Fig?1.pdf` 到了 Windows 上根本创建不出来。

八条闭集原因：`empty` / `whitespace_edge` / `too_long` / `control_char` /
`illegal_char` / `dot_only` / `trailing_dot` / `reserved_name`。**顺序是判据的
一部分**——同一个名字可能同时犯两条，两侧必须报同一条。

规则有两个实现（Python 侧真正落盘，TS 侧在输入的那一刻就地提示——等一次网络
往返再说"这个名字不行"太晚了），由 `tests/golden/filename_vectors.json` 对齐，
pytest 与 vitest 各跑一遍同一份向量。

这里踩到过一个具体的坑：**`str.strip()` 与 `String.trim()` 认的空白字符集不
一样**（U+FEFF 只有 JS 认，`\x1c`–`\x1f` 只有 Python 认）。靠各自语言的内建
函数，两侧对 `"﻿Fig"` 会给出不同答案。所以首尾空白的判定集合是**写死的
一份**，两侧逐字相同，向量里专门有几条为它留的用例。

覆盖策略同样是闭集：`ask`（默认）撞名时**不渲染、不写盘**，回一个 conflict 与
撞名清单，界面给「覆盖」与「另存一份」两条明确出路。静默覆盖用户上一次的
成果是不可逆的。

**旧契约一个字节不变**：没有 `filename` 的请求（`stem` + `dpi`，或更老的
`items[]`+`texts[]`）由 `normalize()` 抬成同一个作业，文件名照旧带时间戳后缀，
回执里照旧有 `files[]` / `export_dir` / `warnings`。老标签页、CI 脚本与
`codex-plugin` 一行不用改。

## 6. 界面：三个问题排在前三位

```text
文件名 → 输出范围 → 格式 → 分辨率（仅位图）→ 规范 → 检查 → 高级选项
```

删掉的（逐项）：预设整行、重复的期刊宽、profile 的 id 与版本号、页面 / 栏位 /
DPI / 格式的大方格、PyMuPDF 与 Codex 插件的实现说明、行内的内部对象标签、
三点菜单里的"打包项目"（**搬到文档菜单**，与"导入项目包"并排——移走不等于
砍掉）、"留档"这个标签、`_时间戳` 后缀。

改的：文件名到最上方并**在输入的那一刻**校验；「课题组出版规范 v1」显示为
「默认规范」；`proof report` → **样式检查报告**，进高级选项；格式卡片只留
「矢量 / 位图」；PPI 只在选了位图时出现；scope 默认跟当前工作流走、用户随时
切；**原图不可用时说出原因，不隐藏选项、不静默改成画布**——一个消失的按钮
无法解释自己，而一次悄悄换掉的范围会让用户拿到一张他没要的图。

检查那一行**只给数量 + 「查看问题」**（ADR 0030 那条链的消费端）：完整清单、
筛选与修复都在左侧问题面板，这里不做第二套。

## 7. 样式检查报告（v3）

只在用户显式开启时生成。前端给"检查结果"那半份（求值发生在前端，ADR 0030），
服务端补上只有它知道的那半份：Tavotto 版本、导出时间、真正落盘的产物名与
尺寸、这次的 scope 与被忽略的变换。

**报告失败不牵连成图**：它自己的失败只算它自己的（作业进 `partial`，图文件
照常在），但必须清楚说明。文件名 `<基名>_style-check.json`；旧契约的那条路
仍写 `<stem>_<时间戳>_proof.json`（`kind` 保持 `tavotto-proof`，机器身份不变）。

不写进报告的：绝对路径、科研数据、图中文字、Agent 信息。

## 8. 被否掉的选项

**「原图导出 = 画布导出 + 一个缩放系数」**：那正好是要挡的东西。原图导出的
尺寸来自 `OriginalOutputSpec`（四档来源，ADR 0028），画布上的落位与它无关；
把两者用一个系数连起来，等于承认"原图"是"画布"的一个特例，而它不是。

**保留时间戳后缀、不做覆盖策略**：时间戳让文件天生撞不了车，代价是用户永远
拿不到自己起的那个名字，导出目录里堆着 `Fig1_0831_143512.pdf`。新路径给出
用户输入的名字 + 明确的覆盖枚举；旧路径保留时间戳（那条路上没人要求过名字）。

**全部产出要么一起成功要么一起丢弃**：PNG 挂了就把已经渲好的 PDF 也扔掉，
比"部分成功"更坏——用户什么都拿不到，而失败的原因只跟其中一个格式有关。

**只靠 SSE 推进度**：SSE 没连上（浏览器演练场、断线、代理）时导出会永远显示
"进行中"。所以轮询是主路，SSE 是加速器。
