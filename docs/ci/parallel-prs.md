# 多 session 并行开 PR：Stacked PR 与 Train Branch

Merge Queue（见 `merge-queue-rollout.md`）解决了「合一个、其余全部追 main
重跑」；它解决不了**真正的 Git 冲突**。本仓库的冲突大头不是源码，是受管
生成物：`web/src/**` 一动，`scripts/build_mcp_widget.py` 就要重建
`codex-plugin/mcp/widget/canvas.html`——四个互不相干的前端 PR 各带一份
重建过的同一个文件，合掉第一个，其余三个全部 DIRTY。

`.github/conflict-domains.json` 声明了这些热点；每个 PR 上的
「PR conflict domains」检查（咨询性，不阻断）会列出与你同域的 open PR
并给建议。两种协作形态按「改动相关不相关」选：

## 相关改动：Stacked PR

同一个 issue 的协议层与 E2E、一个修复与它暴露出来的第二个修复：

```
main
└── PR A：协议            （base: main）
    └── PR B：基于 A 的 E2E（base: PR A 的分支）
```

规矩：

* 上层 PR 的 **base 是下层的 branch**，diff 只展示自己的增量；
* 下层变了，从底向上做 cascading rebase（`git rebase A` 到 B，依次向上）；
* **从底向上进队列**：A 先「Merge when ready」；A 合入后把 B 的 base 改回
  main、rebase 一次，再让 B 进队列；
* 有明确依赖的两个 PR **不要**平行指向 main——那只是把冲突推迟到队列里；
* 用 Merge Queue 的「Merge when ready」，**不要**用普通 auto-merge 代替它；
* 本机 `gh` 没有专门的 stack 子命令也没关系——上面全部用普通的
  `gh pr create --base <下层分支>` 就能做到。

## 不相关但共享生成物：Train Branch

多个独立前端修复都会重建 `canvas.html`（或 playground 产物）时：

```
main
└── train/frontend-2026-08-25
    ├── session A 的源码提交
    ├── session B 的源码提交
    └── session C 的源码提交
```

流程：

1. 开一条 `train/<主题>-<日期>` 分支；各 session 把**源码提交**（不含
   生成物，或含也无妨——最后会统一重建）合进来；
2. 集成 session 解决**真实的源码冲突**；
3. 在最终源码状态上**只跑一次**
   `python scripts/build_mcp_widget.py`（涉及 playground 再跑
   `python scripts/build_browser_playground.py`），只提交这一份最终生成物；
4. 从 train branch 向 main 开**一个**集成 PR，进 Merge Queue。

反模式：多个平行 PR 各自携带自己版本的同一个 bundle——除非它们确实要
彼此独立合并（那就接受「每合一个、其余重建一次」的代价，按队列顺序逐个
rebase + 重建，见 `managed-artifact-conflicts` 的教训）。

## serialize 域

`AGENTS.md` / `CLAUDE.md`、`.github/workflows/**`、`scripts/ci/**`、
release 编排、golden vectors、锁文件：**一次只开一个动它的 PR**。这些文件
的冲突不是文本问题，是语义问题（两个 PR 各自改 CI 控制面，合并后的组合
谁都没验过）；train 与 stack 都救不了，只有先后。

## 与队列的关系速查

| 情形 | 做法 |
|---|---|
| 两个 PR 文件毫无交集 | 各自直接进队列，队列负责组合验证 |
| 相关改动、有依赖 | Stack，从底向上进队列 |
| 不相关、同一生成物 | Train，一个集成 PR 进队列 |
| 同一 serialize 域 | 排队：一个合完，下一个 rebase 再开 |
