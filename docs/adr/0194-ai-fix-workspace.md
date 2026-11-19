# ADR 0194：AI 修复的工作区——启动脚本、原样试跑、一次授权、可度量

日期：2026-11-18 · 状态：**Accepted**（维护者 2026-11-18 四项裁决，见文末；1.0 豁免已给，登记 `docs/1.0-release-readiness.md` §1.8）
相关：[0149 AI 修复工具箱](0149-fix-toolbox.md)、[0147 内置执行器](0147-builtin-agent-runner.md)、[0143 生成可编辑版本](0143-convert-to-editable.md)（影子脚本）、
[0127 执行策略与回退](0127-execution-policy-and-unified-retry-guard.md)、[0192 Agent 工具面](0192-agent-tool-surface.md)。

## 问题

一份用户反馈（Windows 0.21.2）：脚本只定义画图函数，safe 被拦、native 回退跑整份模块没有图。用户开了内置 AI 修复：AI 跑了 18 次工具，重现了失败，
正确判断「是产品缺陷」并起草了反馈——但修不好。同样的项目交给通用编码 agent + Tavotto 插件，用户往往能用上。差距不在模型，主要在四处：

| | 编码 agent + 插件 | 内置 AI 修复（0149） |
|---|---|---|
| 能做的事 | 整个终端：读、新建文件、用用户的 Python 直接跑 | ~20 个窄工具；不能新建文件；Windows 上 `run_command` 每条一张危险卡 |
| 能看到的 | 先 `python fig.py` 看真实结果，再对照 Tavotto | 原样试跑被拒（`native_trial_not_observe`） |
| 什么算成功 | 图出来了 | 只认「原脚本在应用里原样跑通」 |
| 遇到产品缺陷 | 自己写个包装绕开 | 只能上报 |

## 决定

### 一、原则：护栏从「白名单 + 逐条点头」换成「工作区」

AI 修复可以做用户自己在终端里会做的事，但守三条：**原件一个字节不动**、**它新建的东西全在 Tavotto 自己的目录里、一键撤销**、**每一步都有审计**。
凭据位置硬拦（0149 §三）照旧；`outward` 档照旧逐次问。

### 二、启动脚本（launcher）：以原脚本的身份运行

* **是什么**：AI 写的一段 Python，放在项目的 `tavottofile/launchers/` 里（Tavotto 自己的目录，用户的代码文件夹里不多出文件）。有启动脚本的脚本 S，
  Tavotto 运行 S 时**执行启动脚本的字节、身份仍是 S**：`__file__` / `sys.argv[0]` / traceback 文件名都是 S，`sys.path[0]` 是 S 所在目录，
  捕获到的图挂在 S 名下——用户画布上原来那张面板直接更新（维护者裁决：图挂回原脚本）。
* **复用影子脚本机制**（0143）：safe 经 `--script-source`（`worker._redirect_open_code`，只换 runpy 读主脚本那一次；import 系统读 S 时仍是原件，
  所以启动脚本里 `from S import f; f()` 拿到的是用户的原件）；native 经 bridge 同名参数。入口恒 `__main__`。
* **为什么不会遮蔽 import**：启动脚本按字节执行，从不 import；`tavottofile/launchers/` 不进 `sys.path`。
* **身份与过期**：S 的源码修订（`script_revision`）把启动脚本的字节一并算进去：改 / 删启动脚本 = 计划过期、worker 重建。
* **只新建不覆盖**：每个 S 至多一份；AI 改它 = 新写一份替换，旧的进快照；撤销（`rollback` / 卡片上的撤销）= 删除并恢复上一份或无。
* **静态分析**：依赖调研 / 数据绑定 / 路由读启动脚本的字节（与影子脚本同一个 `lookup_active` 口径），因为真正执行的是它。

### 三、原样试跑（`try_run mode=native`）

AI 可以用项目的 Python 原样跑一次 S（native 档，`user_env`），拿到「直接运行 vs Tavotto 运行」的对照。属于会话授权范围（§四）。

### 四、会话开始授权一次（维护者裁决）

点「用 AI 修复」时一句话说明：AI 可以新建辅助文件（只在 Tavotto 的目录里）、用你的 Python 运行脚本、在 Tavotto 管理的环境里装包；
原脚本不改、可一键撤销。同意后这些动作不再逐条弹卡。逐次确认保留给：`run_command` 第②档（沙盒外任意命令）、改用户自己的环境（`select_environment` 等 `user_env` 里的改动）、`outward`。

### 五、成功标准

`finish fixed` = **S 在应用里出图**（无论经不经启动脚本）。

### 六、可度量（评测集）

`scripts/eval/fix_compat/`：反馈包与 Guardian 家族的合成最小复现做题，内置 AI 修复与「编码 agent + Tavotto MCP」用**同一个模型**各跑一遍，
轨迹逐步可分析。之后每个 AI 修不好的反馈包都进题库。§七的范围按评测结果定。

### 七、之后（依评测结果）

提示词从按错误码写死的打法（0149 的 3a–3i）改成原则 + 可检索的经验库；已知打法由确定性代码先跑，模型只处理剩下的。

## 维护者裁决（2026-11-18）

1. 启动脚本放 Tavotto 自己的目录（`tavottofile/launchers/`）。
2. 启动脚本画出的图挂回原脚本那张面板。
3. 会话开始授权一次。
4. 路线五步都做：启动脚本、原样试跑、评测集、工作区与提示词重构。

## 不做

* 不改、不删用户的任何原有文件；启动脚本不放进用户的代码文件夹。
* 不让启动脚本进 `sys.path`、不以模块名 import 它。
* 终端 `tavotto run` 不读启动脚本（那是用户自己敲的命令）。
