# Codex Review 的触发设置（仓库外，需要管理员）

> **仓库里没有任何文件控制 Codex 的触发方式。**
> `grep -rn codex .github/` 只匹配到发布插件的产物名——Codex Review 是一个
> 外部 GitHub App（`chatgpt-codex-connector`），它的行为在 ChatGPT / Codex
> 侧配置。这份文档写清**要改什么**，以及在改成之前怎么办。
>
> 这里**不猜 API**。OpenAI 没有公开「按仓库配置 Codex code review 触发条件」
> 的 REST 端点；下面写的是 UI 路径与可观测的验证方法。

---

## 1. 现状（2026-08-22 实测）

| 观测 | 数据 |
|---|---|
| App 登录名 | `chatgpt-codex-connector` |
| 触发方式 | **每一次 push 一轮**——25 条有 review 的 PR 上，Codex 的轮次与 reviewed commit 数**完全相等，无一例外** |
| 累计 | 113 次 review 提交、188 条 thread |
| 严重度 | P0 **0** ／ P1 37 ／ P2 **151（80%）** |
| 极值 | PR #48 = 18 轮，PR #53 = 15 轮 |

复算：`python3 scripts/admin/codex_review_stats.py`

---

## 2. 要改什么

**目标状态：从「每次 push 自动 review」改成「按需触发」。**

| 保留 | 关闭 |
|---|---|
| 在 PR 上**按需**请求 review（评论 `@codex review` 或在 Codex 界面手动触发） | **每次 push 自动 review** |
| 转为 Ready for review 时跑一轮（如果该选项存在） | 在 **Draft** PR 上 review |
| 严重度 badge（门禁按它分级，见 §4） | — |

设置位置（截至 2026-08-22 的 UI，OpenAI 可能调整）：

```
ChatGPT → Codex → Settings → Code review
  · 选择仓库 Tavotto/Tavotto
  · Automatic review：从 "Every push" 改成 "On request"
    （若只有 on/off，则关掉自动，改用评论触发）
  · Review drafts：关
```

组织级安装的话，路径在 `Settings → Connectors → GitHub → Code review`。

---

## 3. 改成之前怎么办

**靠人执行流程，不靠工具。** `docs/engineering/codex-review-policy.md` §1：

- **PR 先开成 Draft。** Draft 阶段完成实现、自测、作者自审、scope freeze。
  多数 Codex 集成不会 review draft，所以这一步本身就把轮次从
  「push 几次就几轮」降到「Ready 之后几次」。
- **triage 完一次性 push。** 修一条 push 一次 = 多一轮；
  攒着一起 push = 一轮。这是当前唯一能立刻降低轮次的动作。
- **第二轮之后的非阻断 P2 转 issue，不再 push。**

---

## 4. 门禁不依赖这项设置

`scripts/ci/codex_review_gate.py` 与 `.github/workflows/codex-review-gate.yml`
**不请求 review、不 resolve thread、也不假设 Codex 一定在线**：

- Codex 一条 review 都没有 → **neutral，不失败**。
  usage limit、App 掉线、纯文档 PR 都会走到这里。
- 拿不到 GitHub 数据 → neutral + warning。
- 只有 **unresolved P0/P1** 才是 failure。

所以即使这项设置一直没改，门禁照常工作；改了之后它也不用动。

### 4.1 一个要盯住的失效模式

门禁按登录名认 Codex（`scripts/ci/codex_review_gate.py` 的 `CODEX_LOGINS`）。
**登录名一变，门禁会静默变成「这轮很干净」**——这正是本仓库反复撞到的
空门禁形状。两道防线：

1. `tests/test_governance_contracts.py` 钉住那个常量元组的内容，
   改它必须同时改测试（于是会被人看见）；
2. 需要确证时用 `--require-bot`：Codex 一条 review 都没有就判失败。
   **默认不开**——那会让「纯文档 PR」和 usage limit 一起变红。

---

## 5. 管理员待办清单

- [ ] 按 §2 把 Automatic review 从 "Every push" 改成 "On request"
- [ ] 关掉 Draft PR 的 review
- [ ] 改完在下一条 PR 上验证：push 两次，确认只有手动请求时才出现新 review
- [ ] 把实际生效的设置回填到本文件 §1（连同生效日期）
