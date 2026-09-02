# ADR 0039：离线教程项目——包内资源、数据目录里的版本化副本与 Tutorial API

日期：2026-09-02 · 状态：已接受 · 关联：ADR 0001（Project / Canvas / Tab / Object）/
0008（会话认证）/ 0023（落盘权威）/ 0027（接入就绪度）/ 0029（Style / Spec 分层）

## 背景

Tavotto 此前没有任何教程 / onboarding：全仓搜 `tutorial` 零命中。项目选择器里新用户
只有「打开一个目录」一条路，而第一次打开的往往是自己那份还没接好注册表的图库——
第一印象是「这张图不能编辑」。

仓库根的 `examples/figures` 是给开发者与 CI 冒烟用的：它随源码走、不随 wheel 走；
`paper_style.py` 指定 Times New Roman（没装就回退，两台机器画出来不一样）；其中一个
脚本一次出两张图。它不是给用户的教程，也**不能**是——用户机器上没有仓库根。

教程要满足的边界比「放几张示例图」硬得多（共享规则 §4 / §5）：完全离线；不写只读的
site-packages / `.app`（macOS 上写一个字节就破坏代码签名）；不改仓库 `examples/`；不写
用户自己的工程；不装包；**打开时不执行任何脚本**；「重新开始」必须恢复干净状态而不动
用户另存的东西。

## 决定

### 1. 教程资源在 Python 包内，经 `importlib.resources` 访问

`src/tavotto/resources/tutorial_project/` 随 wheel / sdist / 桌面包分发：

```text
tutorial_meta.json            schema 1；tutorial_version、project_name、document_name、
                              document_id、expected_stems、editable_role_preferences、panels[]
tavotto_registry.json         两个脚本 ↔ 两个 stem
paper_style.py                只用 matplotlib 自带的 DejaVu：三个平台画出同一张图
fig1_kinetics.py              曲线 + 图例 + 科学字符（min⁻¹ / α / °C）→ Fig1_kinetics.pdf
fig2_correlation.py           散点 + 拟合 + 一条**故意** 7 pt 的说明 → Fig2_correlation.pdf
tavottofile/Tutorial.json     schema 3 的画布文档，两张图已放上画布、故意没对齐
README.md
```

访问只有一个入口 `engine/tutorial.resource_root()`（`importlib.resources.files("tavotto")`
→ 开发态源码树兜底），与 `engine/profiles.profiles_path()` 同一条纪律。「教程由哪些文件
组成」的唯一出处是 `resource_files()`（walk 目录，跳过 `__pycache__` / `.pyc` / 点文件），
复制、完整性、打包验证都读它——**没有一张手写的文件表**。

元数据里没有绝对路径、没有 gid：前端按 `panels[].key` / `stem` / `editable_roles`（manifest
的 role 名）找目标，按 `spec_issue.text_prefix` 找那条 7 pt 的文字。`document_id` 是前端
打开教程画布时**必须**用的 documentId：它是重置时清自动保存槽位的唯一依据。

### 2. 可写副本落在数据目录，目录名带版本号**和**资源指纹

```text
<data_dir>/tutorial/v<tutorial_version>-<资源内容指纹 12 位>/Tutorial/
<data_dir>/tutorial/v…/state.json     复制时间、版本、指纹、逐文件 sha256
```

Prompt 原案是 `v1/`。只带版本号意味着「改了资源要记得升版本号」，而这条纪律没有任何
技术信号提醒——漏了的表现是用户拿着旧副本、界面按新元数据找元素。把内容指纹放进目录名，
**改了资源就换目录**，旧目录原样留着（那是用户改过的东西，本模块不删用户目录）。

### 3. 打开教程不执行脚本；验证是静态的

`POST /api/tutorial/open` = `ensure_tutorial_copy()`（只复制文件）+ 既有 `open_project()`
（只读注册表 JSON；教程自带注册表，连静态起草都不需要）。脚本只在用户进入图内编辑、
明确要求渲染那一刻才由 worker 跑。`validate_tutorial_resources()` 读 JSON、`compile()`、
读 PDF 首页尺寸、扫 AST 里的外部数据调用 / 网络 import / 绝对路径 / 体积——一行用户代码
都不执行。测试里 worker 真跑教程脚本，那是测试，不是产品行为。

### 4. 普通打开保留进度，只补缺的；「重新开始」才整个换掉

| 情形 | `ensure_tutorial_copy()` 做什么 |
| --- | --- |
| 没有副本 | 临时目录里建完整 → rename 到位（`created`） |
| 有且完整 | 原样复用，用户的改动一个字节不动 |
| 缺文件 / 注册表读不了 | **只补缺的那几个文件**（`repaired=[…]`），其余仍是用户的 |
| `reset=True` | 建新副本 → 旧的 rename 成 `.Tutorial-*.old` → 新的 rename 进去 → 删旧的 |

