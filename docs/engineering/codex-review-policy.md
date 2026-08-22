# Codex Review 使用规则：保留高价值发现，停止无限 review loop

> **量出来的问题**（`docs/audit/2026-08-22-v1-release-process-audit.md` §7）：
> Codex 在**每一次 push** 上都跑一轮，于是 PR #48 被 review 了 18 轮、
> #53 被 review 了 15 轮——**轮次与 commit 数完全相等，无一例外**。
> 188 条发现里 **P0 = 0、P1 = 37、P2 = 151（80%）**。
>
> 这不是「工具太吵」。37 条 P1 里有真的，其中几条直接救了发布链。
> 问题在于**它决定了 PR 什么时候能合**：修一条 → 触发一轮 → 又找到一条 →
> 再修一条。这个循环不收敛，而且它的边际产出在第三轮之后基本都是 P2。

---

## 1. 流程

```
1. PR 以 **Draft** 创建
      └─ Draft 阶段：完成实现、自测、作者自审、**scope freeze**
2. 点 Ready for review  ─────────────►  第 1 轮 Codex
3. findings **一次性 triage**（不是修一条请求一次）
      ├─ P0 / P1 → 本 PR 修
      └─ P2      → 少量局部的顺手修，其余转 issue
4. 代码再次冻结  ───────────────────►  第 2 轮 Codex（final）
5. 第 2 轮之后新出现的**非阻断 P2** → 转 issue，**不触发第三轮**
```

**正常情况下最多两轮。**

### 1.1 允许第三轮的三种例外

只有这三种，且要在 PR 正文里写明是哪一种：

1. 新的 **P0 / P1**；
2. **安全**问题；
3. **数据损坏**相关的修改。

「想让它再看一眼」不是理由。「上一轮还有 P2 没修完」也不是——
那些 P2 的正确出口是 issue。

### 1.2 默认**不**请求全量 review 的改动

- 纯文档
- 版本号同步
- generated artifact 重建（`build_mcp_widget.py`、playground 产物、
  preflight vectors）
- baseline 更新
- 小型 CI 参数修复（超时数值、runner 标签、retention 天数）

这些改动的风险不在「代码写得对不对」，在「产物与源码同不同步」，
而那件事有专门的 `--check` 门禁在管。

---

## 2. 门禁：`scripts/ci/codex_review_gate.py`

由 `.github/workflows/codex-review-gate.yml` 在 PR 上运行。

| 情形 | 结论 | 理由 |
|---|---|---|
| unresolved **P0 / P1** | **failure** | 它们本来就该挡住合并 |
| unresolved **P2 / P3** | warning | P2 的出口是「有 disposition」，可以是「转 issue」。做成硬失败等于逼 PR 无限扩张——那正是要停的循环 |
| 轮次 > 2 | warning + 列出每一轮的 commit | 提醒 scope 没冻住 |
| **读不出严重度** | warning，**并照常计入未处置** | 读不出来 ≠ 不严重。静默当成 P3 是这道门禁最容易长出来的空转形态 |
| Codex **没跑**（usage limit / App 掉线 / 纯文档 PR） | **neutral，不失败** | 会因为外部服务不在线而永久卡住 PR 的门禁，第一次卡住就会被摘掉 |
| 拿不到 GitHub 数据 | neutral + warning | 同上 |

**这道门禁不做的事**（每一条都是有意的）：

- **不自动请求 review** —— 那会重新造出它要消灭的循环；
- **不 resolve 任何 thread** —— disposition 是人的判断；
- **不按评论条数数轮次** —— 按 **reviewed commit** 去重。一轮 review 会产出
  十几条 thread comment，按条数数会把一轮报成十几轮，而那个数字会被拿去
  判「超没超两轮」。

本地跑：

```bash
python3 scripts/ci/codex_review_gate.py --pr 61
python3 scripts/ci/codex_review_gate.py --pr 61 --json    # stdout 只有一行 JSON
```

---

## 3. 关掉「每次 push 自动 review」

**仓库里没有任何文件控制 Codex 的触发方式**——它是外部 GitHub App
（`chatgpt-codex-connector`）。要改必须在 ChatGPT / Codex 侧的设置里操作，
步骤见 `docs/admin/codex-review-settings.md`。

在设置改掉之前，本文件 §1 的流程靠**人**执行：Draft 阶段不点 Ready，
就不会有 review；triage 完一次性 push，就只多一轮。

---

## 4. 每条 thread 合并前必须有 disposition

`main` 的 ruleset 已开 `required_review_thread_resolution: true`，
所以**未 resolve 的 thread 在技术上就挡住合并**。这份规则补的是
「resolve 之前要留下什么」——四种形态，每一种都带一个可验证的产物：

| 形态 | 写法 | 可验证的产物 |
|---|---|---|
| 修了 | `Fixed in <sha>` | 那个 commit |
| 延后 | `Deferred to #<n>` | 一个**已经建好**、带 milestone 与 disposition 标签的 issue |
| 加了护栏 | `Guarded in <sha>, long-term fix #<n>` | commit + issue **两个都要** |
| 不成立 | `False positive: <实测证据>` | 复现脚本 / 日志 / 像素数，**不是推理** |

> **不要求所有 P2 都在当前 PR 修掉。** 「Deferred to #123」是完全正当的
> disposition——前提是 #123 真的存在、真的有 milestone、真的有下一步
> （见 `docs/engineering/p2-lifecycle.md` §1）。

### 4.1 历史欠账

审计时全仓库有 **82 条未处置 thread，其中 78 条在已经合并的 PR 上**。
这些**不追溯**——把它们逐条翻出来的成本远高于价值，而且当时的代码早已改过。
规则从本文件合入之日起对**新 PR** 生效。

真正要防的是它再次积累，而防线是上面那条 ruleset 设置 + 这道门禁，
不是一次性的清算。
