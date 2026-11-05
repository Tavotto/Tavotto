/**
 * 这台机器上画布文字能用的字体（ADR 0169，建在 ADR 0164 的本机字体通道上）。
 *
 * 清单来自本机服务 `/api/source-fonts/catalog`，里面只有名字和身份。选中一族 = 往 `TextObject.sourceFont`
 * 写这一族里的一张脸，文档、导出、预览、缺字体的处理全走 ADR 0164 那一条路。
 *
 * 粗斜：`sourceFont` 永远点名**一张**脸，按 B / I 就换成同族里那张（族的归属来自清单，不按名字猜文件）。
 * 这一族没有那张脸时留在最接近的脸上、对象照样记粗斜，画布与导出都模拟（与 Word 同一做法）。
 * 同族其它脸记在 `siblings`，导出与预览的行内粗斜、中文加粗按它换脸，不用再问清单（ADR 0169 修订）。
 *
 * 中文格单独选、英文留在内置字体时，`sourceFont` 的正文脸点名批准字体本身：批准字体也有 sha256，
 * 不用改文档形状（`isBuiltinFace` 让界面继续显示「衬线体」）。
 */
import { useEffect, useSyncExternalStore } from 'react'
import { fetchSourceFontCatalog } from './api'
import { faceKey, isSourceFont, sameFace } from './sourceFont'
import { CANVAS_TEXT_DEFAULT_FAMILY, effectiveCanvasFamily } from './typography'
import type { SiblingFace, SourceFace, SourceFont, TextObject } from '@/types/document'

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
/** 控件挂载时距上次问完超过这么久就再问一次（后端自己也 60 秒节流，装了新字体不必重启应用） */
export const STALE_MS = 60_000
const EMPTY_CATALOG: FontCatalog = { families: [], builtin: {} }

let catalog: FontCatalog = EMPTY_CATALOG
/** 上一次请求结束的时刻（成功或失败都算）；0 = 还没问过 */
let lastDoneAt = 0
let inFlight = false
const listeners = new Set<() => void>()

async function load(): Promise<void> {
  if (inFlight) return
  inFlight = true
  try {
    const res = await fetchSourceFontCatalog()
    if (!res.ok) return
    catalog = { families: res.families, builtin: res.builtin }
    for (const l of listeners) l()
    if (res.state === 'scanning') setTimeout(load, POLL_MS)
  } catch {
    /* 没有本机字体服务（浏览器 playground / 旧后端）：下拉就是三个内置选项 */
  } finally {
    inFlight = false
    lastDoneAt = Date.now()
  }
}

/** 没问过、或距上次问完满 `STALE_MS` 才再问；同一时刻只有一个请求在飞。 */
export function refreshFontCatalogIfStale(now: number = Date.now()): void {
  if (inFlight) return
  if (lastDoneAt !== 0 && now - lastDoneAt < STALE_MS) return
  void load()
}

const subscribe = (l: () => void) => {
  listeners.add(l)
  return () => {
    listeners.delete(l)
  }
}

/** 字体清单。控件挂载时才去问（打开文字属性之前不扫字体目录），隔 `STALE_MS` 以上再挂载会重新问；问之前是空清单。 */
export function useFontCatalog(): FontCatalog {
  const value = useSyncExternalStore(subscribe, () => catalog)
  useEffect(() => {
    refreshFontCatalogIfStale()
  }, [])
  return value
}

/** 测试用 */
export function resetFontCatalogForTests(next: FontCatalog = EMPTY_CATALOG): void {
  catalog = next
  lastDoneAt = next !== EMPTY_CATALOG ? Date.now() : 0
  inFlight = false
}

const siblingsOf = (f: CatalogFace, faces: readonly CatalogFace[]): SiblingFace[] =>
  faces
    .filter((o) => faceKey(o) !== faceKey(f))
    .map((o) => ({
      sha256: o.sha256,
      face: o.face,
      ...(o.bold ? { bold: true } : {}),
      ...(o.italic ? { italic: true } : {}),
    }))

const faceOf = (family: string, f: CatalogFace, faces: readonly CatalogFace[]): SourceFace => {
  const siblings = siblingsOf(f, faces)
  return { family, sha256: f.sha256, face: f.face, ...(siblings.length ? { siblings } : {}) }
}

const exactFace = (faces: readonly CatalogFace[], bold: boolean, italic: boolean) =>
  faces.find((f) => f.bold === bold && f.italic === italic)

/** 粗斜对不上时取最接近的：先保粗细，再回常规 */
const closestFace = (faces: readonly CatalogFace[], bold: boolean, italic: boolean): CatalogFace =>
  exactFace(faces, bold, italic) ??
  faces.find((f) => f.bold === bold) ??
  exactFace(faces, false, false) ??
  faces[0]

