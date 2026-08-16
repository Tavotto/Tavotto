/** 导出默认值（设置里改，导出对话框作为初始值读取）。localStorage 轻量偏好。 */
const KEY = 'magplot.export.defaults'

export interface ExportDefaults {
  dpi: string
  formats: string[]
  withProof: boolean
}

const DEFAULTS: ExportDefaults = { dpi: '600', formats: ['pdf', 'png'], withProof: false }

export function readExportDefaults(): ExportDefaults {
  try {
    const raw = localStorage.getItem(KEY)
    const v = raw ? JSON.parse(raw) : null
    if (v && typeof v === 'object') {
      return {
        dpi: typeof v.dpi === 'string' ? v.dpi : DEFAULTS.dpi,
        formats: Array.isArray(v.formats) && v.formats.length ? v.formats : DEFAULTS.formats,
        withProof: v.withProof === true,
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
