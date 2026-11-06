# ADR 0166：SVG 计算图表编辑——片段嫁接架构与来源合同

日期：2026-11-04 · 状态：**Accepted**（依据 SG01 六个可复核实验；1.0 豁免登记见 `docs/1.0-release-readiness.md` §1.8）
相关：[0144 SVG 导出保留文字](0144-svg-export-keeps-text-by-default.md)、[0150 同次 draw 配对](0150-pdf-svg-pair-from-one-draw.md)、
[0152 所见即所得文字](0152-wysiwyg-text-editor.md)、[0072 RenderCore / 零 PyMuPDF](0072-default-render-backend-cutover-and-pymupdf-retirement.md)、
[0017 几何权威](0017-display-fallback-vs-geometry-authority.md)；实验记录 `docs/implementation/svg-computational-editing/SG01-EXPERIMENTS.md`。

## 背景

产品已能导出 SVG（X03/ADR 0144）与展示静态 SVG 素材（`svgasset` 只读通道），但 SVG 既不能作为
**计算图表**进入编辑，也不能把外部 SVG 绑定数据后联动重算。用户要求（2026-11-03 实施授权）三条路径：

- **A**：有可信绘图来源的 SVG（Tavotto/其 Figure 产出）→ 复用现有 matplotlib 计算与编辑语义；
- **B**：普通 SVG + 用户显式绑定（轴/系列/数据）→ 成为可计算图表；
- **C**：自包含计算 SVG/项目保存后可校验、重开、继续编辑。

三个候选（全模型重建 / 场景保留+区域重算 / 原生 SVG 自算）经 SG01 实验裁决，数字见实验记录。

## 决定

### 1. 三路径一个架构：场景保留 + 片段嫁接（C2 精化）

- **路径 A 不新建计算**：matplotlib 产出的 SVG 带执行回执/配对身份（svgstore 既有机制）时，
  编辑、重放、导出、写回全部走现有 Figure 管线；SVG 面板只是该 Figure 的**预览与来源身份**。
  禁止按相邻同名 `.py` 猜来源。
- **路径 B 片段嫁接**：
  1. 原 SVG 场景（经 `svgcheck` 校验的字节）是**未绑定区域的唯一权威**，零编辑零重渲染（实验一）；
  2. 用户显式绑定建立 **ChartModel**（轴/尺度/范围/系列/数据引用）与 **Binding**（场景节点 id ↔ 模型对象），
     绑定区域的最小单位是**语义子区域**（axis 子树、单个系列组），不是整棵 axes 子树
     （实验三事实 1：matplotlib 把 title/legend 放在 axes 子树内部，整树替换会静默重画装饰）；
  3. 计算在 **worker 脚手架**完成：一个只有绑定 axes 的 matplotlib Figure（1 unit = 1 pt，rect 对齐场景），
     **只复用 matplotlib 的 locator/formatter/scale/norm**（实验二：分数与独立公式一致到 1e-9；C3 自写刻度规则被计划与实验双重否决）；
     脚手架**直接渲染 SVG 片段**（`svg.hashsalt` 固定 + 元数据剥离 → 字节确定，实验三），
     mathtext/字体/虚线由 matplotlib 自己表达（纯 JSON 回填会丢 mathtext 字形——实验二副产物）；
  4. 父进程做**节点级替换**：被外部引用的 defs 先**上提**到根 defs（实验三事实 2：图例引用系列组内部 marker），
     再以片段换原节点；片段内 id 加后缀隔离；
  5. 编辑后的场景 SVG 即该面板的预览与冻结产物；几何/命中事实由场景 + 脚手架事实派生（不解析浏览器 DOM）。
- **路径 C 自包含包**：`model.json`（闭式 schema）+ 场景 SVG + 数据 + 绑定，冷重放 = 重算片段 + 重组场景，
  **零脚本执行**（实验四：`python -I` 全新进程冷重算与热计算逐字节相同）。仅有来源引用（无数据）的图
  如实提示缺来源，不自动执行陌生代码。
