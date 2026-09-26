# 功能登记表：用户可见的功能不能被悄悄关掉（ADR 0097）

> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 决策与取舍见 `docs/adr/0097-feature-registry.md`。

## 是什么

- **登记表** `docs/features/registry.json` 是「Tavotto 有哪些用户可见的功能」的唯一清单。每条：
  `id`（点分层小写，如 `canvas.select-and-drag`）、`summary`（`zh-CN` / `en-US` 各一句）、
  `entry`（界面上从哪里进）、`platform`（`browser` / `desktop` / `both`）、`status`
  （`active` / `removed`）、`e2e`（覆盖它的真浏览器用例，`{spec, title}`，`title` 是 describe
  标题 + 用例标题以 ` › ` 连接，**精确相等**）、可选 `desktop_manual`（桌面壳里要手动过的步骤）。
- **判据**在 `scripts/ci/feature_registry.py`，四个子命令；AST 那一半在 `web/scripts/e2e-skip-scan.mjs`。
  看护：`tests/test_feature_registry.py`（每条负例一种「功能被关掉而 CI 仍绿」的形状）+
  `node web/scripts/e2e-skip-scan.mjs --self-test`（扫描器自己的反证）。

## 规则

- **每个 active 功能至少一条真浏览器 e2e**（`platform: desktop` 的除外——浏览器 e2e 够不着，
  它们必须写 `desktop_manual`，进 beta 手动清单）。用例在真实界面里操作、看真实结果：元素可见且
  整框在视口内、点得到（中心那一点的最上层就是它）、操作之后画面真的变了（对象屏幕位置、引擎
  重画的 SVG 里元素的框、`data-display="exact"` 且 `data-display-key` 换版、落盘文件的文件头）。
  **只断言「DOM 里有这个节点」的用例不能登记**。核心路径的用例在 `web/e2e/features.spec.ts`，
  选择器认稳定 `data-*`（web/AGENTS.md）。
- **功能用例不许带跳过**：声明期的 `test.skip / fixme / fail(title, fn)`、`test.describe.skip`，
  运行期的 `test.skip(cond)`、`testInfo.skip()`、`test.info().skip()`，落在用例本身、它所在的
  describe（含 beforeEach / beforeAll）、或文件顶层——都判红。平台差异要么在用例里分支断言，要么
  这个功能就不在那个平台登记。
- **用例 → 功能**：给用例打 `{ tag: '@feature:<id>' }`（可以多个）。带标签的用例，那个功能必须
  是 active、并且把这条用例列在 `e2e` 里（防孤儿）。已登记但没带标签的旧用例是允许的（不为加
  标签去碰在飞 PR 正在改的 spec），新写的功能用例一律带。
- **功能下线 / 降级**：让登记表里某功能消失或变弱的 PR，必须
  1. 把那一条改成 `"status": "removed"`，写 `"removed": {"reason", "date"（YYYY-MM-DD）,
     "approved_by"（拍板的人）}`——**不许删条目**（删了 = 门禁红）；
  2. 摘掉用例上的 `@feature:<id>` 标签（或删用例）；
  3. 给 PR 打 **`feature:removal`** 标签。降级 = 旧 id 改 removed + 新 id 登记变弱后的那个能力。
  标签由仓库维护者建一次（`gh label create feature:removal --color B60205 --description "PR 让登记表里的功能下线或降级（ADR 0097）"`）。
- **新功能**：合入它的 PR 同时加一条 active 登记 + 至少一条带标签的 e2e。PR 模板里有这一行。

## 在 CI 里怎么判（主语：谁的、哪个进程、哪个时刻、哪个维度）

| 步骤 | 在哪 | 主语 | 判什么 |
| --- | --- | --- | --- |
| `feature_registry.py check` | `frontend`（PR + merge_group，快线） | Playwright 自己算出来的用例集合（`playwright test --list --reporter=json`，全部 project）× 源码 AST 里每条用例声明的跳过修饰（按 file:line:col 对上） | 登记表形状；登记的用例存在、不是声明期跳过、没有运行期修饰能落到它；带标签的都登记了 |
| `feature_registry.py transitions` | `frontend` | base 提交与本次提交的两份登记表；PR 的标签 | 条目不许消失；active → removed 要 `feature:removal`（merge_group 没有 PR 标签：只判前一条） |
| `feature_registry.py verify-run` | `posix-e2e`（merge_group / full-ci，合并态） | 这次 `pnpm e2e` 的 JSON 报告（`TAVOTTO_E2E_JSON`） | 每条登记的用例都在报告里、每次出现都是 expected / flaky；skipped 判红（skip 不是绿），不在报告里判红（没跑不是绿） |

- 两个步骤都在**已有**的 Gate 闭集里（`frontend` → CI fast gate；`posix-e2e` → CI integration gate），
  所以不需要新登记 required check。静态那一半看不见「从别的模块 import 进来的辅助函数里的 skip」，
  这个盲点由 verify-run 兜——合并态那次真跑里它就是 skipped。
- 输入不可信（清单读不懂、报告是空的、base 提交拿不到）退出码 2，**不许当成通过**：拿不到 base 时
  「删条目」会静默放行。

## 本机

```sh
python3 scripts/ci/feature_registry.py check              # 需要 web/node_modules
node web/scripts/e2e-skip-scan.mjs --self-test
python3 scripts/ci/feature_registry.py beta-checklist     # beta 发行说明引用的手动试用清单
```

跑功能 e2e：`cd web && TAVOTTO_PYTHON=… TAVOTTO_WORKER_PYTHON=… PYTHONPATH="$(cd .. && pwd)/src" npx playwright test e2e/features.spec.ts --project=chromium`
（worktree 里 `PYTHONPATH` 必须是绝对路径；先 `python scripts/build_frontend.py`）。
