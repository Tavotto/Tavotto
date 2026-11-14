import { documentAddressKey, documentNamespaceKey, type DocumentFormat } from './documentFormat'
/**
 * 「这份排版绑定了项目里的哪个文件」——⌘S 写回的目标（ADR 0096）。
 *
 * 一份排版（documentId）要么只在本机（自动保存槽位 `layouts/_autosave/`），要么
 * 还**绑定**着项目里的一个文件 `tavottofile/<名>.json`：从「项目里的排版」打开的、
 * 或者另存进项目的。绑定之后 ⌘S 写回那个文件；自动保存仍只写本机槽位——没按保存
 * 就不动项目里的文件。
 *
 * 按 documentId 存本机（与 `tavotto.tabs.<id>`、`tavotto.projectDoc.<pj>` 同一条纪律：
 * 这是「这份工作副本从哪来、存回哪去」，**不进文档**——文档跟着项目包 / git 走到别的
 * 电脑上，那边的同一个文件不该带着这台电脑的绑定）。
 *
 * **`revision` 是这份工作副本的基线，不是本窗口的一次观察**：它记的是「上次与项目
 * 文件同步（打开 / 写成）时那一份的内容 hash」。刷新之后照样拿它当 `base_revision`，
 * 磁盘上那份被 git pull / 另一台电脑改过的话 hash 对不上，后端 409——正是要挡的事。
 * 与 `lib/layoutRevision.ts`（按窗口、只在内存）不是同一个量：那边回答「本窗口读到过
 * 哪一份」，给另存为；这边回答「这份工作副本基于哪一份」，给 ⌘S。
 *
 * `dirty` = 项目里那份文件落后于这份工作副本（有编辑还没 ⌘S）。跟着绑定一起存，刷新
 * 之后圆点照样在——自动保存把编辑存进了本机，项目里的文件仍是旧的。
 */
const PREFIX = 'tavotto.projectFile.'

export interface ProjectFileBinding {
  /** 后端关联的单调代次；旧本机记录没有，不能据文件名相等绕过 ABA。 */
  sequence?: number
  /** 绑定时开着的项目（`currentProjectId()`，项目路径的稳定 hash） */
  projectId: string
  /** 文件名去掉 `.json`（后端净化之后落盘的那个） */
  name: string
  /** 相对项目根的路径，界面原样说出来：`tavottofile/<名>.json` */
  file: string
  /** 上次同步时项目文件的内容 hash；`null` = 不知道（写前按「没读过」处理） */
  revision: string | null
  dirty: boolean
  /** 最近一次绑定 / 写成的时间（ms）；老数据没有 = 最旧。重开同一文件挑最近的那份身份用 */
  at?: number
}

function isBinding(v: unknown): v is ProjectFileBinding {
  if (!v || typeof v !== 'object') return false
  const b = v as Record<string, unknown>
  return (
    typeof b.projectId === 'string' &&
    !!b.projectId &&
    typeof b.name === 'string' &&
    !!b.name &&
    typeof b.file === 'string' &&
    (b.sequence === undefined || Number.isSafeInteger(b.sequence) && (b.sequence as number) > 0) &&
    (b.revision === null || typeof b.revision === 'string') &&
    (b.at === undefined || typeof b.at === 'number') &&
    typeof b.dirty === 'boolean'
  )
}

/**
 * 本会话里的绑定：**以它为准**，localStorage 只是让刷新之后还认得的尽力而为的副本
 * （#674 评审第 7 轮）。`setItem` 失败（存储被禁用、配额满——排版大时本机副本真会撞满）
 * 时只读持久副本的话，⌘S 写成之后修订号推进不了、圆点灭不掉，下一次带着旧修订号去撞一个
 * 假冲突。读先看这里，写先写这里再尽力持久化；本会话的行为与存储成败无关。
 */
const inSession = new Map<string, ProjectFileBinding | null>()

export function readProjectFile(documentId: string, format?: DocumentFormat): ProjectFileBinding | null {
  if (inSession.has(documentAddressKey(documentId, format))) return inSession.get(documentAddressKey(documentId, format)) ?? null
  try {
    const raw = localStorage.getItem(documentNamespaceKey(PREFIX + documentId, format))
    if (!raw) return null
    const v: unknown = JSON.parse(raw)
    return isBinding(v) ? v : null
  } catch {
    return null
  }
}

/**
 * 判「能不能沿用旧身份」专用：会话缓存与持久副本**两份都看，任何一份 dirty / 对不上就不沿用**。
 * 别的窗口可能后来把持久副本置 dirty（本窗口缓存还是干净旧绑定）；本窗口也可能把缓存置 dirty 而
 * 持久副本写失败（配额满，持久副本停在干净旧值）——两种都得放弃沿用（Codex #1734 P1）。
 * 本会话明确解绑过（缓存里是 null）= 解绑。返回 null = 不可沿用。
 */
