# PR 的风险分层与入队方式（2026-09-18 定，2026-09-19 记入仓库，2026-09-29 加 Codex 评审预算）

审查资源（Codex 额度、维护者的时间）是稀缺的，按证据强度分配：机械证据够硬的 PR 不等
人眼，人眼留给改行为的 PR；Codex 怎么叫见下文「Codex 评审预算」。PR 正文第一段（模板
「基线与风险」）写明归类与理由，并打上对应的 `risk:low` / `risk:normal` / `risk:high`
标签；归类错了是审查意见，不是门禁。

## 三档

| 等级 | 什么算 | 入队方式 |
| --- | --- | --- |
| **低** | 纯文档；纯测试；**逐字搬家**且分发表逐字节相同（HANDLERS 顺序、`_RESTORE` 键、`_needs_state` 标记三者对拍） | CI 绿即 `gh pr merge <n> --auto`，不等审查 |
| **中** | 改行为但边界清楚：拆环 / 提取服务 / 加纯判据模块 / 改一条契约的实现而契约不变 | 推上去后**停下来**：正文里的「审查包」（What this changes / 分别核验 / 反证）给维护者看过，回「#N 可以排」才入队 |
| **高** | 改协议、CI 拓扑、控制流、写回事务、setter / restore 与重放语义、默认后端 | 同中；另外要 `full-ci` 全绿、变异清单逐条附退出码、未验证范围写全 |

「逐字搬家 = 低」的前提是**对拍真的做了**：三张分发表逐字节相同是证据，不是声明。
没对拍的搬家按中。

## 入队的机械判据

按 `docs/ci/parallel-prs.md` 与合并队列的规则：`mergeStateStatus == CLEAN`、重腿
（`posix-e2e` + `windows-exe-smoke` 各分片）在**现役 run** 上全 SUCCESS（被 `full-ci`
标签挤掉的旧 run 留下的 CANCELLED / FAILURE 不算）、未解决评审线程 = 0、每一条 SKIPPED
写得出理由。判据要能区分「没有」与「没取到」——`gh` 的 GraphQL 偶发 EOF 时 stdout 为空，
空输出不是「一切正常」。

## Codex 评审预算（2026-09-29 起）

**为什么改**：Codex 云端开着「自动审查」，而且**每次 push 都完整评审一轮**，纯 rebase、
只解冲突的 push 也算——#716 / #730 在 09-28～29 两天里各被完整评审十余轮，额度主要烧在
这里。评审一次看的是整个 PR，不是上次之后的增量。

规则（作者与代理一律照此执行）：

1. **自动审查关掉**（账号侧：Codex 云端 → Code review → 关掉自动审查；关掉后在本节末尾记
   日期）。此后仓库里唯一的触发方式是 PR 评论 `@codex review`——包括**教别人怎么触发**的评论
   里出现这串字也会触发，提醒里写「请 Codex 评审」。
2. **按档调用**：

   | 等级 | 标签 | Codex |
   | --- | --- | --- |
   | 低 | `risk:low` | 不叫；CI 绿即入队 |
   | 中 | `risk:normal` | **一次**完整评审 |
   | 高 | `risk:high` | 一次完整评审 + 修完后**最多一次**定向复核 |

   高档典型：RenderCore / 渲染闭包、画布坐标与变换、写回事务、Python 环境与 runtime、
   插件 / MCP、安全边界、安装包与发布链、CI 拓扑。中档修了 P1 且改了控制流的，也可以用
   那一次定向复核。
3. **先过确定性检查，再叫 Codex**：fast 档 Gate 绿（叠栈 PR 没有 CI：本地 `ruff` 两条 +
   针对性 pytest / `pnpm test` + `pnpm build` 退出码 0）、正文审查包写完，才评论
   `@codex review`。lint / 类型 / 失败用例不值得花评审额度去发现。
4. **意见一次收齐、一次修完、一次 push**：P1 必修；P2 默认修，不修要在线程里写出理由
   （不成立并附实测、或已另开 issue 且写清为什么不在本 PR），「已记录」不算；本地全验过
   再推，不要一条意见推一次。
5. **定向复核**用这段评论（替换两个占位），不写裸 `@codex review`：

   ```text
   @codex review the changes since <上次评审的 commit>.
   Focus only on whether the findings from that review are resolved
   (<线程链接或编号>) and whether the fixes introduced regressions.
   Do not re-review unchanged code or re-raise answered findings.
   ```

   根 `AGENTS.md` 的「Review guidelines」同样约束它只看这些。复核之后还有新 P1：
   **停下来找维护者**，不开第三轮。
6. **不为 rebase 叫评审**：纯 rebase / 解冲突 / 改正文不触发任何评审；合并资格靠 CI 与
   未解决线程 = 0。修复的核验证据（修复提交在被评 head 里、定向用例红→绿）写在线程回复里，
   再由人 resolve。
7. Codex 不可用（额度用完、服务掉线）时 required checks 照常，不会卡住 PR；中高档 PR 等额度
   恢复补那一次评审，或由维护者人眼审过后在正文写明「未经 Codex 评审」。

## 为什么不做成自动门禁

分层依据（「逐字搬家」「边界清楚」）是语义判断，写成规则只会得到一个能被措辞绕过的
检查；把它放在正文第一段，让审查人一眼看到作者的归类和理由，错了就改归类。
