# ADR 0027：素材接入就绪度 —— 主语固定的事实模型

状态：**Accepted**
日期：2026-08-29
相关：[0025 项目刷新的唯一后端入口](0025-unified-project-refresh.md)（就绪度是它的**读者**，不是第二个刷新器）、
[0026 项目级文件 watcher](0026-project-file-watcher.md)（「变了没有」的判据分辨率两边同级）、
[0013 Runtime Figure 素材](0013-runtime-figure-assets.md)（那条 id 空间**不在**本模型覆盖范围内）、
[0014 安全的 native 执行档](0014-safe-native-execution-profiles.md)（「不静默执行用户脚本」的出处）、
本轨道文档 [`docs/implementation/product-ux-reliability/`](../implementation/product-ux-reliability/STATUS.md)。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 住哪 | 新模块 `engine/readiness.py`（纯标准库，Flask 父进程 import） |
| 主语 | **`/api/panels` 的那一个素材 id**，逐字相同；不是 stem，不是脚本 |
| 状态集合 | 六个互斥值：`editable` / `auto_linkable` / `needs_probe` / `conflict` / `source_missing` / `layout_only`。**闭集，别处不许另起同义状态** |
| 说明 | 稳定 `reason_code`（十个），前端按 code 查自己的语言；后端不返回翻译好的句子 |
| 事实来源 | 只**组合**已有的三份：内存里的注册表、`discover` 静态报告、文件存在性 + 目录可写性。**不新增解析能力** |
| 裁决优先级 | 注册表 > 静态报告（注册表文件就是人工裁决的落处） |
| 冲突 | **绝不自动裁决**——不看文件名相似度、不看 mtime、不看谁"更像新版本" |
| fingerprint | **报告自身**的内容哈希（规范化 JSON、键排序、SHA-256 前 32 位），不是输入的哈希 |
| 缓存 | 项目级、挂在 `RefreshState.readiness`；键是输入的内容签名；刷新在事实真的动了之后额外清一次 |
| 失败的处置 | 静态扫描挂了 → `layout_only` + `source_scan_unavailable` + `conflicts: null`，**这一份不进缓存** |
| 它做什么 | **只报告**。`can_probe` / `can_manual_link` / `can_rescan` 只表示"界面可以提供这个动作" |
| 它不做什么 | 不执行用户脚本、不 probe、不写盘、不改注册表、不发 SSE、不返回可执行命令、不返回绝对路径 |

---

## 1. 背景：三个答案，三个主语

「这张图能不能进图内编辑」在本 ADR 之前由三处各答一次：

| 出处 | 主语 | 它实际回答的问题 |
|---|---|---|
| `/api/panels` 给不给 `script` | **素材** | 注册表映射了这个 stem 没有 |
| `GET /api/registry` 的 `candidates` | **stem** | 静态扫描里谁认领了它 |
| `probe.script_inventory()` 的 `reason` | **脚本** | 这个 `.py` 处于什么状态 |

三句话单独看都对。合起来却没有一句回答了用户的问题——同一张图可以同时是
「不可编辑」（素材面板）、「有候选脚本」（注册表对话框）和「可试运行」
（脚本清单）。差别不在实现质量，在**主语**：三份判据量的是三种不同的对象，
而界面必须对着用户指的那一个东西说一句话。

不定主语的话，说那句话的责任就落到前端，而前端手里只有其中一份事实。

## 2. 裁决：把主语钉死在素材 id 上

`readiness.compute(ctx)` 的输出以 `/api/panels` 的 id 为唯一主键（**逐字
相同**，Windows 上同样是反斜杠），每个 id 恰好落在一个状态上：

| # | 条件 | status | reason_code |
| ---: | --- | --- | --- |
| 1 | 注册表映射了这个 stem，脚本文件**在** | `editable` | `registered_source` |
| 2 | 注册表映射了，脚本文件**不在** | `source_missing` | `registered_script_missing` |
| 3 | 这一轮静态扫描**没跑成** | `layout_only` | `source_scan_unavailable` |
| 4 | **多个**脚本认领同一个 stem | `conflict` | `multiple_source_candidates` |
| 5 | **恰好一个**脚本认领 | `auto_linkable` | 见 §3 |
| 6 | 项目里有产图但输出名要跑才知道的脚本 | `needs_probe` | `runtime_output_unknown` |
| 7 | 其余 | `layout_only` | `no_source_candidate` |