- **项目来源附件**：保存引用固定为schema/sourceId/输入字节SHA256/大小/原场景digest；由权威项目收纳根派生`chart_sources/<sha256>.svg`，不接受客户端路径。冻结整份精确输入，包括自包含包外层；计算仍消费整份输入并实际重放核对，不能借解包后的源跳过验证。附件先原子新建且禁止覆盖，读回校验后正文沿用既有单一原子提交点，不另建历史或事务数据库。schema 4经显式格式入口保存/读取，默认2/3读写合同不变；后端及真实UI正文冷恢复已验，命名文件关联与明确恢复已沿ADR0096/0108后端合同接通，包安装仍待闭合。
- **附件消费者**：inspect/bind/compute/render/PNG及全部导出scope接收可选`chart_source`，存在时只从权威项目附件根读取校验后的精确INPUT，缺失/篡改不回退同名原件；snapshot同时持完整INPUT与包内原场景。显式附件字节身份进入渲染/缓存身份，同一逻辑fileId允许引用不同不可变版本。多页的去重复用单页ExportRequest完整RenderPlan身份，不另列会漏profile/附件/标注/留白的字段表。无引用保留原默认请求/身份。
- **v4项目包产品导出**：同一项目菜单显式使用`/api/documents/v4/package`，不拓宽旧2/3接口。chartpackage派生唯一layout/manifest/计算来源行，app提供全部画布、已核普通资源/登记脚本/Figure配对和实际预算；精确INPUT缺失拒绝，不回退原件。缺普通资源允许准确标注的不完整备份；原SVG已删除而附件完整仍可正常打包。先封口/严格检视，再不可覆盖发布；导出不执行用户脚本。安装前的项目目标计划复用唯一portable成员判据统一assets/scripts，拒绝跨namespace/别名/文件目录冲突。registry/数据依赖/运行配置可携闭包、显式检视与生产安装仍是独立未完成合同，不能以成功导出替代迁移验收。
  可携执行事实使用可选闭式`execution`v1，清单与资源关系共用`packageexecution`校验。已知registry、输入/helper、非敏感原运行配置由现权威收集，`runconfig.import_portable`保原ID但不导入默认/成功事实或机器授权；同脚本的已知无参数与参数未知来源分别保留。静态分析/动态观察不证明任意Python依赖完整，未有完整声明时coverage必须incomplete并由界面准确展示；已知缺文件另列missing。完整声明、外部输入、快照语义检视与安装仍须闭合，不以本批事实收集代替搬迁资格。
  显式`/api/documents/v4/package/inspect`沿同一认证/格式域只读核完整包，上传/元数据/展开预算由`packagebudget.input_budget`唯一提供；`DocumentPackage.copy_required`与原reader共用CRC/计数器，普通数据流式暂存，完整INPUT仍沿原来源权威。`packageinspection`核全部Figure存储成员及正文source/patch/ref，历史档案、marker撤旧事实与当前登记分别解释；SVG/PDF内容分别沿共享安全解析器/pdfbackend。原清单声明保留，缺件与缺口重新计算，不把历史owner/receipt/coverage变成当前运行或安装授权。检视没有正文提交、项目写入或用户脚本执行；独占安装、输入闭包与全量冷重放仍是后续退出条件。
