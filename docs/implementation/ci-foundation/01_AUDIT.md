# 当前审计与对原建议的裁决

## 证据边界

基线 `8b95256c0d08a14bfcfc4c81358894ef01168933`；日期 2026-09-15。读取主 CI 部分关键区段、Playwright 配置、自托管文档、lab reusable workflow 和 Actions run/job 元数据。没有 SSH、没有 host/VM 性能测量，没有对所有历史 run 统计分布。

## 一个实际成功样本

Run `34970490865`，`merge_group`，attempt 1，24 jobs；workflow 创建于 `12:42:59Z`，updated_at 为 `13:43:15Z`。updated_at 不被当作通用的精确完成字段。下表由 job/step 的开始结束时间计算，不是估计。[G02–G05]

| 任务/步骤 | 时长 | 观察 |
|---|---:|---|
| backend-fast · Ubuntu/Python3.10 job | 35分17秒 | GitHub-hosted；分配约2秒 |
| 其中安装依赖 | 16秒 | 不是该任务主要耗时 |
| 其中 pytest | 34分56秒 | 整个默认 pytest 集合，尚需逐例分解 |
| frontend job | 4分13秒 | 与 backend 同时启动，已有并行 |
| 其中 pnpm install | 4秒 | “每次装包80秒”不适用于此样本 |
| 其中 pnpm test | 3分22秒 | 前端主要执行部分 |
| 其中 pnpm build | 22秒 | 勿夸大仅复用构建的单项收益 |
| windows-exe-smoke job | 24分41秒 | 在 backend 结束后才创建 |
| 其中 Playwright 黄金路径步骤 | 18分44秒 | 步骤包围时间，可能包含命令内准备，不等于每条用例时间 |

Windows job 创建于 `13:18:20Z`，开始于 `13:18:22Z`。先前约35分钟不是“它排队等 Windows runner”：它尚未因 needs 满足而创建。必须把 DAG 等待与 runner 排队分开统计。[G03/G05]

## 源码确认

- CI 已有 PR/merge_group/full-ci/main/nightly/release 分层，且只取消旧 PR 的在途运行；不是缺少 concurrency 或所有PR都跑全矩阵。[G06]
- backend-fast 的三个 Python 档位均执行整个默认 `python -m pytest`。backend-platforms 在 mac/Windows 执行 pytest 并打印 durations。[G06]
- `package` 和 `windows-exe-smoke` 的 `needs: [backend-fast, frontend]` 包含等测试判定的边；它们又自己构建前端。是否移除/替换边要由实际 artifact 消费关系确认。[G06]
- Playwright 当前 `workers:1`、`fullyParallel:false`，注释指向真实端口与临时目录资源。不能只改数字而不审计 fixture。[G07]
- package 路径含 `/tmp/smoke`、端口5199和固定sleep8；多个 runner 共一个 OS/用户时需要改为任务隔离与健康检查。[G06]
- 当前 lab 设计是16vCPU/32GiB的独立可信VM；文档禁止 pull_request 在长期lab runner上执行。并不能从用户说“16c/32GB”推出那是物理宿主全部容量。[G08]
- lab reusable workflow 使用 `tavotto-lab`、共享state root、资格独占与cleanup，原假设只有一台runner。同标签加三个runner会改变锁、清理与基准条件，不是免费并行。[G09]

## 原建议保留什么、修改什么

| 原建议 | 裁决 |
|---|---|
| 多 runner/VM 提高并行吞吐 | 保留，但 job 是调度单位，不保证一人一PR；测真实容量、隔离和资源竞争 |
| 4c+4c+8c 分尽16c | 仅示意；vCPU不等于独占物理核，不能忽略宿主和共享盘开销 |
| 6–8+6–8+16–18 GB | 低端28、高端34；高端已超过32。需真实资源余量，不用swap掩盖OOM |
| 每次commit全矩阵不合理 | 同意；仓库已分层，剩余重点是长默认pytest和串行E2E，而非从零再分层 |
| 全部打包/关键兼容移到main/nightly | 拒绝；已有合并前保护仍需在merge_group/full-ci执行 |
| PR Ready才做代表性验证 | 可用，但 ready_for_review只触发一次转换；非草稿后续synchronize仍需验证新SHA |
| 全workflow cancel-in-progress:true | 拒绝；保留按事件区别，特别保护合并候选和发布 |
| 自托管必然2×、省30–60% | 不作为结论；当前样本安装已很短，实际需AB测量 |

## 首先验证的假设

H1：删除非必要 DAG 边可重叠 backend 与安装验证时间，而不丢最终 AND 判定。
H2：pytest 中大量独立组可以平衡分片；无法隔离的测试保留 serial 集合。
H3：Windows E2E 的文件级分片比继续堆 Linux runner 更贴近该样本关键路径。
H4：自托管只有网络、工具版本、磁盘与安全条件齐备才有净收益。

以上均是待验证假设，不承诺把60分钟缩到某个指定数字。
