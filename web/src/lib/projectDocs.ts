/**
 * 「这个项目上次开着哪份文档」——本机一档，按项目 id 存（T02）。
 *
 * 切项目时 `resetForNewProject()` 会换上一份空白文档；改造前没有任何人再把
 * 用户上次在那个项目里排的版换回来，于是「返回 Tutorial 后先出现空白
 * fig_layout」（审计 T02）。这里记的只是一个 documentId + 名字：**不进
 * `.tavotto`、不进撤销、不是自动保存**——文档本身仍在自动保存槽位
 * （`layouts/_autosave/`）里，切回去时按 id 读回来。
 *
 * 与 `tavotto.workspace.<documentId>`、`tavotto.tabs.<documentId>` 同一条纪律：
 * 存的是「用户停在哪」，存不下只影响下次回来的落点。
 *
 * **空白文档不记**：没有内容的文档从不落盘（`flushAutosave` 回 `'empty'`），
 * 记下它的 id 只会在下次切回来时读到一个 404，然后向用户报一份根本不存在
 * 的「找不到上次文档」。
 */
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

export function rememberProjectDocument(projectId: string, ref: ProjectDocumentRef): void {
  try {
    localStorage.setItem(PREFIX + projectId, JSON.stringify(ref))
  } catch {
    /* 存不下只影响「下次切回这个项目落在哪份文档上」 */
  }
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
