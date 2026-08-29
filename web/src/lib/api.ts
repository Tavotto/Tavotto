import { apiUrl, apiUrlFor, withProject, withProjectFor } from '@/lib/session'
import { formatMessage, i18n, literal, msg, t, type UiMessage } from '@/i18n'
import type { FigureDocument, ProjectDocument } from '@/types/document'
import type { PreviewMetadata } from '@/lib/previewBudget'

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
/**
 * 引擎（worker）错误的描述符版本（issue #30）。
 *
 * worker 的 `error` 原文是中文（`脚本执行失败: …`），英文界面直接显示等于
 * 泄漏中文系统文案。code 在 `errors:backend.*` 里有文案时按当前语言渲染，
 * `{{error}}` 用 **traceback 的最后一行**（`RuntimeError: …`——那是诊断
 * 原文，按 i18n 纪律不翻，也不依赖后端消息的中文前缀格式）；没有文案的
 * code 照旧原文透出。两条控制面（Python 池 / workerd）走的都是同一形状。
 */
export function engineErrorMsg(err: unknown): UiMessage {
  if (err instanceof EngineError && err.code && i18n.exists(`backend.${err.code}`, { ns: 'errors' })) {
    const detail = err.traceback.trim().split('\n').at(-1)?.trim() ?? ''
    // 文案要 {{error}} 却拿不到 traceback（老 server / 精简错误体）时退回
    // 原文透出——「The script failed: 」后面空着比中文原文更没用；
    // 不吃占位符的文案（worker_timeout 这类）照常本地化
    const template = String(
      i18n.getResource(i18n.language, 'errors', `backend.${err.code}`) ?? '',
    )
    if (detail || !template.includes('{{error}}')) {
      return msg(`backend.${err.code}`, { error: detail }, 'errors')
    }
  }
  return literal(err instanceof Error ? err.message : String(err))
}

export function backendErrorMsg(e: unknown): UiMessage {
  if (e instanceof ApiError) {
    const code = typeof e.body?.code === 'string' ? e.body.code : ''
    return backendCodeMsg(code, (e.body?.params ?? {}) as Record<string, unknown>, e.message)
  }
  // 后端没给 code（或本地还没有这条文案）：原文照抄，不翻
  return literal(e instanceof Error ? e.message : String(e))
}

/**
 * 「code + params → 当前语言的一句话」的内核，给不经 ApiError 走的结构化
 * 错误用（试运行探测的 result.error 是 200 响应里的**结果**，不是 HTTP
 * 错误）。规则与 `backendErrorMsg` 完全一致：先查 code，查不到用后端原文。
 */
