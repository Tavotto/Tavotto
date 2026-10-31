import { extOf, type PanelInfo } from '@/lib/api'

/** 格式名（不翻译）。位图按扩展名说实话：以前一律写 PNG，JPEG / TIFF 素材也被叫成 PNG */
const FORMAT_LABEL: Record<string, string> = { jpg: 'JPEG', jpeg: 'JPEG', tif: 'TIFF', tiff: 'TIFF' }
export const formatOf = (p: PanelInfo) => {
  if (p.kind === 'pdf') return 'PDF'
  const ext = extOf(p.id)
  return FORMAT_LABEL[ext] ?? (ext.toUpperCase() || 'PNG')
}

/** 文件名截断时固定保留的尾部：扩展名 + 结尾 4 位；没有扩展名（运行时图的 stem）就保留 6 位 */
export const nameTail = (name: string) => {
  const ext = /\.[A-Za-z0-9]{1,5}$/.exec(name)
  return ext ? ext[0].length + 4 : 6
}
