/** 文档模型 —— 单位一律 mm，数组顺序即 z 序（末尾在最上）。 */
import { t } from '@/i18n'
import type { ArtifactValidation, SourceBackedFigure } from '@/lib/artifactValidation'
import { newId } from '@/lib/id'
import type { JournalFieldSource } from '@/lib/journals'
import { displayText, type TextFormat } from '@/lib/richText'
import { migrateFigureFrames } from '@/lib/figureFrameMigration'

export interface ObjectBase {
  id: string
  type: string
  x: number
  y: number
  w: number
  h: number
  locked?: boolean
  hidden?: boolean
  name?: string
  /**
   * 成组：同 groupId 的对象一起选中、一起移动。嵌套组（ADR 0191）里它是对象所在的**最内层**组——
   * 只认 groupId 的旧构建照样把最内层组当一层组用，对象一个不丢。
   */
  groupId?: string
  /**
   * 嵌套组的完整链（ADR 0191）：外 → 内，末项必须等于 `groupId`。只有两层及以上才写；对不上（旧构建改过
   * groupId）就按只有 `[groupId]` 一层读——判据唯一出处 `lib/groupTree.groupPathOf`。
   */
  groupPath?: string[]
  /**
   * 剪切蒙版（ADR 0191）：这个对象是它最内层组的剪切路径（只认闭合形状与路径，判据在 `lib/groupTree`）。
   * 做蒙版时同时写 `hidden: true`：旧构建不画它、也不裁剪，内容照样都在。
   */
  clipMask?: true
  /**
   * 剪切路径本身被隐藏（ADR 0191）：在图层树上对蒙版点「隐藏」= 暂时不裁（Illustrator 同一做法），组里的内容全部露出来；
   * 蒙版仍是 `hidden: true`（旧构建照旧）。只对 `clipMask` 的对象有意义。
   */
  clipOff?: true
  /**
   * 布局组成员的「固定当前位置」：重排时跳过该对象。
   * 只对 layoutGroups 里登记的组生效，普通成组不受影响。
   */
  layoutPinned?: boolean
  /**
   * 任意角度旋转（度，顺时针，绕包围盒中心）。只对 text/arrow/shape 生效；
   * 面板仍走 90° 步进的 rotation（矢量置入的语义限制：非 90 倍数不填满目标矩形）。
   * x/y/w/h 始终是未旋转的包围盒。
   */
  rotationDeg?: number
  /**
   * Agent 加的标注锚在哪里（ADR 0208 §六）：后端 `engine/layoutannotate.py` 写入，Agent 不能直接写（字段表里是保留字段）。
   * 前端现在只原样保留它（载入 / 保存 / 撤销都不动）；第 4 期据它判「这个标注可能已经没对准」并「重新对准」。
   */
  anchor?: AgentAnchor
}

/** 一条锚点：`role` 是元素的哪一端（from / to / at / left / right…），其余正好一个锚点种类（与场景描述同一套）。 */
export interface AgentAnchorRole {
  role: string
  data?: { panel: string; axes?: string; x: number | string; y: number | string }
  element?: { panel: string; gid: string; point?: string }
  panel?: { id: string; point?: string }
  item?: { name: string; point?: string }
  offset_mm?: [number, number]
  /** 脚本 `label=` 的摘要：脚本重跑、gid 重新编号后据它认回同一个元素 */
  identity?: string
}

export interface AgentAnchor {
  v: 1
  by: 'agent'
  anchors: AgentAnchorRole[]
}

/**
 * 线型：显示与导出共用同一枚举（间距按线宽比例换算，比例表在 `lib/shapeGeometry.DASH_RATIOS`，
 * 与后端 `rendercore/geometry.DASH_RATIOS` 同源，ADR 0174）。
 * 后四种是 ADR 0174 加的：旧版本读到不认识的值按实线画，不会报错。
 */
export type DashStyle = 'solid' | 'dashed' | 'dotted' | 'longdash' | 'dashdot' | 'longdashdot'

/**
 * 箭头端型：实心三角 / 开口 V / 垂直短线（尺寸线用）；ADR 0174 加：圆点 / 菱形 / 燕尾。
 * 旧版本读到不认识的端型画不出端型（只剩一条线到端点）。
 */
export type ArrowHeadType =
  | 'none'
  | 'triangle'
  | 'open'
  | 'bar'
  | 'dot'
  | 'diamond'
  | 'stealth'

/** 端型大小三档；缺省 = `medium` = 加档位之前的尺寸，`medium` 不落盘 */
export type HeadSize = 'small' | 'medium' | 'large'

/** 线端 / 拐角（SVG 与 PDF 的同名取值）；缺省见 `lib/shapeGeometry.DEFAULT_LINE_CAP` / `defaultLineJoin` */
export type LineCap = 'butt' | 'round' | 'square'
export type LineJoin = 'miter' | 'round' | 'bevel'

/**
 * 结构化布局组：在轻量成组（groupId）之上叠加的可选排布约束。
 * `id` 即成员们的 groupId —— 拖动任一成员整组移动的既有行为直接复用；
 * 重排以成员当前包围盒左上角为锚点，只重新分配组内相对位置。
 */
export interface LayoutGroup {
  id: string
  kind: 'row' | 'col' | 'grid'
  /** 成员排列顺序（对象 id）；不在文档里的成员自动忽略 */
  order: string[]
  /** 间距 mm */
  gap: number
  /** grid 的列数 */
  cols?: number
  /** 交叉轴对齐：row → 上/中/下；col → 左/中/右 */
  align: 'start' | 'center' | 'end'
  /** 统一尺寸（等宽 / 等高；面板按宽高比联动另一边） */
  uniform?: 'width' | 'height' | null
}

export interface PanelOverride {
  gid: string
  prop: string
  value: unknown
  /**
   * 目标身份（ADR 0083）：写这条编辑那一刻 manifest 里这个 gid 的 `identity`。
   * gid 是位置式的，脚本结构改了会指向别的对象；引擎按它核对，对不上不应用、报
   * warning。可选：旧文档没有它 = 按位置匹配（与引入前一致）。只由
   * `lib/overrideIdentity.stampOverrideIdentities` 在提交时抄写，写入点不手填。
   */
  identity?: string
}

/** 裁剪框：相对原图的归一化比例（0–1），undefined 表示不裁剪。 */
export interface CropRect {
  x: number
  y: number
  w: number
  h: number
}

/**
 * 引擎收得下的裁剪框：四个数有限、宽高为正、落在 [0,1]² 里（右 / 下边容 1e-9 的浮点误差）。
 * 与 `rendercore/ir._crop` 是同一条判据（同源对，两侧各读 `tests/golden/crop_bounds_vectors.json`）；
 * 不合的 crop 会让 RenderCore 拒掉整份排版，写进文档之前拿它把关。
 */
export function cropInBounds(c: CropRect): boolean {
  const { x, y, w, h } = c
  if (![x, y, w, h].every(Number.isFinite)) return false
  return w > 0 && h > 0 && x >= 0 && y >= 0 && x + w <= 1 + 1e-9 && y + h <= 1 + 1e-9
}

/** 面板旋转只做 90° 步进：合成时非 90 倍数不填满目标矩形，语义对不上（`rendercore/placement.py` 同一合同）。 */
export type PanelRotation = 0 | 90 | 180 | 270

