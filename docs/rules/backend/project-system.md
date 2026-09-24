# 项目系统（后端侧）

> 原文出自 `src/tavotto/AGENTS.md`「项目系统（后端侧）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **项目（多开，2026-08-16 起）**：`app.py` 无默认路径；`PROJECTS: dict[id, ProjectCtx]`
  同时端着多个图库，`DEFAULT_PROJECT` 只是「不带 pj 的请求落到哪」。未打开项目时
  API 回 409 `code=no_project`，前端渲染 ProjectPicker。用户级配置在
  `engine/config.py`（macOS `~/Library/Application Support/Tavotto/config.json`，
  测试用 `TAVOTTO_CONFIG_DIR` 重定向——conftest 已全局隔离）。
  每项目设置（导出/备份目录、`allow_write_back` 只读）经
  `PATCH /api/project/settings`；写回类端点先过 `_write_back_forbidden()`。
- **每标签页一个项目**：请求靠 `pj` 认领（`_request_ctx()`）——**查询参数与
  请求头两条路都必须认**：fetch 统一带头，但 `<img src>` 与 EventSource 加不了头，
  只做一条会让一半 API 串到别的项目上。指名一个不存在的 pj 一律 409，
  **绝不悄悄落到默认项目**（那会让标签页对着另一个图库继续编辑）。
  `open_project()` 复用已打开的 ctx，**不再拆别人的台**（旧实现每次切换都
  stop_watcher + shutdown_all + interrupt_all）；关项目走 `close_project()`。
  registry 因此不能再是模块全局：`engine/registry.Registry` 可实例化，
  模块级函数代理到默认实例（老调用方式与测试不动）。worker 池键与
  watcher 也都带项目路径（`pool.norm_dir`，三处同一把尺）。
  **写回基线（baked overrides）同样按项目分键**：`baked_overrides/<项目id>.json`，
  `load_baked(ctx)` / `append_baked(stem, patches, ctx)` 默认取 `current_ctx()`；
  旧的全局 `baked_overrides.json` 只作一次性迁移源（按 `ctx.registry.for_stem`
  过滤搬入，**不删旧文件**——别的项目还要迁；迁过一次分键文件即唯一权威，
  哪怕是空 dict）。`scan_panels` 里的 baked 表是**局部变量**，绝不再做模块级
  缓存——那就是「A 项目扫一遍素材，B 项目的基线全被换掉」。
  **基线绑定文件身份（2026-08-31）**：写回 commit 时随版本条目记
  `files: {名字: {sha1, mtime_ns, size}}`，「文件还是不是写回时那份」的唯一
  判据是 `_baked_matches_file`（size 异→失效；mtime_ns 同→有效零 IO；
  mtime 变 size 同→读 sha1 定夺，touch/网盘同步不误判），`scan_panels`
  据此随 `baked_overrides` 下发 `baked_current`。**失效时基线照发**——
  失效的是「文件长这样」这个假设，不是用户的那组修改；前端
  `isJustBakedBaseline` 读 `baked_current` 决定要不要重新走引擎渲染。
  用户在 Tavotto 之外重跑自己的构建脚本刷新产物，就是这条判据存在的理由：
  没有它，画布预览会永久停在「脚本原值」而图内编辑显示 script+overrides
  （两边都不报错）。旧条目无 `files` 时退回「mtime 晚于写回 ts+120s → 失效」。
  SSE 事件带 `pj`，前端只处理属于本标签页项目的那些。
- **派生状态刷新只有一条编排**（ADR 0025 + 0026）：`app.refresh_project()`
  → `engine/project_refresh.refresh_project_index()`。四个调用方
  （`/api/project/refresh`、`/api/registry/scan`、probe 成功、手工登记）
  加上项目 watcher 全部走它，**谁都不许自己 `discover.merge`、自己 reload
  注册表、自己发第二套 `registry.changed` / `assets.changed`**。
  `engine/project_watch.py` 只负责**发现**：整棵树的轻量快照
  （文件集合 + `(size, mtime_ns)`）、防抖批次、调刷新。它自己发的事件只有
  `panel.file_changed`（这张图的源码变了，请重渲染）与 `project.error`。
  worker 失效在两边各管一半：注册表**关系**变了归刷新，脚本**内容**变了
  与 `paper_style*` 归 watcher。「哪些文件算素材」只有 `iter_assets()`
  一处判据，脚本遍历只有 `discover.iter_all_scripts()` 一处，
  「谁认领了这个 stem」只有 `discover.claims_of()` 一处。
