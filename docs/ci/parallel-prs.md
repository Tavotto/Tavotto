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

## coordinate 域：撞的是名字，不是文本

`docs/adr/**`：两个 PR 各加一份 ADR，**`git merge-tree` 报零冲突**——文件名
不同，git 看到的是两个新文件——而合完的 main 上会躺着两个「ADR 0021」。
2026-08-28 实测撞过一次（`0021-tavotto-run-product-contract` 与
`0021-complexity-aware-editor-preview`）。

这一档既不该 stack（两份 ADR 通常毫不相干），也不该 train（没有共享生成物），
更不该 serialize（ADR 加得很频繁，串行化会拖住一切）。要做的只有一件事：
**开工前看一眼同域 PR 占了哪个号**。

### 已经撞了怎么改号

让**先开的那个**保留编号，后者改。这是**两步**，两步该核的东西不一样——
把它们压成一句话是错的（`--msg-filter` 只动消息，动不了文件名；而真改文件名
必然改树哈希）：

```sh
# 第 1 步：改文件名 + 全仓引用 → 一个**新提交**（树当然会变，这一步不核树）
git mv docs/adr/0021-<slug>.md docs/adr/0022-<slug>.md
#   连带改掉正文标题、以及所有引用它的代码注释 / 文档 / 用例
git commit -am "ADR 改号 0021 → 0022：编号撞了 PR #NNN"
#   核的是：`grep -rn "ADR 0021\|adr/0021-<slug>"` 一条不剩

# 第 2 步：把**历史提交消息**里的旧编号一并改掉（这一步树哈希必须不变）
git log --format='%T' origin/main..HEAD > /tmp/before-trees
FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f \
    --msg-filter 'sed "s/ADR 0021/ADR 0022/g"' origin/main..HEAD
git log --format='%T' origin/main..HEAD > /tmp/after-trees
diff /tmp/before-trees /tmp/after-trees        # 必须一字不差：只动了消息
git log --format='%an <%ae> %ad' --date=iso origin/main..HEAD   # author 与日期原样
```

**别用 `git commit --amend --reset-author`** 改任何一步：它会把 author 日期
也改成现在。第 2 步之后如果分支已经推过，用
`--force-with-lease=<branch>:<你实际看到的远端 SHA>`。

如果改动还没提交就别急着做第 2 步——`filter-branch` 只改历史，工作区那份
得先落进第 1 步的提交里。

这类域在配置里**自带一句处方**（`advice` 字段）。通用兜底文案在这里是
「对的判据 + 错的处方」：它说「留意合并顺序，后合的一侧 rebase 后重跑快线
即可」，而 rebase 根本不会报冲突，重跑快线也发现不了。判据一旦对，人更会
信它说的那句话。

## 与队列的关系速查

| 情形 | 做法 |
|---|---|
| 两个 PR 文件毫无交集 | 各自直接进队列，队列负责组合验证 |
| 相关改动、有依赖 | Stack，从底向上进队列 |
| 不相关、同一生成物 | Train，一个集成 PR 进队列 |
| 同一 serialize 域 | 排队：一个合完，下一个 rebase 再开 |
| 同一 coordinate 域 | 各自挑一个没人占的名字/编号；已撞就后开的那个改 |