「完整」的判据是**存在性**（文件都在 + 注册表能读），不是内容一致：用户写回过的 PDF、
改过的脚本都是进度，普通打开不该抹掉。任一步 rename 失败都把能放回去的放回去；Windows 上
被占用（`PermissionError` / WinError 5 / 32）报 `tutorial_locked` 并说清是哪类占用，复制
失败报 `tutorial_copy_failed`，两种情况旧副本都原样在。残留的 `.Tutorial-*.old/.tmp` 下次
`ensure` 顺手清。

### 5. 重置的范围精确到「只属于教程的」

`POST /api/tutorial/reset`：先 `close_project(pid, wait=True)`（worker 真退出、释放文件），
再原子换副本，再清两样、只清两样：`layouts/_autosave/<document_id>.json`（教程画布的自动
保存槽位）与 `baked_overrides/<项目 id>.json`（写回基线——副本换成原始 PDF 之后它描述的是
已经不存在的写回结果）。项目内的 `tavottofile/`（画布 / 导出 / 版本历史）随目录整个换掉。
**不碰**：别的项目的任何文件、别的文档的自动保存、全局最近列表（路径没变）、项目设置、
遥测同意、onboarding 状态（那是 Prompt 21 的本机状态）。教程之前是默认项目才继续是默认。

### 6. 教程进最近列表，带标记

`/api/projects/recent` 每项多一个 `tutorial: bool`，`project_status()` 同样。它走的是与普通
项目同一条打开路径（`touch_recent` 在 `open_project` 里），不为它开第二条；界面按标记显示
「教程」而不是数据目录里的路径，用户可以像别的项目一样从最近列表移掉（磁盘副本不动）。

### 7. API

```http
GET  /api/tutorial          available / problems[] / tutorial_version / metadata / copy{exists,
                            complete, missing[], registry_ok, version, resource_digest} / project{open, id}
                            —— 不回任何绝对路径
POST /api/tutorial/open     { default?: bool } → { project: ProjectStatus(+drafted/conflicts/reused),
                            tutorial: meta, reset: false, created, repaired[] }
POST /api/tutorial/reset    { default?: bool } → { project, tutorial, reset: true, cleared[] }
                            409 tutorial_locked / 500 tutorial_copy_failed（旧副本仍在并重新打开）
```

三个端点走 `security` 钩子（ADR 0008），客户端**不能**指定目的地。错误码
`tutorial_resources_missing / tutorial_resources_invalid / tutorial_copy_failed / tutorial_locked`
经 `app._tutorial_error` 一个漏斗转成 JSON，双语文案在 `errors:backend.*`。

### 8. 打包

wheel / sdist：`[tool.hatch.build.targets.wheel] packages = ["src/tavotto"]` 自然收进，前提是
文件不被 `.gitignore` 挡（`test_pyproject_keeps_resources_inside_the_wheel_and_sdist` 看住）。
桌面（PyInstaller）：**包内数据文件 Analysis 不会自己收**，`packaging/tavotto.spec` 的 datas
显式加 `tavotto/resources` ——顺手补上此前同样漏掉的 `tavotto/profiles`（出版规范；源码树与
wheel 里都在，冻结产物里本来是没有的）。CI 装 wheel 的冒烟经 `importlib.resources` 验一遍；
两条内置 runtime 的桌面冒烟①带 `--tutorial`：打开、两张图各渲染一次、重置。

## 后果

* 教程资源 9 个文件、37.5 KB（wheel 里多出的就是这些）。
* 改资源（哪怕一个字节）用户会得到一份新副本，旧副本留在旧目录里；数据目录会随资源
  改动次数增长，每份几十 KB。没有做「清理旧版本」——它们是用户改过的东西。
* 「完整 = 存在」意味着用户把脚本改坏了普通打开不会修，只有「重新开始教程」会；这是
  有意的（进度优先于整洁）。
* Prompt 21 的 UI 只消费这里的 API 与元数据，不得再从仓库根 `examples/` 读文件。
* `web/src/lib/api.ts` 的 `ProjectStatus` / `RecentProject` 多了 `tutorial?: boolean`；本阶段
  没有任何 UI 变化。

## 验证

`tests/test_tutorial.py`（47 条）：资源 / 副本 / API 边界 / 读 wheel 与 sdist 成员 / 解包后子进程
经 `importlib.resources` 真读 / worker 真跑两张图；变异反证 22 条（20 红，2 存活各自处置：M9
补用例，M22 是语义 no-op 删掉）；`scripts/smoke_app.py --tutorial` 在真进程上跑通。
