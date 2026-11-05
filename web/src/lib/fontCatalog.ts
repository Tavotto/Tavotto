/**
 * 这台机器上画布文字能用的字体（ADR 0169，建在 ADR 0164 的本机字体通道上）。
 *
 * 清单来自本机服务 `/api/source-fonts/catalog`，里面只有名字和身份。选中一族 = 往 `TextObject.sourceFont`
 * 写这一族里的一张脸，文档、导出、预览、缺字体的处理全走 ADR 0164 那一条路。
 *
 * 粗斜：`sourceFont` 永远点名**一张**脸，按 B / I 就换成同族里那张（族的归属来自清单，不按名字猜文件）。
 * 这一族没有那张脸时按钮置灰（`styleMissing`），不造假粗体。
 *
 * 中文格单独选、英文留在内置字体时，`sourceFont` 的正文脸点名批准字体本身：批准字体也有 sha256，
 * 不用改文档形状（`isBuiltinFace` 让界面继续显示「衬线体」）。
 */
import { useSyncExternalStore } from 'react'
import { fetchSourceFontCatalog } from './api'
import { faceKey, isSourceFont, sameFace } from './sourceFont'
import { effectiveCanvasFamily } from './typography'
import type { SourceFace, SourceFont, TextObject } from '@/types/document'

export interface CatalogFace {
  sha256: string
  face: number
  bold: boolean
  italic: boolean
}

export interface CatalogFamily {
  name: string
  zh: string | null
  cjk: boolean
  generic: string | null
  faces: CatalogFace[]
}

export interface FontCatalog {
  families: CatalogFamily[]
  /** 通用族（`serif` / `sans-serif` / `monospace`）→ 批准字体 */
  builtin: Record<string, { name: string; faces: CatalogFace[] }>
}

const POLL_MS = 2000
const EMPTY_CATALOG: FontCatalog = { families: [], builtin: {} }

let catalog: FontCatalog = EMPTY_CATALOG
let started = false
const listeners = new Set<() => void>()

async function load(): Promise<void> {
  try {
    const res = await fetchSourceFontCatalog()
    if (!res.ok) return
    catalog = { families: res.families, builtin: res.builtin }
    for (const l of listeners) l()
    if (res.state === 'scanning') setTimeout(load, POLL_MS)
  } catch {
    /* 没有本机字体服务（浏览器 playground / 旧后端）：下拉就是三个内置选项 */
  }
}

const subscribe = (l: () => void) => {
  listeners.add(l)
  return () => {
    listeners.delete(l)
  }
}

/** 字体清单。第一次用到时才去问（打开文字属性之前不扫字体目录）；问之前是空清单。 */
export function useFontCatalog(): FontCatalog {
  const value = useSyncExternalStore(subscribe, () => catalog)
  if (!started) {
    started = true
    void load()
  }
  return value
}

/** 测试用 */
export function resetFontCatalogForTests(next: FontCatalog = EMPTY_CATALOG): void {
  catalog = next
  started = next !== EMPTY_CATALOG
}

const faceOf = (family: string, f: CatalogFace): SourceFace => ({ family, sha256: f.sha256, face: f.face })

const exactFace = (faces: readonly CatalogFace[], bold: boolean, italic: boolean) =>
  faces.find((f) => f.bold === bold && f.italic === italic)

/** 粗斜对不上时取最接近的：先保粗细，再回常规 */
const closestFace = (faces: readonly CatalogFace[], bold: boolean, italic: boolean): CatalogFace =>
  exactFace(faces, bold, italic) ??
  faces.find((f) => f.bold === bold) ??
  exactFace(faces, false, false) ??
  faces[0]

const primaryOf = (f: CatalogFace, family: string, cjk: SourceFace | undefined): SourceFont => ({
  ...faceOf(family, f),
  ...(f.bold ? { bold: true } : {}),
  ...(f.italic ? { italic: true } : {}),
  ...(cjk ? { cjk } : {}),
})

const setStyle = (o: TextObject, f: CatalogFace) => {
  o.bold = f.bold
  if (f.italic) o.italic = true
  else delete o.italic
}

