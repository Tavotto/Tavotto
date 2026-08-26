# ADR 0015：编码 Agent 注册表与设置体验

日期：2026-08-26 · 状态：已接受 · 关联：ADR 0005 / 0006 / 0008 / 0012

## 背景

改图助手一直只认两个 CLI，而「只认两个」这件事被写在了**七个地方**：
`ai_bridge` 的 `for name in ("codex", "claude")`、两处 `if agent == …` 分支、
`NPM_PACKAGES` 表、`ai_providers.AGENTS` 元组、前端的
`Record<'codex' | 'claude', …>`、`AGENT_LABEL` 常量、以及
`const other = agent === 'codex' ? 'claude' : 'codex'`。加第三个 Agent 要同时
改这七处，漏一处的表现各不相同（界面上少一行、接口区块永远空着、
localStorage 里那个选择永远被判非法），而没有任何一条用例会红。

设置页那一侧的问题是另一类：一级页面上摆着两个 CLI 路径输入框、每家一个
第三方接口下拉、Base URL / 密钥 / wire api 的入口，而**绝大多数用户什么都
不需要填**——他们装好 codex 或 claude，Tavotto 自动发现、直接用它现有的登录
就够了。首屏把少数人用一次的技术细节摆在正中央，等于把「什么都不用配」
这句话作废。状态表达也只有 `installed: boolean` 一档：「没装」和「装了但
启动不了」长得一模一样，而这两件事的下一步完全相反。

## 决定

### 1. `engine/ai_agents.py` 是「支持哪些编码 Agent」的唯一权威

一个纯标准库的适配层：`AgentDefinition` 声明 id / 显示名 / 图标键 /
可执行文件名 / 第三方接口协议族 / 安装规格 / 模型与推理强度能力 /
有没有无副作用的就绪检查，并实现四个方法——`extra_search_locations()`、
`model_capabilities()`、`readiness(argv)`、`build_command(ctx)` /
`classify_event(line, state)`。生产注册表：

```python
AGENT_REGISTRY = (CodexAgent(), ClaudeAgent())
```

顺序即界面顺序。`ai_bridge.capabilities()` 遍历它，命令构造与输出分类委托
给它，安装包名只从它的 `install_spec` 上取。**未知 id 一律在
`require_agent()` 当场拒绝**，绝不继续往下传（那是「不把请求体里的字符串
拼进命令行」的那道闸）。

反证方式写进了用例：`tests/test_ai_agents.py` 把一个 `_FakeAgent` 塞进注册表，
capabilities 必须原样多出第三条、形状齐全、命令由它自己拼——**一行新的分支
都不用加**。哪天有人在通用层写回硬编码分支，这条会红。

**本次不扩大运行能力**：注册表里只放当前真的能跑起来的 Codex 与 Claude Code。
架构支持第三个，不等于假装已经支持——界面上没有「即将推出」的占位行。

### 2. 依赖方向单向：`ai_agents ← ai_providers ← ai_bridge`

`ai_agents` 不 import 另外两个。第三方接口的注入结果（追加参数 + 追加环境
变量）由 `ai_bridge` 经 `ai_providers` 算好，通过 `RunContext` 传进适配器——
适配器只负责把它拼到自己那套命令行的正确位置。`ai_providers` 不再自己列一份
`AGENTS = ("claude", "codex")`：谁支持接第三方接口，由适配器的
`endpoint_family` 说了算；注入方式按**协议族**（anthropic / openai）分支，
不按 agent 名分支。

### 3. 自动探测保持不变，但候选带上了来源

`shutil.which` → 各平台常见目录（Homebrew / npm 全局 / bun / volta / scoop /
winget / choco / WindowsApps 执行别名 / MSIX 包体 / macOS ChatGPT 应用内置的
codex）→ npm 包内的原生二进制，用户自定义路径最优先；`.cmd`/`.ps1` shim 解析
成真身以绕开 cmd.exe 的元字符再解析；**只有真的 `--version` 起得来才算装了**；
第一个候选坏掉继续试下一个。这一整套一个字节都没退化，只是搬进了
`ai_agents.py`，并且每个候选现在带一个 `source` 标签
（`custom` / `path` / `homebrew` / `common_location` / `npm_global` /
`chatgpt_bundle` / `windows_alias` / `windows_store` / `package_binary`）。

**来源只是诊断信息，不参与「能不能用」的判断**——判据仍然只有一条：
启动验证过没有。来源显示在详情页，用来回答「它到底是从哪儿找到的」。

### 4. 自动探测 ≠ 就绪检查

两件事分开：探测回答「这台机器上有没有一个能启动的可执行文件」，
就绪检查回答「它现在能不能干活」。就绪检查是**无副作用**的，规矩写死：

* 只跑官方 CLI 明确提供的**本地状态命令**——`codex login status`、
  `claude auth status`；
* 不发模型请求、不建会话、不产生 Token 或费用、不改登录状态；
* `stdin=DEVNULL` + 严格超时（10s），任何想要输入的命令必须当场失败而不是
  把设置页挂死；