判定表的机器可读版本是 `REASONS_BY_STATUS`，用例逐条对着它断言——判定分支
以后还会长，那条断言挡的是「悄悄冒出一个前端没见过的组合」：后端全绿，而
用户看到的是一串英文 key。

**同一个 stem 的多份素材共享一条来源关系**（`Fig1.pdf` 与另一个目录下的
`Fig1.png`）：算一次，两个 panel 各计一次数。

`/api/panels` 每项挂的 `capability` 是**同一次计算的投影**，不是第二次判定。
两处各算一遍的话，「素材面板说可编辑、就绪度面板说要试运行」只是时间问题。

## 3. `auto_linkable` 的四个 reason 说的是"卡在哪一步"

可写的正常项目里这一档是**过渡态**（下一次统一刷新就变成 `editable`），
但它必须存在——只读项目和写失败的项目会**长期**停在这里，而那时报
`layout_only` 等于告诉用户"这张图没有源脚本"，那是错的。

优先级从「刷多少次都没用」往「下一次刷新就好了」排：

```text
registry_invalid  >  project_read_only  >  registry_write_failed  >  static_unique_candidate
```

`registry_write_failed` 需要一个新的事实位（`RefreshState.registry_write_failed`）：
静态合并**写**注册表失败时置位、成功时清零。**对外的 `scan_failed` code 没有
改**——那是老 `/api/registry/scan` 的契约，换名字等于让装着旧前端的用户看到
一句英文 key；区分只留在状态里给就绪度用。

## 4. 注册表优先于静态报告

一个 stem 在注册表里有映射时，就绪度**不看**静态报告怎么说。

理由在 `src/tavotto/AGENTS.md` 里已经写了十几个月：「一脚本多产物 / 归属有
歧义的 stem，裁决结果记在各图库自己的注册表文件里，**勿改**」。注册表文件
就是人工裁决的落处。让静态报告推翻它，等于每刷新一次就把用户的决定重新掀
一遍，而用户看到的现象是"我明明指定过了，它每次都又问我一遍"。

静态冲突照旧出现在项目级 `conflicts` 里，带上 `resolved_by`（裁决人）。

`source_missing` 仍然给出候选（注册表指着的脚本没了、另一份此刻正好认领同一
个 stem——改名/重构最常见的形状）：状态照旧是 `source_missing`，但用户手里要
有一条可执行的出路，而不是只有一句"它不见了"。

## 5. 冲突绝不自动裁决

同一个 stem 被两个脚本认领时，`conflict` 是终点。**不看**文件名相似度、
**不看** mtime、**不看**谁的名字更像"新版本"。

猜对九次的代价是第十次静默地把用户的图接到错的脚本上，而那一次没有任何信号
——用户按下"编辑"，出来的是另一张图。裁决走用户显式的手工登记
（`PUT /api/registry`）或试运行（`/api/registry/probe` 按**真实产出**登记）。

## 6. fingerprint 哈希的是输出，不是输入

需求里那四条（`generated_at` 不能进、无关文件 mtime 不能进、绝对路径不能进、
dict 顺序不能进）如果按"哈希输入"来做，就要逐条去防，而每加一个输入维度都得
重新审一遍这四条。

按"哈希输出"做，四条**自动成立**：`generated_at` 不在 body 里所以进不来；
素材和脚本的 mtime 没有进报告，变了它不动；绝对路径本来就一个都不在报告里；
键序由 `sort_keys` 排掉。反过来，任何一个会被用户看见的事实变了它必然变
——因为那个事实就在被哈希的字节里。

代价是输入变了而输出没变时会白算一遍（缓存键另算，**它**才是按输入的）。
这正是要的：前端据 fingerprint 判"要不要重画"，白算一遍它不该看见。

## 7. 「没测量」不是「测量结果是零」

静态扫描失败（目录读不动）时**不报** `no_source_candidate`——那是一句错的
断言，而错的断言比"我还不知道"更糟：它看起来像结论。

