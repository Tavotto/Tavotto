# Codex 插件怎么发出去

目标是「用户一句命令就能装」。按用户要敲几下排，有三条路；下面每条都记了**现在
缺什么**，不是泛泛而谈。

调研基于 codex-cli 0.147.0 实测 + 官方文档（2026-08-18）。

## 1. 现状：仓库即市场（今天就能用）

```sh
codex plugin marketplace add Tavotto/Tavotto && codex plugin add tavotto@tavotto
```

Codex 支持把任意 Git 仓库当插件市场：认的是仓库根的
`.agents/plugins/marketplace.json`（本仓库已经有）。加完之后
`codex plugin marketplace upgrade tavotto` 就能拉新版本。

* **优点**：零审核、零第三方依赖，随 Tavotto 发版即生效（插件版本 ==
  `tavotto.__version__`，`tests/test_codex_plugin.py` 看着）。CLI 与 Codex 桌面应用
  共用同一份 `~/.codex` 插件目录，装一次两边都在。
* **缺点**：用户得先知道这条命令——插件不会出现在任何"目录"里被搜到。

**实测过的坑**：`marketplace.json` 的 `policy` 是枚举。写
`"authentication": "NONE"`（本插件确实不需要认证）会让 `codex plugin marketplace add`
**拒掉整个市场文件**，之后的症状只是"插件列表里没有它"。只认 `ON_INSTALL` / `ON_USE`。

## 2. 目标：官方 Plugins Directory（真正的一句话 / 一次点击）

审核通过后插件进入 **ChatGPT 与 Codex 共用的插件目录**，用户在界面里点安装，
或直接 `codex plugin add tavotto`。

**skills-only 插件明确合格**——官方列的三类可提交形态，第一类就是
"A skills-only plugin that packages reusable workflows"。我们没有 MCP server，
于是**域名验证、tool annotations（readOnlyHint/openWorldHint/destructiveHint）、
审核用 demo 凭据这一整块全部不适用**，这是 skills-only 的红利。

### 前置条件（人的事，不是代码的事）

1. OpenAI Platform 组织里要有 **Apps Management: Write** 权限（组织 owner 自带）。
2. **身份验证**：个人发布做 individual verification，用公司名义发布做 business
   verification。身份对不上会被拒。

### 提交材料清单（❌ = 现在还缺）

| 项 | 状态 |
| --- | --- |
| 插件名 / 短描述 / 长描述 / 分类 | ✅ 已在 `.codex-plugin/plugin.json` 的 `interface` |
| logo | ✅ `assets/tavotto.svg`（门户若要求位图，用 `scripts/build_brand_assets.py` 出 PNG） |
| website URL | ✅ GitHub 仓库 |
| support URL | ❌ 用 GitHub Issues 即可，提交时填 |
| privacy policy URL | ❌ `docs/privacy.md` 得先进 main 并有公开链接 |
| terms of service URL | ❌ 需要一页（AGPL 之外的使用条款） |
| `SKILL.md`（含触发条件与边界） | ✅ |
| ≥3 条起始提示 | ✅ `interface.defaultPrompt` 里三条 |
| **5 条正例 + 3 条负例测试用例** | ❌ 要写：正例给「用户提示 → 期望行为 → 结果形态 → 测试数据」，负例是**应当拒绝**的场景 |
| 发布地区 | 提交时选 |
| release notes | ❌ 随版本写 |

### 流程

门户提交 → OpenAI 审核（**时长不承诺**，官方原话是"review timelines may vary as
OpenAI builds and scales the review process"）→ 批准后**由我们自己点发布**，
提交本身不等于上线。

### 已经关掉的一条路

`github.com/openai/plugins` 那个仓库**已于 2026-08-16 归档只读**，往它提 PR 求收录
这条路不存在了；它现在只是官方插件的示例集。

## 3. 备选：社区市场 codex-marketplace.com（一条命令，但非官方）

```sh
npx codex-marketplace add Tavotto/Tavotto --plugin
```

提交只要给 GitHub 仓库 URL，自动审核 + 人工兜底。但它页脚写着
**"Not affiliated with OpenAI"**——等于把安装入口交给一个第三方 npm 包。
**列为备选，不做首推**：Tavotto 自己发的东西，安装链路不该经过一个我们不控制的中间人。

## 4. 不必等审核的「一次点击」：Tavotto 自己代劳

设置 →「改图助手」里加一个「安装 Codex 插件」按钮，用用户机器上已有的 `codex` CLI
跑第 1 条那两句。AI 桥已经有现成的 CLI 定位与 PATH 增强（`engine/ai_bridge.py` 的
`_search_dirs()` / `_spawn_env()`，Windows 上还处理了商店版执行别名与 `.cmd` 外壳）。

**这不违反「绝不改用户的 `~/.codex/config.toml`」那条纪律**——我们调的是官方 CLI，
由它去写自己的配置；我们一个字节都不碰。失败时把命令原样显示出来让用户自己敲。

## 建议顺序

1. **现在**：README 给第 1 条的一行命令（已做）。
2. **下一步（不阻塞发版）**：补齐 privacy / terms / support 三个 URL 与 5 正 3 负
   用例 → 走官方提交。审核期间第 1 条继续有效。
3. **可选**：官方过审前，做第 4 条的一键安装按钮——它对用户的体验其实和"官方目录"
   一样好，而且完全由我们控制。