* 不支持 / 超时 / 输出看不懂 → `unknown`，**映射为「已安装」**。

**绝不因为「配置目录存在」就宣布已登录，也绝不为了让那一行变绿而偷偷发一个
真实 Prompt。** `claude auth status` 的 JSON 里带着邮箱、组织名和订阅档位——
只取 `loggedIn` 这一个布尔值，其余一个字节都不进 capabilities、日志或诊断包
（`tests/test_ai_bridge.py` 有专门的用例盯着）。

### 5. 状态模型：六个值，不是一个布尔

| 状态 | 语义 | 视觉 |
| --- | --- | --- |
| `ready` | 可启动，且无副作用就绪检查明确成功 | 成功（图标 + 文字） |
| `installed` | 可启动，但无法安全确认登录状态 | 中性 |
| `needs_auth` | 就绪检查明确表明需要登录 | 警示 |
| `broken` | 找到了候选，但全都启动不了 | 危险 |
| `not_installed` | 没找到可启动候选 | 中性灰 |
| `disabled` | 已安装，但用户在 Tavotto 里关掉 | 中性灰 |

前端另有一个本地的 `detecting`（后端不返回它）：**首屏在探测出结果之前
绝不先显示红叉或「未安装」**——那是没有依据的断言。

「没装」不是应用错误，用中性灰；危险色只留给 `broken`。每一档都有自己的图标
形状和自己的一句话，**状态不靠颜色单独表达**。

### 6. `enabled` 与 `usable` 是两件事

```
usable = enabled && 可执行文件能启动 && state ∉ {broken, not_installed, needs_auth}
```

**接了第三方接口时，CLI 自己的登录态不参与判定**（评审 P1 修正）：注入那套
凭据的全部意义就是让 CLI 不必用官方登录跑起来，拿它的登录态去回答「现在能不能
派活」是把判据的主语搞错了——表现是「配好了 DeepSeek 的用户发现 Codex 从选择器
里整个消失」。判据取 **`ai_providers.spawn_overrides()` 是否真的产出了参数或
环境变量**，而不是「配置里有没有一条记录」：codex 侧 `base_url` 为空时它一个
字节都不注入，那种情况 CLI 的登录态仍然算数。就绪检查的原始结论照实记在
`diagnostics.readiness` 里，只是不再当闸。

`enabled` 是**三态**：用户从没表过态时跟着「装没装」走——装上了就能用，
不该逼他先去设置里开一次；**明确关过就一直关着**，下次探测成功也不自动翻
回来。禁用只影响 Tavotto 用不用它：不卸载 CLI、不动 CLI 自己的配置。
判据在后端（`require_usable`），`/api/ai/run` 自己判一次——只靠前端把它从
选择器里藏掉是不够的，那个端点可以被直接调。**`require_usable` 的兜底判据就是
`usable` 那一个字段**（评审 P2 修正）：在那里重列一遍
「installed and enabled and …」是 `usable` 的第二份定义，而两份定义分叉的表现
正是「界面把它藏了、API 还放它进来」。前面几个分支只为给出对得上的稳定 code
（`ai_agent_not_installed` / `ai_agent_disabled` / `ai_agent_needs_auth`），
最后那道 `ai_agent_not_usable` 是兜底——将来 `usable` 多一个成因，它自动跟上。

`state == 'installed'`（登录状态查不准）的 Agent **允许试着用**，但界面必须
诚实显示「已安装」，不能伪装成「可用」。

### 7. 公开契约：动态 `agents[]`，旧的两个硬编码对象整体移除

```
{ agents: AiAgentCaps[], endpoints, presets, checked_at_ms }
```

`providers: Record<'codex'|'claude', …>`、`settings: {codex_path, claude_path}`、
`active: Record<…>` 三处一起删掉，**不留兼容字段**——确认过仓库外没有消费者
（app.py 的诊断、`engine/diagnostics.py`、两条 e2e，全部在本 PR 内迁完）。
留一份「从注册表实时派生」的旧形状看似无害，实际是给下一个人一个继续按旧
形状写代码的入口。

`argv` **不再公开**：前端没有消费者，那就不公开（用例断言它不在响应里）。

**遥测的 agent 白名单取自 `telemetry.EVENTS` 自己的枚举，不是注册表**
（评审 P2 修正）：拿「在不在注册表里」当白名单，在注册表只有两个 Agent 时
恰好等价，加第三个之后就恒真——那个 id 被原样透出，而 `capture()` 只收表里
那几个值，于是该 Agent 的调用被静默丢弃，「加个适配器就完事」这句话当场破功。
`checked_at_ms` 用毫秒 + `_ms` 后缀，与 `AiHistoryEntry.started_ms` 同一约定。

新增端点，全部按 agent id 收敛，照旧走 ADR 0008 的会话认证、没有旁路：