- **捕获时输入声明事实**：本机选择由`declaredinputs`沿既有路径批准/原运行配置与内容CAS管理，真实safe子Worker沿`inputdeclaration`在同一执行起止核声明文件及完整prefix成员。候选经两控制面私有请求传入，新execution_id及同版build capsule核对后才进入回执的可选公开revision；cached/迟到/失败/当前binding不得给旧图补证。无声明原wire/JSON/identity保持。Rust换子进程时仅监督进程的本次dispatch PID能更新父账，不以runtime自报授权。该批不代表native捕获谱系、版本化可携输入清单/资源、声明UI或完整项目安装已完成，任意Python的partial观察口径不变。
- **显式格式隔离**：documentformat集中legacy/v4闭集与目录派生，不改变project_layout_dir资产根；v4命名在documents/v4，autosave在_autosave/v4，版本仍用现信封但位于documents/v4/versions，每层自有目录拒绝symlink/junction。layoutsession的using_namespace在同文件/同锁内选择document_namespaces.v4，复用现有owner/revision/恢复规则，旧程序清理/重写保留该未知顶层字段。未知所选namespace版本拒写；主文件消失且唯一备份含未来namespace时也不覆盖。
- **v4保存与恢复入口**：显式/api/documents/v4下的layouts/autosave/layout-session/versions复用原view、认证、锁、冲突与atomicio；全部目录/候选/owner/清理/版本草图按所选格式，v4不借旧versions。锁内实际重放并冻结整份INPUT、验证附件后单次原子写正文；坏附件严格拒绝，不沿普通Figure的best-effort降级。chart面板只在v4跳过Figure捕获，旧2/3未知chart扩展字段仍按原FigureSnapshot核验。回执chart_sources为canvas_index/object_index/canvas_id/panel_id/chart_source五字段位置数组，不按可重复panelId建Map。GET保留原字节与真实revision/file，另带格式header；旧2/3缺省响应不新增字段。仅校验通过的不可变附件可作为删源后的资产归属旁证，不冒充已记录的owner提交。
- **前端持久化边界**：运行态仍一个schema2活动画布与一个documentStore；legacy/v4决定持久化schema3/4、API/本机缓存/revision/保存队列地址。首次计算字段使旧稿fork到新v4 working ID，保留对象身份及undo；手势取消不升级，undo不降格式。保存上下文持实际发送的深拷贝，位置回执只在同pj/workingId/loadSeq/format且同位置完整输入未变时派生合入，重复位置整份拒绝；不加历史或循环保存。旧缺省键/URL/实参数量保持。版本仍拍活动画布，v4仅单canvas封装；缺thumb按需正文+附件回建。真实UI正式保存、空localStorage新浏览器/新后端自动恢复、继续编辑与三格式产物已验；绑定元信息后端迁移及包搬迁不计作本批完成。细则与看护见`docs/rules/frontend/project-document-and-autosave.md`。
- **跨格式打开意图**：两个namespace的at不是共同总序，时钟回拨和迟到首次保存均可选错稿。复用同一layout-sessions文件、锁与原子写，在版本化document_open_intents记录每项目sequence、latest地址和实际两namespace last/pending的witness；只在明确打开时分配sequence，同值pending重开也分配，普通保存/清理只更新读入时可信的见证。旧程序保留但不更新账本时检测stale，不从时间戳猜真实意图，也不由普通保存洗白。未知新账本版本/非法结构拒写，正常纯legacy默认磁盘与响应保持。显式GET include_open_intent=1才给open_intent共同快照，前端两GET相同snapshot才采用latest；变化有界重读，正常可信latest自动恢复。后台PUT repair:true只确认当前同格式同id意图或原seq晋升pending，不新建意图；异址/显式清空回superseded，stale/untracked/v4未建账本回unconfirmed且零写，前端保待确认。未来账本仍503拒写，不以unconfirmed掩盖。前端明确打开请求还须沿同一发送队列串行跨格式，不能由各namespace独立队列让旧打开请求迟到抢序。
- **原生编辑场景**：`native-manifest/1` 在物理 viewport pt 中创建、应用完整 overrides 并 draw，再经原 viewBox/PAR 与父变换的逆映射接回场景。原生 gid 保留供既有编辑器消费；只隔离原场景派生 DOM 的 id/CSS，原输入字节及绑定摘要不改。最终 manifest 反映真实组合后的 paint order、祖先 opacity 与可表达的矩形 clip。各role可连续放置时沿原source slots；完整共享native树能保留内部插绘时采用shared-native。源compound缺独立成员槽位、显式zorder与source slots冲突或非矩形clip尚未表达时保留receipt及明确原因，但`composed=False`，不能冒充精确编辑资格。
- **一个对象的多个实际目标**：最终manifest的可选`svg_targets[gid]={transform:string[],style:string[]}`为唯一归属事实；数组按最终绘制序列出native pt wrapper内部实际DOM id，去重且不同时含祖先与后代。同一对象只序列化镜像到SVG根`data-tavotto-svg-targets`，原source派生同名属性先移除。前端单一reader共同供transform/style与等待权威时的意图门消费，缺字段保持旧单gid合同，非法显式映射不回退。临时预览仍只改DOM；精确manifest才授权几何写入。成员样式能力仍由既有adapter决定，映射不授予额外setter能力。
- **即时预览坐标**：生成的每个原生 wrapper 携带 `data-tavotto-native-viewport-pt="width height"`，值来自同一次场景物理尺寸；先从原场景派生 DOM 移除同名属性。前端唯一 reader 按目标读取最近 wrapper，平移、缩放锚点、排队预览和 pt 线宽共同消费该基底。普通 SVG 保留旧 root 换算；非法标记拒绝临时预览。此属性仅说明 DOM 局部单位，不能代替配对 manifest 或授予编辑权限。