- **「这张图能不能进图内编辑」只有一处判据（ADR 0027，2026-08-29）**：
  `engine/readiness.py`，主语固定为 `/api/panels` 的那一个**素材 id**。
  六个互斥状态（`editable` / `auto_linkable` / `needs_probe` / `conflict` /
  `source_missing` / `layout_only`）+ 稳定 `reason_code`（十个，闭集），
  判定表的机器可读版本是 `REASONS_BY_STATUS`。HTTP：
  `GET /api/project/readiness`；`/api/panels` 每项的 `capability` 是**同一次
  计算的投影**，不是第二次判定。
  * **别处不许另起同义状态**，前端也不许按 `script` 有没有值自己再判一遍
    ——改造前那三处（`/api/panels` 的 `script`、`/api/registry` 的
    `candidates`、`probe.script_inventory()` 的 `reason`）主语各不相同
    （素材 / stem / 脚本），三句话都对却合不成一句。
  * **只组合已有事实，不新增解析能力**：内存里的注册表 + `discover` 静态
    报告 + 文件存在性 + 目录可写性。**注册表优先于静态报告**（注册表文件
    就是人工裁决的落处）；**冲突绝不自动裁决**（不看文件名、不看 mtime）。
  * **只报告不动手**：`can_probe` / `can_manual_link` / `can_rescan` 只说
    "界面可以提供这个动作"，执行仍归 `/api/registry/probe`、`PUT /api/registry`、
    `POST /api/project/refresh`。不执行用户脚本、不写盘、不改注册表、不发
    SSE、不返回绝对路径。
  * **「没测量」不是「测量结果是零」**：`conflicts: null` = 这一轮没跑静态
    扫描；`registry_valid: null` = 项目里没有注册表文件；`capability` 缺席
    = 这一轮还不知道。三档都不许压成两档。
  * 缓存挂在 `RefreshState.readiness`，键是输入的内容签名；刷新在事实真的
    动了之后额外清一次（依赖方向是 readiness → refresh，**反过来会成环**）。
- **「这张图有多大」的事实层只有一处（ADR 0028，2026-08-29）**：
  `engine/originalspec.py`。它回答**文件自己说了什么**，一个字不掺画布信息
  （没有 x/y/w/h、没有画布缩放、没有页面裁切）——「按原图导出」的**决策**在
  前端 `web/src/lib/originalSpec.ts`，本模块不做决策。
  * `/api/panels` 每项的 `original_spec` 与 `native_w_mm`/`native_h_mm` 是
    **同一次计算的两个投影**。改造前位图那一档在 `scan_panels()` 里现猜一个
    ppi（`600 if png else 300`），猜完既不说也没有别处能对账。
  * **物理密度先量后猜**：PNG 的 `pHYs`、JPEG 的 JFIF 密度、JFIF 只给长宽比
    时 Exif 的 `XResolution`/`YResolution`（纯标准库解析）。读不到才落到
    `ASSUMED_DPI`（取值与改造前逐位相同）并报 `dpi_source: "assumed"`。
  * **别把密度交给图像库的「默认 dpi」**：旧后端时代实测（PyMuPDF 1.28.2 的
    `Pixmap.xres`）它对「没有 pHYs」与「写着 96 dpi」一律回 96，两个不同的答案被压成
    同一个值——而「不知道」正是这里最需要分出来的那一档。RenderCore 的 `rasterio.header_info()`
    同样只报文件自己声明的密度。
  * pHYs 存每米整数像素，300 dpi 读回来是 299.9994；量化误差上界 0.0127 dpi，
    所以「离最近整数 < 0.02 就还原成整数」是去掉编码损失，不是四舍五入。
  * 没测量的维度一律 `None`：矢量不编像素数与 dpi，位图不编 viewBox，
    PDF 的透明度是 `None` 而不是 `False`。
- **离线教程项目（ADR 0039，2026-09-02）**：资源在包内
  `tavotto/resources/tutorial_project/`（经 `engine/tutorial.resource_root()`，
  `importlib.resources` → 源码树兜底，与 `profiles_path()` 同一条纪律），**绝不在
  那里写**；可写副本在 `<data_dir>/tutorial/v<版本>-<资源指纹>/Tutorial/`
  （`ensure_tutorial_copy()`：首次复制 / 幂等复用 / 缺文件只补缺的 / `reset=True`
  临时目录 + 两段 rename 原子替换，失败旧副本原样在）。目录名带**内容指纹**：改了
  资源就换目录，不靠「记得升 `tutorial_version`」。「教程由哪些文件组成」只有
  `resource_files()` 一个出处——加一张图不用改任何清单，wheel / sdist / spec datas
  的对账测试都读它。三个端点 `GET /api/tutorial`、`POST /api/tutorial/open|reset`
  走既有 `open_project()`：**不起草、不 probe、不起 worker**；`validate_tutorial_resources()`
  纯静态。重置只清 `_autosave/<document_id>.json` + `baked_overrides/<pid>.json`，
  别的项目一个字节不碰。教程进最近列表、带 `tutorial` 标记。前端（Prompt 21）
  不得再从仓库根 `examples/` 读文件。
- 前端侧（sessionStorage 的 pj、schema 3、画布会话、自动保存、剪贴板、撤销
  防线等）见 `web/AGENTS.md`。