export function backendCodeMsg(
  code: string | undefined,
  params: Record<string, unknown> | undefined,
  fallback: string,
): UiMessage {
  // 用 exists 而不是 defaultValue 判「有没有这条」：i18n 那边的
  // parseMissingKeyHandler 会把缺失的 key 原样吐回来（界面上看得见是哪条），
  // 那样 defaultValue 永远轮不到，缺文案时用户看到的就是 `backend.xxx`。
  if (code && i18n.exists(`backend.${code}`, { ns: 'errors' })) {
    return msg(`backend.${code}`, params ?? {}, 'errors')
  }
  return literal(fallback)
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

/**
 * `pj` 显式给出时用它，不用全局当前项目：见 `session.apiUrlFor` 的说明——
 * 排队稍后才发出的写入，属于**排队那一刻**的项目。`undefined` = 用全局的。
 */
async function jsonFetch<T>(url: string, init?: RequestInit, pj?: string | null): Promise<T> {
  const res =
    pj === undefined
      ? await fetch(apiUrl(url), withProject(init))
      : await fetch(apiUrlFor(url, pj), withProjectFor(init, pj))
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

/** materialized cache 里的预览 SVG（runtime 面板重开时的首帧占位）。
 * `nonce` 是重跑后的换代计数（runtimeAssetStore.previewNonce）：同一 URL
 * 的 <img> 不会自己重取，重新运行刷新了 cache 之后靠它换 src。 */
export const runtimePreviewUrl = (id: string, nonce?: number) =>
  apiUrl(`/api/runtime/preview?id=${encodeURIComponent(id)}${nonce ? `&t=${nonce}` : ''}`)

/**
 * 位图走原文件、矢量走分档渲染、runtime 走 materialized cache 预览，
 * 未知形态**不给地址**（fail closed：绝不把不认识的 id 猜成文件路径）。
 * runtime 分支的 `mtime` 参数承载的是预览换代计数（重跑后换 src 用），
 * 不是文件 mtime——runtime 素材没有文件。
 */
export const panelSrc = (
  id: string,
  kind: string,
  bucket: number,
  mtime?: number,
): string | null => {
  if (kind === 'raster') return fileUrl(id, mtime)
  if (kind === 'pdf') return renderUrl(id, bucket, mtime)
  if (kind === 'runtime') return runtimePreviewUrl(id, mtime)
  return null
}

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
 * 磁盘上那一份文档的结构化摘要（后端 `document_summary`）。
 * 冲突面板拿它回答「磁盘上现在是什么」——**不做文本 diff**，一份布局 JSON
 * 里全是坐标，逐行 diff 对用户没有意义。
 *
 * `updatedAt` 与 `mtime` 是两个维度，都在：前者是文档自报的编辑时刻（外部
 * 工具改完可能一动不动），后者是文件系统记的写入时刻。
 */
export interface DiskDocumentSummary {
  schema: number | null
  canvases: number
  objects: number
  updatedAt: number | null
  mtime: number
  name: string | null
  revision: string | null
}

/** 修订号基线的哨兵：**我认为磁盘上还没有这份文件**。见下面 putAutosave。 */
export const REVISION_ABSENT = 'absent'

/**
 * `base` = 本标签页最后一次成功落盘时的 updatedAt（跨标签页乐观并发基线）。
 * `baseRevision` = 本标签页最后一次**读到或写成功**的那一份的内容 hash
 * （外部修改基线）。两者的区别不是精度而是**能看见什么**：编辑器外的工具
 * 改完 `tavottofile/*.json` 往往一个字节的 updatedAt 都不动，那种改动只有
 * 内容 hash 看得见。后端带了 `base_revision` 就以它为准。
 *
 * `baseRevision` 传 `REVISION_ABSENT` = 「我读过，磁盘上没有这份文件」。
 * 少了这个哨兵，判据就只钉住了一条边：两个标签页同时新建同一份文档时
 * 双方都没有 hash 可带，后写的那个会把先写的那份整份盖掉。
 */
export const putAutosave = (
  docId: string,
  doc: ProjectDocument,
  base?: number,
  baseRevision?: string,
  pj?: string | null,
) => {
  const q = new URLSearchParams()
  if (base !== undefined) q.set('base', String(base))
  if (baseRevision !== undefined) q.set('base_revision', baseRevision)
  const qs = q.toString()
  return jsonFetch<{ ok: boolean; saved_at: number; revision: string | null }>(
    `/api/autosave/${encodeURIComponent(docId)}${qs ? `?${qs}` : ''}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(doc),
    },
    pj,
  )
}

/** 读到的一份自动保存：`revision` 来自响应头，是后续写入的基线 */
export interface FetchedAutosave {
  doc: unknown
  revision: string | null
}

/** 404（没存过）返回 null；其余错误抛出。`pj` 见 putAutosave。 */
export async function fetchAutosave(
  docId: string,
  pj?: string | null,
): Promise<FetchedAutosave | null> {
  const path = `/api/autosave/${encodeURIComponent(docId)}`
  const res =
    pj === undefined
      ? await fetch(apiUrl(path), withProject())
      : await fetch(apiUrlFor(path, pj), withProjectFor(undefined, pj))
  if (res.status === 404) return null
  if (!res.ok) {
    noteProjectGone(res.status, await errorBody(res))
    throw new Error(`HTTP ${res.status}`)
  }
  return { doc: await res.json(), revision: res.headers.get('X-Tavotto-Revision') }
}

/** 磁盘那一份的摘要；没有这份文件返回 null */
export const fetchAutosaveSummary = (docId: string, pj?: string | null) =>
  jsonFetch<DiskDocumentSummary>(
    `/api/autosave/${encodeURIComponent(docId)}/summary`,
    undefined,
    pj,
  ).catch(() => null)

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
  /**
   * 这个检查点拍的是**哪一张画布**（R-03）。
   * 检查点存的是激活画布的内容，却按 documentId（整个项目）归档；不记下画布
   * 身份，恢复时就只能往「当前激活的那张」上盖。
   * **旧检查点没有这两个字段，而缺席就是缺席**——不要在读到的地方补一个
   * 默认值，那等于替它编一个身份出来。
   */
  canvasId?: string
  canvasName?: string
}

/**
 * 诊断包（ADR 0016）。**POST 而不是点一个链接**：前端状态与交互轨迹只活在
 * 浏览器内存里，得随请求现交上去；老的 GET 端点保留，出的包没有那两个文件。
 *
 * 回的是 zip 字节流，不是 JSON——所以不走 jsonFetch。
 */
export async function postDiagnosticsBundle(payload: unknown): Promise<Blob> {
  const res = await fetch(
    apiUrl('/api/diagnostics/bundle'),
    withProject({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  )
  if (!res.ok) throw new ApiError(`diagnostics_bundle_${res.status}`, res.status, {})
  return res.blob()
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
  payload: {
    name?: string
    description?: string
    auto?: boolean
    doc: FigureDocument
    canvasId?: string
    canvasName?: string
  },
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

/**
 * guard 挡掉一条能力时给出的**稳定 reason**。
 *
 * guard 的完整形态是 `detect → guard/hide → unsupported reason → issue → 修`：
 * 少了 reason 这一环，用户看到的就是「开关就这么消失了，没有任何解释」。
 *
 * `reason` 是 code 不是文案——界面按 code 翻译（`inspector:unsupported.*`），
 * 绝不透传英文。`detail` 里的字段进插值（如 `multi_host_colorbar` 的 hosts）。
 */
export interface UnsupportedProp {
  prop: string
  reason: string
  detail?: Record<string, unknown>
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
   * 引擎明确不宣称、且**说得出为什么**的属性。渲染出口在
   * `inspector/UnsupportedProps.tsx`；没有它这个字段就只到 manifest、没到眼睛。
   */
  unsupported_props?: UnsupportedProp[]
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
  /**
   * 这一版预览该用哪种表示法（ADR 0022）。**加字段协议**：老后端不返回它，
   * 前端按 `vector` 解读，行为与从前逐字节一致。
   *
   * `mode === 'raster'` 时 `svg` **不出现**——那不是渲染失败，是引擎按硬闸
   * 决定不把那份 SVG 读进内存（issue #181：读一份 126 MB 的预览 SVG 就能
   * 让服务进程峰值 RSS 到 1.2 GB，而它一个字节都还没到浏览器）。
   */
  preview?: PreviewMetadata
  /**
   * 只在**这一次响应真的发生了项目环境自动接手**时出现（ADR 0018）：
   * 内置环境缺包 → Tavotto 自己找到并换用了项目的 `.venv`。界面据此给一条
   * 轻量 toast（「已自动使用这个项目的 Python 环境」），不弹阻断式对话框——
   * 用户点的是「渲染」，不是「读一段技术说明」。
   */
  environment_switched?: {
    source: EngineSource
    /** 项目相对路径 */
    python: string
    /** 因为缺哪个包才切的 */
    module: string
  }
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
  /**
   * `missing_dependency` 且**项目环境自动接手也没成**时的结构化原因
   * （ADR 0018）。有它才能把「这个项目附近没有虚拟环境」和「找到了但它
   * 也没有这个包」分开引导。
   */
  projectEnv?: ProjectEnvFailure
  /**
   * `missing_dependency` 时「这个包能怎么修」（ADR 0019）。解析不出可信包名
   * 时 `requirement` 为 null，界面据此**不给**一键安装。
   */
  dependencyRepair?: DependencyRepairOffer
  constructor(
    message: string,
    traceback = '',
    code = '',
    module = '',
    projectEnv?: ProjectEnvFailure,
    dependencyRepair?: DependencyRepairOffer,
  ) {
    super(message)
    this.traceback = traceback
    this.code = code
    this.module = module
    this.projectEnv = projectEnv
    this.dependencyRepair = dependencyRepair
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
      body.project_env as ProjectEnvFailure | undefined,
      body.dependency_repair as DependencyRepairOffer | undefined,
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
      body.project_env as ProjectEnvFailure | undefined,
      body.dependency_repair as DependencyRepairOffer | undefined,
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
    /**
     * 像素门（ADR 0009）：ok = 热态与重放的探针图比过且一致；
     * hot_rebuilt = 热会话在探针中途被重开，本次像素比对作废（如实报告）。
     * replay 为 fresh_only 时该键不出现。
     */
    pixels?: 'ok' | 'hot_rebuilt'
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
  /** field === 'pixels'（像素门，ADR 0009）时的指标 / 越界项 / 阈值 */
  metrics?: Record<string, number | string | boolean>
  exceeded?: Record<string, number>
  tolerance?: Record<string, number>
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

/**
 * 编码 Agent 的 id。**故意是 string 而不是联合类型**：注册表在后端
 * （`engine/ai_agents.py`），前端加一个 `'codex' | 'claude'` 就等于第二份
 * 权威——后端加了第三个 Agent，前端的类型系统会把它判成非法值。
 * 所有 id 一律拿后端返回的 `agents[]` 校验，见 `agentById` / `usableAgents`。
 */
export type AiAgentId = string

/**
 * Agent 的界面状态。语义（以及为什么不是一个 `installed: boolean`）见
 * `docs/adr/0015-coding-agent-registry-and-settings.md`。
 * `detecting` 是**前端本地**的加载态，后端不会返回它。
 */
export type AiAgentState =
  | 'ready'
  | 'installed'
  | 'needs_auth'
  | 'broken'
  | 'not_installed'
  | 'disabled'

export type AiAgentUiState = AiAgentState | 'detecting'

/** 详情页折叠区里的诊断材料；一级列表不显示它们 */
export interface AiAgentDiagnostics {
  /** 后端找过哪些目录（比干甩一句「未安装」有用得多） */
  searched: string[]
  /** 找到了却启动不了的候选（典型：WindowsApps 里坏掉的商店版执行别名） */
  broken_path: string | null
  /** 无副作用就绪检查的结论 */
  readiness: 'ready' | 'needs_auth' | 'unknown'
  /** 稳定诊断串（不含任何账号信息） */
  readiness_detail: string | null
}

export interface AiAgentCaps {
  id: AiAgentId
  display_name: string
  /** 稳定图标键（不是路径、不是远程地址）；前端 AgentIcon 按它画本地 SVG */
  icon_key: string

  state: AiAgentState
  installed: boolean
  /** 用户是否允许 Tavotto 使用它（没表过态时跟着「装没装」走） */
  enabled: boolean
  /** 现在能不能真的派活给它 = enabled && installed && 状态不是坏/未装/需登录 */
  usable: boolean

  version: string | null
  /** 实际生效的可执行文件（自动探测或自定义路径解析后的那一个） */
  executable_path: string | null
  /** 用户设过的自定义可执行文件；null = 自动检测 */
  path_override: string | null
  /** 从哪儿找到的：path / homebrew / npm_global / chatgpt_bundle … */
  detection_source: string | null

  models: string[]
  default_model: string | null
  efforts: string[]
  default_effort: string | null

  /** 当前接管这家 CLI 的第三方接口；null = 用 CLI 自己的登录态 */
  endpoint: AiEndpoint | null
  active_endpoint_id: string | null

  features: {
    third_party_endpoints: boolean
    model_selection: boolean
    effort_selection: boolean
    /** 第三方接口要不要选 wire api（OpenAI 兼容那一族才有 responses/chat） */
    wire_api_selection: boolean
    readiness_probe: boolean
  }

  diagnostics: AiAgentDiagnostics

  /** 有一键安装规格时才有；没有就不显示安装入口 */
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
  agent: AiAgentId
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
  agent: AiAgentId
  base_url: string
  models: string[]
  wire_api?: 'responses' | 'chat'
  note?: string
}

export interface AiCapabilities {
  /** 顺序即界面顺序，由后端注册表决定 */
  agents: AiAgentCaps[]
  endpoints: AiEndpoint[]
  presets: AiEndpointPreset[]
  /** 上次探测完成的时刻（毫秒；与 AiHistoryEntry 的 *_ms 同一约定） */
  checked_at_ms: number
}

/* --- capabilities 的读取助手（前端只认后端返回的 Agent，不自己列名单）--- */

export const agentById = (
  caps: AiCapabilities | null,
  id: AiAgentId | null | undefined,
): AiAgentCaps | null => (id ? (caps?.agents.find((a) => a.id === id) ?? null) : null)

/** 可以派活的 Agent，顺序沿用后端 */
export const usableAgents = (caps: AiCapabilities | null): AiAgentCaps[] =>
  caps?.agents.filter((a) => a.usable) ?? []

/**
 * 显示名。回退顺序：当前 capabilities → 调用方给的快照 → id。
 *
 * **空串按「没有」处理**：`?? ` 会把 `''` 当成有效值，而这里防的正是
 * 「标签变成一片空白」——旧会话没存过 agentLabel 时它就是空串。
 */
export const agentDisplayName = (
  caps: AiCapabilities | null,
  id: AiAgentId | null | undefined,
  fallback?: string | null,
): string => agentById(caps, id)?.display_name || fallback || id || ''

/**
 * 这一刻实际该用哪个 Agent：首选仍可用就用它，否则落到第一个可用的。
 *
 * **不改用户的首选值**——首选那个只是暂时不可用（没登录 / 关掉了 / CLI 正在
 * 升级），恢复以后它还该是默认项。返回 null = 一个可用的都没有。
 */
export const effectiveAgent = (
  preferred: AiAgentId | null | undefined,
  caps: AiCapabilities | null,
): AiAgentId | null => {
  const list = usableAgents(caps)
  if (preferred && list.some((a) => a.id === preferred)) return preferred
  return list[0]?.id ?? null
}

/** 新增/更新一个第三方接口；api_key 留空 = 保留原值 */
export const saveAiEndpoint = (rec: {
  id?: string
  label: string
  agent: AiAgentId
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

/** 选中某个 Agent 当前使用的接口；id 传 '' = 回到 CLI 自带登录态 */
export const setAiEndpointActive = (agent: AiAgentId, id: string) =>
  jsonFetch<AiCapabilities>('/api/ai/endpoints/active', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent, id }),
  })

export const fetchAiCapabilities = (refresh = false) =>
  jsonFetch<AiCapabilities>(`/api/ai/capabilities${refresh ? '?refresh=1' : ''}`)

/**
 * 通用 Agent 设置。两个字段都可选：
 *   * `enabled` —— Tavotto 用不用它（不卸载 CLI、不动 CLI 自己的配置）；
 *   * `path_override` —— 自定义可执行文件，`''` 表示恢复自动检测。
 * 后端验证不过时抛错，**原来的设置不会被覆盖**。
 */
export const patchAiAgent = (
  agent: AiAgentId,
  patch: { enabled?: boolean; path_override?: string },
) =>
  jsonFetch<AiCapabilities>(`/api/ai/agents/${encodeURIComponent(agent)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })

/** 一键安装 CLI（后台 `npm install -g`）；包名由后端注册表决定，这里不传 */
export const startAiInstall = (agent: AiAgentId) =>
  jsonFetch<AiInstallState>(`/api/ai/agents/${encodeURIComponent(agent)}/install`, {
    method: 'POST',
  })

export const fetchAiInstallStatus = (agent: AiAgentId) =>
  jsonFetch<AiInstallState>(`/api/ai/agents/${encodeURIComponent(agent)}/install`)

export interface AiHistoryEntry {
  id: string
  provider: AiAgentId
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
  agent: AiAgentId
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

/* ------------------------- 项目刷新（统一入口） ---------------------------- */

/**
 * 刷新的来由。**闭集**，与后端 `engine/project_refresh.REASONS` 同源——它进
 * 日志、进事件、以后还会进遥测维度，后端对表外的值一律归成 `manual`。
 */
export type RefreshReason =
  | 'manual'
  | 'watcher'
  | 'registry'
  | 'probe'
  | 'codex'
  | 'ai'
  | 'open'
  | 'external'

export interface ProjectRefreshResult {
  reason: RefreshReason
  registry: {
    added_scripts: string[]
    removed_scripts: string[]
    changed_scripts: string[]
    /** 脚本 → 变了哪几个字段（entry / cost / notes / stems） */
    script_changes: Record<string, string[]>
    added_stems: string[]
    removed_stems: string[]
    moved_stems: { stem: string; from: string; to: string }[]
    /** `null` = 这一轮没跑静态扫描，**不是**"没有冲突" */
    conflicts: Record<string, string[]> | null
    conflicts_changed: boolean
  }
  assets: {
    added: string[]
    removed: string[]
    changed: string[]
    /** true = 这一轮在**建基线**（项目刚打开），不是"什么都没变" */
    baseline: boolean
  }
  scripts: Record<string, RegistryEntry>
  changed_paths: string[]
  /** 本次实际发出的事件名；无差异时是空数组 */
  published: string[]
}

/** 显式刷新当前项目的派生事实。**绝不执行用户脚本**（要跑脚本走 probe）。 */
export const refreshProject = (reason: RefreshReason = 'manual') =>
  jsonFetch<ProjectRefreshResult>('/api/project/refresh', {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })

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
  /**
   * 注册表变了。**一次刷新一条事件**（后端统一刷新服务批量发布，不为十几个
   * 脚本发十几条）：`scripts` / `stems` 是本次全部受影响的；`script` 只在
   * **恰好一个脚本变**时才有——那正是 probe 与手工登记这两条老路径的形状，
   * 保留它是为了老客户端。`conflicts` 缺席 = 这一轮没跑静态扫描，
   * **不是**"没有冲突"。
   */
  | ({
      kind: 'registry.changed'
      reason?: RefreshReason
      scripts?: string[]
      stems?: string[]
      added_scripts?: string[]
      removed_scripts?: string[]
      changed_scripts?: string[]
      conflicts?: Record<string, string[]>
      script?: string
    } & ProjectScoped)
  /** 素材（PDF/PNG/JPG）变了：`ids` = 三类的并集，够用时不必再看细分 */
  | ({
      kind: 'assets.changed'
      reason?: RefreshReason
      ids: string[]
      added: string[]
      removed: string[]
      changed: string[]
    } & ProjectScoped)
  /**
   * 项目级的后台失败（今天只有一个来源：文件 watcher 触发的刷新没成功）。
   *
   * **它是可恢复的**：内存里的注册表原封不动，watcher 线程继续跑，用户把
   * 注册表改回合法 JSON 之后下一轮自动重试。`code` 走 `errors:*` 那张码表
   * （两种语言都有文案），`params` 是它的插值。
   */
  | ({
      kind: 'project.error'
      reason?: RefreshReason
      code: string
      params?: Record<string, string>
    } & ProjectScoped)
  | ({ kind: 'probe.started'; script: string } & ProjectScoped)
  | ({
      kind: 'native.session'
      /** 事件发生那一刻会话的完整状态（后端不发增量，发的是快照） */
      session: NativeSessionInfo
      /** 这一步本身：`{seq, state, at, …}`（时间线用） */
      event: { seq: number; state: NativeSessionState; at: number } & Record<string, unknown>
    } & ProjectScoped)
  | { kind: 'engine.bootstrap'; state: string; log: string; error: string | null }
  | ({ kind: 'engine.dependency' } & DependencyProgress)
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
  'assets.changed',
  'project.error',
  'probe.started',
  'native.session',
  'engine.bootstrap',
  'engine.dependency',
  'ai.delta',
  'ai.done',
] as const

/**
 * 事件 → 「这条事件牵涉到哪些东西」的三个纯函数。
 *
 * 有它们的理由是**可选字段**：`registry.changed` 同时带批量的 `scripts` 与
 * 单脚本兼容字段 `script`，`assets.changed` 带一个并集 `ids` 外加三个细分。
 * 不收口的话每个事件处理器都要自己写一遍「先看批量、没有再看单条、都当数组
 * 处理」——写三遍就会有一遍漏掉兼容字段，而那条路径正是 probe 与手工登记
 * （最常见的两条）走的形状。
 *
 * 三个函数都**容忍畸形载荷**：SSE 的 payload 是 `JSON.parse` 出来的，类型
 * 声明是我们对后端的期望，不是运行时保证。非数组、数组里混着非字符串，一律
 * 当作「这一维没有信息」而不是抛异常——一条读不懂的事件不该拖垮整条流。
 */
const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []

const union = (...lists: unknown[]): string[] => {
  const out = new Set<string>()
  for (const list of lists) for (const v of strings(list)) out.add(v)
  return [...out]
}

/** 这条事件牵涉到的**脚本键**（`registry.changed` 的单条兼容字段一并收进来） */
export function affectedScriptsOf(event: ServerEvent): string[] {
  if (event.kind === 'registry.changed') {
    return union(event.scripts, event.script ? [event.script] : [])
  }
  if (event.kind === 'panel.file_changed') return union(event.scripts)
  return []
}

/** 这条事件牵涉到的**图名（stem）** */
export function affectedStemsOf(event: ServerEvent): string[] {
  if (event.kind === 'registry.changed' || event.kind === 'panel.file_changed') {
    return union(event.stems)
  }
  return []
}

/** 这条事件牵涉到的**素材 id**（新增 / 删除 / 内容变化的并集） */
export function affectedAssetIdsOf(event: ServerEvent): string[] {
  if (event.kind !== 'assets.changed') return []
  return union(event.ids, event.added, event.removed, event.changed)
}

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
  /** 稳定 code + params（issue #30）：前端据此按界面语言渲染，error 是回退 */
  code?: string
  params?: Record<string, unknown>
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
  | 'project_venv'    // 项目自带的 .venv（内置缺依赖时自动接手，ADR 0018）
  | 'managed_project_env' // Tavotto 替这个项目建的隔离环境（ADR 0019）
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

/**
 * 当前项目的渲染环境（ADR 0018）。全局环境之外**每个项目还有自己的一份**：
 * 项目自带 `.venv` 时 Tavotto 会自动换过去，用户也可以只为这个项目指定。
 */
export interface ProjectEnvironment {
  open: boolean
  /** 稳定枚举，与全局那份同一套（`project_venv` / `bundled` / …） */
  source?: EngineSource
  source_label?: string
  /** 项目内的解释器显示成项目相对路径（`.venv/bin/python`） */
  python?: string
  /** true = 自动接手的结果，而不是用户挑的 */
  automatic?: boolean
  /** 自动接手的触发原因（目前只有 `missing_dependency`） */
  trigger?: string
  /** 因为缺哪个包才切的 */
  module?: string
  /** 在这个项目里发现到的候选虚拟环境（项目相对路径），可能是空表 */
  can_use_project_venv?: string[]
  /** Tavotto 替这个项目建过的隔离环境（ADR 0019）；没建过 exists=false */
  managed?: ManagedEnvironment
}

/**
 * Tavotto 管理的项目环境。它相对「改用户 .venv」的唯一优势就是**可删可重建**，
 * 所以界面要能显示「装了什么」并给出重建入口。
 */
export interface ManagedEnvironment {
  exists: boolean
  state: string
  python_version: string
  created_at: number
  last_used?: number
  installed: { distribution: string; resolved_version: string }[]
}

/**
 * 项目环境**没能**自动接手时的结构化原因。四种情况用户要做的事完全不同，
 * 混成一句「缺少依赖包」等于把可执行的出路藏起来。
 */
export interface ProjectEnvFailure {
  /** project_env_not_found / project_env_module_missing /
   *  project_env_no_matplotlib / project_env_unsupported_python /
   *  project_env_unusable / project_env_already_attempted */
  code: string
  module: string
  venv: string
  candidates: string[]
  python_version: string
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
  /** 当前项目那一份（没打开项目时是 `{ open: false }`） */
  project?: ProjectEnvironment
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

/**
 * 只为**当前项目**指定渲染解释器（ADR 0018）。`null` = 清除，回到默认链条。
 *
 * 与 `setEngineEnvironment` 的区别就是作用域：那个写全局设置，会连带改变
 * 别的项目；这个只影响当前项目，且存的是项目相对路径（项目挪走仍然有效）。
 */
export const setProjectEnvironment = (python: string | null) =>
  jsonFetch<{ ok: boolean; project: ProjectEnvironment }>('/api/engine/environment', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scope: 'project', python }),
  })

// ---------------------------------------------------------------------------
// 受控依赖修复（ADR 0019）
//
// 两步，刻意分开：先 plan（说清楚装什么、装到哪、会不会改你的环境），用户点
// 确认之后才 install。**install 的请求体里只有 plan_id**——装什么不由那次请求
// 说了算，否则一个构造出来的请求就能把「装 lmfit 到项目环境」换成别的事。
// ---------------------------------------------------------------------------
/** 一个可安装的需求（后端解析出来的，前端不自己拼包名） */
export interface DependencyRequirementInfo {
  import_name: string
  distribution: string
  specifier: string
  requirement: string
  /** project_declared / curated / user_specified —— 没有「猜的」这一档 */
  resolution_source: 'project_declared' | 'curated' | 'user_specified' | ''
  confidence: string
  installable: boolean
}

/** 一个可选的安装目标 */
export interface DependencyTarget {
  kind: 'project_venv' | 'tavotto_managed'
  /** 项目相对路径（项目 venv 才有） */
  venv: string
  python: string
  /** true = 会修改用户自己的环境，界面必须说清楚 */
  modifies_user_environment: boolean
  creates_environment: boolean
  /** null = 还不知道（后端正在探基础解释器），界面照常列出来 */
  available: boolean | null
  reason: string
}

/**
 * 「这个缺的包能怎么修」。`requirement` 为 null = 解析不出可信包名，
 * 那时**不给一键安装**，只给「指定安装包…」与「选择其他 Python」。
 */
export interface DependencyRepairOffer {
  import_name: string
  /** 哪个脚本缺的（项目相对路径）——创建计划时要把它交回去 */
  script: string
  requirement: DependencyRequirementInfo | null
  targets: DependencyTarget[]
  rounds_remaining: number
  managed?: ManagedEnvironment
  /** dependency_unresolved / dependency_repair_rounds_exhausted */
  code?: string
}

/** 后端发出来的安装计划。`plan_id` 是这次授权的凭据，不可猜、有有效期。 */
export interface DependencyRepairPlan extends DependencyRequirementInfo {
  plan_id: string
  target_kind: 'project_venv' | 'tavotto_managed'
  python: string
  creates_environment: boolean
  modifies_user_environment: boolean
  network_required: boolean
  expires_at: number
}

/** 安装进度。前端**按 state 换文案，不解析日志**。 */
export interface DependencyProgress {
  plan_id: string
  state: 'idle' | 'preparing' | 'creating_env' | 'installing' | 'verifying' | 'done' | 'failed' | 'cancelled'
  log: string
  error: string | null
  code: string
  import_name?: string
  distribution?: string
  target_kind?: string
  script?: string
  result?: { python?: string; version?: string; distribution?: string } | null
}

export const createDependencyPlan = (body: {
  module: string
  script: string
  target: 'project_venv' | 'tavotto_managed'
  distribution?: string
}) =>
  jsonFetch<{ plan: DependencyRepairPlan }>('/api/engine/dependency/plan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

export const installDependencyPlan = (planId: string) =>
  jsonFetch<{ started: boolean } & DependencyProgress>('/api/engine/dependency/install', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan_id: planId }),
  })

