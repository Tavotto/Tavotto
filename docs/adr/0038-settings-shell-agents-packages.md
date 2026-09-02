# ADR 0038：稳定的设置外壳、编码 Agent 页面精简与受管环境的包管理

日期：2026-09-02 · 状态：已接受 · 关联：ADR 0015（编码 Agent 注册表与设置）/
0018（项目环境自动接手）/ 0019（受控依赖修复）/ 0021（`tavotto run` 与环境租约）/
0029（Style / Spec 分层）

## 背景

设置对话框此前是「按内容撑高、上限 86vh」的通用 Dialog：切到「编码 Agent」时外框
比「常规」高出一倍，切回去又缩回去，用户看到的是整块窗口在跳。十个分区里三个只有
一两行（侧栏行为 / 画布与编辑 / 快捷键），「样式与规范」一页里塞着一个 Segmented
切换两类完全不同的配置；「关于」页同时承担品牌、隐私、渲染环境（含完整解释器
绝对路径）、五条诊断项，渲染环境卡片在同一页出现两次。

编码 Agent 一级列表上每行第二行是 `codex-cli 0.42.0 · /opt/homebrew/bin/codex`——
内部包名与安装目录默认暴露；页面顶上一段「Tavotto 会自动发现……」的解释；
「Tavotto for Codex」是一张带边框的大卡片，里面只有一个外链。

包管理没有产品面：ADR 0019 的受管环境只能在脚本缺包时被动装一个包，用户没有
地方看「这个环境里装了什么」、没法升级或卸载、也不知道它是不是正在用。

## 决定

### 1. 外壳尺寸是合同，不是内容的函数

`SettingsDialog` 用固定外框：宽 `SHELL_WIDTH = 760`、高 `SHELL_HEIGHT = 600px`
（Dialog 新增 `height` 属性；`max-h-[86vh]` / `max-w-[calc(100vw-2rem)]` 仍由 Dialog
兜底，小屏上按它收缩）。标题与导航固定，**内容区自己滚**（`[data-settings-content]`
`overflow-y-auto`）。切分区时外框一个像素都不动——`e2e/settings-shell.spec.ts`
切遍十一个分区逐个比 `boundingBox`。

窄于 640 CSS px（等价于小窗口或 150% 缩放）时导航从左栏变成顶部一行可横滚的分区
条，内容区照旧独立滚动。desktop / browser 复用同一个外壳，没有平台分支。

切页策略：内容区滚回顶部，焦点留在导航（用户正在导航）。导航是 roving tabindex：
Tab 只落在当前项，↑ ↓ ← → Home End 在项之间走并搬焦点。

### 2. 十一个分区，两个别名表

```text
general    常规        语言 / 自动保存 / 恢复默认布局 / 快捷键速查表
interface  界面        侧栏钉住 ×2 / 拖动坐标轴带关联元素 / 去画布设置
project    项目        当前项目 / 脚本数 / 导出目录 / 备份目录 / 只读
style      样式        Style 清单（ProfilesSettings kind="style"）
spec       规范        Spec 清单 + 本项目绑定关系（kind="spec"）
export     导出        导出默认值（Style / Spec 不再混在这里）
ai         编码 Agent  （id 不改，AiPanel 按它深链）
packages   包管理      新增
diagnostics 诊断       新增（从「关于」拆出）
update     更新
about      关于与隐私  产品 / 许可证 / 匿名用量统计
```

旧 id 走别名：`profiles → spec`、`canvas → interface`、`sidebars → interface`、
`shortcuts → general`（`resolveSection()`）。不认识的 id 落回上一次的分区，不白屏。

导出面板「编辑规范」深链到 `spec`，并带 `returnTo: 'export'`：关掉设置时回到导出
面板（`uiStore.settingsReturnTo`，闭集只有这一个值；每次打开都重置）。

### 3. 编码 Agent 一级页面：名称 · 版本号 · 状态，仅此

```text
[图标] Codex          0.151.0    可用            [开关] ›
[图标] Claude Code               需要登录        [开关] ›
```

移除：顶部解释段、第二行的 `包名 版本 · 路径`、「Tavotto for Codex」卡片外框与
说明段（只剩一行「名字 + 查看使用指南」）。**版本号只显示数字部分**
（`agentVersionLabel`）：`--version` 的第一行有时是 `codex-cli 0.151.0`，有时是 shim
的报错（带完整路径）——抽不出版本号就**不显示**，原文只在详情里。第二行只在未安装
/ 装坏了时出现（说下一步）。

路径、检测来源、找过的位置、就绪检查、安装命令全在详情里，并各有「复制」
（`settings/CopyButton.tsx`）。图标沿用本地 lucide 线性图标 + 中性色块：不加载远程
图片、不内嵌各家商标、表里没有的 key 落到通用图标（不会变成空白）。

Agent 状态与版本仍只来自 `GET /api/ai/capabilities`（ADR 0015 的唯一发现服务），
前端不猜路径。

### 4. 诊断页：健康状态 + 复制诊断 + 导出诊断包

首屏只有三件事：健康检查（坏的在前、坏的说原因、好的只有名字）、「复制诊断」、
「导出诊断包」。**`cli_*` 检查项不在这里显示**——Agent 页已经说清了；诊断包里照旧带。
渲染环境不正常时恢复卡片常驻；正常时它只在「技术详情」折叠区里出现**一张**
（此前「关于」页有两张）。解释器绝对路径、切换解释器入口都在技术详情里。
内置包版本清单从渲染环境卡里删掉，归包管理页。

