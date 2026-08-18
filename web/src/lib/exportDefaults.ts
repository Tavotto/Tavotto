/** 导出默认值（设置里改，导出对话框作为初始值读取）。localStorage 轻量偏好。 */
import { DEFAULT_PROFILE_ID, hasProfile } from './profile'

const KEY = 'magplot.export.defaults'

export interface ExportDefaults {
  dpi: string
  formats: string[]
  withProof: boolean
  /**
   * 上次用过的出版规范。**只是新文档的初值**——文档一旦写了自己的
   * `doc.profile`，那个才说了算（规范属于这张图，不属于这台机器）。
   */
  profileId: string
}

const DEFAULTS: ExportDefaults = {
  dpi: '600',
  formats: ['pdf', 'png'],
  withProof: false,
  profileId: DEFAULT_PROFILE_ID,
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

export function writeExportDefaults(patch: Partial<ExportDefaults>): ExportDefaults {
  const next = { ...readExportDefaults(), ...patch }
  try {
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    /* 忽略存储失败 */
  }
  return next
}