export const cancelDependencyPlan = (planId: string) =>
  jsonFetch<{ cancelling: boolean }>('/api/engine/dependency/cancel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan_id: planId }),
  })

/** 删掉并重建当前项目的 Tavotto 隔离环境（用户自己的 .venv 没有这个操作） */
export const rebuildManagedEnvironment = () =>
  jsonFetch<{ started: boolean; requirements: string[] }>(
    '/api/engine/environment/managed/rebuild',
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
  )

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

/** 脚本清单条目的稳定 reason code（`engine/probe.py` 的 REASON_* 表） */
export type ScriptReason =
  | 'registered'
  | 'static_candidate'
  | 'dynamic_stems'
  | 'no_static_output'
  | 'infrastructure'
  | 'unparseable'

/**
 * 项目内一个 .py 的清单条目：普通脚本不因静态分析解不出产物就从产品里消失。
 * `reason` 解释它此刻的状态；`can_probe` 为 true 的都可以「试运行」。
 */
export interface ScriptInventoryEntry {
  script: string
  registered: boolean
  static_stems: string[]
  entry_candidates: string[]
  reason: ScriptReason
  can_probe: boolean
}

export interface RegistryView {
  source: string
  scripts: Record<string, RegistryEntry>
  candidates: RegistryCandidate[]
  conflicts: Record<string, string[]>
  /** 项目内全部合理 .py（含 show-only 与基础设施脚本；被 prune 的目录不列） */
  all_scripts: ScriptInventoryEntry[]
}

