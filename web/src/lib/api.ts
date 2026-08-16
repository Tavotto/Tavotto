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

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body?.error) detail = body.error
    } catch {
      /* 非 JSON 错误体，保留状态码 */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const fetchPanels = () => jsonFetch<PanelsResponse>('/api/panels')

/* ----------------------------- 项目（Project） ------------------------------ */
/** 层级见 docs/adr/0001-project-canvas-tab-object.md；未打开项目时后端回 409。 */

export interface ProjectStatus {
  open: boolean
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
  current: boolean
}

export const fetchProject = () => jsonFetch<ProjectStatus>('/api/project')

export const fetchRecentProjects = () =>
  jsonFetch<{ recent: RecentProject[] }>('/api/projects/recent').then((r) => r.recent)

export const openProjectApi = (path: string, create = false) =>
  jsonFetch<ProjectStatus>('/api/projects/open', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, create }),
  })

export const removeRecentProject = (path: string) =>
  jsonFetch<{ ok: boolean }>('/api/projects/remove', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })

export interface BrowseResult {
  path: string
  parent: string | null
  dirs: { name: string; path: string }[]
}

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
  `/api/render?id=${encodeURIComponent(id)}&w=${bucket}${stamp(mtime)}`

export const fileUrl = (id: string, mtime?: number) =>
  `/api/file?id=${encodeURIComponent(id)}${stamp(mtime)}`

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

export const putAutosave = (docId: string, doc: ProjectDocument) =>
  jsonFetch<{ ok: boolean }>(`/api/autosave/${encodeURIComponent(docId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(doc),
  })

/** 404（没存过）返回 null；其余错误抛出 */
export async function fetchAutosave(docId: string): Promise<unknown | null> {
  const res = await fetch(`/api/autosave/${encodeURIComponent(docId)}`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
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
  /** order：可重排列表（图例条目顺序），value 为原始序号排列，options 为显示文字 */
  type: 'text' | 'number' | 'color' | 'bool' | 'enum' | 'pair' | 'rect' | 'order'
  value: unknown
  min?: number
  max?: number
  step?: number
  unit?: string
  options?: string[]
  /** 归到哪个可折叠小节（排版 / 背景 / 描边）；无值 = 基本属性，平铺在前 */
  group?: string
}

export interface ManifestElement {
  gid: string
  role: string
  label: string
  /** figure 分数坐标，y 向下：[x, y, w, h] */
  bbox: [number, number, number, number]
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
  anchor?: [number, number]
  drag_prop?: string
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
}

export class EngineError extends Error {
  traceback: string
  /** 机器可读的原因；'no_worker_python' = 缺渲染环境，界面给引导而不是甩错误文字 */
  code: string
  constructor(message: string, traceback = '', code = '') {
    super(message)
    this.traceback = traceback
    this.code = code
  }
}

export async function engineRender(
  id: string,
  patches: unknown[],
  signal?: AbortSignal,
): Promise<EngineRenderResponse> {
  const res = await fetch('/api/engine/render', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, patches }),
    signal,
  })
  const body = await res.json().catch(() => ({}) as Record<string, unknown>)
  if (!res.ok) {
    throw new EngineError(
      (body.error as string) || `渲染失败（HTTP ${res.status}）`,
      (body.traceback as string) || '',
      (body.code as string) || '',
    )
  }
  return body as EngineRenderResponse
}

/** 高清位图预览：含 imshow 的面板用 SVG 显示会糊，退出编辑态后走这个 */
export const enginePngUrl = (id: string, bucket: number, rev: number) =>
  `/api/engine/png?id=${encodeURIComponent(id)}&w=${bucket}&rev=${rev}`

export async function engineSvg(id: string, rev: number, signal?: AbortSignal): Promise<string> {
  const res = await fetch(`/api/engine/svg?id=${encodeURIComponent(id)}&rev=${rev}`, { signal })
  if (!res.ok) throw new EngineError(`取 SVG 失败（HTTP ${res.status}）`)
  return res.text()
}

/** 用当前 overrides 全质量重出该 stem 的 PDF+PNG，原子替换 figures 里的原文件 */
export const updateSourceFiles = (id: string, patches: unknown[]) =>
  jsonFetch<{ updated: string[]; backup_dir: string }>('/api/engine/update_source', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, patches }),
  })

/* -------------------------------- AI 桥 ----------------------------------- */

export interface AiProviderCaps {
  installed: boolean
  path: string | null
  version: string | null
  models: string[]
  default_model: string | null
  efforts: string[]
  default_effort: string | null
}

export interface AiCapabilities {
  providers: Record<'codex' | 'claude', AiProviderCaps>
}

export const fetchAiCapabilities = (refresh = false) =>
  jsonFetch<AiCapabilities>(`/api/ai/capabilities${refresh ? '?refresh=1' : ''}`)

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

export type ServerEvent =
  | { kind: 'render.started'; id: string; cost?: string; cold?: boolean }
  | { kind: 'render.done'; id: string; rev?: number }
  | { kind: 'render.failed'; id: string; error?: string }
  | { kind: 'panel.file_changed'; scripts?: string[]; stems?: string[] }
  | { kind: 'engine.bootstrap'; state: string; log: string; error: string | null }
  | { kind: 'ai.delta'; session: string; text: string; kindOf?: AiDeltaKind }
  | {
      kind: 'ai.done'
      session: string
      status: string
      changed: boolean
      diff: string
      script: string
      error?: string
    }

const EVENT_KINDS = [
  'render.started',
  'render.done',
  'render.failed',
  'panel.file_changed',
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
    source = new EventSource('/api/events')
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
  `/api/engine/history/preview?id=${encodeURIComponent(id)}&n=${n}&w=${w}`

export const restoreHistory = (id: string, n: number) =>
  jsonFetch<{
    updated: string[]
    backup_dir: string
    patches: { gid: string; prop: string; value: unknown }[]
  }>('/api/engine/history/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, n }),
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
  /** GitHub Releases 上的最新 tag（离线或从未检查过时缺席） */
  latest?: string
  update_available?: boolean
  notes?: string
  published_at?: string | null
  html_url?: string
  /** 安装方式决定能不能代劳升级：source 检出只提示 git pull */
  method: 'pip' | 'pipx' | 'source'
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
// 渲染环境（缺 matplotlib 时的自助安装）
// ---------------------------------------------------------------------------
export interface EngineEnvironment {
  ok: boolean
  python: string | null
  matplotlib: string | null
  /** true = 用的是 Magplot 自建的环境，而非用户自己的 */
  managed: boolean
  state: 'idle' | 'running' | 'done' | 'failed'
  /** ok=false 时才有：能不能替用户装一个 */
  can_install?: boolean
  base_python?: string | null
  error?: string | null
}

export interface BootstrapProgress {
  state: 'idle' | 'running' | 'done' | 'failed'
  log: string
  error: string | null
}

export const fetchEngineEnvironment = () =>
  jsonFetch<EngineEnvironment>('/api/engine/environment')

export const installEngineEnvironment = () =>
  jsonFetch<{ started?: boolean } & BootstrapProgress>(
    '/api/engine/environment/install', { method: 'POST' })

export const setEngineEnvironment = (python: string | null) =>
  jsonFetch<EngineEnvironment>('/api/engine/environment', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ python }),
  })
