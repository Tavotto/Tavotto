import { apiUrl, withProject } from '@/lib/session'
import { formatMessage, i18n, literal, msg, t, type UiMessage } from '@/i18n'
import type { FigureDocument, ProjectDocument } from '@/types/document'

export interface PanelInfo {
  id: string
  name: string
  folder: string
  kind: 'pdf' | 'raster'
  native_w_mm: number
  native_h_mm: number
  px_w?: number
  px_h?: number
  mtime: number
  /** 有值 = 由 matplotlib 脚本产出，可参数化编辑 */
  script?: string
  cost?: string
  /**
   * 「写回原始文件」时一并烙下的 override 基线。
   * 磁盘上的 PDF/PNG 已经包含这些修改，新建面板实例要继承它，
   * 否则进编辑态会看到「脚本原始状态」而不是文件当前的样子。
   */
  baked_overrides?: { gid: string; prop: string; value: unknown }[]
}

export interface PanelsResponse {
  figures_dir: string
  panels: PanelInfo[]
}

/** 带上服务器错误体的 Error：某些端点会附带可操作的线索（如就近可用路径） */
export class ApiError extends Error {
  status: number
  body: Record<string, unknown>
  constructor(message: string, status: number, body: Record<string, unknown>) {
    super(message)
    this.status = status
    this.body = body
  }
}

/**
 * 后端错误 → 当前语言的一句话。
 *
 * 约定见 `src/tavotto/app.py` 顶部：用户会看到的失败带稳定 `code` + `params`，
 * `error` 里的中文原文只是回退。**先查 code**，查不到才用原文——后端不知道
 * 用户选了哪门语言，让它去猜等于把语言偏好搬到服务端。
 *
 * 没 code、或本前端还不认识这个 code 时原样透出后端那句话：一句中文总比
 * 一串 `errors:backend.xxx` 有用，也比「发生了未知错误」有用。
 */
export function backendErrorText(e: unknown): string {
  return formatMessage(backendErrorMsg(e))
}

/**
 * 同一条错误的**描述符**版本，给活得比一次渲染长的地方用（常驻错误 toast、
 * 确认框、历史标签）。
 *
 * `backendErrorText` 是当场翻的：错误 toast 会一直挂到用户手动关掉，中途
 * 切语言时 `StatusToasts` 会重渲染，可里面那句话早已被 `literal()` 冻成上
 * 一门语言，**再也换不回来**（code 与 params 都被拼进字符串了）。
 */
export function backendErrorMsg(e: unknown): UiMessage {
  if (e instanceof ApiError) {
    const code = typeof e.body?.code === 'string' ? e.body.code : ''
    // 用 exists 而不是 defaultValue 判「有没有这条」：i18n 那边的
    // parseMissingKeyHandler 会把缺失的 key 原样吐回来（界面上看得见是哪条），
    // 那样 defaultValue 永远轮不到，缺文案时用户看到的就是 `backend.xxx`。
    if (code && i18n.exists(`backend.${code}`, { ns: 'errors' })) {
      const params = (e.body?.params ?? {}) as Record<string, unknown>
      return msg(`backend.${code}`, params, 'errors')
    }
  }
  // 后端没给 code（或本地还没有这条文案）：原文照抄，不翻
  return literal(e instanceof Error ? e.message : String(e))
}

/* --------------------- 项目失效（409 no_project）的统一出口 ------------------- */
/**
 * 后端不认本标签页的项目了：进程重启后 PROJECTS 清空、或项目被别处关掉，而
 * sessionStorage 里还留着旧 pj。此后**每个**请求都是 409 `no_project`
 * （app.py 的 _request_ctx 绝不悄悄落到默认项目），界面却停在原地——点重试
 * 也只是再 409 一次。
 *
 * 所以在请求出口集中认这个码，触发一次「回到项目选择」，然后照常把错误抛给
 * 调用方（语义一点不变，组件层不需要各自认识这个码）。用回调注册而不是直接
 * import projectStore：那边 import 了本模块，反向再引一次就成了循环依赖。
 */
let noProjectHandler: (() => void) | null = null
/** 节流：一屏十几个请求会同时 409，恢复动作只能跑一次 */
let noProjectFired = false

export function setNoProjectHandler(fn: (() => void) | null): void {
  noProjectHandler = fn
}

/** 重新认领到项目后调用：下一次项目失效时还要能把用户送回选择器 */
export function armNoProjectRecovery(): void {
  noProjectFired = false
}

/**
 * 409 + `code=no_project` → 触发一次恢复。**只认这一个码**：同样是 409 的
 * `stale_write`（自动保存的乐观并发）与 `file_locked`（写回撞上独占锁）各有
 * 各的处理，误伤它们等于把用户的改动扔了。
 */
function noteProjectGone(status: number, body: Record<string, unknown>): void {
  if (status !== 409 || body?.code !== 'no_project') return
  if (noProjectFired || !noProjectHandler) return
  noProjectFired = true
  try {
    noProjectHandler()
  } catch {
    /* 恢复动作自己出错，不能连累本次请求的错误往上抛 */
  }
}

/** 失败响应的 JSON 错误体（非 JSON 时给空对象）；只在 !res.ok 时调用 */
const errorBody = (res: Response): Promise<Record<string, unknown>> =>
  res
    .json()
    .then((b) => (b ?? {}) as Record<string, unknown>)
    .catch(() => ({}) as Record<string, unknown>)

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(apiUrl(url), withProject(init))
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    let body: Record<string, unknown> = {}
    try {
      body = (await res.json()) as Record<string, unknown>
      if (typeof body?.error === 'string') detail = body.error
    } catch {
      /* 非 JSON 错误体，保留状态码 */
    }
    noteProjectGone(res.status, body)
    throw new ApiError(detail, res.status, body)
  }
  return res.json() as Promise<T>
}

export const fetchPanels = () => jsonFetch<PanelsResponse>('/api/panels')

/* ----------------------------- 项目（Project） ------------------------------ */
/** 层级见 docs/adr/0001-project-canvas-tab-object.md；未打开项目时后端回 409。 */