export const fetchRegistry = () => jsonFetch<RegistryView>('/api/registry')

export const scanRegistry = () =>
  jsonFetch<{
    changes: { added_scripts: string[]; added_stems: Record<string, string[]> }
    conflicts: Record<string, string[]>
    scripts: Record<string, RegistryEntry>
  }>('/api/registry/scan', { method: 'POST' })

/** 一张捕获 Figure 的结构化描述（`engine/figcapture.py` 的唯一实现，原样透传） */
export interface CapturedFigureDescriptor {
  asset_id: string
  script: string
  entry: string
  stem: string
  capture_source: 'savefig' | 'pyplot'
  execution_profile: 'safe' | 'native'
  original_artifact: string | null
  size_mm: [number, number]
  source_fingerprint: string
  can_writeback_artifact: boolean
  can_writeback_source: boolean
}

/** 试运行失败的结构化错误：稳定 code + params；traceback 只是诊断详情 */
export interface ProbeError {
  code: string
  /** 后端中文原文（回退）；界面先按 code 查 `errors:backend.*` */
  message: string
  params?: Record<string, unknown>
  traceback?: string
}

export interface ProbeResult {
  script: string
  entry: string | null
  stems: string[]
  descriptors: CapturedFigureDescriptor[]
  error: ProbeError | null
  tried: string[]
  registered?: boolean
  timings?: Record<string, number>
  /** pyplot 兜底超过上限被丢掉的张数（0 = 没丢） */
  dropped_figures?: number
  /** multiple_stem_conflict 时：stem → 现登记的归属脚本 */
  stem_conflicts?: Record<string, string>
}