### 2. 导出：SVG→IR 翻译器（新增 `rendercore/` 模块），五格式合同不变

- 计算面板导出 PDF/PNG/TIFF：编辑后场景 SVG → **IR**（`rendercore` 纯 stdlib 解析 + 现有 pikepdf/fontTools/uharfbuzz/pypdfium2）
  → 既有 `pdfwriter`（Canonical PDF）→ 既有栅格管线；**零 PyMuPDF 不变**，父进程不 import matplotlib 不变。
- SVG 格式导出：**直接交付编辑后场景字节**（同静态素材的 copy 基准语义），不经翻译器。
- 原生编辑的生产导出使用可选`chart_render_profile`与既有完整原序`overrides`，共同进入原图/标注/留白/画布/多页的冻结计算身份和作业内缓存；不是只冻结model/binding或排序后的patch hash。原图SVG以显式native profile写v2包，缺省legacy/空编辑仍按原v1合同；无chart请求的payload与计划身份不新增字段。
- 计算原图EPS：完整冻结SVG由`pdfbackend.prepare_original_eps`经RenderCore的共享SVGIR/批准字形准备有界PS，再由既有owned chart worker的固定`pack_eps`动作封EPS；不要求原Python脚本，不重新计算另一份场景。物理尺寸、SVG/PS摘要与完整原序编辑共同核验；标注/留白先复用同一完整合成SVG，再走同一契约。不能无损表达的透明/图像/渐变等逐格式明确失败，不静默栅格化或丢装饰。旧Python/live EPS及画布`eps_not_for_canvas`保持。
- 翻译器能力逐项声明（native / unsupported 点名元素）：native=路径命令（含弧转换）、基础形状、路径 clip、
  opacity、dash、文字（批准脸+HarfBuzz，旋转经 Group 变换，字体不可解析时**按披露回退或逐项失败**，不静默换脸）；
  unsupported=渐变/mask/filter/pattern → 该格式逐项失败带元素清单（同 `eps_annotations_unsupported` 的诚实模式）。
- 文字层：PDF 文字层保留真实 Unicode（pdfwriter 既有 ToUnicode/ActualText 机制）；实验三核对文本层含标题/图例/新刻度。
- 2026-11-06 保真接线：局部 CTM 只应用一次，根视口按物理 pt 与 preserveAspectRatio 映射；use/符号视口、继承字号、实际 CSS 层叠及嵌入图像走同一 IR。多成员 clipPath 是各自填充规则的并集，用 ClipUnion 表达；PDF 以矢量 luminosity mask 与隔离 Form 保留曲线及嵌套透明，SVG 保留各成员 clip-rule。不能拼成一个绕向路径，否则反向重叠会相消；单 Path 原分支保持原样。
- 连续文字按 SVG text chunk 统一布局：绝对 x/y 开新 chunk，相对 dx/dy、样式或 tspan 边界不拆锚定；空白归一后用既有 typography/HarfBuzz shaping，整段应用 text-anchor，逐字旋转不改变水平基线前进。22 项相同批准字体的 Chromium 参考（其中 9 项再经 PDFium 基线对拍）及精确文本层回归已通过；不据此宣称双向文字、竖排、textPath 或全部 baseline 模式等价。

### 3. 身份与安全（扩展现有合同，不新增旁路）

