# ADR 0097：功能登记表——用户可见的功能不能被后面的修改悄悄关掉

日期：2026-09-26 · 状态：**Accepted**（方案由用户批准；实现随本 PR）
相关：[0043 插件发行通道](0043-plugin-stable-channel.md)（CI 里「现建现验」的先例）、
`docs/rules/ci/gate-discipline.md`（反证与空转门禁）、`docs/rules/repo/predicate-subject.md`（判据的主语）

## 问题

用户问：「如何避免后面的修改把前面做好的给关了？」现状回答不了：

- 前端五千多条 vitest 跑在 jsdom 里，量不到几何、遮挡、视口、条件分支——功能被挤出视口、
  被浮层挡住、被一个 `if` 跳过，单测照样绿；
- 没有一份「用户可见功能」的清单，一个功能被关掉时没有任何东西会注意到它**不见了**；
- 各 PR 在自己的分支上绿，合并态没有整体验过「那些功能还在」；历史上有过压缩合并把别人的
  功能静默反做、CI 全绿的事故（#522）。

## 裁决

### 一、一份机器可读的登记表，是「有哪些功能」的唯一出处

`docs/features/registry.json`。每条一个用户可见的能力：id、中英一句话、入口、平台
（`browser` / `desktop` / `both`）、状态（`active` / `removed`）、覆盖它的 e2e（`{spec, title}`，
按 Playwright 的标题路径精确相等）、桌面壳里要手动过的步骤。JSON 而不是 YAML：判据脚本
纯标准库、CI 的 frontend job 只有系统 python3。

### 二、每个 active 功能至少一条真浏览器 e2e，且它不许被跳过

真浏览器（Playwright，沿用 `web/e2e/fixtures.ts`），断言用户看得见的结果：可见且在视口内、
点得到、操作后画面 / 引擎产物 / 落盘文件真的变了。只在桌面壳里存在的功能（原生文件夹选择框、
关窗三选一、Finder 定位、更新、内置渲染环境、Codex 集成）浏览器够不着，登记 `platform: desktop`
并写手动步骤，由 `feature_registry.py beta-checklist` 生成 beta 手动试用清单。

跳过的三种形态都判：声明期（`test.skip(title, fn)` → `--list` 里 expectedStatus = skipped）、
运行期（`test.skip(cond)` 等，读源码 AST——子串会被注释和字符串满足）、以及静态看不见的
（从别的模块 import 进来的辅助函数里 skip）——最后一种由合并态真跑的报告兜：登记的用例在那次
运行里是 skipped 或根本不在，判红。

### 三、判据分两处，都在已有的 Gate 闭集里

- 静态（`check` + `transitions`）在 `frontend` job：PR 与 merge_group 都跑，几秒钟，CI fast gate 管；
- 真跑（`verify-run`）在 `posix-e2e` 的 e2e 之后：merge_group（合并态）与 full-ci 上跑，CI integration
  gate 管。

没有新开 job、没有新的 required context：新 required context 必须先有产出它的 job 在 main 上跑出过
结论才能登记，否则锁死仓库（required-checks-ordering）；放进已有 job 的步骤，第一次执行就是本 PR
自己的合并组，红了只踢本 PR。代价：功能登记表的结论不单独显示成一个 check 名，要点进
frontend / posix-e2e 看步骤——可接受，Gate 的红绿是同一个。

### 四、功能下线是一个显式动作，不是一次删除

条目不许从登记表里消失（`transitions` 与 base 比）；下线 = 改 `removed` + 原因 / 日期 / 批准人
+ PR 打 `feature:removal` 标签。「降级」按下线处理：旧 id 改 removed，变弱后的能力另起一个 id。
merge_group 事件没有 PR 标签，只判「不许消失」，标签在 PR 上判过。

### 五、标签：新用例必须带，旧用例不强制

`@feature:<id>` 标签让「这条用例在守一个功能」在 spec 里看得见，并支撑反向判据（带标签却
没登记 = 孤儿）。已登记的旧用例不强制补标签：`golden-paths` / `tutorial` / `i18n` 等 spec 正被
在飞 PR 改动，改它们的声明行只会制造冲突；登记表那一侧已经能判出它们被删 / 改名 / 跳过。

## 不做

- 不改仓库的 required checks 设置（见三）。
- 不按「登记表以外的所有 e2e」加门禁：登记表只对用户可见功能负责，其余 e2e 照旧。
- 不在登记表里做覆盖率统计或功能树：一条功能一行，粒度到「用户会说『这个坏了』」的那一层。

## 已知盲点

- 功能 e2e 本身写弱了（只看 DOM）登记表判不出——靠评审与本 ADR 二的写法约束。
- `platform: desktop` 的功能只有手动清单，没有自动判据；桌面壳自动化另议。
- 一个功能被改坏但它的 e2e 恰好没量到那一维，照样绿——登记表守的是「功能还在、还被量」，
  不是「量得全」。
