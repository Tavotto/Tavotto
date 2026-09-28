# ADR 0104：同一份插件目录兼作 DeepSeek Harness bundle

日期：2026-09-28 · 状态：**Accepted**（用户 2026-09-28 要求「针对 dsh 做一个专门的插件包」；实现随本 PR）
相关：[0103 Claude Code 插件](0103-claude-code-plugin.md)、[0043 插件发行分支](0043-plugin-stable-channel.md)、
`docs/implementation/multi-host-mcp/hosts.md`「DeepSeek Harness」。

## 问题

DSH 以前只能靠配置生成器打印一段 Cordis YAML，用户手工合并进 profile 的 `cordis.patch.yml`，技能再手工复制到
`.dsh/skills/`。DSH 自己的扩展单位是 **bundle**：一个 npm 包，`package.json` 里写 `dsh.bundle.patch`，用
`dsh plugin --profile <名> add <规格>` 经 pnpm 装进 profile（deepseek-harness `21638c56` 的
`.agents/notes/implemented/architecture/2026-08-05-profile-plugin-bundles.md`）。难点只有一个：补丁里**没有
「本包目录」变量**，而 mcp-client 的 `command` 要绝对路径；`!!js` 能用的只有 `process.*`、`ctx.*`、
`dshHomePath()`，`ctx.baseUrl` 指的是根配置目录，不是 bundle 目录。

## 裁决

1. **不另起包、不另起分支。** `codex-plugin/` 加 `package.json`（`name: tavotto-dsh`、`private: true`、
   `dsh.bundle.patch`）与 `dsh/`（胶水插件 + 补丁）。用户规格是 pnpm 的 git 子目录写法
   `git+https://github.com/Tavotto/Tavotto.git#plugin-stable&path:/codex-plugin`——与 Codex / Claude 两个市场同一个
   提交、同一个目录。不发 npm（`private` 挡住误发）；三份文件进 `STAGE_REQUIRED`，发行链不改。
2. **包目录由胶水插件提供，补丁里不写用户代码。** `dsh/index.js` 按 `import.meta.url` 算出包根，把
   `{command, args, toolCallTimeoutMs, skillsDir}` 作为普通 Cordis 服务 `tavotto` 提供；补丁里 mcp-client 行与
   技能行 `inject: [tavotto]`，`!!js` 只读 `ctx.tavotto.*` 与 `process.cwd()`。这是 dsh-web-app 的 `webStartup`
   同一个做法（DSH 自己拒绝了「bundle manifest 里放 pre-boot context 模块」，选的就是「普通插件 + 服务」）。
   配置生成器那条「不生成宿主执行的代码」不受影响：这里执行的是**包里随发行件签出的**那一个文件，不是
   为某台机器生成的表达式。
3. **同一个启动器、同一个超时出处。** POSIX 上 `/bin/sh <launch.cmd> <server.py>`（不依赖 pnpm 保不保留执行位），
   Windows 上 `cmd /d /c`（Node 不经 shell 起不了 `.cmd`）。超时读 `.mcp.json` 的 `tool_timeout_sec` 换成毫秒。
   serverName 与 `.mcp.json` 同名，工具暴露为 `mcp__tavotto__<原名>`。
4. **技能靠第二个本地技能提供者。** base 里那行 `skill-filesystem` 不动（补丁会整条替换 config，改它等于抹掉
   用户的设置）；另插一行 `providerName: tavotto`、`includeDefaultRoots: false`、`customSkillDirs` 只指包内
   `skills/`、`watch: false`。
5. **授权 = dsh 启动目录。** DSH 的 MCP 客户端 `capabilities: {}`（不回 roots、不支持 elicitation），server
   退回自己的 cwd；补丁照 DSH 官方 MCP 指南写 `cwd: !!js process.cwd()`。在 HOME 里启动 dsh 会把 HOME 授权
   出去——README 与技能都明说「在项目目录里启动」。
6. **不做**：内嵌画布（DSH 只投影文本与图片）、npm 发布、`dsh plugin` 以外的安装入口。YAML patch 路线保留，
   与 bundle 二选一（同名 serverName 的第二行加载失败）。

## 看护

`tests/test_dsh_bundle.py`：包身份与版本；`dsh.bundle.patch` / `main` 指到真文件；`files` 覆盖胶水读的路径；
补丁三行的 name / inject / serverName / 字段；用真 `node` 跑胶水的 `launchSpec`（darwin / linux / win32 三种）核对
启动器、server、毫秒超时与技能目录；技能名与描述长度合 dsh 目录的规矩；`STAGE_REQUIRED`；README 那行由
`brand.DSH_*` 拼出。十一条变异（包名、胶水行名、serverName、秒当毫秒、直接执行启动器、`files` 漏 `mcp/`、
打开默认技能根、去掉 cwd、摘 staging 要求、README 分支改 main、技能目录拼错）各自打红。

## 验收证据（2026-09-28，DSH 0.1.7-rc.2 从 npm 装进 scratch 前缀，macOS）

全程隔离的 `DSH_HOME` / `DSH_AGENTS_HOME`，不碰用户环境；模型是本地假的 Anthropic Messages 服务
（`DEEPSEEK_BASE_URL`），第一轮下发 `tavotto_health` 调用。

- `dsh plugin --profile headless add <目录>` → profile 的 `dsh.profile.bundles` 多出 `tavotto-dsh`；
  `--dump-config` 里三行按预期组装。
- `dsh --profile headless` 真跑一轮：模型收到 10 个 `mcp__tavotto__*` 工具；`tavotto_health` 返回
  「引擎就绪、根来源 cwd = 启动目录」；技能目录里有 `tavotto-figure`。反证：删掉技能那一行后目录里就没有了。
- 规格换成 `git+file://<本地仓库>#plugin-stable&path:/codex-plugin`（与真实规格同形）：装进来的包带着画布产物与
  启动器；同样跑通；发行分支加一个提交后 `dsh plugin … update tavotto-dsh` 取到新内容。
- `dsh web --no-open --port 0` 带 bundle 启动，Tavotto server 起来。
- 尚未做：从 GitHub 真实 `plugin-stable` 安装（要等下一次 promote）、真模型、Windows、Desktop 版 DSH。