/**
 * 面板素材的形态（AssetSource，ADR 0013）：
 *   pdf / raster —— FileAsset：磁盘文件（矢量 / 位图）
 *   runtime      —— RuntimeFigureAsset：一次受控执行捕获的 Figure，
 *                   fileId 是 `runtime:` 前缀的不透明稳定 id，磁盘上
 *                   **没有**（或不依赖）原始产物
 * 未来新增的取值对旧构建是未知形态：消费方一律经 panelKind() 判别并对
 * unknown fail closed（当缺失素材显示，绝不猜成文件路径）。
 */
export type PanelFileKind = 'pdf' | 'raster' | 'svg' | 'runtime'

/**
 * 面板素材是不是**矢量文件**（pdf / svg）：按有效字号（pt）而不是有效 DPI 评质量、
 * 来源判「矢量」都问它。新增矢量文件形态只改这一处（runtime 是执行捕获的图，不在内）。
 */
export const isVectorFileKind = (kind: string | null | undefined): boolean =>
  kind === 'pdf' || kind === 'svg'

/**
 * RuntimeFigureAsset 的持久化描述块（CapturedFigureDescriptor 的子集，
 * ADR 0013 §3）。它是**恢复线索**而不是权威：渲染时后端仍以注册表为准，
 * 注册表条目丢失时才用它兜底（且 fail closed：重算 id 必须与 fileId 一致）。
 */
export interface RuntimePanelSource {
  script: string
  entry: string
  stem: string
  captureSource: 'savefig' | 'pyplot'
  fingerprint: string
  sizeMm: [number, number]
  /**
   * 产出这张图的运行配置引用（T03；本机不透明 id `rc_…`，**不是参数值**，可以安全地随文档分享）。缺席 = 没给参数。
   * 在别的机器上打开时那边没有这条引用，后端会显式拒绝（`run_config_missing`）而不是用空参数去跑。
   */
  runConfig?: string
}

/**
 * `PanelObject.dataOrigin` 的存储形状（`version: 1`）。`file.path` 只能是项目相对的 POSIX 路径（登记时判，读出来再判一次）；
 * `dataSha256` = 画这张图时读的数据文件原始字节的 sha256，重开后据它判「数据变没变」；`fingerprint` = 这张图来源脚本的指纹（含脚本内容 sha256）。
 */
export interface PanelDataOrigin {
  version: 1
  script: string
  file: { path: string; sheet: string | null }
  headerRow: number | null
  answers: Record<string, unknown>
  spec: Record<string, unknown> | null
  fontRange: { min_pt?: number; max_pt?: number } | null
  /** 候选身份（`candidateIdentity`，不含误差类型） */
  identity: string
  kind: 'sd' | 'sem' | 'ci95'
  /** 误差画成阴影带（趋势的 `line_band`）而不是误差线 */
  band: boolean
  fingerprint: string
  dataSha256: string
}

/** Exact script/configuration ownership, derived and verified only by the backend. */
export interface FigureExecutionOwner {
  script: string
  run_config: string | null
}

/** Immutable, backend-verified Figure snapshot bound to one source execution and patch set. */
/**
 * SVG 计算图表的文档内状态（ADR 0182）。形状与 `types/chartModel.ts` 的
 * ChartModel / ChartBinding 同源；这里按 id 引用而不复制判据。
 */
export interface PanelChartState {
  model: import('./chartModel').ChartModel
  binding: import('./chartModel').ChartBinding
}

/** 不可变 INPUT SVG 附件；字段与 engine/documents.CHART_SOURCE_KEYS 严格同源。 */
export interface ChartSourceRef {
  schema: 1
  sourceId: string
  bytesSha256: string
  sizeBytes: number
  sceneDigest: string
}

export interface FigureSnapshot {
  version: 1
  sourceId: string
  patchHash: string
  receiptId: string
  snapshotId: string
  /** Backend-verified archived execution ownership; absent in older snapshots, never inferred. */
  executionOwner?: FigureExecutionOwner
}

/** Historical execution input; independent of the current full override list/output snapshot. */
export interface FigureOrigin {
  version: 1
  snapshot: FigureSnapshot
  patches: PanelOverride[]
}

/**
 * native-manifest/2 计算框的持久记号：只存布局落位（`originPt` 是渲染外框
 * `rect_pt` 在源 viewport pt 坐标里的左上角）与 profile，**不存第二份科学模型**——
 * 模型与绑定仍只在 `PanelObject.chart`。形状判据的唯一出处是引擎
 * `engine/chartframe.py`（前端消费方 `lib/chartFrame.ts`）；缺席 = 面板仍是
 * native-manifest/1 的 source viewport 落位。旧前端读到会忽略。
 */
export interface PanelChartFrame {
  version: 1
  renderProfile: 'native-manifest/2'
  originPt: [number, number]
}

export interface PanelObject extends ObjectBase {
  type: 'panel'
  fileId: string
  fileKind: PanelFileKind
  nativeW: number
  nativeH: number
  pxW?: number
  /**
   * 像素高。与 `pxW` 成对，只有位图源有；**源文件消失之后原图规格还要报得出
   * 像素网格**，所以它跟 `pxW` 一样存进文档（可选字段，老文档没有它 =
   * 那一维未知，不许补默认值）。
   */
  pxH?: number
  script?: string | null
  cost?: string
  /** 仅 fileKind === 'runtime'：捕获来源的持久化描述（见 RuntimePanelSource） */
  source?: RuntimePanelSource
  overrides: PanelOverride[]
  figureSnapshot?: FigureSnapshot
  figureOrigin?: FigureOrigin
  /** Nonsemantic source/frame guard. Never changes the meaning of overrides. */
  artifactValidation?: ArtifactValidation
  /**
   * 来源驱动采用 guard（ADR 0139 PR2），与 `artifactValidation` **并列且互斥**、形状不同。
   * 旧前端读到它会忽略，把面板当未采用 PNG 显示原像素；老项目不会获得这个字段。
   */
  sourceBackedFigure?: SourceBackedFigure
  /**
   * 用户点了「用脚本重画」（T-005，ADR 0139 修订 2026-11-24）：零编辑也**显示重画**，不显示磁盘原像素——
   * 画布与画布导出是同一份重画。只在来源驱动采用（`sourceBackedFigure`）旁边有意义；清采用时一并清掉。
   * 旧前端读到会忽略——回到「零编辑显示原像素」（安全的降级）。
   */
  showsRedraw?: true
  /**
   * SVG 计算图表状态（ADR 0182，仅 fileKind === 'svg'）：显式绑定的声明式模型与绑定。
   * 只存**语义真值**（模型 + 绑定）；组合后的 SVG 是派生物，按模型 rev 重算，不进文档。
   * 旧前端读到它会忽略——面板退化为静态 SVG 预览，不丢数据。
   */
  chart?: PanelChartState
  chartSource?: ChartSourceRef
  /**
   * native-manifest/2 计算框的布局落位（见 `PanelChartFrame`）：只持久
   * origin/profile，科学语义仍只在 `chart`。旧前端读到会忽略——面板退化为
   * /1 的 source viewport 预览，不丢数据。
   */
  chartFrame?: PanelChartFrame
  /**
   * 锁定的图内元素 gid：画布命中测试跳过它们，避免误选误拖。
   * 只影响交互，不影响渲染与导出；元素树里仍可选中（用于解锁）。
   */
  lockedGids?: string[]
  /**
   * 「从数据到图」画出的这张图是怎么画出来的（只给误差线是 Tavotto 算出来的图：点误差线 / 阴影带时换 SD / SEM / 95% CI 用，ADR 0178）。
   * 可选字段，schema 不升；旧构建读到会忽略（图照常可用）。**只放在用户自己的项目文档里**：不进遥测、诊断包、分享审阅与导出请求（逐条有测试钉住）。
   * 形状与读法的唯一出处 `lib/dataFigureOrigin.readPanelOrigin`（版本不认识 / 形状不对 / 路径不是项目相对 = 当没有）。
   */
  dataOrigin?: PanelDataOrigin
  crop?: CropRect
  /**
   * 90° 步进旋转。**x/y/w/h 永远是旋转后的页面落位包围盒**，
   * 内容（未旋转）的显示尺寸在 90/270 时与 w/h 互换 —— 见 panelContentSize。
   */
  rotation?: PanelRotation
  /** 0–1；导出时是透明组（整体 alpha，仍是矢量，ADR 0065；旧后端时代 <1 会退成位图）。 */
  opacity?: number
  /**
   * 水平 / 垂直翻转（作用于内容空间，先翻转后旋转，与 CSS transform 一致）。
   * 导出时翻转面板是 `cm` 里的负缩放，仍是矢量（U10 起 RenderCore，ADR 0065；
   * 旧后端时代按导出 DPI 位图嵌入，与 opacity<1 相同的明示取舍——已不再需要）。
   */
  flipH?: boolean
  flipV?: boolean
  /** 宽高比锁定，缺省 true = 现状的等比联动；显式关掉后 W/H 各改各的。 */
  aspectLocked?: boolean
  /**
   * 图幅语义的记号（ADR 0098）：`1` = 按「脚本 savefig 的裁切框就是图幅」放上来 / 迁移过。
   * 缺席 = 升级前的面板，读档时由 `lib/figureFrameMigration.migrateFigureFrames` 迁移（必要时补一条
   * `figure.frame = "figsize"`，保持它导出过的样子）。磁盘格式不升版。
   */
  figureFrame?: 1
}

