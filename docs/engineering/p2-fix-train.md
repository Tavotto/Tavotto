# P2 Fix Train：按根因分组修，不按发现顺序修

> 按发现顺序逐条修，会得到一串互相无关的小 PR，每一条都要过一遍完整矩阵，
> 而**同一个根因的第二个消费点必然被漏掉**——本仓库已经连着撞到三次
> （`CLAUDE.md` 的「判据的主语」一节，以及 memory 里的
> 「共享判据修一处不算修完」）。
>
> Fix train 的核心是：**一个 PR 只处理一个根因家族，并把这个家族的所有
> 消费点一次扫完。**

---

## 1. 规则

1. P2 **不按发现顺序**逐条修；
2. 按**根因 / 不变式**分组；
3. **一个 fix train PR 只处理一个根因家族**；
4. fix train 中**不新增功能**；
5. **不扩大公开支持面**（不新增平台、不新增 artist family、不新增入口）；
6. 每个问题**先独立复现**；
7. **先写失败测试**，看着它红；
8. 实现修复；
9. **sweep 同形状的消费者**——这是 fix train 存在的理由；
10. 做 **regression proof**：把修复拿掉，确认测试真的变红；
11. 运行目标测试；
12. **最后**再运行完整矩阵。

第 9 步与第 10 步是这份流程里唯二不能省的。省掉 9，下一次同形状的 bug 会
在另一个消费点冒出来；省掉 10，你加的是一道空门禁
（`docs/1.0-release-readiness.md` §3 有全文）。

---

## 2. 根因家族

| 家族 | 它回答的问题 | 已知成员 |
|---|---|---|
| `state-restore` | getter 回的形式，setter 还原得回去吗 | §3.5「真正的原样有时是一个**模式**」 |
| `shared-state` | 一处改动会广播到几个 artist | alias / originals / dirty group / replay（§4.2） |
| `capability-truthfulness` | 宣称的能力，画面真的会变吗 | Collection 族（§4.6）、`honours_stroke_style`（§4.3） |
| `axes-traversal` | 遍历 axes 的权威在哪一处 | 多宿主色条（§4.8） |
| `platform-encoding` | 编码、路径分隔符、文件占用 | `test_windows_regressions.py` 全体 |
| `worker-lifecycle` | 进程状态与管道 | #58 未读管道、#46 一次性 worker 目录泄漏 |
| `release-harness-contract` | 门禁真的执行了吗 | #54 空转的 slow、#55 从没跑过的升级验收、#61 汇总自己挂掉 |
| `distribution-artifact-contract` | 下游怎么知道产物叫什么 | #63 SBOM glob、artifact manifest |
| `support-claim-drift` | 文档说的和代码做的 | #39、支持矩阵 |

**新发现先归家族，再排期。** 归不进任何家族的，要么是新家族（写进这张表），
要么说明它还没被理解透。

---

## 3. 优先级

### A. v1.0 之前

- `release:blocker`
- silent wrong
- 数据损坏
- **当前 PR 引入的**
- UI 死控件且会误导
- 正式支持平台的核心流程
- release pipeline 无法完成

### B. v1.0.1 / v1.0.2（patch train）

- 在支持范围内
- 失败可见
- 局部可修
- **不需要核心架构重写**

### C. v1.1

- 架构型
- 低频
- 非静默
- **有 guard**
- 有替代路径
- 有边界测试

---

## 4. 本轮的 train 编组

每个 PR 一个根因家族，**不混装**：

| PR | 家族 | 内容 |
|---|---|---|
| `chore/review-and-p2-governance` | 流程 | P2 生命周期、issue/PR 模板、labels、Codex 策略与门禁、管理员脚本 |
| `ci/release-harness-stabilization` | `release-harness-contract` | #61/#62 对应问题、summary 可靠性、单值 glob、runner 残留自愈 |
| `ci/release-orchestrator` | `distribution-artifact-contract` | 单一编排、artifact manifest、publish=false、去掉轮询 |
| `ci/release-health-canary` | `release-harness-contract` | 定期 dry-run、workflow freshness、从未执行 job 检测 |
| `fix/v1-release-blocking-p2` | `axes-traversal` | **仅当**存在 release-blocking P2 时才建（多宿主色条 guard） |

**#53（lab runner bootstrap）保持独立**——它改的是 runner 自身的安装脚本，
与发布链最后一公里不是同一个家族，混进来只会让两边都难 review。

---

## 5. 每个 P2 issue 必须有的四件东西

- **milestone**（`1.0-blocker` / `v1.0.1` / `v1.1`）
- **disposition**（`.github/labels.yml` 的六个之一）
- **acceptance test**（文件名 + 用例名，且**它见红过**）
- **next action**（谁、下一步做什么；不是「以后再说」）

缺任何一项，这条 issue 就是档案不是待办——见
`docs/engineering/p2-lifecycle.md` §1。
