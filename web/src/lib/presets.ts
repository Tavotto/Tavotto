import { newId } from '@/lib/id'
import { msg, t, type UiMessage } from '@/i18n'
import { modKey } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { clientToMm, useViewportStore } from '@/store/viewportStore'
import type {
  ArrowObject,
  CanvasObject,
  ShapeKind,
  ShapeObject,
  TextObject,
} from '@/types/document'

/**
 * 科研预设：全部是既有对象类型（arrow/shape/text）的参数组合，成组落到
 * 视口中心附近。没有新的导出路径——矢量保真由既有导出器天然保证。
 */

/** 视口中心的文档坐标；视口未就绪时退回页面中心 */
function dropPoint(): { x: number; y: number } {
  const vp = useViewportStore.getState()
  if (vp.viewW && vp.viewH) {
    return clientToMm(vp.originX + vp.viewW / 2, vp.originY + vp.viewH / 2)
  }
  const page = useDocumentStore.getState().doc.page
  return { x: page.w / 2, y: page.h / 2 }
}

/**
 * 落一组对象。**历史标签与 toast 各自成条**——以前 toast 是拿历史标签正则
 * 掐掉「插入」二字拼出来的，换了语言那条正则立刻失效。
 */
function place(objs: CanvasObject[], label: UiMessage, name: string): void {
  if (!objs.length) return
  useDocumentStore.getState().commit(label, (d) => {
    d.objects.push(...objs)
  })
  useSelectionStore.getState().set(objs.map((o) => o.id))
  useUiStore
    .getState()
    .setStatus(msg('status.inserted', { name, undo: modKey('Z') }, 'workspace'))
}

const baseArrow = (x: number, y: number, w: number, h: number): ArrowObject => ({
  id: newId('a'),
  type: 'arrow',
  x, y, w, h,
  start: { rx: 0, ry: 0.5 },
  end: { rx: 1, ry: 0.5 },
  strokePt: 1,
  color: '#1B1B18',
  head: 'end',
  headStart: 'none',
  headEnd: 'triangle',
})

const baseText = (x: number, y: number, w: number, text: string, sizePt = 9): TextObject => ({
  id: newId('t'),
  type: 'text',
  x, y, w, h: 6,
  text,
  sizePt,
  bold: false,
  color: '#1B1B18',
  align: 'center',
})

const baseShape = (kind: ShapeKind, x: number, y: number, w: number, h: number): ShapeObject => ({
  id: newId('s'),
  type: 'shape',
  shape: kind,
  x, y, w, h,
  strokePt: 1,
  color: '#1B1B18',
  fill: null,
})

/** 新形状：三角形 / 菱形 / 多边形 / 大括号（画布中心落一个默认尺寸的实例） */
export function insertShape(kind: ShapeKind): void {
  const c = dropPoint()
  const isBrace = kind === 'brace'
  const w = isBrace ? 6 : 20
  const h = isBrace ? 24 : 16
  const shape = baseShape(kind, c.x - w / 2, c.y - h / 2, w, h)
  if (kind === 'polygon') shape.sides = 6
  const name = t(`shape.${kind}`)
  place([shape], msg('history.insertShape', { name }, 'workspace'), name)
}

export type PresetId =
  | 'reversible'
  | 'dimension'
  | 'scalebar'
  | 'axes'
  | 'crystal'
  | 'errorbar'
  | 'magnifier'
  | 'callout'
  | 'braceGroup'

/** 预设清单只留 id；名称与说明按 id 查 `dialogs:presets.items.<id>` */
export const PRESET_IDS: PresetId[] = [
  'reversible',
  'dimension',
  'scalebar',
  'axes',
  'crystal',
  'errorbar',
  'magnifier',
  'callout',
  'braceGroup',
]

export const presetLabel = (id: PresetId) => t(`presets.items.${id}.label`, { ns: 'dialogs' })
export const presetHint = (id: PresetId) => t(`presets.items.${id}.hint`, { ns: 'dialogs' })

/** 常用希腊字母 / 数学 / 单位符号：点击即插入一个文字对象 */
export const SYMBOLS = [
  'α', 'β', 'γ', 'Δ', 'δ', 'θ', 'λ', 'μ', 'π', 'σ', 'ω', 'Ω',
  '±', '×', '·', '≈', '≤', '≥', '∝', '∞', '→', '⇌',
  '°', '℃', 'Å', '²', '³', '⁻¹', 'μm', 'nm',
]