export interface ProjectStatus {
  open: boolean
  /** 后端给这个项目的短 id；本标签页据此认领项目（见 lib/session.ts） */
  id?: string
  figures_dir?: string
  name?: string
  exists?: boolean
  writable?: boolean
  scripts?: number
  settings?: { export_dir?: string; backup_dir?: string; allow_write_back?: boolean }
  export_dir?: string
  backup_dir?: string
  /** 打开动作附带：注册表是静态扫描草稿 / stem 归属冲突列表 */
  drafted?: boolean
  conflicts?: string[]
}

export interface RecentProject {
  path: string
  name: string
  last_opened: number
  exists: boolean
  /** 已在后端打开（其它标签页可能正用着）；有值即为它的项目 id */
  id?: string | null
  opened?: boolean
  current: boolean
}

export const fetchProject = () => jsonFetch<ProjectStatus>('/api/project')

/** 后端进程里打开着的全部项目（多标签页各开各的时用来做快速切换） */
export const fetchOpenProjects = () =>
  jsonFetch<{ projects: ProjectStatus[]; default: string | null }>('/api/projects').then(
    (r) => r.projects,
  )

export const openProjectApi = (path: string, create = false) =>
  jsonFetch<ProjectStatus>('/api/projects/open', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, create }),
  })

export const fetchRecentProjects = () =>
  jsonFetch<{ recent: RecentProject[] }>('/api/projects/recent').then((r) => r.recent)

export const removeRecentProject = (path: string) =>
  jsonFetch<{ ok: boolean }>('/api/projects/remove', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })

export interface DirEntry {
  name: string
  path: string
}

export interface BrowseResult {
  path: string
  parent: string | null
  dirs: DirEntry[]
  /** Windows 的盘符（此电脑那一层）；POSIX 上只有 `/` */
  roots: DirEntry[]
  /** 主目录 / 桌面 / 文档 等常用起点 */
  shortcuts: DirEntry[]
  /** 当前列的是「驱动器」那一层虚拟根 */
  is_roots: boolean
  writable?: boolean
}

/** 目录列举。`'@roots'` = 列驱动器（Windows 靠它跨到 D 盘）。 */
export const browseDirs = (path?: string) =>
  jsonFetch<BrowseResult>(
    `/api/projects/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`,
  )

export const patchProjectSettings = (patch: {
  export_dir?: string
  backup_dir?: string
  allow_write_back?: boolean
}) =>
  jsonFetch<{ settings: ProjectStatus['settings']; export_dir: string; backup_dir: string }>(
    '/api/project/settings',
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    },
  )

/**
 * 图片 URL 一律带 `m=<mtime>`：/api/render 的响应是长缓存，URL 不变浏览器就吃
 * 本地缓存、请求根本到不了服务端。mtime 来自 /api/panels，「写回原始文件」后会变，
 * 跨会话也稳定（比自增计数器语义更准）。
 */
const stamp = (mtime?: number) => (mtime ? `&m=${mtime}` : '')

export const renderUrl = (id: string, bucket: number, mtime?: number) =>
  apiUrl(`/api/render?id=${encodeURIComponent(id)}&w=${bucket}${stamp(mtime)}`)

export const fileUrl = (id: string, mtime?: number) =>
  apiUrl(`/api/file?id=${encodeURIComponent(id)}${stamp(mtime)}`)

/** 位图走原文件、矢量走分档渲染 —— 与后端缓存策略一致 */
export const panelSrc = (
  id: string,
  kind: 'pdf' | 'raster',
  bucket: number,
  mtime?: number,
) =>
  kind === 'raster' ? fileUrl(id, mtime) : renderUrl(id, bucket, mtime)

/* ----------------------------- 布局存取 ----------------------------------- */

export const fetchLayoutNames = () =>
  jsonFetch<{ layouts: string[] }>('/api/layouts').then((r) => r.layouts)

export const fetchLayout = (name: string) =>
  jsonFetch<unknown>(`/api/layouts/${encodeURIComponent(name)}`)

export const saveLayout = (name: string, doc: FigureDocument | ProjectDocument) =>
  jsonFetch<{ ok: boolean }>(`/api/layouts/${encodeURIComponent(name)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(doc),
  })

/* --------------------------- 文档自动保存（磁盘） ---------------------------- */
/** 文档主体的可靠落盘（后端原子写）；localStorage 只留索引与崩溃兜底副本 */

/**
 * `base` = 本标签页最后一次成功落盘时的 updatedAt（乐观并发基线）。
 * 带上它，后端发现磁盘上已经比它更新（另一个标签页存过）就回 409
 * `stale_write` 而不是整份覆盖。不带 = 后端不校验（首次写、旧路径都照常）。
 */
export const putAutosave = (docId: string, doc: ProjectDocument, base?: number) =>
  jsonFetch<{ ok: boolean }>(
    `/api/autosave/${encodeURIComponent(docId)}${base === undefined ? '' : `?base=${base}`}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(doc),
    },
  )