const familyFacesOf = (c: FontCatalog, sf: SourceFace): { name: string; faces: CatalogFace[] } | undefined =>
  [...c.families, ...Object.values(c.builtin)].find((fam) =>
    fam.faces.some((f) => sameFace({ ...f, family: '' }, sf)),
  )

/** 这张脸是批准字体（英文留在内置、中文选了本机字体） */
export const isBuiltinFace = (c: FontCatalog, sf: SourceFace): boolean =>
  Object.values(c.builtin).some((fam) => fam.faces.some((f) => faceKey(f) === faceKey(sf)))

/** 选了本机字体这一族。自带中文字形的字体不留单独的中文脸（中文就是它自己画的）。 */
export function setLocalFamily(o: TextObject, c: FontCatalog, name: string): void {
  const fam = c.families.find((f) => f.name === name)
  if (!fam || (isSourceFont(o.sourceFont) && o.sourceFont.family === name)) return
  const f = closestFace(fam.faces, o.bold === true, o.italic === true)
  const cjk = !fam.cjk && isSourceFont(o.sourceFont) ? o.sourceFont.cjk : undefined
  o.sourceFont = primaryOf(f, fam.name, cjk)
  setStyle(o, f)
  // 别的机器上没有这个字体时回退到哪一族
  if (fam.generic) o.fontFamily = fam.generic as TextObject['fontFamily']
}

/** 英文选回内置字体：中文格选过本机字体就留着，正文脸换成批准字体；否则 `sourceFont` 整条清掉。 */
export function setBuiltinFamily(o: TextObject, c: FontCatalog, generic: string): boolean {
  const cjk = isSourceFont(o.sourceFont) ? o.sourceFont.cjk : undefined
  const fam = c.builtin[generic]
  if (!cjk || !fam) return false
  const f = closestFace(fam.faces, o.bold === true, o.italic === true)
  o.sourceFont = primaryOf(f, fam.name, cjk)
  o.fontFamily = generic as TextObject['fontFamily']
  return true
}

/** 选中文字体。`null` = 回到自动（批准的中文字体）。 */
export function setLocalCjkFamily(o: TextObject, c: FontCatalog, name: string | null): void {
  const sf = isSourceFont(o.sourceFont) ? o.sourceFont : undefined
  if (name === null) {
    if (!sf) return
    if (isBuiltinFace(c, sf)) delete o.sourceFont
    else delete sf.cjk
    return
  }
  const fam = c.families.find((f) => f.name === name)
  if (!fam) return
  const cjk = faceOf(fam.name, closestFace(fam.faces, false, false))
  if (sf) {
    sf.cjk = cjk
    return
  }
  const builtin = c.builtin[effectiveCanvasFamily(o)]
  if (!builtin) return
  o.sourceFont = primaryOf(closestFace(builtin.faces, o.bold === true, o.italic === true), builtin.name, cjk)
}

/** 目标粗斜在这个对象的字体族里没有对应的脸（按钮该置灰）。清单不认识这张脸时不算：走 ADR 0164 的失效规则。 */
export function styleMissing(o: TextObject, c: FontCatalog, bold: boolean, italic: boolean): boolean {
  if (!isSourceFont(o.sourceFont)) return false
  const fam = familyFacesOf(c, o.sourceFont)
  return !!fam && !exactFace(fam.faces, bold, italic)
}

/** 按 B / I：换成同族里那张脸。返回 false = 清单不认识这张脸，调用方走原来的写入。 */
export function restyleLocalFace(o: TextObject, c: FontCatalog, bold: boolean, italic: boolean): boolean {
  if (!isSourceFont(o.sourceFont)) return false
  const fam = familyFacesOf(c, o.sourceFont)
  if (!fam) return false
  const f = exactFace(fam.faces, bold, italic)
  if (!f) return true // 这一族没有这张脸：不动（按钮本来就是灰的）
  o.sourceFont = primaryOf(f, o.sourceFont.family, o.sourceFont.cjk)
  setStyle(o, f)
  return true
}

/** 下拉里的显示名表（`option_labels` 同一形状）。 */
export const catalogLabels = (c: FontCatalog): Record<string, string> =>
  Object.fromEntries(c.families.flatMap((f) => (f.zh ? [[f.name, f.zh]] : [])))