/**
 * 一张字体脸的**身份**（ADR 0164）：文件 sha256 + 面序号，名字只用于显示。
 * 文档里不存字体文件、不存路径。
 */
export interface SourceFace {
  family: string
  /** 字体文件字节的 sha256（小写十六进制，64 位）；认脸只看它 */
  sha256: string
  /** 字体集（.ttc）里的第几张脸；单脸文件为 0 */
  face: number
  /**
   * 同族的其它粗斜（ADR 0169 修订），最多三张。行内 `\\textbf{}` / `\\textit{}` 与中文加粗按它换脸。
   * 归属来自字体清单；缺席 = 这张脸是单个文件（复制自图内文字），行内粗斜用 `fontFamily` 的批准脸。
   */
  siblings?: SiblingFace[]
}

/** 同族里另一张脸：只有身份和它自己的粗斜，名字跟随所在的 `SourceFace`。 */
export interface SiblingFace {
  sha256: string
  face: number
  bold?: boolean
  italic?: boolean
}

/**
 * 画布文字「用原图那个字体」（ADR 0164，修订 ADR 0060）。**可选：老文档没有它 = 现状逐字不变**，
 * 磁盘格式不升版；导出载荷缺席时也不多字段。`fontFamily` 继续当回退族：本机找不到这张脸 /
 * 许可不让嵌入 / 在浏览器 playground 里，都按 `fontFamily` 的批准脸画，名字原样保留、给提示，
 * **绝不换掉再改文档**。用户在属性页改字体族 / 粗细 / 斜体就清掉它。
 *
 * 值的唯一产生者是 `lib/sourceFont.sourceFontFromElement`（图内文字的 manifest 身份 → 这里）。
 */
export interface SourceFont extends SourceFace {
  /** 这张脸自己的粗斜（来自原图），不是对象的粗斜开关 */
  bold?: boolean
  italic?: boolean
  /** 原图里中日韩字符实际由哪张脸画；缺席 = 批准的 CJK 脸（随族：衬线 Noto Serif SC，其余 Noto Sans SC） */
  cjk?: SourceFace
}

export interface TextObject extends ObjectBase {
  type: 'text'
  text: string
  sizePt: number
  /**
   * 字体族。**可选：老文档没有它 = 从没设过 = 继承默认（衬线）**，
   * 磁盘格式不升版，导出载荷也不多字段。生效值一律经
   * `lib/typography.effectiveCanvasFamily()` 取，别处不许写第二个默认值。
   *
   * 取值是三个通用族的闭集（`CANVAS_TEXT_FAMILIES`）——合成跑在没有
   * matplotlib 的 Flask 进程里，画得出来的就是批准字体集合里的 Liberation 三族。
   */
  fontFamily?: 'serif' | 'sans-serif' | 'monospace'
  /** 字体的身份：原图字体（ADR 0164）或用户选的本机字体（ADR 0169）；见 `SourceFont`。可选，缺席 = 用 `fontFamily` 对应的批准族。 */
  sourceFont?: SourceFont
  /**
   * 排向（ADR 0163「横排 / 竖排」）。**可选：老文档没有它 = 横排**，磁盘格式不升版，导出载荷也不多字段；
   * 回到横排时**删字段**。`'vertical'` = 从上到下、列从右往左（CSS `vertical-rl`）：中日韩字头朝上，
   * 拉丁与数字侧躺，全角标点用竖排字形；可与 `rotationDeg` 叠加（先竖排排版、再整体旋转）。
   *
   * 框语义：`x/y/w/h` 仍是未旋转的框。竖排里 `h` 是「行长」（列按它折行），`w` 由列数撑开
   * （画布上的 `TextView` 量出来写回，右边缘不动）；`align` 左 / 中 / 右 = 列内的 上 / 中 / 下。
   */
  writingMode?: 'vertical'
  /**
   * 科学文本解释档位。**可选：老文档没有它 = auto**（与升级前逐字符同样的
   * 渲染），磁盘格式不升版。
   *
   *   auto        只有「不然就是方框」的 Unicode 上下标才合成
   *   scientific  认得的 Unicode 上下标一律合成：字体统一，代价是 PDF
   *               文本层里的 `⁵` 变成 `5`（实测），所以必须由用户明确选
   *
   * 闭集与语义的唯一出处是 `lib/richText.ts`（↔ `src/tavotto/richtext.py`）。
   */
  interpretation?: 'auto' | 'scientific'
  /**
   * `text` 的写法（ADR 0152）。**可选：老文档没有它 = 旧标记**（`^{…}` / `_{…}`），读法逐字
   * 不变、磁盘格式不升版。`'latex'` = LaTeX 子集（`\textit{}` / `\textbf{}` / `$…$`），由
   * 所见即所得编辑框写入。闭集与语义的唯一出处是 `lib/richText.ts`（↔ `src/tavotto/richtext.py`）。
   */
  textFormat?: TextFormat
  bold: boolean
  /** 可选：旧文档无此字段，undefined 即 false（后端 t.get("italic") 同义）。
   *  斜体只作用于拉丁字形——导出走 times-italic，宋体无斜体变体。 */
  italic?: boolean
  color: string
  align: 'left' | 'center' | 'right'
  underline?: boolean
  /** 行距倍数；缺省 1.25（与历史行为一致） */
  lineHeight?: number
  /** 内边距 mm（配背景 / 描边使用）；缺省 0 */
  padding?: number
  /** 背景填充色；null/undefined = 无背景 */
  bg?: string | null
  /** 文本框描边；无 borderColor = 不描边 */
  borderColor?: string | null
  borderPt?: number
}