const primaryOf = (
  f: CatalogFace,
  family: string,
  faces: readonly CatalogFace[],
  cjk: SourceFace | undefined,
): SourceFont => ({
  ...faceOf(family, f, faces),
  ...(f.bold ? { bold: true } : {}),
  ...(f.italic ? { italic: true } : {}),
  ...(cjk ? { cjk } : {}),
})

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
  o.sourceFont = primaryOf(f, fam.name, fam.faces, cjk)
  // 别的机器上没有这个字体时回退到哪一族
  if (fam.generic) o.fontFamily = fam.generic as TextObject['fontFamily']
}

/** 英文选回内置字体：中文格选过本机字体就留着，正文脸换成批准字体；否则 `sourceFont` 整条清掉。 */
export function setBuiltinFamily(o: TextObject, c: FontCatalog, generic: string): boolean {
  const cjk = isSourceFont(o.sourceFont) ? o.sourceFont.cjk : undefined
  const fam = c.builtin[generic]
  if (!cjk || !fam) return false
  const f = closestFace(fam.faces, o.bold === true, o.italic === true)
  o.sourceFont = primaryOf(f, fam.name, fam.faces, cjk)
  o.fontFamily = generic as TextObject['fontFamily']
  return true
}

/**
 * 只把英文那格恢复成默认：中文脸（`sourceFont.cjk`）保住，正文脸换回默认族的批准字体，`fontFamily` 删掉回到继承。
 * 没有中文脸（或清单里没有默认族）返回 false，调用方走 `resetCanvasText` 整条清掉。
 */
export function resetLocalFamily(o: TextObject, c: FontCatalog): boolean {
  const cjk = isSourceFont(o.sourceFont) ? o.sourceFont.cjk : undefined
  const fam = c.builtin[CANVAS_TEXT_DEFAULT_FAMILY]
  if (!cjk || !fam) return false
  const f = closestFace(fam.faces, o.bold === true, o.italic === true)
  o.sourceFont = primaryOf(f, fam.name, fam.faces, cjk)
  delete o.fontFamily
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
  const cjk = faceOf(fam.name, closestFace(fam.faces, false, false), fam.faces)
  if (sf) {
    sf.cjk = cjk
    return
  }
  const builtin = c.builtin[effectiveCanvasFamily(o)]
  if (!builtin) return
  o.sourceFont = primaryOf(closestFace(builtin.faces, o.bold === true, o.italic === true), builtin.name, builtin.faces, cjk)
}

/** 按 B / I：换成同族里最接近的脸，对象记下要的粗斜（没有的由画布和导出模拟）。返回 false = 清单不认识这张脸，调用方走原来的写入。 */
export function restyleLocalFace(o: TextObject, c: FontCatalog, bold: boolean, italic: boolean): boolean {
  if (!isSourceFont(o.sourceFont)) return false
  const fam = familyFacesOf(c, o.sourceFont)
  if (!fam) return false
  const f = closestFace(fam.faces, bold, italic)
  o.sourceFont = primaryOf(f, o.sourceFont.family, fam.faces, o.sourceFont.cjk)
  o.bold = bold
  if (italic) o.italic = true
  else delete o.italic
  return true
}

const LABELS = new WeakMap<FontCatalog, Record<string, string>>()
/**
 * 下拉里的显示名表（`option_labels` 同一形状）。同一份清单永远是同一个对象：下拉模型的行与撞名表都按它的引用缓存，
 * 每次渲染重造一份，几百个族的行就每次重造（#1762 后符号插入慢了一倍）。
 */
export function catalogLabels(c: FontCatalog): Record<string, string> {
  let hit = LABELS.get(c)
  if (!hit) LABELS.set(c, (hit = Object.fromEntries(c.families.flatMap((f) => (f.zh ? [[f.name, f.zh]] : [])))))
  return hit
}

const LISTS = new WeakMap<FontCatalog, Map<string, { key: string; list: string[] }>>()
/**
 * 名字列表的稳定引用：内容没变就还是上一次那个数组。适配器每次 `fieldOf` 都要现拼选项，
 * 数组换了引用，下游按引用缓存的下拉行就全部失效。`tag` 区分同一份清单上的不同列表。
 */
export function stableNames(c: FontCatalog, tag: string, names: readonly string[]): string[] {
  let byTag = LISTS.get(c)
  if (!byTag) LISTS.set(c, (byTag = new Map()))
  const key = names.join('\u0000')
  const hit = byTag.get(tag)
  if (hit && hit.key === key) return hit.list
  const list = [...names]
  byTag.set(tag, { key, list })
  return list
}
