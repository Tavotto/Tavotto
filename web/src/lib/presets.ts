import { newId } from '@/lib/id'
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

function place(objs: CanvasObject[], label: string): void {
  if (!objs.length) return
  useDocumentStore.getState().commit(label, (d) => {
    d.objects.push(...objs)
  })
  useSelectionStore.getState().set(objs.map((o) => o.id))
  useUiStore.getState().setStatus(`已插入${label.replace(/^插入/, '')}（⌘Z 可撤销）`)
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
  place([shape], `插入${{ triangle: '三角形', diamond: '菱形', polygon: '多边形', brace: '大括号' }[kind as 'triangle' | 'diamond' | 'polygon' | 'brace']}`)
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

export const PRESETS: { id: PresetId; label: string; hint: string }[] = [
  { id: 'reversible', label: '可逆反应箭头', hint: '两条反向箭头（⇌）' },
  { id: 'dimension', label: '尺寸线', hint: '双向箭头 + 两端界线' },
  { id: 'scalebar', label: '比例尺', hint: '粗线 + 长度标注' },
  { id: 'axes', label: '坐标方向', hint: 'x / y 方向角标' },
  { id: 'crystal', label: '晶向箭头', hint: '箭头 + [001] 标注' },
  { id: 'errorbar', label: '误差标注', hint: '工字线 + ± 值' },
  { id: 'magnifier', label: '局部放大框', hint: '虚线框 + 引出线 + 放大框' },
  { id: 'callout', label: '引线标注', hint: '引线 + 文字' },
  { id: 'braceGroup', label: '括号分组', hint: '大括号 + 说明文字' },
]

/** 常用希腊字母 / 数学 / 单位符号：点击即插入一个文字对象 */
export const SYMBOLS = [
  'α', 'β', 'γ', 'Δ', 'δ', 'θ', 'λ', 'μ', 'π', 'σ', 'ω', 'Ω',
  '±', '×', '·', '≈', '≤', '≥', '∝', '∞', '→', '⇌',
  '°', '℃', 'Å', '²', '³', '⁻¹', 'μm', 'nm',
]

export function insertSymbol(sym: string): void {
  const c = dropPoint()
  const t = baseText(c.x - 5, c.y - 3, 10, sym)
  place([t], `插入符号 ${sym}`)
}

export function insertPreset(id: PresetId): void {
  const c = dropPoint()
  const g = newId('g')
  const label = PRESETS.find((p) => p.id === id)?.label ?? id
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
  place(objs, `插入${label}`)
}
