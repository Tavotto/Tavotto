# 从 Magplot 0.7 迁移到 Tavotto

2026-08-20 的改名是**干净断裂**：Tavotto 运行时不读任何 Magplot 时代的
路径与格式标识（理由见 `src/tavotto/engine/brand.py`）。给 0.7.x 用户的
迁移路是一个**一次性工具**，不是运行时兼容层：

```bash
tavotto doctor --migrate --dry-run   # 先看计划，一个字节不写
tavotto doctor --migrate             # 执行
tavotto doctor --rollback-migration  # 后悔药：删除迁移时创建的文件
```

`tavotto doctor`（不带参数）检测到旧数据时也会提示这条命令——你不需要知道
任何内部目录的名字。

## 迁什么

| 内容 | 说明 |
|---|---|
| 配置（最近项目、项目设置、渲染解释器等） | **合并**：Tavotto 已有的设置永远优先，旧配置只补缺 |
| 画布布局（含命名画布） | 复制 |
| 布局版本历史 | 复制 |
| 自动保存 | 复制 |
| 论文样式 | 复制 |
| 写回基线（baked overrides） | 复制 |
| AI 会话历史与快照 | 复制 |

不迁：渲染缓存（可再生）、导出成品（在你的项目目录或自选目录里，不在
应用数据里）。

## 承诺

- **只复制，绝不覆盖**：目标位置已有同名文件时跳过并逐条报告；
- **旧数据一个字节不动**：Magplot 的目录原样保留，确认无误后自行删除；
- **幂等**：重跑一遍不会做任何事；
- **可回滚**：迁移报告（数据目录 `migration/from-magplot.json`）记录本次
  创建的每一个文件，`--rollback-migration` 只删这些。

## 图库目录（你磁盘上的项目）

**注册表不需要迁移。** 旧图库里的 `mm_registry.json` Tavotto 一直读得懂
（下次写出时自动换成 `tavotto_registry.json`，旧文件保留）。项目目录里的
`canvases/` 等旧位置也是只读兼容的。

**只有 `magplotfile/` 要你自己搬**，这是本工具**刻意不碰**的一处——它在
你自己的项目目录里，不在应用数据里，而项目在哪只有你知道。不搬的话，
0.7 时代放在里面的命名画布、导出与布局版本历史在 Tavotto 里看不到
（文件本身一个字节没丢，仍在原处）。

**别用 `mv <项目>/magplotfile <项目>/tavottofile`。** 目标目录很可能已经
存在了——你只要在 Tavotto 里打开过这个项目，它就被建出来了（打开项目时
会解析导出目录，顺手创建 `tavottofile/export/`）。而目标已存在时，`mv` 的
两操作数目录形式是把源目录**移进**目标，结果是 `tavottofile/magplotfile/…`，
埋得比原来还深，Tavotto 照样一个都看不见——`mv` 还一声不吭地退出 0。

改用下面两行：目标在不在都对，且**绝不覆盖**你已有的文件。

```bash
mkdir -p <项目>/tavottofile
cp -R -n <项目>/magplotfile/. <项目>/tavottofile/
```

`-n` = 目标已有同名文件就跳过，与本工具「只复制、绝不覆盖」是同一个语义。
跳过了哪些不用猜，列出来看：

```bash
diff -rq <项目>/magplotfile <项目>/tavottofile
```

`... differ` 的那几条就是两边都有、内容不同的：**留下的是你现在这份**，
0.7 的那份仍在 `magplotfile/` 里，自己比对后决定要哪个。
`Only in ...tavottofile` 是你在 Tavotto 里新建的，不是冲突。

**旧目录一个字节没动**，确认无误后自行删除——与上面「承诺」一节同一套做法。

（`cp -n` 跳过文件时，macOS 返回 1 且什么都不打印，GNU 返回 0。**别拿返回码
当判据**，看上面那条 `diff`。）

## 项目包（.magplot 文件）

「打开项目包」不看扩展名、只认包内结构——`.magplot` 包可以直接在
Tavotto 里检视，不需要转换。新导出的包一律是 `.tavotto`。

## 桌面应用

0.7.0 桌面版的应用标识符与 Tavotto 不同（`com.erwanjun.magplot` →
`com.tavotto.tavotto`），Tauri 的升级检测互相看不见对方。

- **Windows**：Tavotto 的 NSIS 安装器会自动检测并**静默卸载**旧版
  Magplot（`installer.nsi` 的 `MigrateMagplot`，2026-08-25 起）——不管是
  双击新安装包，还是在旧版 Magplot 里点「检查更新」走的静默升级路径，
  装完只剩 Tavotto 一个。在此之前的安装包不带这段：装完会出现两个并存的
  应用，旧 Magplot 还在提示「有新版本」，表现是「应用内更新点了没用」。
  旧版的卸载不影响用户数据——数据在用户目录里，不在应用目录里。
- **macOS**：请先删除旧的 Magplot.app，再安装 Tavotto。

装好后跑上面的迁移命令接回旧数据。