/** 端点相对包围盒的比例坐标（0–1）；箭头与直线形状同构 */
export interface EndPoint {
  rx: number
  ry: number
}

export interface ArrowObject extends ObjectBase {
  type: 'arrow'
  /** 端点相对包围盒的比例坐标（0–1） */
  start: EndPoint
  end: EndPoint
  strokePt: number
  color: string
  /** 旧字段：none/end/both（实心三角）。新文档写 headStart/headEnd。 */
  head: 'none' | 'end' | 'both'
  headStart?: ArrowHeadType
  headEnd?: ArrowHeadType
  /** 两端端型大小；缺省 = 中（不写字段，ADR 0174） */
  headSizeStart?: HeadSize
  headSizeEnd?: HeadSize
  dash?: DashStyle
  /** 描边不透明度 0–1；缺省 1（不写字段）。作用于杆与端型 */
  strokeOpacity?: number
  /** 线端 / 拐角；缺省 = 圆头 / 圆角（现在的画法），不写字段 */
  lineCap?: LineCap
  lineJoin?: LineJoin
  /** 尖角的斜接上限（≥ 1）；缺省 = `shapeGeometry.STROKE_MITER_LIMIT`（10），等于缺省不写 */
  miterLimit?: number
  /** 自定义虚线：线段 / 间隙交替的长度（pt，绝对值）；有它就不看 `dash`。缺省 / 空 = 不用 */
  dashPattern?: number[]
}

export type ShapeKind =
  | 'rect'
  | 'ellipse'
  | 'line'
  | 'triangle'
  | 'diamond'
  | 'polygon'
  | 'brace'
  /** 星形（ADR 0185 M1）：`sides` = 角数（缺省 5），`starRatio` = 内径比（缺省 `DEFAULT_STAR_RATIO`） */
  | 'star'
  /** 弧线（ADR 0185 M1）：框的左下角到右上角的四分之一椭圆弧，只有描边 */
  | 'arc'

/** 星形的缺省角数与内径比（正五角星的内外径比 ≈ 0.382）；与后端 `geometry.DEFAULT_STAR_POINTS / DEFAULT_STAR_RATIO` 同源 */
export const DEFAULT_STAR_POINTS = 5
export const DEFAULT_STAR_RATIO = 0.382

/** 只有描边、没有填充也没有描边开关的形状（直线 / 大括号 / 弧线）；判据只有这一处，后端 `geometry.STROKE_ONLY_SHAPES` 同一张表 */
export const STROKE_ONLY_SHAPES: ReadonlySet<string> = new Set(['line', 'brace', 'arc'])
export const isStrokeOnlyShape = (shape: string): boolean => STROKE_ONLY_SHAPES.has(shape)

export interface ShapeObject extends ObjectBase {
  type: 'shape'
  shape: ShapeKind
  strokePt: number
  color: string
  fill: string | null
  /**
   * 直线端点（与箭头同构的包围盒比例坐标）。**只有 shape==='line' 用得上**，
   * 其它 shape 忽略。旧文档没有这两个字段 —— 缺省 (0,0.5)→(1,0.5) 即包围盒
   * 水平中线，与新增端点之前的画法逐点一致（schema 不升版）。
   * 读取一律走 lineEndpoints()，别在各处手写缺省值。
   */
  start?: EndPoint
  end?: EndPoint
  /** 圆角半径 mm（rect 专用） */
  cornerRadius?: number
  /** 正多边形边数（polygon 专用，3–12）；星形的角数（star，3–12，缺省 5） */
  sides?: number
  /** 星形内径比（star 专用，0.05–0.95；缺省 `DEFAULT_STAR_RATIO`，等于缺省不写字段） */
  starRatio?: number
  /** 填充透明度 0–1；缺省 1 */
  fillOpacity?: number
  dash?: DashStyle
  /**
   * 描边开关：**只有 `false` 会落盘**（= 无边框）；缺省 / `true` = 有描边。
   * 只对封闭形状有意义，直线与大括号只有描边、一律有。读取走 `shapeStrokeOn()`。
   */
  stroke?: boolean
  /** 描边不透明度 0–1；缺省 1（不写字段） */
  strokeOpacity?: number
  /** 线端 / 拐角；缺省见 `lib/shapeGeometry`，等于缺省就不写字段 */
  lineCap?: LineCap
  lineJoin?: LineJoin
  /** 尖角的斜接上限（≥ 1）；缺省 = `shapeGeometry.STROKE_MITER_LIMIT`（10），等于缺省不写 */
  miterLimit?: number
  /** 自定义虚线：线段 / 间隙交替的长度（pt，绝对值）；有它就不看 `dash`。缺省 / 空 = 不用 */
  dashPattern?: number[]
  /** 填充渐变（ADR 0186）：有它且有填充时覆盖 `fill`；`fill` 仍留着做旧版本的降级色。读取只走 `lib/gradient.readGradient` */
  fillGradient?: Gradient
  /** 描边渐变（ADR 0186）：有它且有描边时覆盖 `color`（端型仍用 `color`） */
  strokeGradient?: Gradient
  /**
   * 描边对齐（只对封闭形状）：缺省 = 内侧（描边整个在框里、外沿贴框，加属性之前就是这样）；
   * `center` = 骑在轮廓上（框边就是线的中线）；`outside` = 整个在框外。等于缺省不写
   */
  strokeAlign?: 'center' | 'outside'
}

/** 渐变的一个色标（ADR 0186）：位置 0–1、颜色 #rrggbb、不透明度缺省 1 */
export interface GradientStop {
  offset: number
  color: string
  opacity?: number
}

/**
 * 渐变（ADR 0186）：坐标是对象包围盒的比例（与路径锚点同一套），随缩放一起走。线性 = 起点 → 终点；
 * 径向 = 同心圆，半径按包围盒长边的比例。只做 pad（两端外面延续首尾色）。
 */
export type Gradient =
  | { kind: 'linear'; stops: GradientStop[]; from: [number, number]; to: [number, number] }
  | { kind: 'radial'; stops: GradientStop[]; center: [number, number]; radius: number }

/** 路径上的一个点：包围盒比例坐标 [rx, ry]（与 `EndPoint` 同一套，可以落在 [0,1] 之外） */
export type PathPoint = [number, number]

/** 路径的一个锚点（ADR 0185 §二）。手柄是绝对坐标（同一坐标系）；缺省 = 与锚点重合 */
export interface PathNode {
  p: PathPoint
  in?: PathPoint
  out?: PathPoint
  /** 平滑点（拖一侧手柄另一侧共线）；缺省尖角，不写字段 */
  smooth?: true
}

export interface PathSubpath {
  closed: boolean
  nodes: PathNode[]
  /**
   * 实时圆角的记录（ADR 0185 M7 方案 B）：`nodes` 照旧是圆过之后的几何（导出、旧版本都只读它），这里另记圆之前的尖角锚点
   * （同一套框比例坐标）与每个尖角的半径（相对框的长边 `max(w, h)`，0 = 没圆）——再调圆角时从尖角重新圆一遍。
   * **只经 `lib/liveCornerRecord.liveCornerRecord` 读**：记录与 `nodes` 对不上（别的操作改过几何）就当没有。
   */
  liveCorners?: { sharp: PathNode[]; radii: number[] }
}

