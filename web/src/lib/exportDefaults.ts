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

function pushRemote(value: ExportDefaults): void {
  if (remoteMissing) return
  remoteTail = remoteTail.then(() =>
    putExportDefaultsRemote(value).then(
      () => undefined,
      (e: unknown) => {
        if (e instanceof ApiError && e.status === 404) remoteMissing = true
      },
    ),
  )
}

export function writeExportDefaults(patch: Partial<ExportDefaults>): ExportDefaults {
  const next = { ...readExportDefaults(), ...patch }
  writeCache(next)
  pushRemote(next)
  return next
}

/**
 * 启动时从后端取回导出默认值，覆盖本机缓存。后端还没存过、而本机有（同一个 origin 升级
 * 上来的旧偏好）：推一份上去。后端没有这组端点 / 不可达：什么都不动，本机那份照旧生效。
 */
export async function hydrateExportDefaults(): Promise<void> {
  await remoteTail
  const remote = await fetchExportDefaultsRemote()
  if (remote === undefined) return
  if (remote.defaults && typeof remote.defaults === 'object') {
    writeCache(remote.defaults)
    return
  }
  let local: string | null = null
  try {
    local = localStorage.getItem(KEY)
  } catch {
    /* 读不了就没有可迁移的 */
  }
  if (local) pushRemote(readExportDefaults())
}
