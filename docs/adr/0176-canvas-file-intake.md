# ADR 0176：拖 / 粘贴文件进画布——拖进来的每一样东西按类型接住，图片复制进项目文件夹成为素材

- 状态：已接受（2026-11-10，产品负责人裁决；1.0 豁免见 `docs/1.0-release-readiness.md` §1.8，原话「豁免，都开始做」）
- 相关：[0008 会话认证](0008-unified-local-session-auth.md)、[0092 主页拖放](0092-home-native-file-drop.md)、[0028 原图规格](0028-original-output-spec.md)、[0120 恢复门](0120-document-restore-verdict-and-recoverable-last.md)、
  [0143 生成可编辑版本](0143-convert-to-editable.md)、[0145 联系作者](0145-contact-the-maker.md)（附件上限的参照）、`docs/rules/repo/same-origin-pairs.md`

## 背景

产品负责人 2026-11-10 的裁决：用户都是小白，**任何失败出口只能落三种结局**——① 静默做完并说一句；② 一句话 + 一个主按钮，按钮直接完成用户原来想做的事；
③ 真做不了，主按钮「发送问题反馈」。禁止纯文字报错，禁止「请去某处做某事」。

此前画布对「系统文件拖进来」几乎都是死路：

- 把 PNG / PDF / SVG 拖到画布上，一律说「「x.png」不是 Python 脚本。请拖入 .py 文件。」——前端**没有任何「把一张现成图片放进画布」的能力**，素材只来自后端对项目文件夹的扫描；
- ⌘V 粘贴截图完全静默；
- 一次拖多个文件只处理第一个 `.py`，其余不说；
- 拖项目外的 `.py` 只说「请回到主页把它拖进来」（没有按钮）；拖项目包也被说成「不是 Python 脚本」（真入口在顶栏「导入项目包」）。

## 决定

**拖进画布（和粘贴）的每一样东西都按类型接住**。图片的路径是「字节上传 → 后端按内容验证并复制进项目文件夹 → 成为素材 → 放到落点」：
复用素材扫描与 `addPanelToCanvas`，**不造第二套对象模型**；恢复门（ADR 0120 的 `deferUserEntry`）因为经 `addPanelToCanvas` 而自动生效。

### 一、终态表（每种东西 → 结局）