/**
 * 自由路径（ADR 0185 §二）。`x/y/w/h` 仍是权威包围盒（按曲线实际范围算，不按控制点），
 * 锚点与手柄是它的比例坐标。填充 / 描边字段与 `ShapeObject` 逐字相同（ADR 0174），
 * 端型字段与 `ArrowObject` 同名同义、只对开放子路径生效。读取缺省值一律走 `readPath()`。
 */
export interface PathObject extends ObjectBase {
  type: 'path'
  subpaths: PathSubpath[]
  /** 缺省 nonzero，不写字段 */
  fillRule?: 'evenodd'
  strokePt: number
  color: string
  fill: string | null
  fillOpacity?: number
  /** 只有 `false` 会落盘（= 无描边） */
  stroke?: boolean
  strokeOpacity?: number
  dash?: DashStyle
  lineCap?: LineCap
  lineJoin?: LineJoin
  /** 尖角的斜接上限（≥ 1）；缺省 = `shapeGeometry.STROKE_MITER_LIMIT`（10），等于缺省不写 */
  miterLimit?: number
  /** 自定义虚线：线段 / 间隙交替的长度（pt，绝对值）；有它就不看 `dash`。缺省 / 空 = 不用 */
  dashPattern?: number[]
  /** 填充渐变（ADR 0186）：有它且有填充时覆盖 `fill`；`fill` 仍留着做旧版本的降级色。读取只走 `lib/gradient.readGradient` */
  fillGradient?: Gradient
  /** 描边渐变（ADR 0186）：有它且有描边时覆盖 `color`（端型仍用 `color`） */
  strokeGradient?: Gradient
  /**
   * 描边对齐（ADR 0185 M2）：缺省居中（不写字段，与 Illustrator 的路径相同——注意形状的缺省是内侧）。只在**全部子路径
   * 闭合**且有描边时生效（`pathGeom.pathStrokeAlign`）：内侧 = 两倍线宽的描边裁到填充区域里，外侧 = 裁到填充区域外
   */
  strokeAlign?: 'inside' | 'outside'
  headStart?: ArrowHeadType
  headEnd?: ArrowHeadType
  headSizeStart?: HeadSize
  headSizeEnd?: HeadSize
}

/**
 * 路径文字（ADR 0190）：一行字沿一条路径排开。**路径是自带的一份**（`subpaths`，框比例坐标，
 * 与 `PathObject` 同一套），不引用别的对象——原来那条线删了、挪了，字都还在原处；从一条路径 / 形状生成时与它成一个组（一起挪）。
 * 字的落位只在后端（`rendercore/pathtext`：按弧长逐字落到路径上），画布照后端给的字形轮廓画。文字样式字段与 `TextObject` 同名同义。
 * 旧构建不认识这个类型：显示占位、不让导出（`isKnownObject`），不会悄悄排成普通文字。
 */
export interface PathTextObject extends ObjectBase {
  type: 'pathText'
  text: string
  sizePt: number
  bold: boolean
  italic?: boolean
  color: string
  fontFamily?: 'serif' | 'sans-serif' | 'monospace'
  /** 字排在这条路径上（只用第一条画得出的子路径）；路径本身不画 */
  subpaths: PathSubpath[]
  /** 起点：路径全长的比例 0–1；缺省 0（不写） */
  startOffset?: number
  /** 字串以起点为准的对齐：start（缺省，不写）/ center / end */
  textAlign?: 'center' | 'end'
  /** 翻到路径另一侧（沿反方向走）；只写 true */
  flip?: true
  /**
   * 离线（pt）：基线离开路径中心线多远，沿字的上方量；0 = 基线贴在线上，负 = 往线上压。没写 = 自动（线宽 / 2 + 0.2 em +
   * 降部深度，字不碰线；只在后端算，`rendercore/pathtext.auto_offset_pt`）
   */
  baselineOffsetPt?: number
  /** 生成时一起成组的那条线（只用来取它的描边宽度算自动离线；那条线没了 / 隐藏 / 没描边 = 0） */
  lineId?: string
}

/** `readPath` 读出来的锚点：手柄缺省已补成锚点本身 */
export interface ResolvedPathNode {
  p: PathPoint
  in: PathPoint
  out: PathPoint
}

/** 路径的缺省值统一读取（画布、命中、缩略图、导出载荷同用这一处；后端 `geometry.read_path` 同一规则） */
export function readPath(
  o: Pick<PathObject, 'subpaths' | 'fillRule' | 'stroke' | 'headStart' | 'headEnd'>,
): {
  subpaths: { closed: boolean; nodes: ResolvedPathNode[] }[]
  fillRule: 'nonzero' | 'evenodd'
  strokeOn: boolean
  headStart: ArrowHeadType
  headEnd: ArrowHeadType
} {
  return {
    subpaths: (o.subpaths ?? []).map((sp) => ({
      closed: sp.closed === true,
      nodes: (sp.nodes ?? []).map((n) => ({ p: n.p, in: n.in ?? n.p, out: n.out ?? n.p })),
    })),
    fillRule: o.fillRule === 'evenodd' ? 'evenodd' : 'nonzero',
    strokeOn: o.stroke !== false,
    headStart: o.headStart ?? 'none',
    headEnd: o.headEnd ?? 'none',
  }
}

/**
 * 面板素材形态的唯一判别器。未知取值（更新的文档 schema 里的新形态）回
 * 'unknown'——消费方必须 fail closed：按缺失素材显示 / 跳过文件请求，
 * **绝不**把它当成相对路径去猜文件（那会显示出另一个面板的图）。
 */
export function panelKind(o: Pick<PanelObject, 'fileKind'>): PanelFileKind | 'unknown' {
  return o.fileKind === 'svg' || o.fileKind === 'pdf' || o.fileKind === 'raster' || o.fileKind === 'runtime'
    ? o.fileKind
    : 'unknown'
}

/** RuntimeFigureAsset 面板（磁盘上没有原件；写回入口整个不出现） */
export const isRuntimePanel = (o: Pick<PanelObject, 'fileKind'>): boolean =>
  o.fileKind === 'runtime'

/** 新旧箭头端型统一读取：旧 head 字段映射为三角头 */
export function arrowHeads(o: ArrowObject): { start: ArrowHeadType; end: ArrowHeadType } {
  if (o.headStart != null || o.headEnd != null) {
    return { start: o.headStart ?? 'none', end: o.headEnd ?? 'none' }
  }
  return {
    start: o.head === 'both' ? 'triangle' : 'none',
    end: o.head === 'end' || o.head === 'both' ? 'triangle' : 'none',
  }
}

/**
 * `arrowHeads` 的逆：把两个新端型压回旧 `head` 字段。
 *
 * 写新端型的每一处都得顺手维护它——旧构建、旧后端读到这份文档时只认 `head`，
 * 不维护的话一支「起点开口 V、终点无」的箭头在老版本里会变回默认的实心三角。
 * 规则只有这一份：属性栏改端型（`StrokeSection`）与直线切换成箭头
 * （`lib/shapeSwitch`）读的是同一个函数。
 *
 * ADR 0174 的新端型（dot / diamond / stealth）也只算「这一端有端型」：旧构建读到
 * 一份带新端型的文档时优先认 `headStart` / `headEnd`，不认识的端型画不出端型（线到端点），
 * 不会崩；`head` 只在两个新字段都缺席时才被读（那是旧文档），所以这里不需要为新端型选一个「近似的旧值」。
 */
