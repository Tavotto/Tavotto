/**
 * 导出默认值（设置里改，导出对话框作为初始值读取）。
 *
 * **权威在后端**（#715 PR-B，`GET/PUT /api/preferences/export-defaults`，数据目录按用户一份）：
 * 桌面版换了端口就是换了 origin、一份空的 localStorage，1200 ppi 会静默退回 600 ppi（#715 证据）。
 * 本机 `tavotto.export.defaults` 只当同步读取用的缓存：启动时 `hydrateExportDefaults()` 用后端那份
 * 覆盖它，写入时两边都写。后端没有这组端点（404：playground、嵌入画布、旧后端）时只剩本机这份，
 * 即改造前的行为。字段的语义只在本文件（后端只守「是个不大的 JSON 对象」）。
 */
import { ApiError, fetchExportDefaultsRemote, putExportDefaultsRemote } from './api'
import { DEFAULT_PROFILE_ID, hasProfile } from './profile'

const KEY = 'tavotto.export.defaults'

export interface ExportDefaults {
  dpi: string
  formats: string[]
  withProof: boolean
  /**
   * 上次用过的出版规范。**只是新文档的初值**——文档一旦写了自己的
   * `doc.profile`，那个才说了算（规范属于这张图，不属于这台机器）。
   */
  profileId: string
  /** 上次是否勾了「严格核验产物」（ADR 0068）；缺省不勾 = standard */
  strictInspection: boolean
}

const DEFAULTS: ExportDefaults = {
  dpi: '600',
  formats: ['pdf', 'png'],
  withProof: false,
  profileId: DEFAULT_PROFILE_ID,
  strictInspection: false,
}

export function readExportDefaults(): ExportDefaults {
  try {
    const raw = localStorage.getItem(KEY)
    const v = raw ? JSON.parse(raw) : null
    if (v && typeof v === 'object') {
      return {
        dpi: typeof v.dpi === 'string' ? v.dpi : DEFAULTS.dpi,
        formats: Array.isArray(v.formats) && v.formats.length ? v.formats : DEFAULTS.formats,
        withProof: v.withProof === true,
        strictInspection: v.strictInspection === true,
        // 存着一个已经删掉的 profile id 时退回默认：不能让一条陈旧的偏好
        // 把整个导出对话框卡在一个不存在的规范上
        profileId: hasProfile(v.profileId) ? (v.profileId as string) : DEFAULTS.profileId,
      }
    }
  } catch {
    /* 用默认值 */
  }
  return DEFAULTS
}

function writeCache(value: unknown): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(value))
  } catch {
    /* 忽略存储失败 */
  }
}

/** 后端回过 404 = 没有这组端点：本模块实例里不再往上推（重启后重新探测）。 */
let remoteMissing = false
/** 推给后端的写入串行：连着改两次，后发的必须后到。 */
let remoteTail: Promise<void> = Promise.resolve()

/**
 * 本机这份**还没被后端确认**：推送失败（非 404）时留着这个标记。下次 `hydrateExportDefaults()`
 * 见到它就以本机为准并重推，不拿后端那份更旧的值盖掉用户刚改的 DPI / 格式（#719 Codex P2）。
 * 值是这次写入的唯一标识，只用来认「确认的是不是这一次」。
 */
const PENDING_KEY = 'tavotto.export.defaults.pending'

let tokenSeq = 0
/** 每次写入唯一：同一毫秒里写两次时时刻会撞，认确认只能认这个（#719 Codex P2） */
function newToken(): string {
  tokenSeq += 1
  return `${Date.now().toString(36)}-${tokenSeq.toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

function readPending(): string | null {
  try {
    return localStorage.getItem(PENDING_KEY)
  } catch {
    return null
  }
}

function setPending(token: string | null): void {
  try {
    if (token === null) localStorage.removeItem(PENDING_KEY)
    else localStorage.setItem(PENDING_KEY, token)
  } catch {
    /* 存不下：退化成改造前「失败即丢」 */
  }
}

function pushRemote(value: ExportDefaults, token: string): void {
  if (remoteMissing) return
  remoteTail = remoteTail.then(() =>
    putExportDefaultsRemote(value).then(
      () => {
        if (readPending() === token) setPending(null)
      },
      (e: unknown) => {
        if (e instanceof ApiError && e.status === 404) remoteMissing = true
        /* 其余失败：待确认标记留着，下次启动 hydrate 时重推 */
      },
    ),
  )
}

export function writeExportDefaults(patch: Partial<ExportDefaults>): ExportDefaults {
  const next = { ...readExportDefaults(), ...patch }
  const token = newToken()
  writeCache(next)
  setPending(token)
  pushRemote(next, token)
  return next
}

/**
 * 启动时从后端取回导出默认值，覆盖本机缓存。后端还没存过、而本机有（同一个 origin 升级
 * 上来的旧偏好）：推一份上去。后端没有这组端点 / 不可达：什么都不动，本机那份照旧生效。
 */
/**
 * 「后端那份已经取回来、覆盖了本机缓存」的监听者。导出对话框在 `App` 里**常驻挂载**，它的
 * 初值只在挂载那一刻读一次缓存——换了 origin 的首启，那一刻缓存还是空的（600 ppi）；取回之后
 * 要通知它重读，否则这一整次会话里对话框都显示并按 600 导出（#719 Codex P1）。设置页同理。
 */
const hydratedListeners = new Set<() => void>()

export function onExportDefaultsHydrated(cb: () => void): () => void {
  hydratedListeners.add(cb)
  return () => {
    hydratedListeners.delete(cb)
  }
}

export async function hydrateExportDefaults(): Promise<void> {
  await remoteTail
  const remote = await fetchExportDefaultsRemote()
  if (remote === undefined) return
  // 本机有一次后端没确认的改动：本机为准，重推（它是这个 origin 上用户最后一次的意图）
  const pending = readPending()
  if (pending !== null) {
    pushRemote(readExportDefaults(), pending)
    return
  }
  if (remote.defaults && typeof remote.defaults === 'object') {
    writeCache(remote.defaults)
    for (const cb of [...hydratedListeners]) cb()
    return
  }
  let local: string | null = null
  try {
    local = localStorage.getItem(KEY)
  } catch {
    /* 读不了就没有可迁移的 */
  }
  if (local) {
    const token = newToken()
    setPending(token)
    pushRemote(readExportDefaults(), token)
  }
}