```
PATCH /api/ai/agents/<id>          { enabled?, path_override? }
POST  /api/ai/agents/<id>/install
GET   /api/ai/agents/<id>/install
```

`PATCH /api/ai/settings` 与旧的两个安装端点一并删除。稳定错误码：
`ai_agent_unknown` / `ai_agent_disabled` / `ai_agent_not_installed` /
`ai_agent_install_unsupported` / `ai_agent_executable_invalid` /
`ai_agent_probe_timeout`，经 `app.py` 的**一个漏斗** `_agent_error()` 转成
JSON，文案在前端 i18n。`tests/test_error_codes.py` 的扫描范围因此扩到
`engine/ai_bridge.py`，并且认 `AgentError("<code>")` 这种写法——只扫 app.py
的话这批 code 一个都看不见，而看不见的门禁等于没有门禁。

### 8. 旧配置迁移：迁完就删旧键

`ai.codex_path` / `ai.claude_path` → `ai.agents.<id>.path_override`。
规则写成正则（`^(agent)_path$`）而不是列举两个名字，加第四个 Agent 时不用
再改；迁移走既有的原子落盘，**迁完立刻删掉旧键**——两份权威并存的话，
一边改路径另一边不知道，下次探测用哪份全看读取顺序。新结构已经有该 Agent
时旧键只是残留，丢掉、不覆盖新的。配置里不认识的 agent id 原样留着
（降级回旧版不该丢用户的设置），设置页照常工作。

### 9. 手动路径与第三方接口降级到详情里的高级设置

一级页面只回答一个问题：**这台机器上有哪些编码 Agent、现在能不能用**。
路径输入框、Base URL、密钥、wire api 一个都不在那儿。理由是使用频率：
自动探测覆盖了绝大多数情况，手填路径是探测失败时的兜底，第三方网关是少数
用户配一次的东西——把它们摆在首屏，用户读到的第一句话就变成「原来我得配
一堆东西」。

自定义路径改成**显式「验证并保存」**，不再靠失焦提交：issue #89 的成因正是
「打开设置再移走一次焦点，空字符串就把用户存好的路径删掉」。后端用与自动
探测同一套 shim 解析 + `--version` 验证，验不过就抛错、**一个字节都不写**；
草稿留在输入框里，「恢复自动检测」是另一次明确的点击。

一键 npm 安装也搬进详情：用户必须先看到将要运行的那条确切命令
（`npm install -g @openai/codex`）并确认。包名只从适配器的固定 install spec
取；没有 npm 时只引导去装 Node.js LTS，**绝不代下载安装器**；npm 说成了不
算数——后端重新真探测，起不来就如实显示「安装不可用」。

### 10. 「Tavotto 用 Agent」与「Agent 用 Tavotto」是两个概念

设置页分成两个方向明确的小节：

* **在 Tavotto 中使用编码 Agent** —— 借本机的 CLI 改图脚本（上面这一整套）；
* **在编码 Agent 中使用 Tavotto** —— 把 Tavotto 装进 Codex（ADR 0005/0006 的
  插件 + 技能 + 内嵌画布）。

**「本机装了 codex CLI」绝不写成「Tavotto for Codex 已安装」。** 第二节这次
只给一个「查看使用指南」的外链（常量在 `web/src/lib/brand.ts`，组件里不手写
仓库地址）：ADR 0012 的 `tavotto codex install/doctor --json` 仍是 Proposed、
在当前代码里还没有实现，**本次不另写第二套安装器**，也不虚构「已安装」状态。

## 后果

* 加第三个 Agent = 往 `AGENT_REGISTRY` 里放一个适配器 + 一个图标键，
  前后端都不需要改分支；`tests/test_ai_agents.py` 的 Fake Adapter 用例反证
  了这一点。
* `path_override` 来自 HTTP 请求体、最终会被 spawn，所以「是个文件」远远不够
  （CodeQL `py/path-injection`）。四道闸：非空且不含 NUL → `realpath` 归一化
  （`..` 与符号链接在**判断之前**解掉）→ 存在的普通文件且可执行 → **文件名必须
  指向该 Agent**。最后那条挡的是「把 Tavotto 指向 `/bin/sh`」那一整类：那不是
  「路径填错了」，那是拿一个任意可执行文件换掉将要被启动的程序。判据放得很松
  （只要求包含，`codex.exe` / `codex-cli` / `run-codex.sh` 都过）。
* 就绪检查依赖两家 CLI 的本地状态子命令。它们改名或去掉的话，探测会落到
  `unknown` → 界面显示「已安装」——**降级是安全的**（不谎报可用、不谎报
  需要登录），只是少了一档信息。
* 状态从一个布尔变成六档，前端的分支变多了；代价换来的是「没装」和
  「装坏了」终于分得开，而这两件事的下一步完全相反。
* `providers` 形状整体移除是一次破坏性契约变更。确认过没有仓库外消费者；
  真有第三方在读它的话，升级时会拿到 `KeyError` 而不是静默的错值。
