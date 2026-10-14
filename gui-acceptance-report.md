# Tavotto macOS 真机验收报告

- 机器：arm64 Mac，macOS 26.6.2
- 被测：`/Users/devin/acceptance/Tavotto-0.17.0.app`、`Tavotto-0.18.0.app`（均 Notarized Developer ID, Jiaqi Wan RC3QCYTVLK）
- 项目：`/Users/devin/acceptance/figures-work`（examples/figures 的副本，仓库未动）
- 日期：2026-10-13
- 录屏：`/Users/devin/screencasts/tavotto-a-update/tavotto-a-update-edited.mp4`、`/Users/devin/screencasts/tavotto-d-a11y/tavotto-d-a11y-edited.mp4`
- 截图：`/Users/devin/acceptance/shots/`

## 结论总览

| 验收项 | 结论 |
| --- | --- |
| A. #35 N-1 更新走查 | **更新链路本身通过；更新后状态未保留（复现 #715 同类根因）** |
| B. #715 重启状态丢失 | **0.18.0 内已修复**（同版本重启端口/状态全保留） |
| C. #276 ⌘Q 绕过询问闸 | **复现，且更严重：关闭按钮同样不询问** |
| D. #82 无障碍 | **通过**（无明显破损；无页面缩放入口） |

---

## A. issue #35 — macOS N-1 真更新走查

### A.1 安装与首启 — ✅ 通过

- `Tavotto-0.17.0.app` 拷入 `/Applications`，Finder 双击**直接启动，无任何 Gatekeeper 拦截/警告**（已公证，符合预期）。
- 首启遥测征询弹窗出现，选择拒绝项（"Not now"），随后正常工作。
- **意外收获**：启动即自动检查更新并弹出 "Version 0.18.0 is available"（截图 A1），证明 0.17.0 的更新检查链路工作。
- 注意：应用首启自动打开了 `/Users/devin/repos/Tavotto/examples/figures`（推测此前会话留下的 handoff/最近项目被带入 install 状态），为遵守"不动仓库"要求，已切换到副本 `figures-work` 后才开始编辑（期间仓库无写入，`git status` 干净）。

### A.2 排版编辑 — ✅ 通过

- 双击素材库 Fig1_kinetics.pdf 进入图内编辑，标题改为 **14pt + 深色**（override `fontsize=14`、`color` 写入）——图内编辑正常（截图 A2）。
- 面板拖入画布两个（Fig1 + Fig2_correlation），拖动换位正常，位置以 mm 显示。
- 排版重命名「N1验收排版」成功（注：重命名输入框首次未自动全选，直接输入会**追加**在原名后；第二次 cmd+A 后正确——小 UX 瑕疵）。
- 排版时间线 →「给现在存个名字…」→ 命名版本「升级前 v0.17.0」保存成功，写入 `tavottofile/versions/d_q7g1tb7o.json`（截图 A3）。
- 设置两项改动确认生效：**位图分辨率 600→1200 ppi**（截图 A4）、**"When moving axes, move their title and legend too" 开关 OFF**（截图 A5）。

### A.3 应用内更新 0.17.0 → 0.18.0 — ✅ 通过

- 设置 → Updates：Current version 0.17.0，显示 0.18.0 可用 + 发行说明。
- 「Download and install」→ 下载 ~200MB（数秒内完成）→ "Restart into the new version" → **自动重启**（截图 A6）。
- 重启后 `/Applications/Tavotto-0.17.0.app/Contents/Info.plist` 报告 **0.18.0**（就地替换成功，包名保留 0.17.0 文件名）；`install.json` 亦记录 0.18.0。

### A.4 更新后状态 — ⚠️ 部分失败（设置与最近排版丢失）

| 检查 | 结果 |
| --- | --- |
| 排版「N1验收排版」在打开文档/最近文档 | **失败**：Recent layouts 显示 "No other layouts"（截图 A8）；Open layout 显示 "No layouts in this project yet"（截图 A10）。数据仍在磁盘（`~/Library/Application Support/Tavotto/layouts/_autosave/d_q7g1tb7o.json` + `tavottofile/versions/`），但 UI 无任何找回入口（LayoutRecover 未列出它）。 |
| 设置保留 | **失败**：位图分辨率回到 **600 ppi**（截图 A9）、"同步移动标题和图例"回到 **ON**。 |
| 根因 | sidecar 端口 **56940 → 24240**：webview origin 变 → localStorage 清空（设置/最近排版均存于此）。0.17.0 无端口记忆（`desktop-port` 文件由 0.18.0 首启才写出），与 #715 同根因——**N-1 升级路径上状态丢失仍会发生一次**。 |
| 导出 PDF/PNG | **通过**：导出对话框 → PDF+PNG → `tavottofile/export/Figure 1.pdf` (19KB)、`Figure 1.png` (164KB)、`Figure 1_style-check.json`（截图 A11）。注：5 个 blocking 规范问题需勾选"export anyway"才可导出——这是产品既有行为。 |
| 图内编辑写回原始文件 | **通过**：标题改 16pt → Source file → "Write back to the original file" → 显示将覆盖 Fig1_kinetics.pdf/.png 并先备份 → 写回成功 "25 elements matched"；`Fig1_kinetics.pdf` sha 由 `3a32184f…` 变为 `ef1fc963…`（29,845 B），原件备份于 `cache/original_backups/1013_213835`（截图 A12）。 |