export function legacyHead(heads: {
  start: ArrowHeadType
  end: ArrowHeadType
}): ArrowObject['head'] {
  const { start, end } = heads
  return start !== 'none' && end !== 'none' ? 'both' : end !== 'none' ? 'end' : 'none'
}

/** 形状此刻有没有描边。直线 / 大括号只有描边，不看 `stroke` 字段（画布、缩略图、导出同用这一处） */
export function shapeStrokeOn(o: Pick<ShapeObject, 'shape' | 'stroke'>): boolean {
  return isStrokeOnlyShape(o.shape) || o.stroke !== false
}

/** 直线形状（端点语义只对这一种 shape 生效） */
export type LineShape = ShapeObject & { shape: 'line' }

/**
 * 端点语义的线状对象：箭头 + 直线形状。两者的 start/end 结构一致，
 * 因而共用端点手柄（OverlaySvg.LinearEndpoints）与端点拖拽
 * （interactions.startEndpointDrag），也都不给包围盒缩放柄。
 */
export type LinearObject = ArrowObject | LineShape

export const isLinear = (o: CanvasObject): o is LinearObject =>
  o.type === 'arrow' || (o.type === 'shape' && o.shape === 'line')

/**
 * 线状对象的端点统一读取：直线的 start/end 是可选字段（旧文档没有），
 * 缺省兜底为包围盒水平中线 (0,0.5)→(1,0.5)；箭头两个字段必填，原样返回。
 * 渲染 / 拖拽 / 导出的每个读取点都走这里，缺省值只此一份。
 */
export function lineEndpoints(o: { start?: EndPoint; end?: EndPoint }): {
  start: EndPoint
  end: EndPoint
} {
  return { start: o.start ?? { rx: 0, ry: 0.5 }, end: o.end ?? { rx: 1, ry: 0.5 } }
}

/** 对象的任意角度旋转（面板恒 0——它走 90° 步进的 rotation） */
export const objectRotation = (o: CanvasObject): number =>
  o.type === 'panel' ? 0 : (o.rotationDeg ?? 0)

export type CanvasObject = PanelObject | TextObject | ArrowObject | ShapeObject | PathObject | PathTextObject

/**
 * 这个构建认得的对象类型（闭集）。更新的构建可能往文档里写进新类型（自由路径 `path` 是 ADR 0185 加进来的第一例）；
 * 本构建读到不认识的类型时**原样保留**（读盘、保存、换画布都不碰它），只画一个占位框，
 * 不让它被选中、移动、删除，也不让它在导出里静默消失（导出前拦下）。判据只有这一处。
 */
export const KNOWN_OBJECT_TYPES: ReadonlySet<string> = new Set(['panel', 'text', 'arrow', 'shape', 'path', 'pathText'])

/** 本构建认得这个对象（否则是更新的构建写进来的，见 `KNOWN_OBJECT_TYPES`） */
export const isKnownObject = (o: { type: string }): boolean => KNOWN_OBJECT_TYPES.has(o.type)
export type ObjectType = CanvasObject['type']

export interface Guide {
  axis: 'x' | 'y'
  pos: number
}

export interface PageSetup {
  w: number
  h: number
  /** 页面背景色；transparent=true 时导出与画布都不铺底色 */
  bg?: string
  transparent?: boolean
  /** 安全区域页边距（mm） */
  margin?: number
  /**
   * 用户自己定过这张画布的大小（属性栏宽高 / 预设 / 横竖 / 拖页面边）。可选，磁盘格式不升版；
   * 一键排版「画布：自动」据此选「保持画布大小」。一键排版自己写页面尺寸时**不**置它。
   */
  userSized?: boolean
}

/**
 * 一键排版留在画布上的「网格记忆」（ADR 0199，拖分隔线调比例用）。可选字段，旧文档没有；
 * 只在每张 `cells` 里的图都还在、且位置与大小仍等于 `boxes`（0.05 mm 内）时才算有效——
 * 用户挪过 / 缩过 / 删过任何一张就静默失效（`lib/quickLayoutGrid.quickLayoutValid`）。
 */
export interface QuickLayoutMemo {
  rows: number
  cols: number
  /** 画布对象 id → [行, 列] */
  cells: Record<string, [number, number]>
  colRatios: number[]
  rowRatios: number[]
  /** 排完那一刻各图在页面上的框（mm，[x, y, w, h]） */
  boxes: Record<string, [number, number, number, number]>
  target: 'single' | 'double' | 'page'
  gapMm: number
  /** 那一次用的画布模式（已解析：保持 / 跟着内容） */
  canvas: 'keep' | 'fit'
  fixSpec: boolean
  /** 那一次作为固定盒子（障碍物）参与的图 id（拖分隔线重排时照样带上） */
  fixedIds?: string[]
}

/**
 * 这张图按哪套出版规范做预检 / 导出。**可选字段，旧文档没有它**
 * ——那时按 `id` 去全局清单取现值，`id` 也没有就用默认规范。
 *
 * `snapshot` 是 Session 10（ADR 0029）加的：**选中那一刻生效的规则全文**。
 * 有它的时候它说了算——「项目结果稳定」优先于「规范升级自动生效」：改一次
 * 全局规范就让上个月定稿的图换一套判据，用户没有任何办法知道结论为什么变了。
 * 全局那份后来变了，界面提示「有新版可同步」，由用户明确确认（进文档历史）。
 *
 * **判据是内容不等，不是版本号**：版本号是给人看的，谁都可能忘了改它。
 * 三个新字段全部可选，磁盘格式不升版；老文档没有 = 从没绑过快照。
 */
export interface DocumentProfile {
  id: string
  /** 期刊自定义覆盖（如把双栏宽改成 178mm）；结构见 lib/profile.ts */
  journal?: Record<string, unknown>
  /** 绑定那一刻的规则全文。**规则进文档只有这一个位置**（见 lib/specBinding.ts） */
  snapshot?: Record<string, unknown>
  /** 快照取自的那一版的展示用版本号。只用来说人话，判据一个字都不看它 */
  snapshotVersion?: string
  /** 用户明确选了「跟着全局走」。缺省 = 没选 = 按快照 */
  follow?: boolean
  /** 按期刊选的：选那一刻的期刊 id / 库版本 / 采集日期 / 期刊名（ADR 0155 §4）；缺省 = 没按期刊选过 */
  journalRef?: { id: string; catalogRev: number; retrieved: string; label: string }
  /** 生效字段出处的精简版（`lib/journals.JournalFieldSource`），出处卡离线也读得到 */
  journalSources?: Record<string, JournalFieldSource>
  /** 选期刊之前的规范绑定与页宽（`specBinding.JournalPrev`）：「不按期刊」据此退回 */
  journalPrev?: {
    profile: {
      id: string
      journal?: Record<string, unknown>
      snapshot?: Record<string, unknown>
      snapshotVersion?: string
      follow?: boolean
    } | null
    pageW: number
    setW?: number
  }
}

/**
 * 这张画布**跟随**哪套样式（ADR 0081）。**可选字段，旧文档没有它 = 没绑定**，
 * schema 不升版。
 *
 * 与规范绑定（`DocumentProfile`）同一个形状思路：`id` 指向全局样式库里的那一条，
 * `snapshot` 是**这张画布最后一次对齐到的样式内容**。判「样式变了没有」看的是
 * 内容不等（`specBinding.sameRules`），不是版本号。与规范不同的是：规范只检查、
 * 从不改图；样式绑定的意思就是「这张画布上的图长成这套样式的样子」，库里那一条
 * 改了，画布跟着改（一条可撤销的历史）。全局那条找不到了（换了台电脑 / 删了）时
 * 快照照样说了算。唯一出处 `store/styleBinding.ts`。
 */
