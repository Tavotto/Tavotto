# ADR 0170：所有 AI 编辑器按 Codex 的体验接入

日期：2026-11-06 · 状态：**Accepted**（维护者 2026-11-06：「我希望所有的编辑器都能获得和codex一样的接入体验」「当ai发现插件不是最新版的时候，要自动更新或者提醒用户更新」；1.0 豁免见 `docs/1.0-release-readiness.md` §1.8）
修订：`docs/rules/plugin/plugin-and-marketplace.md` 更新检查第三条底线（「绝不自动下载或执行」）由本 ADR 裁决 6 收窄
相关：[0121 应用内更新](0121-app-update-source-order-and-fallback.md)、[0134 同意版本 3](0134-consent-version-3-export-result-and-error-summary.md)、`docs/implementation/multi-host-mcp/hosts.md`

## 动机

点「接入」后，Codex 得到插件、技能和更新通道，其余编辑器只得到一条 MCP 配置，AI 读不到 15 KB 的 `SKILL.md` 和 7 份参考文档。更新检查只挂在 `handoff.py`，AI 走 MCP 工具时从不查版本，技能里「收尾提醒一次」到不了用户。

## 要保持的不变量

1. 点一下就接，不选文件夹（ADR 0009 §1b）。
2. MCP 服务端只有一份，桌面版仍写 `tavotto-cli mcp`。
3. 用户改过的文件不覆盖，断开不删不属于 Tavotto 的目录。
4. 工具调用不等网络，stdout 归协议。
5. 不让用户因为这项工作重新同意遥测。

## 第三路径

逐家做插件要维护七种清单，且 Claude 桌面版、Trae、WorkBuddy 没有静默安装入口；改装 Claude 插件还要联网并另备引擎。这里把问题换成：**技能随桌面版走**。桌面版已签名且自带 MCP 服务端，把同一份技能带进包里，接入和同步都是本机复制，版本天然和服务端一致。只有 Codex 插件自带服务端、不跟桌面版走，需要联网更新。

## 裁决

| # | 问题 | 裁决 | 落点 |
| --- | --- | --- | --- |
| 1 | 技能从哪来 | `packaging/tavotto.spec` 把 `codex-plugin/skills/tavotto-figure` 收进 `skills/tavotto-figure`，缺了拒绝打包 | `hostskills.skill_source` |
| 2 | 写到哪 | 各编辑器的用户级技能目录（查证 2026-11-06，见 hosts.md）；Trae 只写已存在的编辑器目录 | `hostskills.SKILL_DIRS` |
| 3 | 谁的文件 | 副本带 `.tavotto-skill.json`（版本 + 内容 sha256）。记录不符或没有记录一律 `modified`，不覆盖、不删除 | `hostskills._classify` |
| 4 | 没有技能目录的编辑器 | Claude 桌面版不做 `.mcpb`（要弹窗确认、私发包不自动更新）。MCP 工具 `tavotto_guide` 回技能全文，`section` 只认 `references/` 里真实存在的文件 | `tavotto_mcp/guide.py` |
| 5 | 遥测 | `tavotto_guide` 不进 `usage.TOOLS` 与遥测闭集，走现有 `other`，合同和 `CONSENT_VERSION` 不动（维护者裁决）。`test_mcp_usage` 显式写出这一个例外 | `usage.py` |
| 6 | 更新 | 技能：桌面版启动时同步，只做本机复制。Codex 插件：桌面版启动时走现有 `tavotto codex upgrade`（下载、校验、换目录），失败只提示。第三条底线收窄为「**插件和 MCP 进程**绝不自动下载或执行」，桌面版经用户可关的更新开关可以 | 第三个 PR |
| 7 | 关闭方式 | 不加新设置项：跟随桌面版现有的后台自动下载开关，另有 `TAVOTTO_DISABLE_UPDATE_CHECK=1`（维护者裁决） | 第三个 PR |
| 8 | AI 看到什么 | MCP 结果带 `update`，话术按安装方式给。缓存在调用线程读，到期后台刷新。`TAVOTTO_NO_UPDATE_CHECK`、`TAVOTTO_DISABLE_UPDATE_CHECK`、设置里的 `auto_check` 任一关闭就不联网（`docs/rules/backend/update-check.md`「关了一个包都不发」） | `tavotto_mcp/updatenotice.py` |
| 9 | 技能失败 | MCP 已写成时不回滚，整次 `ok=false`，带 `mcp_connected=true` | `hostconnect.connect` |

## 不做 / 边界

* 内嵌画布不写新功能。官方写明支持 MCP Apps 的只有 Cursor、VS Code、Claude 桌面版，其余查不到或不支持，同一组工具走完后在桌面版看图。
* Cursor、VS Code 也读 `~/.claude/skills`，同时接入 Claude Code 时可能出现同名技能，待真机确认。
* Trae 国际版、WorkBuddy 的技能目录只有本机观察或第三方说法，待真机确认。
* 更新清单在 GitHub Release，大陆常连不上。不往镜像加文件（要补上传、校验与回填）：后台线程问不到 GitHub 时读镜像已有的 `index.json`，版本号够提醒用，不下载任何东西（维护者同意补镜像，2026-11-06 选了这个不动生产的做法）。
* pip 安装没有 MCP 服务端，也没有技能来源，行为不变。

## 验证

* `tests/test_host_skills.py`：内容完整、幂等、改过不覆盖、加文件算改过、同名目录不动、符号链接不动、断开只删自己的、中文和空格路径、guide 白名单。
* `tests/test_hosts_connect.py`：接入多一步 `skill`、失败如实报告、已装 Claude 插件不复制。
* `tests/test_mcp_usage.py`：guide 落 `other`。

## 反证

| 变异 | 红在 |
| --- | --- |
| 去掉 `install_skill` 的「改过不覆盖」分支 | `tests/test_host_skills.py` 中改过、加文件、同名目录、符号链接 4 条，`tests/test_hosts_connect.py` 1 条 |
| 去掉 `_current` 的 `network_allowed()` 判断 | `tests/test_mcp_update_notice.py::test_user_switches_stop_the_background_request` 两条 |
| 去掉 guide 的 `section` 白名单 | `tests/test_host_skills.py` 中路径穿越各写法与 server 接线共 8 条 |