- 2026-11-06 接线细化：计算产物使用 `SourceArtifact.origin=declarative`，由 `render_revision` 标识模型/绑定/来源/环境版本，不伪造 Figure 执行回执。声明式 Canonical PDF 与 SVG companion 必须同来源同版本；static/execution 既有配对与缺省序列化保持原样。RenderPlan 继续消费同一个来源解析器，不另建计算图表导出管线。
- 不能建立 Canonical PDF 时，原生声明式 SVG 可作为其自身的 SVG 表达进入同一计划；例外精确限定 kind=svg 且 origin=declarative，不扩普通 static FILE_KINDS，不允许嵌套 companion。SVG 写出核对字节摘要并保留物理尺寸/视口，PDF 逐项明确失败，不能造空 PDF。完整 SVG viewport 不再额外施加一次同边界 Path 裁剪；普通 PDF/SVG companion 路径不变。
- 计算面板的版本身份 = **场景字节摘要 + 模型摘要 + 数据摘要 + 绑定摘要 + 计算环境身份**（worker matplotlib/引擎版本）；
  任一变化都不得复用旧几何/旧快照（SG02 冻结 schema）。
- 原生manifest渲染另有`native-manifest/1` profile；既有`legacy-scaffold/1`和自包含v1字节不变。v2包闭式携带profile与完整原序overrides，恢复先实际重放存储状态再处理新请求。原生身份除原序列表外纳入精确JSON摘要，保留同档应用顺序、i64整数和1/1.0在文字setter中的实际区别；不修改旧patch_hash或ChartModel的数值身份。父层仅校验形状/预算，setter与目标所有权仍须worker核准，包摘要不授予执行或字体文件权限。
- v2检查结果以可选`restored_render`返回包内profile与完整原序编辑，`restored_chart`仍只有model/binding；草稿预览不写正文，当前文档/加载代次/面板输入匹配后，经现有一次事务同时确认模型与编辑。当前面板的编辑（包括空列表）优先于导入包。携编辑重绑时由后端核对原科学对象归属；数据/范围/列名可变，同一位置gid不能证明重排后的对象相同。结构迁移尚待逐对象映射，拒绝时保留草稿与编辑，不默清。
- 导入与元数据：`svgcheck` 边界原样（实验六拒绝 DTD/脚本/外链）；模型元数据**闭式字段集**校验，
  不含可执行代码、不含完整机器路径；自动候选只辅助、确认前不进计算重放与写回。
- 写回：计算面板的写回资格**显式化**（有可重放模型与数据者才可），走既有 prepare→verify→commit 事务（SG13）；
  覆盖实验数据文件与覆盖 SVG 是不同操作。
- worker/父进程边界不松：脚手架只在 worker（matplotlib）；父进程只做 stdlib 树操作与 IR 翻译。
- 计算probe/scaffold共用固定svgchart command runner，解释器/激活沿pool权威，进程沿既有DesktopProcess ownership与envlease；同一请求deadline覆盖stdin、stdout与响应验证。输出spool走data_dir，字节/JSON结构预算消费SVGBudget/chartprofile，stderr仅保有界tail；进程非零退出不能被成功JSON掩盖。未确认组退出保留lease和chart admission，确认后才释放；chart类容量消费动态pool.max_alive，不声称全产品总进程cap。旧desktop启动/gate时序不变，不引入任意argv或用户脚本入口。

### 4. 明确不做（本轮）

- 不从普通 SVG 猜测/反推原始数据；OCR/路径反算只出候选。
- 不把静态 SVG 塞进 `FILE_KINDS` 冒充可放置原件；静态素材通道行为不变（`assets.svg-preview` 回归底线）。
- 不新增原生依赖（resvg/svg2pdf 等不引入）；渐变等表达不了的特性以逐格式失败披露。
- 不重写执行入口、版本时间线、认证、渲染闭包（1.0 收敛纪律）。

## 后果

- 新模块：worker 侧脚手架命令（engine/，纯 stdlib import 链不破坏）、父侧场景替换器与 SVG→IR 翻译器（rendercore/）。
- SG02 冻结 ChartModel/Binding/SourceDescriptor schema 与错误码；SG03 扩展安全静态结构接受集；
  SG05 场景身份与坐标链；SG06 绑定流程；SG07 最小闭环（实验三即其原型）。
- 反证基线：削掉 defs 上提 → 图例 marker 悬空（svgcheck 拒绝）；削掉脚手架 rect 对齐 → 点位置偏离公式；
  削掉闭式 schema → 可执行字段进入模型（SG02 起各批自带反证）。
- 1.0 豁免：本 ADR 是用户 2026-11-03 明确要求的扩展授权记录；边界以本文 §4 为准。