export interface DocumentStyle {
  id: string
  /** 最后一次对齐到的样式内容（`StyleProfileData` 的形状）。**样式进文档只有这一个位置** */
  snapshot: Record<string, unknown>
  /**
   * 已脱离样式：用户撤销过一次样式修改 / 跟随（ADR 0081 §十二）。画布停在撤销回去的样子，
   * 不再跟随库；再明确选一次绑定才恢复。只由撤销落下（`commit` 的 `undoAlso`），重做把它撤掉
   */
  detached?: true
  /**
   * 哪些 override 是**样式写的**（ADR 0081 §十三）：`面板 id @ 素材` → gid → prop → 那一条。存在即表示
   * 样式写的；不在表里的 override 一律是用户的（老文档没有这个字段 = 全是用户的，保守：不让位）。
   * 只在「此刻那条 override 的值仍是 `value`」时算数：用户在属性页里改过它，它就归用户了。
   * 随绑定一起进历史（撤销 / 重做与 override 同一次回退）；解绑 / 恢复原样随 `style` 一起消失。
   */
  owned?: Record<string, Record<string, Record<string, StyleOwnedOverride>>>
}

/** 样式写下的一条 override（ADR 0081 §十三） */
export interface StyleOwnedOverride {
  /** 样式写下的值（脚本坐标系，即 override 里那个值） */
  value: unknown
  /**
   * 样式写入那一刻**脚本的原生值**（manifest 口径）。之后精确 manifest 的 `value_original` 与它不等 =
   * 脚本改过这一项 → 样式让位。说不出（老引擎、写在用户 override 之上又没有 `value_original`）时缺席 =
   * 不知道，永不让位
   */
  base?: unknown
}

/* ---------------------- 推荐配色的色键表（ADR 0210 §四） ---------------------- */

/** 一组占的槽：数字 = 方案里第几个颜色；`neutral` = 方案的中性灰（对照组，用户确认后才设） */
export type ColorSlot = number | 'neutral'

export interface ColorKey {
  /** 表内稳定 id（`k1`、`k2`…），只增不复用 */
  key: string
  slot: ColorSlot
  /** 归入这一组的显式 label 身份（`l1:` + 16 位十六进制）；并入别的组时把对方的抄进来 */
  identities: string[]
}

/** 配色写下的一条 override：此刻 override 的值仍是 `value` 才算配色的，否则就是用户手改过的 */
export interface ColorKeyOwned {
  panel: string
  gid: string
  prop: string
  value: string
}

export interface ColorKeyTable {
  version: 1
  /** 当前方案的目录 id */
  scheme: string
  keys: ColorKey[]
  owned: ColorKeyOwned[]
}

export interface FigureDocument {
  schema: 2
  name: string
  page: PageSetup
  objects: CanvasObject[]
  guides: Guide[]
  /** 可选的结构化布局组；旧文档没有该字段，自由排版行为完全不变 */
  layoutGroups?: LayoutGroup[]
  /** 可选的出版规范绑定；缺省走默认 profile */
  profile?: DocumentProfile
  /** 可选的样式绑定（ADR 0081）；缺省 = 不跟随任何样式 */
  style?: DocumentStyle
  /** 单图编辑的画布留白（ADR 0161）；缺省 = 全 0，画布就是图幅 */
  framePad?: FramePad
  /** 单图画布处在「紧凑」停靠点上的那几边（ADR 0161）：图幅变了按新的内容边缘重算，不保留旧 mm */
  framePadTight?: FramePadSide[]
  /** 一键排版的网格记忆（见 `QuickLayoutMemo`）；缺省 = 没排过或已清掉 */
  quickLayout?: QuickLayoutMemo
  /** 推荐配色的色键表（ADR 0210）；缺省 = 这张画布没用过推荐配色，旧文档逐字不变 */
  colorKeys?: ColorKeyTable
}

/**
 * 单图编辑的画布留白（ADR 0161）：图四周各加多少 mm（**导出 mm**，即图幅的 mm），负值 = 裁进图幅。
 * 挂在画布上：模式按画布判、单图一张图一张画布；变成多图排版时保留不用，删回一张原样回来。
 * 只在 Tavotto 的导出里生效，不写回脚本。
 */
export interface FramePad {
  l: number
  t: number
  r: number
  b: number
}
export type FramePadSide = keyof FramePad

/* ------------------------- schema 3：项目 / 画布 --------------------------- */
/**
 * 对象层级见 docs/adr/0001-project-canvas-tab-object.md。
 * schema 3 顶层是「项目文档」：一个项目多张画布（Canvas）。
 * 运行时激活画布仍以 FigureDocument（schema 2 形状）活在 documentStore.doc，
 * 因此既有画布编辑代码零改动；持久化与读档统一走 ProjectDocument。
 */

export interface CanvasData {
  id: string
  name: string
  page: PageSetup
  objects: CanvasObject[]
  guides: Guide[]
  layoutGroups?: LayoutGroup[]
  /** 每张画布各自的出版规范绑定；缺省走默认 profile */
  profile?: DocumentProfile
  /** 每张画布各自的样式绑定（ADR 0081）；缺省 = 不跟随任何样式 */
  style?: DocumentStyle
  /** 单图编辑的画布留白（ADR 0161）；缺省 = 全 0 */
  framePad?: FramePad
  framePadTight?: FramePadSide[]
  quickLayout?: QuickLayoutMemo
  /** 每张画布各自的推荐配色色键表（ADR 0210）；缺省 = 没用过 */
  colorKeys?: ColorKeyTable
}

/**
 * 本构建能读写的最高版本。**比它更高的文档一律不打开**——旧构建对新
 * 字段的语义一无所知，「尽力打开」等于用旧规则重写用户的新数据。
 *
 * 严格同源：`src/tavotto/engine/documents.py` 的 `SCHEMA_CURRENT`。
 */
export const SCHEMA_CURRENT = 4
/** 项目文档（一个项目多张画布）；schema 4 在同一结构上多出计算图表状态。 */
export const SCHEMA_PROJECT = 3

export interface ProjectDocument {
  schema: typeof SCHEMA_PROJECT | typeof SCHEMA_CURRENT
  project: { id: string; name: string }
  /** 数组序 = 画布显示顺序 */
  canvases: CanvasData[]
  activeCanvasId: string
  createdAt: number
  updatedAt: number
}

/** 激活画布（内存态）↔ 画布数据（持久化）之间的换算 */
export function canvasToDoc(c: CanvasData): FigureDocument {
  return {
    schema: 2,
    name: c.name,
    page: c.page,
    objects: c.objects,
    guides: c.guides,
    ...(c.layoutGroups ? { layoutGroups: c.layoutGroups } : {}),
    ...(c.profile ? { profile: c.profile } : {}),
    ...(c.style ? { style: c.style } : {}),
    ...(c.framePad ? { framePad: c.framePad } : {}),
    ...(c.framePadTight?.length ? { framePadTight: c.framePadTight } : {}),
    ...(c.quickLayout ? { quickLayout: c.quickLayout } : {}),
    ...(c.colorKeys ? { colorKeys: c.colorKeys } : {}),
  }
}