| 拖入 / 粘贴的东西 | 结局类 | 做什么、说什么 |
| --- | --- | --- |
| PNG / JPG / TIFF / PDF / SVG（内容经后端验证） | ① | 复制进项目文件夹 → 重扫素材 → 放在落点；说「已放进画布：「名字」」（多张说张数） |
| 同一张图再拖一次 | ① | 后端复用已有那份（不写第二份），照样再放一张到画布 |
| 同名但内容不同的图 | ① | 后端自动改名 `名 (2).ext`，不覆盖；放上去、说同一句 |
| 一次拖多张图 | ① | 依次上传、依次放：第一张在落点，其余交给常规摆放（避让已有对象，自然错开）；最后**一句**汇总 |
| ⌘V 粘贴截图 / 复制来的图片文件（无文字、焦点不在输入框 / 对话框） | ① | 同一条路径；文件名 `pasted-20261110-213455.png`（文件管理器复制来的沿用原名）；落点交给常规摆放（对象粘贴本来也没有鼠标落点） |
| 恢复门关着时拖 / 贴图 | ① | 上传照做，放置排到文档就绪再执行一次（`addPanelToCanvas` 的 `deferUserEntry`），**不丢**；排队时丢掉落点（见 canvas 规则） |
| 项目里的 `.py` | ① | 打开并运行，图放进**这张**画布、落在放下的位置（2026-10-27 拍板，不变） |
| 项目外的 `.py`（拿到路径） | ② | 「「x.py」不在当前项目里，还没有运行。」主按钮「打开它所在的项目」：打开其所在文件夹并运行（与主页同一条路径，`openItsProject`） |
| 项目外的 `.py`，主按钮打开项目失败 | ③ | 「没能打开「x」所在的项目。」+ 发送问题反馈 |
| 项目里的 R / MATLAB 脚本 | ① | 出「生成可编辑版本」那张卡（`foreignScriptStore`），不运行 |
| 项目外的 R / MATLAB 脚本 | ② | 同项目外 `.py`：主按钮「打开它所在的项目」，打开后出那张卡 |
| `.py`，桌面壳没交来路径 | ② | 「请在接下来的窗口里再选一次「x」」并弹系统选择器，选中后运行（不变） |
| `.py` / R / MATLAB，浏览器里（拿不到路径） | ③ | 「浏览器拿不到「x」在哪个文件夹，没法直接运行它。」+ 发送问题反馈（原来是「请去左侧脚本库」） |
| 项目包（`.tavotto` / `.magplot` / zip） | ① | 走顶栏「导入项目包」**同一条**流程（`lib/packageImport.openPackageFile`）；同批别的文件这次不处理，说一声 |
| 项目包打不开 | ③ | 「「x」没能作为项目包打开。{原因}」+ 发送问题反馈 |
| 一次拖了几个脚本 | ① | 运行第一个，说「其余 N 个脚本这次没有运行」 |
| 后端不收的图（不是图片 / 损坏 / 加密 PDF / 范围外的 TIFF / 不安全的 SVG / 超限 / 只读 / 磁盘满） | ③ | 「「x」没能放进画布。{原因}」+ 发送问题反馈；项目里一个字节不留 |
| 别的图片格式（WebP / GIF / BMP / HEIC…） | ③ | 「这种格式的图还放不进画布，能放的有 PNG、JPG、TIFF、PDF、SVG。」+ 发送问题反馈 |
| 数据文件、`.ipynb`、文件夹、未知类型 | ③ | 「放不进画布，能放进来的有图片、脚本和项目包。」+ 发送问题反馈 |
| 本批里有多处没成 | ③ | 汇成一句「有 N 个文件没能放进画布」+ 发送问题反馈 |
| 一批里既有脚本又有没成的 | 说话顺序 | 脚本那一句（异步，要等壳交路径）先说，没成的那句排在它后面——带按钮的问题有最后发言权（`flushTail`） |
| 上传 / 重扫期间换了项目 | —— | 整批作废，什么都不放、不说（A 项目的响应绝不落进 B） |
| 上传期间切到了别的画布 | ① | 放进现在的画布、不带落点（旧画布的页面坐标放到别处会落在页外） |
| 「发送问题反馈」入口被关（引擎说不可用） | —— | 句子照说，不画按钮（那是用户 / 部署自己关的） |
| 没有项目（playground / MCP 画布）时拖图 | ③（无按钮） | 不发请求；「「x」没能放进画布。这里没有项目文件夹可放。」（那里没有反馈入口，不画按钮） |
| 没有打开项目时拖图进**主页** | 不变 | 主页没有项目文件夹可放；本 ADR 不做新流程。只把那句话改成不误导的「「x」打不开。主页可以拖入脚本（.py、.R、.m）或项目文件夹。」（见「后续」） |

### 二、新端点 `POST /api/assets/import`

```
POST /api/assets/import?name=<文件名>
Content-Type: application/octet-stream     body = 文件原始字节
→ 201 {id, name, file, kind, reused:false, renamed, bytes}     新写入
→ 200 {id, name, file, kind, reused:true,  renamed:false, bytes} 复用了内容相同的已有文件
→ 4xx/5xx {error, code, params}             code 见下表
```

- `id` 与 `GET /api/panels` 的素材 id 是同一个值（项目相对路径）；`kind` 是 `pdf` / `svg` / `raster`。前端据此 `assetStore.load({force:true})` 重扫并定位。
- 项目由请求头 `X-Tavotto-Project` 指定（与所有写端点一致）；没有项目 409 `no_project`；指名不存在的项目 409，绝不落默认项目。
- **错误码**（全部带中英文案，`tests/test_error_codes.py` 看护）：`asset_import_empty`(400)、`asset_import_unsupported_type`(415，内容不是支持的图片)、
  `asset_import_too_large`(413，`limit_mb`)、`asset_import_unreadable`(422，损坏 / 加密 PDF / 解析失败)、`svg_asset_unsupported`(422，沿用素材扫描的码)、
  `tiff_*`(422，沿用 `tiffprobe.ERROR_CODES`)、`asset_import_not_writable`(403，只读 / 无权限)、`asset_import_disk_full`(507)、`asset_import_write_failed`(500)、
  `asset_import_not_visible`(409，落盘后核不到它在素材清单里，已撤回)、`asset_import_name_exhausted`(409)。
- 引擎侧只有两个文件：`engine/assetimport.py`（纯标准库、Flask 父进程一侧：嗅探、清洗名字、挑落点、不覆盖落盘）与 `app.py` 里的端点；
  「这个文件能不能成为素材、有多大」**只有 `app._asset_probe` 一份**——`scan_panels` 与本端点共用，所以「复制进去了、素材库里却没有」在结构上不会发生。

### 三、安全边界