export function insertSymbol(sym: string): void {
  const c = dropPoint()
  const obj = baseText(c.x - 5, c.y - 3, 10, sym)
  place([obj], msg('history.insertSymbol', { symbol: sym }, 'workspace'), sym)
}

export function insertPreset(id: PresetId): void {
  const c = dropPoint()
  const g = newId('g')
  const name = presetLabel(id)
  const objs: CanvasObject[] = []
  const grouped = <T extends CanvasObject>(o: T): T => ({ ...o, groupId: g })

  switch (id) {
    case 'reversible': {
      const top = baseArrow(c.x - 12, c.y - 3, 24, 3)
      const bottom = baseArrow(c.x - 12, c.y, 24, 3)
      bottom.start = { rx: 1, ry: 0.5 }
      bottom.end = { rx: 0, ry: 0.5 }
      objs.push(grouped(top), grouped(bottom))
      break
    }
    case 'dimension': {
      const a = baseArrow(c.x - 15, c.y - 2, 30, 4)
      a.headStart = 'triangle'
      a.headEnd = 'triangle'
      a.head = 'both'
      // 竖界线：line 画的是盒内水平中线，转 90° 得到以盒中心为轴的竖线
      const capL = baseShape('line', c.x - 18, c.y - 0.25, 6, 0.5)
      const capR = baseShape('line', c.x + 12, c.y - 0.25, 6, 0.5)
      capL.rotationDeg = 90
      capR.rotationDeg = 90
      objs.push(grouped(a), grouped(capL), grouped(capR))
      break
    }
    case 'scalebar': {
      const bar = baseShape('line', c.x - 10, c.y - 1, 20, 2)
      bar.strokePt = 2.5
      const t = baseText(c.x - 10, c.y + 1.5, 20, '10 μm')
      objs.push(grouped(bar), grouped(t))
      break
    }
    case 'axes': {
      const xa = baseArrow(c.x, c.y - 1.5, 14, 3)
      const ya = baseArrow(c.x - 1.5, c.y - 14, 3, 14)
      ya.start = { rx: 0.5, ry: 1 }
      ya.end = { rx: 0.5, ry: 0 }
      const tx = baseText(c.x + 14.5, c.y - 3, 6, 'x')
      const ty = baseText(c.x - 3, c.y - 19, 6, 'y')
      tx.italic = true
      ty.italic = true
      objs.push(grouped(xa), grouped(ya), grouped(tx), grouped(ty))
      break
    }
    case 'crystal': {
      const a = baseArrow(c.x - 8, c.y - 1.5, 16, 3)
      const t = baseText(c.x - 8, c.y + 2, 16, '[001]')
      objs.push(grouped(a), grouped(t))
      break
    }
    case 'errorbar': {
      const a = baseArrow(c.x - 1.5, c.y - 8, 3, 16)
      a.start = { rx: 0.5, ry: 0 }
      a.end = { rx: 0.5, ry: 1 }
      a.headStart = 'bar'
      a.headEnd = 'bar'
      a.head = 'both'
      const t = baseText(c.x + 2.5, c.y - 3, 12, '±0.1')
      t.align = 'left'
      objs.push(grouped(a), grouped(t))
      break
    }
    case 'magnifier': {
      const small = baseShape('rect', c.x - 20, c.y - 6, 10, 8)
      small.dash = 'dashed'
      const line = baseShape('line', c.x - 10, c.y - 2, 8, 1)
      line.dash = 'dashed'
      const big = baseShape('rect', c.x - 2, c.y - 10, 22, 18)
      objs.push(grouped(small), grouped(line), grouped(big))
      break
    }
    case 'callout': {
      const l = baseArrow(c.x - 10, c.y, 12, 8)
      l.headEnd = 'none'
      l.headStart = 'none'
      l.head = 'none'
      l.start = { rx: 0, ry: 1 }
      l.end = { rx: 1, ry: 0 }
      const t = baseText(c.x + 2, c.y - 8.5, 20, '标注')
      t.align = 'left'
      objs.push(grouped(l), grouped(t))
      break
    }
    case 'braceGroup': {
      const b = baseShape('brace', c.x - 12, c.y - 12, 5, 24)
      const t = baseText(c.x - 12 - 18, c.y - 3, 16, '分组')
      t.align = 'right'
      objs.push(grouped(b), grouped(t))
      break
    }
  }
  place(objs, msg('history.insertPreset', { name }, 'workspace'), name)
}
