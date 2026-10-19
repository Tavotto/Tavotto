# 04 · 安全模型

依据：安全审计（`01-existing-system-audit.md` §四）。原则：TAF 不新增任何执行路径与授权来源；
凡是 CLI 自身的隔离行为都标为「CLI 的行为，不是 Tavotto 的保证」，并在用到之前实测。

## 一、资产

用户脚本与原始数据（可能是未发表数据）；第三方 API 密钥（`config.json`，0700 目录）；本机用户权限；用户全局
Codex / Claude 配置；提示词、图中文字、列名等隐私内容；Tavotto 的 AI 历史库（明文 prompt）。

## 二、信任边界

1. 浏览器页面 → Flask：会话认证（0008）。TAF 端点挂在同一 app、不加公共路径、不读请求体里的自报身份。
2. Flask → Agent CLI：Agent 是**不可信执行体**。它的输出（文件改动、工具参数、自述「已完成」）一律由后端独立检查。
3. Agent 产出的代码 → worker：对抗性代码。safe 档不是沙盒（下节）。
4. 用户数据（CSV 单元格、列名、脚本注释）→ 提示词：不可信指令来源。
5. 项目目录 ↔ 候选工作区 ↔ 提交：只复制、提交前比对版本、单文件原子替换。

## 三、safe 档能保证 / 不能保证什么

| 能 | 不能 |
| --- | --- |
| 拦 `Path.unlink`、`os.remove/unlink/rmdir/rename/replace`、`shutil.rmtree/move`、`Path.write_text`；cwd 切临时目录；`savefig` 捕获 | 网络；读任何文件（含 `~/.ssh`、环境变量里的密钥）；`subprocess` / `os.system` / `ctypes`；`open(绝对路径, "w")`；CPU / 内存 / 磁盘（只有超时与 kill） |

推论：**TAF 不执行模型写的代码去「试一试」**。路径 B 里 Agent 改的是用户自己的脚本，提交后由刷新触发的渲染与
今天完全一样（用户发起的 AI 改写 → 刷新），没有新增执行；数据出图 v1 的脚本由 Tavotto 模板生成（ADR 0138 §四）。

## 四、Agent CLI 的写入面

| 现状 | TAF 之后 |
| --- | --- |
| cwd = 整个项目根；只靠提示词约束写入；diff 只看目标脚本 | 候选模式：cwd = 候选目录（`data_dir()/cache/ai_candidates/<sid>/`）；L1 比对候选树（只许目标脚本变）；spawn 前后对原项目做有界文件指纹，非目标文件变化即 L1 failed 并列出文件 |
| Codex `--sandbox workspace-write`；Claude `--permission-mode acceptEdits`，Bash 是否放行取决于用户全局 allow 规则 | 参数不变（不改用户全局配置，也不在 Prompt Pack 里可调）；Phase 3 入口条件：在两个 CLI 上实测「写 cwd 外文件」「执行 shell」的实际结果并记录版本（R-03） |
| `spawn_env()` 全量继承环境 | 不变（收窄环境会让用户依赖的 PATH / 代理配置失效，属于另一个决定）；在 R-07 里登记 |

## 五、提示注入

- 用户数据进提示词只在 data_to_figure，且只进**受控摘要**：列名、类型、计数、少量样本值，每个值截断并标注为数据；
  模型输出只接受结构化 JSON（列映射建议），后端校验列名真实存在、拒绝多余字段——模型无法借此写文件或执行代码。
- 改图任务不内联脚本内容（今天也不内联，Agent 自己读）；脚本注释里的指令属于用户自己的文件，风险与今天相同，
  缓解是候选目录 + 文件指纹 + 不可扩大的 `output_contract`。
- FigureBench `FB-D08`（`csv_inputs/injection.csv`）是这条的回归场景。

## 六、路径与提交

- 用户或模型派生的路径，碰文件系统之前一律 `projectenv.contained_path`；**不用** `app.safe_resolve`（它只放行 PDF / 图片后缀）。
- 复制候选时遇到符号链接：不跟随、不复制目标，目标脚本本身是符号链接 → 不建候选、降级到原地模式（并如实标注）。
- 提交：持 `script_guard` → 当前 sha256 == `base_sha256` → `scriptbackup.replace`。校验与写入之间的 TOCTOU 窗口与今天的
  revert 相同（进程内锁 + 跨进程靠 `ai_history` 的脚本认领），不宣称更强。
- 多文件提交：v1 **不支持**。`atomicio` 只有单文件原子；写回事务的「全量备份 + 逐个替换 + 失败回滚」是尽力回滚，
  不是事务。需要多文件的任务结局为 `partial` 并说明。

## 七、执行授权

验证渲染恒为 safe，不调用 `execpolicy.decide_fallback`，不继承 `_desktop_initiated()`；native 图（会话离线即
`NATIVE_SESSION_OFFLINE`，不回退 safe）的数据保持检查记 `unknown`。CLI / MCP 侧不 import `execpolicy` / `runroute` 的
既有测试保持；TAF 若要在 MCP 暴露，另开 ADR。

## 八、隐私

- Phase 0–5 **不加遥测事件**。需要时与其他待发事件攒成一次 `CONSENT_VERSION` 升版，属性只用闭集（任务类型枚举、
  结局枚举、验证 verdict 枚举、有界计数），绝不带提示词、路径、列名、脚本、图中文字、模型输出。
- 证据目录在 `data_dir()/cache/ai_tasks/`，不进诊断包；诊断只取 `taskdiag` 白名单投影（verdict / reason_code / 计数）。
- Harness Lab 只用 FigureBench 合成数据与开发者自己的测试项目；不读用户数据目录；真实模型基准是单独命令、显式计费。

## 九、TAF 绝不做的事

1. 用测试旁路（`--insecure-no-auth`）跑真实任务；加公共路径。
2. 信任请求体里的「桌面」自报，或让 Agent 流程借桌面授权走 native。
3. 把 safe worker 当成执行模型代码的沙盒。
4. 候选工作区用符号链接或硬链接。
5. 不核「只改了允许的文件」就提交；让 Agent 扩大 `output_contract`。
6. 不经 `contained_path` 把派生路径交给 open / spawn。
7. 往遥测、日志、诊断包写提示词、脚本、CSV 内容、列名、路径、模型输出。
8. 修改 `~/.claude` / `~/.codex`。
9. 无上限的修正，或在结果不确定时自动重来。
10. 让 Harness Lab 的产物改动安全面（`03-contracts.md` §九）。