1. **会话认证不绕过**：端点走全局 `before_request` guard（ADR 0008），没有旁路；`tests/test_asset_import.py::test_endpoint_sits_behind_the_session_guard` 起真服务器验证 401 / 凭 bootstrap cookie 才 201。
2. **只写当前项目的项目根**，客户端不能指定目录：只收 `name`（basename，`/` 与 `\` 都按分隔符、取最后一段）；`dir` / `path` / `folder` 这类参数被忽略（有用例）。
3. **文件名清洗**：NFC 归一；非法字符 `<>:"/\|?*` 与控制字符换 `_`；去首尾空白与点（隐藏文件不是素材、Windows 会吃尾点）；Windows 保留设备名（`con`、`nul`、`com1`…，带不带扩展名）前缀 `_`；
   基名上限 100 字符 / 180 UTF-8 字节；清洗后仍过不了 `exportreq.check_filename`（与导出文件名同一道判据）就回退 `image`。非 ASCII 原样保留。
4. **类型按内容判，不信后缀**：魔数 / 文本形状嗅探出 `pdf` / `svg` / `png` / `jpeg` / `tiff`，其余一律 415；扩展名由内容决定（用户给的是同类型别写法 `.jpeg` / `.tiff` 才沿用）。
   嗅探只是粗判，**最终以素材扫描的探针为准**（落盘前在隐藏临时文件上跑 `_asset_probe`，TIFF 先过 `tiffprobe.check`）。
5. **落盘前先验证、不留半个文件**：字节先经 `atomicio.write_bytes` 写成同目录隐藏临时文件（`.tavotto-import-<随机>.<ext>`，素材扫描与 watcher 不认隐藏文件），验证过了再放到最终名字上；任何一环失败都清临时文件。
6. **绝不覆盖用户已有的文件**：放到最终名字用硬链接（目标存在由内核拒绝，没有「查了再放」的缝），文件系统不支持硬链接时退到「持锁查一次 + `atomicio.publish_file`」；同一把进程内锁串行化「挑名字 → 落盘」，并发导入不会挑中同一个名字。
7. **大小上限 100 MiB**：整份字节要在内存里过一遍（验证 + 哈希 + 写盘），所以必须有界，且上限卡在**读取本身**（`_read_body_capped`，不靠 `Content-Length`，分块传输同样有效）。
   依据：联系作者附件的 50 MiB 是「合计、可发送」的口径；期刊图常见 600–1200 dpi 的 TIFF（双栏宽 1200 dpi 灰度约 70 MB），所以放宽一倍；再大的位图也过不了渲染后端的像素预算，放进来只会变成一张画不出来的素材。
8. **不联网、不写遥测**：文件只在本机复制；没有新增遥测事件，白名单不变（`docs/privacy.md`「Project analysis stays local」补了一条）。

### 四、同名规则（写进 ADR 的约定）

在项目根这一层（只看这一层）：

1. 已有**同基名（或 `基名 (n)`）、同扩展名、大小与 sha256 都相同**的文件 → 复用它（用户把同一张图拖了两次，也包括拖的是上次改过名的那张）。复用的那份若被「同 stem 的 PDF」让位藏起来（素材扫描不列它），不复用、改走新名字。
2. 否则取第一个「基名没被任何素材占用」的名字：`名`、`名 (2)`、`名 (3)`…「占用」= 同层有同基名（不分大小写、NFC）的**任一**素材扩展名的文件。
   这条同时避开两件事：覆盖 / 撞名（大小写不敏感的文件系统上 `A.png` 与 `a.png` 是同一个文件），以及**与已有 PDF / 位图同 stem 导致其中一份被素材扫描让位藏起来**（`iter_assets` 的矢量让位规则；反过来新导入的 PDF 也不能把用户已有的同 stem PNG 藏掉）。
3. 落盘后再用 `iter_assets` 核一遍它真在素材清单里，不在就撤回并回 `asset_import_not_visible`。

### 五、前端