/** 试运行：真的跑一遍脚本，按它**实际产出**的文件名登记（冷启动可能要几分钟） */
export const probeScript = (script: string, cost?: string) =>
  jsonFetch<ProbeResult>('/api/registry/probe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ script, cost }),
  })

/**
 * 取消一个在跑的试运行。后端置取消标志并**硬杀**该脚本的 worker 会话——
 * 阻塞中的 probe 请求随即以 `execution_cancelled` 返回。幂等：没有在跑的
 * 返回 `{cancelling: false}`（取消与跑完天然赛跑，输了不是错误）。
 */
export const cancelProbe = (script: string) =>
  jsonFetch<{ cancelling: boolean }>('/api/registry/probe/cancel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ script }),
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

/* --------------------- Runtime Figure 素材（ADR 0013） --------------------- */

/**
 * stale 状态（稳定枚举，与后端 runtimeasset.STALE_* 同字面量）。
 * `rerun_failed` 的 producer 在前端：runtime 面板的一次重跑渲染失败时
 * 由 runtimeAssetStore 置上，后端不产它。
 */
export type RuntimeStaleStatus =
  | 'fresh'
  | 'possibly_stale'
  | 'missing_source'
  | 'missing_environment'
  | 'needs_rerun'
  | 'rerun_failed'

export interface RuntimeStatus {
  id: string
  status: RuntimeStaleStatus
  script: string | null
  stem: string | null
  entry: string | null
  /** 脚本注册表里是否还有它（false = 靠文档描述块兜底，重跑前需重新登记） */
  registered: boolean
  /** materialized cache 是否可用（true = runtimePreviewUrl 取得到首帧占位） */
  cached: boolean
  /**
   * **上一次这张图是怎么产生的**（`enginesession.profile_of`，ADR 0021 §9）。
   *
   * `native` = 它出自用户自己的 Python 进程。那条会话结束之后 cache 里仍然
   * 有一张预览，但对象级编辑与权威导出都不可用了——界面靠这个字段在**重开
   * 文档那一刻**就说得出来，而不是等用户点进图内编辑撞上 409 才知道。
   *
   * 老后端不返回它 → undefined，按 safe 处理（未知不等于 native）。
   */
  execution_profile?: 'safe' | 'native'
}

/**
 * 查询 runtime 素材的 stale 状态。**只读**：后端绝不因此执行脚本。
 * `source` 是文档里持久化的描述块，注册表条目丢失时作恢复线索。
 */
export const fetchRuntimeStatus = (
  id: string,
  source?: { script: string; stem: string },
) =>
  jsonFetch<RuntimeStatus>('/api/runtime/status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, source }),
  })