export function bindingForReuse(documentId: string, projectId: string, file: string, format: DocumentFormat = 'legacy'): ProjectFileBinding | null {
  if (inSession.has(documentAddressKey(documentId, format)) && inSession.get(documentAddressKey(documentId, format)) === null) return null
  const seen: ProjectFileBinding[] = []
  const cached = inSession.get(documentAddressKey(documentId, format))
  if (cached) seen.push(cached)
  try {
    const raw = localStorage.getItem(documentNamespaceKey(PREFIX + documentId, format))
    if (raw) {
      const v: unknown = JSON.parse(raw)
      if (isBinding(v)) seen.push(v)
      else return null
    }
  } catch {
    /* 持久副本读不了：只看会话缓存 */
  }
  if (seen.length === 0) return null
  if (seen.some((b) => b.dirty || b.projectId !== projectId || b.file !== file)) return null
  return seen.reduce((x, y) => ((y.at ?? 0) > (x.at ?? 0) ? y : x))
}

export function writeProjectFile(documentId: string, binding: ProjectFileBinding, format?: DocumentFormat): ProjectFileBinding {
  const stamped = { ...binding, at: Date.now() }
  inSession.set(documentAddressKey(documentId, format), stamped)
  try {
    localStorage.setItem(documentNamespaceKey(PREFIX + documentId, format), JSON.stringify(stamped))
  } catch {
    /* 存不下只影响刷新之后 ⌘S 还认不认得这个文件（认不得就按第一次保存问一次名字） */
  }
  return stamped
}

export function forgetProjectFile(documentId: string, format?: DocumentFormat): void {
  // 记成「已解绑」而不是删掉：删掉的话 removeItem 也失败时会读回那份旧的持久副本
  inSession.set(documentAddressKey(documentId, format), null)
  try {
    localStorage.removeItem(documentNamespaceKey(PREFIX + documentId, format))
  } catch {
    /* 同上 */
  }
}

/** 仅供用例：模块级 Map 会跨用例活下来（用例清 localStorage 时一并清这里） */
export const forgetProjectFilesInSession = (): void => inSession.clear()

/**
 * 同一个项目文件上次是哪份排版（documentId）绑着的——重开它时沿用这个身份，版本时间线
 * （按 documentId 分存，ADR 0101 / 0140）才接得上（QA-WIN-46）。
 *
 * 只认**没有未保存修改**的绑定：`dirty` 的那份本机槽位里有项目文件里没有的编辑，
 * 重开项目文件会把槽位盖成项目文件的内容，所以那种情况仍给新身份、旧槽位留作「本机排版」。
 * 多份都绑着同一个文件（旧版本每次重开都新建一份）时按最近绑定 / 写成的排前面。
 * 调用方再逐个问「是不是在别的窗口开着」，开着的跳过（ADR 0096 的「打开两次是两个会话」不变）。
 * 另存为不受影响：它把当前排版改绑新文件，旧文件不再有绑定，副本不会继承别的文件的历史。
 */
export function findDocumentsForProjectFile(projectId: string, file: string, format: DocumentFormat = 'legacy'): string[] {
  const ids = new Set<string>()
  const prefix = documentNamespaceKey(PREFIX, format)
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (k?.startsWith(prefix)) ids.add(k.slice(prefix.length))
    }
  } catch {
    /* 读不了持久副本就只看本会话 */
  }
  for (const key of inSession.keys()) {
    if (format === 'legacy') {
      if (!key.startsWith('[')) ids.add(key)
    } else {
      try {
        const address: unknown = JSON.parse(key)
        if (Array.isArray(address) && address.length === 2 && address[0] === format && typeof address[1] === 'string') ids.add(address[1])
      } catch { /* legacy key */ }
    }
  }
  const hits: { id: string; at: number }[] = []
  for (const id of ids) {
    const b = bindingForReuse(id, projectId, file, format)
    if (!b) continue
    hits.push({ id, at: b.at ?? 0 })
  }
  // 最近绑定 / 写成的在前（它最可能带着用户的历史）；时间相同按 id 排，结果稳定
  return hits.sort((x, y) => y.at - x.at || (x.id < y.id ? -1 : 1)).map((h) => h.id)
}

/**
 * 此刻这个标签页能用的绑定：**只认当前项目的**。一份在项目 A 里绑定的排版被从「最近
 * 排版」带进项目 B 时，⌘S 不能写回 A 的文件（那条请求带的是 B 的 pj，落不到 A），
 * 也不能把 A 的绑定抹掉（回到 A 还要用）——在 B 里它就是一份还没存进项目的排版。
 */
export function bindingForProject(
  binding: ProjectFileBinding | null,
  projectId: string | null,
): ProjectFileBinding | null {
  return binding && projectId && binding.projectId === projectId ? binding : null
}