处置是三档而不是两档：

| 字段 | `null` / 缺席 | 具体值 |
|---|---|---|
| `conflicts` | 这一轮没跑静态扫描 | `[]` = 扫过了、没有冲突 |
| `project.registry_valid` | 项目里根本没有注册表文件（还没起草过） | `false` = 有、但读不回来 |
| `PanelInfo.capability` | 这一轮还不知道（扫描与素材遍历之间新出现的素材） | 有值 = 算过了 |

状态仍给 `layout_only`，因为那是此刻唯一还成立的**能力**陈述（这张图还能
缩放、裁剪、对齐、标注、导出）；reason code 负责说清"我们这一轮没看见"。

**这一份报告不进缓存。** 缓存一次失败等于让一次瞬时的目录读错误把就绪度
永久钉死，而用户看到的现象是"我修好了它还是说不行"。

## 8. 缓存：键是内容签名，刷新是第二道判据

两层，键都是**输入**的内容签名：

* 贵的那层是 `discover.discover()`（逐脚本 `ast.parse`），键是候选脚本集合 +
  各自的 `(size, mtime_ns)`；
* 外层是整份报告，键还含注册表内容、素材集合、目录可写性、磁盘注册表合法性、
  上一次写失败与否。

**分辨率与 watcher 同级**（ADR 0026 的两维签名），刻意不在就绪度这一侧单独
收紧成内容哈希：收紧一侧只会让两个模块对"变了没有"给出不同答案，而 watcher
发现不了的改动根本不会触发刷新。

统一刷新在**确认事实真的动了之后**额外把缓存清一次。这是第二道判据而不是
唯一那道——签名盖不住「同尺寸 + 同一个 mtime_ns 刻度里的就地改写」，而那正是
刷新自己写注册表时最容易撞上的形状。无差异的刷新一句都不动（不变式：
无差异 = 零事件、零写盘、零 worker 失效、零缓存失效）。

依赖方向是 `readiness → project_refresh`；反过来 `project_refresh` **不**
import `readiness`（会成环），它清缓存的方式是把 `RefreshState.readiness`
置回 `None`，那个槽位的形状归 readiness 自己管。

**进出都深拷贝**：缓存里那份是唯一权威。共享一个可变 dict 出去的话，某个
消费者往 `candidates` 里 append 一下，之后每一次请求都会带着那条脏数据
——而它看起来完全像是后端算出来的。

## 9. 只报告，不动手

`can_probe`（手里有具体候选）、`can_manual_link`（项目可写）、`can_rescan`
（项目级）都只表示"**界面**可以提供这个动作"。执行仍分别归
`POST /api/registry/probe`、`PUT /api/registry`、`POST /api/project/refresh`。

就绪度**不返回任何可执行的 shell 命令**，也不返回绝对路径——路径是本机信息，
诊断细节走既有的 `/api/diagnostics`，不从这条只读通道漏出用户的目录结构。
注册表校验失败时 issue 的 params 只给文件名，不给异常原文（那里面带着
`Registry.load_data` 的 `source`，也就是绝对路径）。

边界靠依赖方向守着，不靠注释：模块只 import `discover` / `registry` /
`project_refresh` 三个纯读模块，连 `engine/probe.py` 和 `engine/pool.py` 都
不 import。用例的证据是两层——磁盘上的 CANARY（脚本真跑起来会写下 `RAN.txt`）
加上 probe / worker 池入口全部换成会炸的桩。只有后者的话，"没运行"证明的是
"我们桩住的那几个入口没被调用"，绕开它们的第五条路照样能跑起脚本。

## 10. 不覆盖 runtime figure 素材

ADR 0013 的 runtime 素材（注册表里磁盘无原件的 `(script, stem)`，id 带
`runtime:` 前缀）**不在**本模型里：它们按定义就有脚本，而且 id 空间不同，
混进来会破坏「id 与 `PanelInfo.id` 逐字相同」这条——那正是本 ADR 存在的理由
（§1 的三个主语）。要给它们做状态显示的话，走 `/api/runtime/assets` 那条线，
不要把两个 id 空间揉进同一个数组。