/**
 * 素材库「图」区的一条 RuntimeFigureAsset（`runtimeasset.list_assets` 原样）。
 * `descriptor` 只有物化过 cache 才有——「添加到画布」的数据源；没有它的
 * 条目要先「运行并发现图」（尺寸与捕获来源都只有运行后才知道）。
 */
export interface RuntimeAssetInfo {
  id: string
  script: string
  stem: string
  entry: string
  status: RuntimeStaleStatus
  cached: boolean
  size_mm: [number, number] | null
  capture_source: 'savefig' | 'pyplot' | null
  descriptor: CapturedFigureDescriptor | null
}

/** runtime 素材清单。**只读**：后端绝不因此执行脚本。 */
export const fetchRuntimeAssets = () =>
  jsonFetch<{ assets: RuntimeAssetInfo[] }>('/api/runtime/assets')

/* ------------------ Tavotto Run · native 会话（ADR 0021） ------------------ */

/**
 * 会话状态的**闭集**——与后端 `nativesession.STATES` 同字面量（ADR 0021 §5.1）。
 *
 * 不用几个互相矛盾的 boolean：`running` + `atBarrier` + `dead` 三个布尔有八种
 * 组合，其中五种没有意义，而没有意义的那几种迟早会出现在某条分支上。
 */