- `lib/canvasFileIntake.ts`：按类型分类（`intakeKindOf` / `planIntake`；后缀表 `ASSET_EXTENSIONS` 与后端同源，见下）、`importAndPlace`（上传 → 重扫 → 放置，带项目 / 画布守卫）、说话的两个出口（`reportProblems` / `reportImageResult`，问题一律带「发送问题反馈」主按钮）、剪贴板图片（`pastedAssetFiles` / `pastedImageName` / `intakePastedImages`）。
- `canvas/useCanvasScriptDrop.ts`：整次放下的分派（图片 / 脚本 / R·MATLAB / 项目包 / 其余）与 `.py` 的壳配对（ADR 0092 不变）；`openItsProject`。
- `lib/packageImport.ts`：顶栏「导入项目包」与画布拖放共用的 `openPackageFile`（失败原样抛出，顶栏说原因，画布给反馈按钮）。
- `lib/clipboard.ts` 的 `handlePasteEvent`：不是对象负载时再看图片文件；**有文字就不拦**（电子表格 / 网页的复制常带预览图，那是在粘文字）；输入框 / 对话框让位沿用 `runDiscreteAction` 的判据。
- 桌面壳的原生拖放处理是关掉的（`disable_drag_drop_handler`），DOM `drop` 仍带 `File`（名字 + 字节）：**图片走 DOM `File` 的字节上传，浏览器与桌面版同一条路，没有改 Rust**（`drop_paths.rs` 的分类与 `lib/desktop.ts`、`lib/foreignScript.ts` 的同源对不动）。
  macOS / Windows 壳对图片拖放仍会发一条 `unsupported` 原生事件：本次放下里没有脚本时它不被配对、过了宽限期静默丢弃（`createCanvasDropPairer` 的既有行为：只等到壳的事件 = 什么都不做）。
- 拖动时的落点预览（`useDropGhost`）**对系统图片文件不出**：dragover 时浏览器只给 `kind` / `type`，读不到文件内容，没有图的原始尺寸，画虚线框就是造假尺寸。

### 六、严格同源对

「哪些后缀算素材图片」：`engine/assetimport.EXTENSIONS` ↔ `web/src/lib/canvasFileIntake.ASSET_EXTENSIONS`，两侧各自与 `tests/golden/asset_import_extensions.json` 比，后端再与素材扫描认的后缀（`engine/project_refresh`）比。

## 不做的事

- **不做别的图片格式的转换**（WebP / GIF / BMP / HEIC）：素材扫描不认，如实说清；浏览器端转 PNG 是后续的可选项（见下）。
- **不收数据文件**（CSV / Excel…）：「用数据画图」要求数据在项目文件夹里，把数据文件复制进项目是另一个裁决（隐私与写入范围更大），这里只给「发送问题反馈」。
- **`.ipynb` 的转换**是另一个 PR；这里只保证不再说「不是 Python 脚本」。
- 不收文件夹（浏览器的 `File` 区分不了文件夹与无后缀文件；壳的 `folder` 事件不配对）。
- 不为主页（没有项目）造新流程；不改 Rust；不放松任何安全边界。
- 不新增界面控件：本批只有拖放行为与通知轨上的一句话 / 一个按钮。

## 验证

- 后端 `tests/test_asset_import.py`：五种类型都能导入并出现在 `/api/panels`；内容嗅探（改后缀 / 无后缀 / 假冒的 PNG）；空体、截断、加密 PDF、多页 PDF、范围外 TIFF、不安全 SVG；
  文件名穿越 / 保留名 / 非 ASCII / NFD / 超长；同名同内容复用、同名异内容改名不覆盖、用户自己的文件不覆盖、大小写不敏感撞名、与同 stem 的 PDF / 位图互相让位、并发不撞名；
  超限（含分块无 `Content-Length`，且对照「上限之内收得进来」）、磁盘满 / 只读 / 无权限 / 其它写失败的 code、放置失败与「落盘后核不到」都不留文件；无项目 / 指名不存在的项目；真服务器上的会话认证。
  反证：取消「基名被占用就改名」的判断，七条用例变红。
- 前端：`lib/canvasFileIntake.test.ts`（分类、与 golden 同源、上传 / 放置 / 落点 / 项目与画布守卫 / 恢复门不丢、说话与按钮）、`canvas/useCanvasScriptDrop.test.ts`（整次放下的每个出口）、`lib/clipboardImagePaste.test.ts`（粘贴）。
- `pnpm test && pnpm build && pnpm i18n:check`、`python scripts/build_browser_playground.py --check`（playground / MCP 画布没有这个后端也没有项目：`currentProjectId() === null` 时不发请求，每个文件说一句「这里没有项目文件夹可放」，不报 HTTP 错误；有用例）、`python scripts/dev/agents_budget.py --check`。

## 后续（建议，未做）

1. 主页拖图：没有项目可放。建议「用这张图新建一个项目」或「选一个项目」——需要产品负责人拍板的新流程。
2. 浏览器里拖项目里的 `.py`（拿不到路径）：可按文件名 + 内容哈希在项目脚本里匹配，唯一命中就运行项目里那份——比现在的「发送问题反馈」好，但属于「猜」，需拍板。
3. WebP / GIF / BMP：前端用画布转成 PNG 再上传（静默做完并说一句），不动后端。
4. 数据文件拖进画布：复制进项目 + 打开「用数据画图」表单并预选该文件。
5. 文件夹拖进画布：打开为项目（与项目外脚本同一个主按钮）。
