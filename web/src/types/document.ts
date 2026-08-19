/** 文档模型 —— 单位一律 mm，数组顺序即 z 序（末尾在最上）。 */
import { t } from '@/i18n'
import { newId } from '@/lib/id'

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
  /** 轻量成组：同 groupId 的对象一起选中、一起移动。不做嵌套组。 */
  groupId?: string
  /**
   * 布局组成员的「固定当前位置」：重排时跳过该对象。
   * 只对 layoutGroups 里登记的组生效，普通成组不受影响。
   */
  layoutPinned?: boolean
  /**
   * 任意角度旋转（度，顺时针，绕包围盒中心）。只对 text/arrow/shape 生效；
   * 面板仍走 90° 步进的 rotation（PyMuPDF 矢量置入的语义限制）。
   * x/y/w/h 始终是未旋转的包围盒。
   */
  rotationDeg?: number
}

/** 虚线样式：显示与导出共用同一枚举（间距按线宽比例换算） */
export type DashStyle = 'solid' | 'dashed' | 'dotted'

/** 箭头端型：实心三角 / 开口 V / 垂直短线（尺寸线用） */
export type ArrowHeadType = 'none' | 'triangle' | 'open' | 'bar'

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
}

/** 裁剪框：相对原图的归一化比例（0–1），undefined 表示不裁剪。 */
export interface CropRect {
  x: number
  y: number
  w: number
  h: number
}

/** 面板旋转只做 90° 步进：PyMuPDF 合成时非 90 倍数不填满目标矩形，语义对不上。 */
export type PanelRotation = 0 | 90 | 180 | 270

export interface PanelObject extends ObjectBase {
  type: 'panel'
  fileId: string
  fileKind: 'pdf' | 'raster'
  nativeW: number
  nativeH: number
  pxW?: number
  script?: string | null
  cost?: string
  overrides: PanelOverride[]
  /**
   * 锁定的图内元素 gid：画布命中测试跳过它们，避免误选误拖。
   * 只影响交互，不影响渲染与导出；元素树里仍可选中（用于解锁）。
   */
  lockedGids?: string[]
  crop?: CropRect
  /**
   * 90° 步进旋转。**x/y/w/h 永远是旋转后的页面落位包围盒**，
   * 内容（未旋转）的显示尺寸在 90/270 时与 w/h 互换 —— 见 panelContentSize。
   */
  rotation?: PanelRotation
  /** 0–1；<1 时导出的 PDF 里该面板改为位图嵌入（矢量 xobject 没有整体 alpha）。 */
  opacity?: number
  /**
   * 水平 / 垂直翻转（作用于内容空间，先翻转后旋转，与 CSS transform 一致）。
   * 导出时翻转面板按导出 DPI 位图嵌入（PyMuPDF 矢量置入不支持镜像），
   * 与 opacity<1 相同的明示取舍。
   */
  flipH?: boolean
  flipV?: boolean
  /** 宽高比锁定，缺省 true = 现状的等比联动；显式关掉后 W/H 各改各的。 */
  aspectLocked?: boolean
}

export interface TextObject extends ObjectBase {
  type: 'text'
  text: string
  sizePt: number
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
  dash?: DashStyle
}

export type ShapeKind =
  | 'rect'
  | 'ellipse'
  | 'line'
  | 'triangle'
  | 'diamond'
  | 'polygon'
  | 'brace'

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
  /** 正多边形边数（polygon 专用，3–12） */
  sides?: number
  /** 填充透明度 0–1；缺省 1 */
  fillOpacity?: number
  dash?: DashStyle
}

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

export type CanvasObject = PanelObject | TextObject | ArrowObject | ShapeObject
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
}

/**
 * 这张图按哪套出版规范做预检 / 导出。
 * **可选字段，旧文档没有它 —— 缺省即规范文件里的 default_profile**
 * （规则本身一条都不存在文档里，只存 id 与期刊覆盖；规范升级后旧文档
 * 自动跟着新规则走，而不是把一份过期的规则冻在布局文件里）。
 */
export interface DocumentProfile {
  id: string
  /** 期刊自定义覆盖（如把双栏宽改成 178mm）；结构见 lib/profile.ts */
  journal?: Record<string, unknown>
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
}

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
}

export interface ProjectDocument {
  schema: 3
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
  }
}

/**
 * 读档统一入口：schema 2 迁移为单画布项目（内容逐字段搬运、不改值），
 * schema 3 原样校验通过。不认识的负载返回 null。
 */
export function migrateToProject(raw: unknown): ProjectDocument | null {
  const d = raw as Record<string, unknown> | null
  if (!d || typeof d !== 'object') return null
  if (d.schema === 3 && Array.isArray(d.canvases) && d.canvases.length > 0) {
    const pd = d as unknown as ProjectDocument
    const active = pd.canvases.some((c) => c.id === pd.activeCanvasId)
    return active ? pd : { ...pd, activeCanvasId: pd.canvases[0].id }
  }
  if (d.schema === 2 && Array.isArray(d.objects)) {
    const legacy = d as unknown as FigureDocument
    const canvas = docToCanvas(legacy, newId('c'))
    return {
      schema: 3,
      project: { id: newId('p'), name: canvas.name },
      canvases: [canvas],
      activeCanvasId: canvas.id,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    }
  }
  return null
}

export function emptyProject(): ProjectDocument {
  const canvas: CanvasData = {
    id: newId('c'),
    name: 'Fig 1',
    page: { w: 150, h: 100 },
    objects: [],
    guides: [],
  }
  return {
    schema: 3,
    project: { id: newId('p'), name: 'fig_layout' },
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
    name: 'fig_layout',
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
      return o.text.trim().slice(0, 18) || t('objectType.emptyText')
    case 'arrow':
      return t('objectType.arrow')
    case 'shape':
      return t(`shape.${o.shape}`)
  }
}