export type NativeSessionState =
  | 'pending_confirmation'
  | 'waiting_for_cli'
  | 'starting_python'
  | 'running_script'
  | 'waiting_for_figure'
  | 'barrier'
  | 'continuing'
  | 'ended'
  | 'detached'
  | 'failed'

/** 终态：进了就不再出来（`detached` 也是——Tavotto 已经放手了）。 */
export const NATIVE_TERMINAL_STATES: readonly NativeSessionState[] = [
  'ended',
  'detached',
  'failed',
]

export const isNativeTerminal = (s: NativeSessionState): boolean =>
  NATIVE_TERMINAL_STATES.includes(s)

/**
 * 待确认的一条交接（`nativehandoff.sanitized()` 原样）。
 *
 * **这份里没有 token、没有端口、没有 host，也没有 argv 的值**——前端能提交的
 * 只有 `native_id`，连哪儿是后端的事（ADR 0021 §4）。`arg_count` 只有数量：
 * 确认界面要说得出「带了 3 个参数」，但那些参数的内容不该经过界面。
 */
export interface NativePending {
  native_id: string
  created_at: number
  expires_at: number
  project_root: string
  interpreter: string
  cwd: string
  target_kind: 'script' | 'module'
  target_display: string
  arg_count: number
  command_fingerprint: string
  permission_key: string
  python_version: string
  /** 这个（项目 × 解释器 × schema）此前已被「记住」——无需再问一次 */
  remembered: boolean
}