「复制诊断」走新端点 `GET /api/diagnostics/summary`：与诊断包**同一份**
`build_report()`（已脱敏）摊平成文本（`diagnostics.render_text`），界面先把文本摆
出来让用户看过再复制——不在前端另拼一份采集。

### 5. 包管理：目标环境只有一种，操作是作业

**目标只有这个项目的 Tavotto 受管环境**（ADR 0019 §九，项目作用域）。系统 Python、
用户的 `.venv`、内置 runtime 都不在这条面上——`create_package_job(project, op, spec)`
的签名里没有解释器参数，作业里的解释器只能是 `managedenv.python_of(project)`
（用例结构性钉住）。没有环境时第一次安装顺带创建（与缺包修复同一条 `_create_managed`）。

两份清单（`list_managed_packages`）：

* **内置** = 基础栈（`BASE_PACKAGES`）+ 它们在**目标环境里现算**的传递依赖闭包 +
  pip / setuptools / wheel（`protected_distributions`）。只读；卸它一律
  `package_protected`。不在源码里抄一份 matplotlib 的依赖清单——那会随版本漂。
  环境还没建时退到内置 runtime 的 manifest 清单（`builtin_source = bundled_runtime`）
  或「创建时会装上」（`planned`）。
* **用户安装** = `environment.json` 账上的 `installed_by_tavotto`，每条带
  `reason`（`missing_dependency` 缺包修复装的 / `user_requested` 用户自己装的）、
  请求的规范、账上版本、**环境里的实际版本**与状态（`installed` / `missing` /
  `changed`）、是否落在保护闭包里（用户自己装了个 numpy → 只读）、账上谁依赖它。

盘点走 `importlib.metadata`（一次子进程 `inventory()`，不解析 `pip list` 的输出），
环境不存在时一个子进程都不起。

三种操作 install / update / uninstall 都是**两步**：`POST /api/engine/packages/plan`
形成作业（不改任何东西：语法、目标、保护、磁盘、忙都在这一步判；卸载时把依赖者
交回去），`POST /api/engine/packages/run` 只收 `job_id`。作业绑定项目 + 环境指纹
（`repair_plan_stale` 同一条判据）、有效期同 plan。与缺包修复共用：

* **同一个执行器** `_run_pip`（`_pip_install` / `_pip_uninstall` 是它的两个 argv 出处）；
* **同一把环境锁** `pool.mutating_environment` → `envlease`（safe worker / native
  会话 / 修复 / 作业四方一张表；有 native 会话时 `environment_in_use_by_native_session`）；
* **同一份脱敏** `_sanitize`、同一个自检 `worker_self_test`、同一条记账
  `managedenv.record_install` / 新增 `forget_install`。

pip 用法上只加了两条：`--upgrade` **只**给 update（策略仍是 pip 默认 only-if-needed，
升它不顺手升依赖）；`pip uninstall -y`（确认发生在界面上）。install 默认 argv 一个
字节没变（既有用例逐字节钉住）。

验证：install / update 后 `inventory()` 里必须有它（`package_not_found_after_install`），
uninstall 后必须没有（`package_still_installed`）；随后 `probe_environment`（matplotlib
在）+ `worker_self_test`（真起一次 worker）——**环境改完必须还能画图**，不能就标
`incomplete`。成功后 `projectenv.remember(...)` 让这个项目用这个环境（与缺包修复
同一条处置：装进去却不用它，用户看到的是「装了怎么还缺」）。

**没有回滚。** pip 没有事务（ADR 0019 §八），本 ADR 不假装有：每次改动前后各记一份
`pip freeze`（脱敏后）到 `<env>/snapshots/`（最多 12 份），界面上常驻一句「没有回滚，
有快照，坏了可重建」。取消一律标 `incomplete`。

磁盘：install / update 前 `shutil.disk_usage` 少于 200 MB → `package_disk_low`
（卸载不查——它正是腾空间的动作）。

### 6. 样式 / 规范拆成两个分区

`ProfilesSettings` 按 `kind` 渲染，不再有 Segmented。规范页顶部一行说清**本项目**
现在按哪套检查、用的是选择时的快照 / 跟随全局 / 内置默认（判据只有
`lib/specBinding.resolveDocumentSpec` 一份，与导出面板同一个）；全局改了而项目仍按旧
快照时给「更新到当前」。内部 id / 版本 / 修订号进「详情」折叠区（`profileText.ts` 的
纪律：默认视图不出现 id 与版本号）。不复制 Profile store。

## 后果

* 设置外壳有了尺寸合同；代价是短分区（常规）下半屏留白——这是有意的，留白比跳动便宜。
* 编码 Agent 一级页面信息变少了：装坏的 shim 那种「版本」现在一个字都不显示，用户
  要进详情才看得到原文。这正是想要的：一级页面上出现的每个字都该是用户要认的。
* 包管理是本仓库第二个会往磁盘装第三方代码的入口，但它没有第二套机制：pip 调用、锁、
  脱敏、自检、记账全部复用 ADR 0019 的那一份，新增的只有作业模型与盘点。
* 「内置」由依赖闭包现算，意味着 matplotlib 换版本、依赖变了，保护范围自动跟上；
  代价是每次打开包管理页起一个子进程（几百毫秒）。
* 环境状态 API（`managedenv.state()`）多带了 `reason` 枚举（两值，不是用户内容）。
* 卸载不可逆这件事写在页面上而不是藏在确认框里。用户看到「没有回滚」四个字会犹豫
  ——这是正确的犹豫。