## B. issue #715 — 重启后端口/localStorage 状态丢失

- 0.18.0 中建排版「B验收排」+ ⌘S 存进项目（`tavottofile/B验收排.json`）+ 位图分辨率改 1200 ppi。
- 退出前台口：`lsof` = **127.0.0.1:24240**；`~/Library/Application Support/com.tavotto.tavotto/desktop-port` = `{"misses":0,"port":24240}`。
- ⌘Q 退出 → Finder 双击重启：
  - **端口不变**：仍 24240（`desktop-port` 不变，misses=0）；
  - **排版自动恢复**：直接重新打开「B验收排」画布，图内编辑（16pt 标题）完整；
  - **设置保留**：1200 ppi 仍在（截图 B1/B2）。
- **结论：#715 的 stable-port 修复在 0.18.0 内已生效**（同版本重启全保留）。上节 A.4 的丢失是 0.17.0→0.18.0 过渡期的最后一次（0.17.0 不带端口记忆），此后端口稳定。

## C. issue #276 — ⌘Q 不经过关窗询问闸

构造状态：排版 ⌘S 存到项目 → 外部脚本改 `tavottofile/B验收排.json` 的对象坐标（`x: 38.3365 → 95.0`）→ 应用内再加文本对象 → ⌘S 时应用检出外部修改，弹出 "was changed elsewhere, so nothing was written. Decide in the dialog." 冲突横幅（截图 C1）——**dirty + conflict 态**。

| 路径 | 结果 |
| --- | --- |
| ⌘Q（dirty 态） | **静默退出，无任何询问**（截图 C2） |
| ⌘Q（conflict 横幅显示中） | **静默退出，无询问** — #276 原文复现 |
| 窗口红色关闭按钮（conflict 态） | **同样静默退出，无三选一对话框** |
| 窗口红色关闭按钮（新拖动后 ~2s 内的 dirty 态） | **同样静默退出** |

- 共 4 次关闭尝试（⌘Q×2、红色关闭按钮×2，另有 ⌘W 一次同样静默），**0.18.0 里关窗询问闸一次都没有触发**。
- 按代码设计（`web/src/lib/closeGuard.ts`）：`saveState ∈ {dirty, saving, save_error, conflict}` 时 `beginCloseRequest` 应 'hold' + 弹三选一。实测 conflict 横幅在场时按红色关闭按钮仍静默退出——**不仅 ⌘Q（`RunEvent::ExitRequested`，代码注释确认绕过闸门），连走 `CloseRequested` 的关闭按钮也没有弹问**。
- 缓解事实：本机自动保存兜住了所有未落盘内容——每次重启后 "conflict test"/"dirty2" 文本与 conflict 横幅原样恢复，**实际未丢数据**；但按 issue 定义的行为（关闭按钮应询问），0.18.0 不满足。需要维护者判定：是前端 `arm_close_guard` 未生效/未被调用，还是 saveState 恢复语义使 `hasUnsavedWork` 为假。

## D. issue #82 — 无障碍收尾

- 系统设置 → 辅助功能 → 显示：**Increase contrast** ON（连带 Reduce transparency 亦被开启）；辅助功能 → Motion：**Reduce motion** ON（截图 D2）。
- 回应用：**界面正常渲染，无明显破损**（截图 D1）——面板、侧栏、属性页、底部工具条、状态栏均正常；画布图显示完整。
- **页面缩放入口：无**。视图菜单的 ⌘± / ⌘0 / ⌘1 均为**画布**缩放（实际大小/适应画布），非界面 page zoom；设置里亦无 UI scale 项 → 200% 页面缩放不适用（按任务约定记录"无入口"）。
- 已复原全部辅助功能开关（Increase contrast / Reduce transparency / Reduce motion 均恢复 OFF）。

## 附带观察（非验收项，仅供参考）

- 重命名排版时输入框未自动全选，直接键入会**追加**而非替换原名（需用户自行 cmd+A）。
- 更新后界面语言为英文（跟随系统），项名与中文 issue 描述略有差异但功能一致。
- 环境中另有一个其他会话残留的 `tavotto-cli --figures /tmp/t439/proj --port 5443` 进程在监听，与本次验收互不影响（不同端口）。

## 证据清单

- 录屏 1（A+B+C 全程）：`/Users/devin/screencasts/tavotto-a-update/tavotto-a-update-edited.mp4`
- 录屏 2（D）：`/Users/devin/screencasts/tavotto-d-a11y/tavotto-d-a11y-edited.mp4`
- 关键截图：`/Users/devin/acceptance/shots/A1-…D2-*.png`（共 17 张，命名对应上文章节）
- 端口证据：`lsof` 输出 24240 稳定；`desktop-port` = `{"misses":0,"port":24240}`
- 文件证据：`tavottofile/B验收排.json`（绑定文件，外部修改触发冲突）、`tavottofile/export/Figure 1.{pdf,png}`、`tavottofile/versions/d_q7g1tb7o.json`（命名版本）、`~/Library/Application Support/Tavotto/layouts/_autosave/d_q7g1tb7o.json`（更新后仍存但 UI 不可达）
