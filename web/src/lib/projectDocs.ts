/**
 * 「这个项目上次开着哪份排版」——按项目记（T02），**以后端为准**（#715 PR-B）。
 *
 * 切项目时 `resetForNewProject()` 会换上一份空白文档；改造前没有任何人再把
 * 用户上次在那个项目里排的版换回来，于是「返回 Tutorial 后先出现空白
 * fig_layout」（审计 T02）。这里记的只是一个 documentId + 名字：**不进
 * `.tavotto`、不进撤销、不是自动保存**——文档本身仍在自动保存槽位
 * （`layouts/_autosave/`）里，切回去时按 id 读回来。
 *
 * **权威在后端**（`GET /api/layout-session`、`PUT /api/layout-session/last`，数据目录
 * `state/layout-sessions.json`，`engine/layoutsession.py`）：桌面版每次启动 sidecar 可能换端口，
 * 换了端口就是换了 origin、一份空的 localStorage（#715）。本机的
 * `tavotto.projectDoc.<pj>` 只当缓存：后端没有这组端点（404：playground、嵌入画布、旧后端）
 * 或此刻不可达时退回它——那正是改造前的行为；后端有、但这个项目还没记过时，本机缓存是同一个
 * origin 升级上来的旧记录，拿它当迁移源并推一份上去。稳定端口与这份文件的关系见 #715 方案与
 * ADR 0108（桌面 origin 稳定与会话状态的后端权威，§三）。
 *
 * **空白文档不记**：没有内容的文档从不落盘（`flushAutosave` 回 `'empty'`），
 * 记下它的 id 只会在下次切回来时读到一个 404，然后向用户报一份根本不存在
 * 的「找不到上次文档」。
 */
import { ApiError, fetchLayoutSession, putLayoutSessionLast } from '@/lib/api'
import type { CanvasData, FigureDocument } from '@/types/document'

const PREFIX = 'tavotto.projectDoc.'

export interface ProjectDocumentRef {
  id: string
  /** 文档名（用户内容），只用来在「找不到上次文档」那句话里指名 */
  name: string
}

export function readProjectDocument(projectId: string): ProjectDocumentRef | null {
  try {
    const raw = localStorage.getItem(PREFIX + projectId)
    if (!raw) return null
    const v = JSON.parse(raw) as Partial<ProjectDocumentRef>
    return typeof v.id === 'string' && v.id ? { id: v.id, name: typeof v.name === 'string' ? v.name : '' } : null
  } catch {
    return null
  }
}

function writeCache(projectId: string, ref: ProjectDocumentRef): void {
  try {
    localStorage.setItem(PREFIX + projectId, JSON.stringify(ref))
  } catch {
    /* 存不下只影响「下次切回这个项目落在哪份文档上」 */
  }
}

/**
 * 后端回过 404 = 这个后端没有这组端点（旧后端 / playground / 嵌入画布）：本模块实例里不再
 * 往上推。模块级而不是持久化——换一次后端（重启）自然重新探测。
 */
let remoteMissing = false

/**
 * 往后端推的写入**串行**：两次记录（A 然后 B）若并发发出、B 先到，后到的 A 会把
 * 「上次开着的」改回旧的那份。读之前也先等它排空（`loadProjectDocument`）。
 */
let remoteTail: Promise<void> = Promise.resolve()

function pushRemote(projectId: string, ref: ProjectDocumentRef): void {
  if (remoteMissing) return
  remoteTail = remoteTail.then(() =>
    putLayoutSessionLast({ doc_id: ref.id, name: ref.name }, projectId).then(
      () => undefined,
      (e: unknown) => {
        if (e instanceof ApiError && e.status === 404) remoteMissing = true
        /* 其余失败：本机缓存照样记着，下一次文档或名字变化时再推 */
      },
    ),
  )
}

/** 记「这个项目现在开着这份排版」：本机缓存 + 后端（权威）。 */
export function rememberProjectDocument(projectId: string, ref: ProjectDocumentRef): void {
  writeCache(projectId, ref)
  pushRemote(projectId, ref)
}

/**
 * 只问后端：`ProjectDocumentRef` = 记着的那份；`null` = 后端有这组端点、这个项目还没记过；
 * `undefined` = 后端没有这组端点 / 此刻不可达（调用方退回本机的旧逻辑）。
 * 读到了就顺手更新本机缓存。
 */
export async function fetchRemoteProjectDocument(
  projectId: string,
): Promise<ProjectDocumentRef | null | undefined> {
  await remoteTail
  const remote = await fetchLayoutSession(projectId)
  if (remote === undefined) return undefined
  if (!remote.last) return null
  const ref = { id: remote.last.doc_id, name: remote.last.name }
  writeCache(projectId, ref)
  return ref
}

/**
 * 这个项目上次开着哪份排版：后端优先；后端没有这组端点时读本机缓存（旧逻辑）；后端有、
 * 但还没记过时，本机缓存是同一个 origin 升级上来的旧记录——用它，并推一份上去。
 */
export async function loadProjectDocument(projectId: string): Promise<ProjectDocumentRef | null> {
  const remote = await fetchRemoteProjectDocument(projectId)
  if (remote) return remote
  const local = readProjectDocument(projectId)
  if (remote === null && local) pushRemote(projectId, local)
  return local
}

export function forgetProjectDocument(projectId: string): void {
  try {
    localStorage.removeItem(PREFIX + projectId)
  } catch {
    /* 同上 */
  }
}

/**
 * 文档有没有值得回来的东西。判据与 `documentStore.hasContent` 一致
 * （任一画布有对象 / 参考线，或不止一张画布）；`doc` 是激活画布的热态，
 * 可能比 `canvases` 里那份新，所以两处都看。
 */
export function documentHasContent(s: {
  doc: Pick<FigureDocument, 'objects' | 'guides'>
  canvases: readonly Pick<CanvasData, 'objects' | 'guides'>[]
}): boolean {
  if (s.doc.objects.length > 0 || s.doc.guides.length > 0) return true
  if (s.canvases.length > 1) return true
  return s.canvases.some((c) => c.objects.length > 0 || c.guides.length > 0)
}