/** 404（没存过）返回 null；其余错误抛出 */
export async function fetchAutosave(docId: string): Promise<unknown | null> {
  const res = await fetch(apiUrl(`/api/autosave/${encodeURIComponent(docId)}`), withProject())
  if (res.status === 404) return null
  if (!res.ok) {
    noteProjectGone(res.status, await errorBody(res))
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json()
}

export const deleteAutosave = (docId: string) =>
  jsonFetch<{ ok: boolean }>(`/api/autosave/${encodeURIComponent(docId)}`, {
    method: 'DELETE',
  })

/* --------------------------- 布局版本时间线 -------------------------------- */
/**
 * 整份布局文档的版本历史，按 documentId 存在服务器 layouts/_versions/ 下，
 * 刷新、换浏览器标签页都还在。与「写回原始文件」的单图版本历史无关：
 * 恢复布局版本只改文档内容，不碰 figures 里的任何文件。
 */

export interface LayoutVersionMeta {
  id: string
  name: string
  ts: number
  auto: boolean
  description: string
  objects: number
  page?: { w: number; h: number }
}

export const fetchVersions = (docId: string) =>
  jsonFetch<{ versions: LayoutVersionMeta[] }>(
    `/api/versions/${encodeURIComponent(docId)}`,
  ).then((r) => r.versions)

export const fetchVersionDoc = (docId: string, vid: string) =>
  jsonFetch<{ doc: FigureDocument } & LayoutVersionMeta>(
    `/api/versions/${encodeURIComponent(docId)}/${encodeURIComponent(vid)}`,
  )

export const createVersion = (
  docId: string,
  payload: { name?: string; description?: string; auto?: boolean; doc: FigureDocument },
) =>
  jsonFetch<{ version?: LayoutVersionMeta; skipped?: boolean }>(
    `/api/versions/${encodeURIComponent(docId)}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  )

export const updateVersion = (
  docId: string,
  vid: string,
  patch: { name?: string; description?: string; auto?: boolean },
) =>
  jsonFetch<{ version: LayoutVersionMeta }>(
    `/api/versions/${encodeURIComponent(docId)}/${encodeURIComponent(vid)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    },
  )

export const duplicateVersion = (docId: string, vid: string) =>
  jsonFetch<{ version: LayoutVersionMeta }>(
    `/api/versions/${encodeURIComponent(docId)}/${encodeURIComponent(vid)}/duplicate`,
    { method: 'POST' },
  )

export const deleteVersion = (docId: string, vid: string) =>
  jsonFetch<{ ok: boolean }>(
    `/api/versions/${encodeURIComponent(docId)}/${encodeURIComponent(vid)}`,
    { method: 'DELETE' },
  )

/* --------------------------- 论文样式预设 ---------------------------------- */

import type { StylePreset } from './stylePresets'

export const fetchStyles = () =>
  jsonFetch<{ styles: StylePreset[] }>('/api/styles').then((r) => r.styles)

export const saveStyle = (style: StylePreset) =>
  jsonFetch<{ style: StylePreset }>('/api/styles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(style),
  }).then((r) => r.style)

