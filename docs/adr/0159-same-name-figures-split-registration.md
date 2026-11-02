# ADR 0159：同名不同脚本的图分开登记

日期：2026-11-02 · 状态：**Accepted**（维护者 2026-11-02 裁决：反馈包 TVD-TZQ9 选「同名不同目录自动分开登记」+ 首次登记走 A3；1.0 豁免已给）
相关：[0013 runtime 素材身份](0013-runtime-figure-assets.md)、[0148 智能修复路由](0148-fix-router.md)（同日修订：冲突不再路由进 AI）；
`docs/rules/backend/registry-discovery-and-probe.md`「冲突只报告不裁决」一节；#1251（`artifacts`：磁盘文件 → 图）。

## 动机

项目里的图按 **stem**（保存文件名去掉扩展名）做扁平命名空间：注册表 `stem → 脚本`，runtime 身份 `runtime:<脚本>#<stem>`，
覆盖记录、缓存、预览产物、编辑请求都按它找图。科研里极常见的「复制一份脚本换新样本、输出到另一个文件夹」于是必然失败：
第二份脚本跑完、图也捕获到了，登记时 `multiple_stem_conflict`（不静默抢走原来那份的图是对的），而手工指认只能把图名**改归**
新脚本——原来那份就丢了这些图。用户两份都要，没有正确选项。

## 决定

1. **登记时分开**（`probe.register_probed` → `_split_same_name`）：本次产出的 stem 已被另一份仍在磁盘上的脚本登记时，
   若**证明了两份写的是同一个文件**（注册表 `artifacts` 与本次执行写出的文件有交集）→ 照旧 `multiple_stem_conflict`、注册表零改动；
   其余一律分开：这份的图以 `原图名@脚本文件名` 登记（`figcapture.aliased_stem`；再撞就 `-2`、`-3`…），别名表
   `stem_aliases: {原图名: 登记图名}` 写进这个脚本的注册表条目。还剩一张真冲突就整次失败、一个别名也不写。
   * 为什么不是「两边路径都已知且不同才分开」：`artifacts` 只记项目根一层的同名原件（`figcapture.find_artifacts`），存进子文件夹的图
     两份脚本都没有路径记录——那条判据在 TVD-TZQ9 的形状上永远判不出「不同」。被分开的最坏情形是两份脚本恰好写同一个子文件夹里的
     同一个文件：各自都能编辑，磁盘上那个文件归最后保存的那个——与用户分别运行两份脚本的结果相同。
2. **在捕获层改名**：别名表是执行身份的一部分——`ExecutionSpec.stem_aliases`，唯一来源 `registry.stem_aliases_for`（`safe_spec` /
   native 构造函数缺省就从注册表取，池重开会话用会话自己那份）。只有非空时 `worker_argv` / `bridge_argv` 才多 `--stem-aliases` 两个 token、
   `stable_payload()` 才多一键——没有别名的 argv 与稳定身份逐字节不变（golden）。safe worker 与 native runner 在收图之后
   （pyplot 兜底之后）、图进入编辑会话之前调同一个 `figcapture.apply_stem_aliases`，之后 instrument、描述符、预览、编辑请求、runtime
   身份、覆盖记录全都只见登记图名，不会和原来那份串。找原件按「它写文件时的名字」（`savefig_file_stem`，与 `panel-2 → panel` 同一张表）。
   浏览器 playground 没有注册表，不受影响。
3. **别名粘住**：`discover.register` 保留已有别名（登记图名还在本次 stems 里就留），下一次运行探测结果里只有登记图名，不会退回原名撞上
   原来那份。登记图名被别的脚本认领走（手工指认）时，指向它的别名跟着摘掉。
4. **首次分开那次执行不发布（A3）**：那次执行里的图还叫原名。`register_probed` 回 `stem_aliases_added`；调用方（准备会话的
   `_finalize_script_attempt`、`/api/registry/probe`）不物化它的缓存，桌面拥有的 native 会话收掉；池的复用判定多一条
   `_aliases_current`（会话带的别名表 ≠ 注册表此刻的 → 「图名别名已变」，重起）。用户第一次打开新脚本的图时按登记图名重跑一次，
   之后照常。代价：只在第一次，素材栏缩略图在那次打开之后才出现。
5. **界面显示原图名**：`runtimeasset.list_assets` 对分开登记的图带 `display_name`（原图名），素材栏用 `runtimeAssetName()` 显示它；
   身份（`stem`、id）不变。`Registry.display_name()` 是后端同一判据。

## 不做 / 已知限制

- 不改用户脚本；不改错误文案与失败卡（真冲突的卡与原来一样，ADR 0148 修订后主按钮是「发送问题反馈」）。
- 原归属脚本写在项目根、新脚本写在子文件夹的同名图：新脚本的描述符会把根目录那份同名文件也算成自己的原件（`find_artifacts`
  只按名字看根目录），因而判成「同一个文件」，仍报冲突。要根治得让捕获层记下每张图 savefig 的真实路径（含子文件夹）——另立。
- 画布面板标题、图层名等其它显示位置仍显示登记图名；首批只改素材栏。

## 看护

`tests/test_same_name_split.py`（注册表清洗 / 粘住、捕获层改名、spec 与 argv、池的复用判定、显示名）；
`tests/test_script_probe.py::TestRegistryEffects`（真跑：复制脚本分开登记、再跑直接产出登记图名、同一文件仍冲突）。