export function docToCanvas(doc: FigureDocument, id: string): CanvasData {
  return {
    id,
    name: doc.name,
    page: doc.page,
    objects: doc.objects,
    guides: doc.guides,
    ...(doc.layoutGroups ? { layoutGroups: doc.layoutGroups } : {}),
    ...(doc.profile ? { profile: doc.profile } : {}),
    ...(doc.style ? { style: doc.style } : {}),
    ...(doc.framePad ? { framePad: doc.framePad } : {}),
    ...(doc.framePadTight?.length ? { framePadTight: doc.framePadTight } : {}),
    ...(doc.quickLayout ? { quickLayout: doc.quickLayout } : {}),
    ...(doc.colorKeys ? { colorKeys: doc.colorKeys } : {}),
  }
}

/** 升级前的面板迁到 ADR 0098 的图幅语义（没有要改的原样返回，引用不变） */
function withFigureFrames(pd: ProjectDocument): ProjectDocument {
  let changed = false
  const canvases = pd.canvases.map((c) => {
    const objects = Array.isArray(c.objects) ? migrateFigureFrames(c.objects) : c.objects
    if (objects === c.objects) return c
    changed = true
    return { ...c, objects }
  })
  return changed ? { ...pd, canvases } : pd
}

/**
 * 读档统一入口：schema 2 迁移为单画布项目（内容逐字段搬运、不改值），
 * schema 3 / 4 原样校验通过。不认识的负载返回 null。
 */
export function migrateToProject(raw: unknown): ProjectDocument | null {
  const d = raw as Record<string, unknown> | null
  if (!d || typeof d !== 'object') return null
  if ((d.schema === SCHEMA_PROJECT || d.schema === SCHEMA_CURRENT) && Array.isArray(d.canvases) && d.canvases.length > 0) {
    const pd = withFigureFrames(d as unknown as ProjectDocument)
    const active = pd.canvases.some((c) => c.id === pd.activeCanvasId)
    return active ? pd : { ...pd, activeCanvasId: pd.canvases[0].id }
  }
  if (d.schema === 2 && Array.isArray(d.objects)) {
    const legacy = d as unknown as FigureDocument
    const canvas = docToCanvas({ ...legacy, objects: migrateFigureFrames(legacy.objects) }, newId('c'))
    return {
      schema: SCHEMA_PROJECT,
      project: { id: newId('p'), name: canvas.name },
      canvases: [canvas],
      activeCanvasId: canvas.id,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    }
  }
  return null
}

/**
 * 默认画布名（审计 T05）：**全产品只有这一个格式**。第一张画布、新建画布、
 * 教程项目里的画布都叫「Figure N」——此前空文档叫 Fig 1、新建的叫 Fig 2、
 * 教程里的叫 Figure 1，三种写法混在一个标签行里。
 *
 * 与下面那个**不是一回事**：这是画布名（标签行上那个），下面那个是文档名
 * （顶栏那个 / 另存的文件名）。
 */
export const defaultCanvasName = (n: number): string => `Figure ${n}`

/**
 * 新文档的默认名。**只在创建那一刻取**，之后就是用户内容（重命名、另存、
 * 最近文档列表里显示的都是它），所以按当时的界面语言给一个可读的名字，
 * 而不是 `fig_layout` 那种实现习惯（审计 T03）。它也会成为「另存为」的默认
 * 文件名，所以取值必须磁盘安全：不含路径分隔符与保留字符。
 */
export const defaultDocumentName = (): string =>
  t('document.defaultName', { ns: 'workspace' })

export function emptyProject(): ProjectDocument {
  const canvas: CanvasData = {
    id: newId('c'),
    name: defaultCanvasName(1),
    page: { w: 150, h: 100 },
    objects: [],
    guides: [],
  }
  return {
    schema: 3,
    project: { id: newId('p'), name: defaultDocumentName() },
    canvases: [canvas],
    activeCanvasId: canvas.id,
    createdAt: Date.now(),
    updatedAt: Date.now(),
  }
}

/**
 * 对象类型名。**是函数不是常量表**：常量在模块求值那一刻就把当时的语言
 * 定死了，之后切语言再也换不掉。
 */
export const objectTypeLabel = (type: ObjectType): string =>
  t(`objectType.${type}`, { ns: 'common' })

export function emptyDocument(): FigureDocument {
  return {
    schema: 2,
    name: defaultDocumentName(),
    page: { w: 150, h: 100 },
    objects: [],
    guides: [],
  }
}

/* ------------------------- 面板旋转 / 裁剪的坐标换算 ------------------------- */

export const ROTATIONS: PanelRotation[] = [0, 90, 180, 270]

export function panelRotation(o: PanelObject): PanelRotation {
  const q = (((Math.round((o.rotation ?? 0) / 90) % 4) + 4) % 4) * 90
  return q as PanelRotation
}

/** 90/270 时内容与包围盒的长宽互换 */
export const rotationSwaps = (r: PanelRotation) => r === 90 || r === 270

/** 内容（未旋转）在页面上的显示尺寸 */
export function panelContentSize(o: PanelObject): { w: number; h: number } {
  return rotationSwaps(panelRotation(o)) ? { w: o.h, h: o.w } : { w: o.w, h: o.h }
}

/** 内容未裁剪时的完整显示尺寸（缩放 % / 等效字号 / Fit 都以它为准） */
export function panelFullSize(o: PanelObject): { w: number; h: number } {
  const c = panelContentSize(o)
  return { w: c.w / (o.crop?.w ?? 1), h: c.h / (o.crop?.h ?? 1) }
}

/** 未裁剪的完整内容尺寸对应的原生长宽比（mm 口径，与 nativeW/H 同源） */
export const panelAspect = (o: PanelObject) => o.nativeW / o.nativeH

export const panelAspectLocked = (o: PanelObject) => o.aspectLocked !== false

/** 内容空间向量 → 页面空间（顺时针为正，y 向下，与 CSS rotate 一致） */
export function rotateVec(x: number, y: number, r: PanelRotation): [number, number] {
  switch (r) {
    case 90:
      return [-y, x]
    case 180:
      return [-x, -y]
    case 270:
      return [y, -x]
    default:
      return [x, y]
  }
}

/** 页面空间向量 → 内容空间（rotateVec 的逆） */
export function unrotateVec(x: number, y: number, r: PanelRotation): [number, number] {
  return rotateVec(x, y, ((360 - r) % 360) as PanelRotation)
}

/**
 * 图层树/历史/检查器里显示的对象名。
 *
 * **用户自己起的名字、文字内容、素材文件名一律原样透出，绝不翻译**——
 * 只有「箭头」「矩形」这类兜底的类型名才是界面文案。
 */
export function objectLabel(o: CanvasObject): string {
  if (o.name) return o.name
  switch (o.type) {
    case 'panel':
      return o.fileId.split('/').pop()?.replace(/\.[^.]+$/, '') ?? t('objectType.panel')
    case 'text':
      return displayText(o.text, o.textFormat).trim().slice(0, 18) || t('objectType.emptyText')
    case 'arrow':
      return t('objectType.arrow')
    case 'shape':
      return t(`shape.${o.shape}`)
    case 'path':
      return t('objectType.path')
    case 'pathText':
      return o.text.trim().slice(0, 18) || t('objectType.pathText')
    default:
      // 更新的构建写进来的类型（`isKnownObject`）：不许回 undefined（图层树、版本差异会显示成空）
      return t('objectType.unknown')
  }
}