export const deleteStyle = (id: string) =>
  jsonFetch<{ ok: boolean }>(`/api/styles/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })

/* ------------------------------- 导出 ------------------------------------- */

/** 导出载荷里的一个对象；四类结构与后端统一契约一一对应 */
interface ExportBox {
  x_mm: number
  y_mm: number
  w_mm: number
  h_mm: number
}

export type ExportObject =
  | (ExportBox & {
      type: 'panel'
      id: string
      overrides?: { gid: string; prop: string; value: unknown }[]
      /** 0–1 归一化、top-origin，与 PanelObject.crop 同构，原样直传 */
      crop?: { x: number; y: number; w: number; h: number }
      /** 0/90/180/270；x/y/w/h 已是旋转后的落位，后端只负责把内容转进去 */
      rotation?: 0 | 90 | 180 | 270
      /** 0–1；<1 时后端改用位图嵌入（矢量 xobject 没有整体 alpha） */
      opacity?: number
      /** 翻转（内容空间，先翻转后旋转）；有翻转时后端按导出 DPI 位图嵌入 */
      flip_h?: boolean
      flip_v?: boolean
    })
  | (ExportBox & {
      type: 'text'
      text: string
      size_pt: number
      bold: boolean
      italic: boolean
      color: string
      align: string
      underline?: boolean
      line_height?: number
      padding_mm?: number
      bg?: string
      border_color?: string
      border_pt?: number
      rotation_deg?: number
    })
  | (ExportBox & {
      type: 'arrow'
      start: { rx: number; ry: number }
      end: { rx: number; ry: number }
      stroke_pt: number
      color: string
      head: 'none' | 'end' | 'both'
      head_start?: 'none' | 'triangle' | 'open' | 'bar'
      head_end?: 'none' | 'triangle' | 'open' | 'bar'
      dash?: 'dashed' | 'dotted'
      rotation_deg?: number
    })
  | (ExportBox & {
      type: 'shape'
      shape: string
      /** line 专用端点（比例坐标）；缺省时后端按包围盒水平中线画 */
      start?: { rx: number; ry: number }
      end?: { rx: number; ry: number }
      stroke_pt: number
      color: string
      fill: string | null
      corner_radius_mm?: number
      sides?: number
      fill_opacity?: number
      dash?: 'dashed' | 'dotted'
      rotation_deg?: number
    })

export interface ExportRequest {
  page_w_mm: number
  page_h_mm: number
  dpi: number
  formats: string[]
  stem: string
  /** 数组序 = z 序（底 → 顶），与 document.objects 一致 */
  objects: ExportObject[]
  /** 可选：导出 proof report，随成图写到 exports/<stem>_<ts>_proof.json */
  proof?: Record<string, unknown>
}

export interface ExportResponse {
  files: { name: string; url: string }[]
  export_dir?: string
  /** 面板重渲染时 worker 报的警告（元素不存在 / 属性不支持）。
   *  导出照常完成，但成图可能与画布不完全一致——必须让用户看见。 */
  warnings?: string[]
}

export const exportFigure = (req: ExportRequest) =>
  jsonFetch<ExportResponse>('/api/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })

/* --------------------------- 可复现项目包 ---------------------------------- */

export interface PackageResult {
  name: string
  url: string
  assets: number
  missing: string[]
}

export const createPackage = (
  stem: string,
  doc: FigureDocument | ProjectDocument,
  settings: Record<string, unknown>,
) =>
  jsonFetch<PackageResult>('/api/package', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stem, doc, settings }),
  })

export interface PackageOpenResult {
  /** 旧包 schema 2 / 新包 schema 3；switchDocument 统一迁移 */
  doc: FigureDocument | ProjectDocument
  manifest: {
    created_at?: string
    figures_dir?: string
    page?: unknown
    export_settings?: Record<string, unknown>
    scripts?: string[]
  }
  /** 当前图库缺失的素材 id */
  missing: string[]
  /** 存在但内容与打包时不一致（sha1 漂移） */
  drifted: string[]
}

export function openPackage(file: File): Promise<PackageOpenResult> {
  const form = new FormData()
  form.append('package', file)
  return jsonFetch<PackageOpenResult>('/api/package/open', { method: 'POST', body: form })
}


/* --------------------------- 参数化渲染引擎 -------------------------------- */

/** manifest 里一个可编辑字段；ElementInspector 完全由它驱动，前端不硬编码属性名 */
export interface EditableField {
  prop: string
  /**
   * order：可重排列表（图例条目顺序），value 为原始序号排列，options 为显示文字。
   * number_list：一串数（固定刻度位置），value 为 number[]。
   */
  type:
    | 'text'
    | 'number'
    | 'color'
    | 'bool'
    | 'enum'
    | 'pair'
    | 'rect'
    | 'order'
    | 'number_list'
  value: unknown
  min?: number
  max?: number
  step?: number
  unit?: string
  options?: string[]
  /** 归到哪个可折叠小节（排版 / 背景 / 描边）；无值 = 基本属性，平铺在前 */
  group?: string
}

/** 路径几何的一条子路径（figure 分数、y 向下） */
export interface GeometryPath {
  points: [number, number][]
  closed: boolean
}

/**
 * 元素**真正画出来的那条路径**（曲线 / 填充 / 独立形状）。
 *
 * 为什么需要它：bbox 回答的是「占了多大一块」，当不了选中轮廓也当不了命中
 * 判据——斜曲线、fill_between、多边形的包围盒里绝大部分是空白，用它画选择框
 * 会画出一个与图形对不上的矩形，用它做命中会让用户在空白处误选。
 *
 * 约定（与 engine/pathgeom.py 同源）：
 * * 坐标与 bbox 同一套：figure 分数、y 向下；
 * * 它是**渲染派生数据**：每次渲染由引擎现算，不进用户文档、不是 override、
 *   不参与写回。xlim / scale / axes position / figsize / aspect / 色条方向一变，
 *   下一版 manifest 里它自然就是新的——前端**绝不**自己推算它；
 * * bbox 一个字节没少：布局、缩放、对齐、resize 手柄仍然只认 bbox，
 *   没有 geometry 的元素（文字、图例、子图、散点）也仍然只用 bbox。
 * * 散点与「只有 marker 没有连线」的曲线**有意不给** geometry：一个 marker
 *   一条小路径撑爆 manifest，而那条穿过点位的折线图上根本不存在。
 */
export interface ElementGeometry {
  kind: 'polyline' | 'path' | 'multi_path'
  paths: GeometryPath[]
  /** 这条路径是填充的（选中时给一层很淡的底色，命中含内部） */
  fill: boolean
  /** 这条路径有描边 */
  stroke: boolean
  /** 描边宽度（pt）。命中容差要把可见墨迹的半宽算进去，宽度只有引擎知道 */
  stroke_pt?: number
  /** 裁剪框（figure 分数、y 向下）；只有矩形裁剪才给，非矩形裁剪不给 */
  clip?: [number, number, number, number]
}

export interface ManifestElement {
  gid: string
  role: string
  label: string
  /** figure 分数坐标，y 向下：[x, y, w, h] */
  bbox: [number, number, number, number]
  /** 真实路径（见 ElementGeometry）；没有就退回 bbox */
  geometry?: ElementGeometry
  editable: EditableField[]
  draggable: boolean
  /** axes：可在画布上直接拖动/缩放子图占比（写 position override） */
  resizable?: boolean
  /**
   * 自己没有几何属性、位置由别的元素决定时，指向那个元素的 gid。
   * imshow 位图就是这样贴合宿主 axes 的：拖它等于拖宿主子图。
   */
  geom_gid?: string
  /** 该 axes 其实是色条轴；属性页应改用它的色条元素 */
  is_colorbar?: boolean
  colorbar_gid?: string
  /**
   * 色条元素的**稳定语义身份**（`cbar:<宿主 axes gid>:<序号>`）与宿主 gid。
   * `axes_i.colorbar` 是按 fig.axes 的排序编出来的名字，语义身份才是「这是谁的
   * 色条」；引擎在 index 里两个都认，所以旧文档的 gid 照旧生效。
   */
  colorbar_key?: string
  host_gid?: string
  /**
   * 拖动这个 axes 时该一起走的**其他 axes**（色条轴、twinx/twiny 的孪生轴）。
   * 由引擎裁决（只有那边有 matplotlib 的共享关系与落点），前端只负责把同一个
   * 位移发给它们。子图自己的标题/轴标签不在这里——它们是 Axes 的孩子，
   * set_position 一挪天然跟着走。
   */
  follow_gids?: string[]
  anchor?: [number, number]
  drag_prop?: string
  /**
   * 图内独立箭头（脚本 add_patch 的 FancyArrowPatch）的两个端点
   * （figure 分数、y 向下）。有它 = 可整体拖动、可拖单个端点，
   * 写 endpoints_frac override（[ax, ay, bx, by]）。
   */
  arrow_endpoints?: [number, number][]
}

export interface Manifest {
  stem: string
  size_mm: [number, number]
  elements: ManifestElement[]
}

export interface EngineRenderResponse {
  rev: number
  manifest: Manifest
  warnings?: string[]
  /**
   * 阶段计时（毫秒）：worker 的 script_build_ms / patch_apply_ms /
   * canvas_draw_ms / manifest_ms + 控制面的 queue_wait_ms / total_ms。
   * 键集合随后端演进，前端只存不解释（暂无 UI）。
   */
  timings?: Record<string, number>
  /**
   * 本次渲染的预览 SVG（请求带 inline_svg 时才有）。**与 manifest 同一响应
   * 才能保证配对**：单独 GET /api/engine/svg 读的是磁盘上那一份，另一个变体
   * 或另一个标签页的渲染插进来就会拿到别人的图，而元素框还是这次的。
   */
  svg?: string
}

export class EngineError extends Error {
  traceback: string
  /**
   * 机器可读的原因，界面据此换成对应的出口而不是甩错误文字：
   *   no_worker_python         没有可用的渲染环境（源码/pip 安装模式）
   *   bundled_runtime_missing  桌面版该带的内置环境没带 → 让用户重装
   *   bundled_runtime_invalid  内置环境残缺/损坏 → 同上
   *   missing_dependency       脚本要的包当前环境里没有 → 让用户换自己的环境
   */
  code: string
  /** code === 'missing_dependency' 时缺的那个包名 */
  module: string
  constructor(message: string, traceback = '', code = '', module = '') {
    super(message)
    this.traceback = traceback
    this.code = code
    this.module = module
  }
}

/** 渲染环境缺件类错误：这些不是「脚本报错」，不该给 traceback 而该给出口 */
export const ENVIRONMENT_CODES = [
  'no_worker_python',
  'bundled_runtime_missing',
  'bundled_runtime_invalid',
  'missing_dependency',
] as const

export interface EngineRenderOptions {
  signal?: AbortSignal
  /**
   * 这一次预览 SVG 里**嵌入位图**的 dpi（不给 = worker 的默认）。
   * 只对含图像的面板有意义：连续调整期间降到 100 能省三分之一往返、
   * 四分之三传输；纯矢量图上一分钱都不值（docs/perf-baseline.md）。
   */
  previewDpi?: number
}

export async function engineRender(
  id: string,
  patches: unknown[],
  opts: EngineRenderOptions = {},
): Promise<EngineRenderResponse> {
  const res = await fetch(apiUrl('/api/engine/render'), withProject({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // inline_svg 恒发：SVG 必须与 manifest 原子配对（见 EngineRenderResponse.svg）
    body: JSON.stringify({
      id,
      patches,
      inline_svg: true,
      ...(opts.previewDpi ? { preview_dpi: opts.previewDpi } : {}),
    }),
    signal: opts.signal,
  }))
  const body = await res.json().catch(() => ({}) as Record<string, unknown>)
  if (!res.ok) {
    noteProjectGone(res.status, body)
    throw new EngineError(
      (body.error as string) || t('render.failed', { ns: 'errors', status: res.status }),
      (body.traceback as string) || '',
      (body.code as string) || '',
      (body.module as string) || '',
    )
  }
  return body as EngineRenderResponse
}

/**
 * 高清位图预览：含 imshow 的面板用 SVG 显示会糊，退出编辑态后走这个。
 *
 * **按 patches 出图**，与热会话当前是哪个变体无关——旧的 `/api/engine/png`
 * 从 live figure 直接 savefig，同文件多变体时后渲染的那个会把像素喂给别人
 * （「一个面板显示了另一个面板的图」）。返回 Blob 而不是 URL：`<img src>`
 * 只能发 GET，而这里要把整份 patches 带上去。
 */
export async function enginePreviewPng(
  id: string,
  patches: unknown[],
  bucket: number,
  signal?: AbortSignal,
): Promise<Blob> {
  const res = await fetch(apiUrl('/api/engine/preview_png'), withProject({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, patches, w: bucket }),
    signal,
  }))
  if (!res.ok) {
    const body = await errorBody(res)
    noteProjectGone(res.status, body)
    throw new EngineError(
      (body.error as string) || t('render.previewPngFailed', { ns: 'errors', status: res.status }),
      (body.traceback as string) || '',
      (body.code as string) || '',
      (body.module as string) || '',
    )
  }
  return res.blob()
}

/**
 * 写回事务的成功响应（update_source 与 history/restore 同构）。
 *
 * 写回是覆盖用户原始文件的一步，后端把它做成了 prepare → verify → commit 的
 * 事务：前置校验（素材 mtime / 脚本 sha1）、干净重放校验（全新 worker 全量重放
 * 一遍，与热态 manifest 逐元素比几何）、提交（备份 + 原子替换 + 尺寸自检）。
 * 任一环不过一律 409 且原文件零改动，所以成功路径上 `warnings` 恒为空。
 */
export interface WriteBackResponse {
  updated: string[]
  backup_dir: string
  warnings: string[]
  /** patchspec 权威哈希，与 baked 版本条目里的同源 */
  patch_hash: string
  /** 写回后各文件的新 sha1 */
  source_sha1: Record<string, string>
  /** 干净重放出的 manifest 的内容指纹 */
  manifest_hash: string
  verification: {
    /** ok = 与热态逐元素比过且一致；fresh_only = 热态不是这组 patches，无从对照 */
    replay: 'ok' | 'fresh_only'
    /** 逐项比对过的元素数 */
    elements: number
    reason?: string
  }
  /** 落盘后页面尺寸与 manifest 对不上（文件已替换，备份仍在） */
  post_check?: 'size_mismatch'
}

/** 写回被阻断时后端给的 code（全部 409，原文件一个字节都没动） */
export interface WriteBackDiff {
  gid: string
  field: string
  hot: unknown
  fresh: unknown
}

/** 用当前 overrides 全质量重出该 stem 的 PDF+PNG，原子替换 figures 里的原文件。
 *  annotations 非空 = 顺带把画布标注烙进原图（坐标已换算成该图自身的 mm）。
 *  expectedMtime = 前端手里这份素材的 mtime，与磁盘对不上后端回 409
 *  source_changed（素材被工具之外改过，按旧状态覆盖会吃掉别人的改动）。 */
export const updateSourceFiles = (
  id: string,
  patches: unknown[],
  annotations?: ExportObject[],
  expectedMtime?: number,
) =>
  jsonFetch<WriteBackResponse & { baked: boolean }>('/api/engine/update_source', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      id,
      patches,
      ...(annotations?.length ? { annotations } : {}),
      ...(expectedMtime ? { expected_mtime: expectedMtime } : {}),
    }),
  })

/* -------------------------------- AI 桥 ----------------------------------- */

export interface AiProviderCaps {
  installed: boolean
  path: string | null
  /** 实际启动命令（npm 的 .cmd 外壳会被解析成真正的 exe / node 脚本） */
  argv: string[] | null
  version: string | null
  models: string[]
  default_model: string | null
  efforts: string[]
  default_effort: string | null
  /** 当前接管这家 CLI 的第三方接口；null = 用 CLI 自己的登录态 */
  endpoint: AiEndpoint | null
  /** 未安装时：后端找过哪些目录（比干甩一句「未安装」有用得多） */
  searched?: string[]
  /** 找到了却启动不了的候选（典型：WindowsApps 里坏掉的商店版执行别名） */
  broken_path?: string
  /** 未安装时的一键安装可行性与当前进度 */
  install?: AiInstallState & { method: 'npm'; package: string | null; available: boolean }
}

/** `npm install -g` 的进度（后台线程，前端轮询） */
export interface AiInstallState {
  status: 'idle' | 'running' | 'done' | 'error'
  code?: 'npm_missing' | 'npm_failed' | 'installed_but_not_found' | 'timeout' | 'spawn_failed'
  log?: string
}

/** 第三方 API 接入。密钥永远不回传，只给「有没有」和尾四位。 */
export interface AiEndpoint {
  id: string
  label: string
  agent: 'codex' | 'claude'
  base_url: string
  models: string[]
  default_model: string | null
  wire_api: 'responses' | 'chat'
  has_key: boolean
  key_hint: string
}

export interface AiEndpointPreset {
  id: string
  label: string
  agent: 'codex' | 'claude'
  base_url: string
  models: string[]
  wire_api?: 'responses' | 'chat'
  note?: string
}

export interface AiCapabilities {
  providers: Record<'codex' | 'claude', AiProviderCaps>
  endpoints: AiEndpoint[]
  presets: AiEndpointPreset[]
  active: Record<'codex' | 'claude', string | null>
}

/** 新增/更新一个第三方接口；api_key 留空 = 保留原值 */
export const saveAiEndpoint = (rec: {
  id?: string
  label: string
  agent: 'codex' | 'claude'
  base_url: string
  api_key?: string
  models?: string[]
  default_model?: string | null
  wire_api?: 'responses' | 'chat'
}) =>
  jsonFetch<AiCapabilities>('/api/ai/endpoints', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(rec),
  })

export const deleteAiEndpoint = (id: string) =>
  jsonFetch<AiCapabilities>(`/api/ai/endpoints/${encodeURIComponent(id)}`, { method: 'DELETE' })

/** 选中某家 CLI 当前使用的接口；id 传 '' = 回到 CLI 自带登录态 */
export const setAiEndpointActive = (agent: 'codex' | 'claude', id: string) =>
  jsonFetch<AiCapabilities>('/api/ai/endpoints/active', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent, id }),
  })

export const fetchAiCapabilities = (refresh = false) =>
  jsonFetch<AiCapabilities>(`/api/ai/capabilities${refresh ? '?refresh=1' : ''}`)

/** 一键安装 CLI（后台 `npm install -g`）；进度用 fetchAiInstallStatus 轮询 */
export const startAiInstall = (agent: 'codex' | 'claude') =>
  jsonFetch<AiInstallState>('/api/ai/install', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent }),
  })

export const fetchAiInstallStatus = (agent: 'codex' | 'claude') =>
  jsonFetch<AiInstallState>(`/api/ai/install/status?agent=${agent}`)

export const patchAiSettings = (patch: { codex_path?: string; claude_path?: string }) =>
  jsonFetch<AiCapabilities & { settings: Record<string, string> }>('/api/ai/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })

export interface AiHistoryEntry {
  id: string
  provider: 'codex' | 'claude'
  model: string | null
  effort: string | null
  scope: string | null
  target: string | null
  script: string | null
  prompt: string
  diff: string
  changed: boolean
  status: string
  error: string | null
  started_ms: number
  ended_ms: number | null
  pinned: boolean
  revert_available: boolean
  transcript: { kind: string; text: string }[]
}

export const fetchAiHistory = (opts: {
  q?: string
  status?: string
  pinned?: boolean
  limit?: number
  offset?: number
}) => {
  const params = new URLSearchParams()
  if (opts.q) params.set('q', opts.q)
  if (opts.status) params.set('status', opts.status)
  if (opts.pinned) params.set('pinned', '1')
  params.set('limit', String(opts.limit ?? 20))
  params.set('offset', String(opts.offset ?? 0))
  return jsonFetch<{ total: number; sessions: AiHistoryEntry[] }>(
    `/api/ai/history?${params}`,
  )
}

export const deleteAiHistory = (sid: string) =>
  jsonFetch<{ ok: boolean }>(`/api/ai/history/${encodeURIComponent(sid)}`, {
    method: 'DELETE',
  })

export const pinAiHistory = (sid: string, pinned: boolean) =>
  jsonFetch<{ ok: boolean }>(`/api/ai/history/${encodeURIComponent(sid)}/pin`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pinned }),
  })

export interface AiRunRequest {
  agent: 'codex' | 'claude'
  id: string
  prompt: string
  gid?: string | null
  label?: string | null
  overrides?: unknown[]
  model?: string | null
  effort?: string | null
  scope?: string
  target?: string
  canvas?: string | null
}

export const aiRun = (req: AiRunRequest) =>
  jsonFetch<{ session: string; script: string }>('/api/ai/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })

export const aiRevert = (sid: string) =>
  jsonFetch<{ ok: boolean; script: string }>(`/api/ai/sessions/${sid}/revert`, { method: 'POST' })

export const aiCancel = (sid: string) =>
  jsonFetch<{ ok: boolean }>(`/api/ai/sessions/${sid}/cancel`, { method: 'POST' })

/* ------------------------------ SSE 事件 ---------------------------------- */

/** ai.delta 的内容分类：流式增量 / 正文终稿 / 思考 / 动作 */
export type AiDeltaKind = 'delta' | 'message' | 'thinking' | 'action'

/** 事件所属项目（后端一个进程同时端着多个图库）；缺省 = 与项目无关的全局事件 */
interface ProjectScoped {
  pj?: string
}

export type ServerEvent =
  | ({ kind: 'render.started'; id: string; cost?: string; cold?: boolean } & ProjectScoped)
  | ({ kind: 'render.done'; id: string; rev?: number } & ProjectScoped)
  | ({ kind: 'render.failed'; id: string; error?: string } & ProjectScoped)
  | ({ kind: 'panel.file_changed'; scripts?: string[]; stems?: string[] } & ProjectScoped)
  | ({ kind: 'registry.changed'; script: string; stems: string[] } & ProjectScoped)
  | { kind: 'engine.bootstrap'; state: string; log: string; error: string | null }
  | { kind: 'ai.delta'; session: string; text: string; kindOf?: AiDeltaKind }
  | ({
      kind: 'ai.done'
      session: string
      status: string
      changed: boolean
      diff: string
      script: string
      error?: string
    } & ProjectScoped)

const EVENT_KINDS = [
  'render.started',
  'render.done',
  'render.failed',
  'panel.file_changed',
  'registry.changed',
  'engine.bootstrap',
  'ai.delta',
  'ai.done',
] as const

/** 订阅后端事件流；端点缺失时静默关闭，不无限重连刷屏。 */
export function subscribeEvents(
  onEvent: (e: ServerEvent) => void,
  onOpen?: () => void,
): () => void {
  let source: EventSource | null = null
  let closed = false

  try {
    source = new EventSource(apiUrl('/api/events'))
  } catch {
    return () => {}
  }

  for (const kind of EVENT_KINDS) {
    source.addEventListener(kind, (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data)
        // ai.delta 的 payload 里也有个 kind（message/thinking/action），
        // 会和事件名冲突，取出来另存为 kindOf
        const { kind: payloadKind, ...rest } = data
        onEvent({ kind, ...rest, ...(payloadKind ? { kindOf: payloadKind } : {}) } as ServerEvent)
      } catch {
        /* 忽略无法解析的事件体 */
      }
    })
  }
  // EventSource 自带重连；每次连上都回调一次，调用方据此复查构建版本
  source.addEventListener('open', () => onOpen?.())
  source.addEventListener('error', () => {
    if (source?.readyState === EventSource.CLOSED && !closed) {
      closed = true
      source.close()
    }
  })

  return () => {
    closed = true
    source?.close()
  }
}

/* --------------------------- 「写回原始文件」版本历史 --------------------------- */

export interface HistoryVersion {
  n: number
  ts: string
  count: number
  patches: { gid: string; prop: string; value: unknown }[]
}

export const fetchHistory = (id: string) =>
  jsonFetch<{ versions: HistoryVersion[] }>(
    `/api/engine/history?id=${encodeURIComponent(id)}`,
  )

/** n = -1 表示脚本原始状态，永远可用作时间线起点 */
export const historyPreviewUrl = (id: string, n: number, w = 400) =>
  apiUrl(`/api/engine/history/preview?id=${encodeURIComponent(id)}&n=${n}&w=${w}`)

export const restoreHistory = (id: string, n: number, expectedMtime?: number) =>
  jsonFetch<
    WriteBackResponse & { patches: { gid: string; prop: string; value: unknown }[] }
  >('/api/engine/history/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      id,
      n,
      ...(expectedMtime ? { expected_mtime: expectedMtime } : {}),
    }),
  })

/* ------------------------------ 构建版本 ---------------------------------- */

/** 服务器上 dist/index.html 当前引用的 bundle 名（如 index-DaQsrMg2） */
export const fetchBuildVersion = () => jsonFetch<{ build: string }>('/api/version')

/**
 * 本页面自己跑的是哪个 bundle——直接从 <script src> 提取，
 * 不走构建期注入：零配置，且天然就是"真正加载的那个文件"。
 */
export function currentBuildId(): string | null {
  const el = document.querySelector<HTMLScriptElement>('script[src*="assets/index-"]')
  const m = el?.src.match(/assets\/(index-[^.]+)\.js/)
  return m ? m[1] : null
}

/* ------------------------- 组图 ↔ 子图 修改同步 ---------------------------- */

export interface SyncPatch {
  gid: string
  prop: string
  value: unknown
  /** 点位越界后被钳进画布，位置可能需要微调 */
  clamped?: boolean
}

export interface SyncResult {
  mapped: SyncPatch[]
  /** 版面几何类（axes position / size_mm），不可跨图搬运 */
  skipped: SyncPatch[]
  /** 目标图里找不到对应元素 */
  unmatched: SyncPatch[]
}

export const syncOverrides = (fromId: string, toId: string, patches: unknown[]) =>
  jsonFetch<SyncResult>('/api/engine/sync_overrides', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ from_id: fromId, to_id: toId, patches }),
  })

// ---------------------------------------------------------------------------
// 检查更新
// ---------------------------------------------------------------------------
export interface UpdateStatus {
  /** 正在跑的版本 */
  current: string
  /** 桌面模式：Python updater 停用，升级归安装包；界面只给 Releases 链接 */
  desktop?: boolean
  /** GitHub Releases 上的最新 tag（离线或从未检查过时缺席） */
  latest?: string
  update_available?: boolean
  notes?: string
  published_at?: string | null
  html_url?: string
  /** 安装方式决定能不能代劳升级：source 检出只提示 git pull；桌面模式缺席 */
  method?: 'pip' | 'pipx' | 'source'
  can_self_update?: boolean
  upgrade_command?: string
  auto_check: boolean
  /** true = 这次没联网，回的是上次的结果 */
  cached?: boolean
  checked_at_ms?: number
  repo_url: string
  releases_url: string
  error?: string
}

export interface UpdateApplyResult {
  ok: boolean
  command: string
  log: string
  /** 升级成功后进程仍是旧代码，必须重启 */
  restart_required: boolean
}

export const checkUpdate = (force = false) =>
  jsonFetch<UpdateStatus>(`/api/update/check${force ? '?force=1' : ''}`)

export const patchUpdateSettings = (patch: { auto_check: boolean }) =>
  jsonFetch<{ auto_check: boolean }>('/api/update/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })

export const applyUpdate = () =>
  jsonFetch<UpdateApplyResult>('/api/update/apply', { method: 'POST' })

// ---------------------------------------------------------------------------
// 匿名用量统计
//
// **后端刻意不回 install_id**：界面只需要知道「现在发不发」。把假名标识交给
// 前端只会让它出现在截图、localStorage 与前端日志里，而界面拿它没有任何用处。
// ---------------------------------------------------------------------------
export type TelemetryConsent = 'unset' | 'enabled' | 'disabled'

export interface TelemetrySettings {
  consent: TelemetryConsent
  /** 现在到底发不发（consent=enabled 且没被硬开关关掉） */
  enabled: boolean
  /** `TAVOTTO_NO_TELEMETRY=1`：管理员/CI 关的，界面要说清楚不是用户自己关的 */
  hard_disabled: boolean
  consent_version: number
  saved_consent_version: number
  /**
   * 同意过，但同意的是**上一版采集范围**（后端升了 CONSENT_VERSION）。
   * 与 `consent === 'unset'`（从没问过）分开：两种都要再问一次，但这一种
   * 不是新用户——重新同意不换 install_id、也不再发 telemetry_enabled。
   */
  needs_reconsent: boolean
}

export const fetchTelemetrySettings = () =>
  jsonFetch<TelemetrySettings>('/api/telemetry/settings')

export const patchTelemetryConsent = (
  consent: TelemetryConsent,
  source: 'first_run' | 'settings',
) =>
  jsonFetch<TelemetrySettings>('/api/telemetry/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ consent, source }),
  })

/**
 * 语义事件（服务端推断不出来的那几个）。属性经过与后端**同一份**白名单校验，
 * 白名单外的一律 400——前端这一侧没有「想发什么就发什么」的通路。
 */
export const postTelemetryEvent = (event: string, properties: Record<string, unknown>) =>
  jsonFetch<{ accepted: boolean }>('/api/telemetry/event', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ event, properties }),
  })

// ---------------------------------------------------------------------------
// 渲染环境（缺 matplotlib 时的自助安装）
// ---------------------------------------------------------------------------
/** 渲染解释器是从哪来的——同一条路径，来源不同排障含义完全不同 */
export type EngineSource =
  | 'env_override'    // 环境变量 TAVOTTO_WORKER_PYTHON
  | 'configured'      // 用户在设置里指定的
  | 'managed_venv'    // Tavotto 在源码模式下自建的 venv
  | 'bundled'         // Windows 桌面版随包附带的内置环境
  | 'current_process' // Tavotto 自身的解释器（pip install tavotto[worker]）
  | 'system'          // 探测到的系统 Python / Conda
  | ''

/** 内置渲染环境（Windows 桌面版随包附带）的现状 */
export interface BundledRuntime {
  present: boolean
  valid: boolean
  /** 这个安装形态**本该**带内置环境吗——false 时缺失是正常的，不该报错 */
  expected: boolean
  python: string | null
  packages: Record<string, string>
  build: Record<string, unknown>
  code: string
  error: string | null
}

export interface EngineEnvironment {
  ok: boolean
  python: string | null
  source: EngineSource
  matplotlib: string | null
  /** true = 用的是 Tavotto 自建的 venv，而非用户自己的 */
  managed: boolean
  /** true = 用的是随安装包附带的内置环境（装完即用，不联网） */
  bundled: boolean
  runtime: BundledRuntime
  state: 'idle' | 'running' | 'done' | 'failed'
  /** ok=false 时才有：能不能替用户装一个 */
  can_install?: boolean
  base_python?: string | null
  /** ok=false 时的机器可读原因（bundled_runtime_missing / …） */
  code?: string
  base_error?: string | null
  error?: string | null
  /** ?probe= 时才有：各包实测 import 到的版本，null = import 不到 */
  imports?: Record<string, string | null>
}

export interface BootstrapProgress {
  state: 'idle' | 'running' | 'done' | 'failed'
  log: string
  error: string | null
}

export const fetchEngineEnvironment = (probe?: boolean) =>
  jsonFetch<EngineEnvironment>(
    `/api/engine/environment${probe ? '?probe=1' : ''}`)

export const installEngineEnvironment = () =>
  jsonFetch<{ started?: boolean } & BootstrapProgress>(
    '/api/engine/environment/install', { method: 'POST' })

export const setEngineEnvironment = (python: string | null) =>
  jsonFetch<EngineEnvironment>('/api/engine/environment', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ python }),
  })

/* --------------------------- 脚本注册表（stem ↔ 脚本） ----------------------- */
/**
 * 「面板上没有 ⚡」几乎总是注册表的问题，以前只能手改 tavotto_registry.json。
 * 这组接口把整条链路搬到界面上：看现状 → 重扫 → 跑一遍认领 → 手工裁决。
 */

export interface RegistryEntry {
  entry: string
  cost: string
  notes: string
  stems: string[]
}

export interface RegistryCandidate {
  script: string
  entry: string
  stems: string[]
  new_stems: string[]
  unresolved: string[]
  /** 静态解不出文件名（stem 来自运行期数据）——只能靠试运行探测 */
  dynamic_names: boolean
  save_calls: number
  registered: boolean
}

export interface RegistryView {
  source: string
  scripts: Record<string, RegistryEntry>
  candidates: RegistryCandidate[]
  conflicts: Record<string, string[]>
}

export const fetchRegistry = () => jsonFetch<RegistryView>('/api/registry')

export const scanRegistry = () =>
  jsonFetch<{
    changes: { added_scripts: string[]; added_stems: Record<string, string[]> }
    conflicts: Record<string, string[]>
    scripts: Record<string, RegistryEntry>
  }>('/api/registry/scan', { method: 'POST' })

export interface ProbeResult {
  script: string
  entry: string | null
  stems: string[]
  error: string | null
  tried: string[]
  registered?: boolean
}

/** 试运行：真的跑一遍脚本，按它**实际产出**的文件名登记（冷启动可能要几分钟） */
export const probeScript = (script: string, cost?: string) =>
  jsonFetch<ProbeResult>('/api/registry/probe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ script, cost }),
  })

export const writeRegistryEntry = (payload: {
  script: string
  entry: string
  stems: string[]
  cost?: string
  notes?: string
}) =>
  jsonFetch<{ scripts: Record<string, RegistryEntry> }>('/api/registry', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