/** 一条 native 会话对外的全部状态（`NativeSession.public_state()` 原样）。 */
export interface NativeSessionInfo {
  session_id: string
  project_root: string
  interpreter: string
  interpreter_fingerprint: string
  target_kind: 'script' | 'module'
  target_display: string
  cwd: string
  arg_count: number
  python_version: string
  state: NativeSessionState
  barrier_reason: string
  process_pid: number
  stems: string[]
  descriptors: CapturedFigureDescriptor[]
  script_error: { type?: string; message?: string } | null
  terminal_error: { code?: string; message?: string } | null
  exit_code: number | null
  figures_captured: number
  started_at: number
  last_event_at: number
  /** 单调递增的事件序号：迟到的事件按它判「这条比我手里的旧」 */
  sequence: number
  /** 此刻能不能做对象级编辑（= 停在屏障上）。后端说了算，前端不自己推 */
  editable: boolean
}

/** 一次 build 的结果：stems + 描述符 + （如实报的）冲突。 */
export interface NativeBuildResult {
  session: NativeSessionInfo
  stems: Record<string, unknown>
  descriptors: CapturedFigureDescriptor[]
  /** 这些 stem 已被另一条还活着的会话占着——**报出来，不静默抢过来** */
  conflicts?: { code: string; stems: string[] }
}

/** 记住过的一条许可（设置里的撤销入口用）。 */
export interface NativePermission {
  permission_key: string
  interpreter: string
  remembered_at: number
  schema: number
}

export const fetchNativePending = (nativeId: string) =>
  jsonFetch<{ pending: NativePending }>(
    `/api/native/pending/${encodeURIComponent(nativeId)}`,
  )

/**
 * 用户点了「运行并连接」。**这一步之后 CLI 才会 spawn 用户的 Python**
 * （ADR 0021 §7）——所以确认之前一行用户代码都没跑。
 *
 * 请求体里只有 `remember`：interpreter / target / host / port 一律由后端从
 * descriptor 文件读。界面确认的是哪条 invocation，执行端就只能执行那条
 * （「请求体不能替换 invocation」由后端用例看护）。
 */
export const approveNativePending = (nativeId: string, remember: boolean) =>
  jsonFetch<{ session: NativeSessionInfo }>(
    `/api/native/pending/${encodeURIComponent(nativeId)}/approve`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ remember }),
    },
  )

/** 用户点了「取消」。CLI 正盯着这份 descriptor，会当场收摊并退出 3。 */
export const cancelNativePending = (nativeId: string) =>
  jsonFetch<{ cancelled: boolean }>(
    `/api/native/pending/${encodeURIComponent(nativeId)}/cancel`,
    { method: 'POST' },
  )

export const fetchNativeSessions = (projectRoot?: string) =>
  jsonFetch<{ sessions: NativeSessionInfo[] }>(
    `/api/native/sessions${
      projectRoot ? `?project_root=${encodeURIComponent(projectRoot)}` : ''
    }`,
  )

/**
 * 在屏障处 build 一次：拿到 stems / descriptors，并绑定 live route。
 *
 * **由界面显式调**，后端不在收到 barrier 事件时自动发——那条事件是在 reader
 * 线程里收到的，而 build 的响应要由**同一个** reader 读回来（ADR 0021 §5.2）。
 */
export const buildNativeSession = (sessionId: string) =>
  jsonFetch<NativeBuildResult>(
    `/api/native/sessions/${encodeURIComponent(sessionId)}/build`,
    { method: 'POST' },
  )

const nativeAction = (sessionId: string, action: string) =>
  jsonFetch<{ session: NativeSessionInfo }>(
    `/api/native/sessions/${encodeURIComponent(sessionId)}/${action}`,
    { method: 'POST' },
  )

/** 继续运行脚本。**runner 会先把 Figure 恢复成脚本原样**（ADR 0021 §8）。 */
export const continueNativeSession = (sessionId: string) =>
  nativeAction(sessionId, 'continue')

/** 放手：脚本继续正常跑完，Tavotto 不再控制它。**不杀进程。** */
export const detachNativeSession = (sessionId: string) =>
  nativeAction(sessionId, 'detach')

/** 结束用户脚本——**明确的危险操作**，退出码固定 5，不伪装成 continue。 */
export const terminateNativeSession = (sessionId: string) =>
  nativeAction(sessionId, 'terminate')

export const fetchNativePermissions = (projectRoot?: string) =>
  jsonFetch<{ permissions: NativePermission[] }>(
    `/api/native/permissions${
      projectRoot ? `?project_root=${encodeURIComponent(projectRoot)}` : ''
    }`,
  )

export const forgetNativePermission = (permissionKey: string, projectRoot?: string) =>
  jsonFetch<{ removed: boolean }>('/api/native/permissions', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ permission_key: permissionKey, project_root: projectRoot }),
  })
