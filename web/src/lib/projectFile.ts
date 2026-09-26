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
  /** 绑定时开着的项目（`currentProjectId()`，项目路径的稳定 hash） */
  projectId: string
  /** 文件名去掉 `.json`（后端净化之后落盘的那个） */
  name: string
  /** 相对项目根的路径，界面原样说出来：`tavottofile/<名>.json` */
  file: string
  /** 上次同步时项目文件的内容 hash；`null` = 不知道（写前按「没读过」处理） */
  revision: string | null
  dirty: boolean
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
    (b.revision === null || typeof b.revision === 'string') &&
    typeof b.dirty === 'boolean'
  )
}

export function readProjectFile(documentId: string): ProjectFileBinding | null {
  try {
    const raw = localStorage.getItem(PREFIX + documentId)
    if (!raw) return null
    const v: unknown = JSON.parse(raw)
    return isBinding(v) ? v : null
  } catch {
    return null
  }
}

export function writeProjectFile(documentId: string, binding: ProjectFileBinding): void {
  try {
    localStorage.setItem(PREFIX + documentId, JSON.stringify(binding))
  } catch {
    /* 存不下只影响刷新之后 ⌘S 还认不认得这个文件（认不得就按第一次保存问一次名字） */
  }
}

export function forgetProjectFile(documentId: string): void {
  try {
    localStorage.removeItem(PREFIX + documentId)
  } catch {
    /* 同上 */
  }
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
