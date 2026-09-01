/**
 * 标注字体这条新能力的**全链路**：文档 → 载荷 → 写回 → 复制粘贴 → 老文档。
 *
 * 单独一个文件是因为它跨了五个模块：控件那边测得再全，只要其中一段忘了把
 * `fontFamily` 带上，用户看到的就是「界面上改了，导出的图没变」。
 */
import { describe, expect, it } from 'vitest'

import { toExportObjects } from './exportPayload'
import { collectPanelAnnotations } from './writeBackAnnotations'
import { CANVAS_TEXT_DEFAULT_FAMILY, effectiveCanvasFamily } from './typography'
import { migrateToProject, type PanelObject, type TextObject } from '@/types/document'

const text = (over: Partial<TextObject> = {}): TextObject => ({
  id: 't1',
  type: 'text',
  text: 'H2O',
  sizePt: 9,
  bold: false,
  color: '#000000',
  align: 'left',
  x: 2,
  y: 2,
  w: 20,
  h: 8,
  ...over,
})

const panel = (over: Partial<PanelObject> = {}): PanelObject => ({
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 0,
  y: 0,
  w: 80,
  h: 60,
  ...over,
})

describe('导出载荷', () => {
  it('设过就发，没设过就不发——老文档**发出去的字节**不变', () => {
    // 主语是**序列化之后的载荷**，不是那个 JS 对象：`{ font_family: undefined }`
    // 里键是在的，`JSON.stringify` 才是后端真正收到的东西。量错主语的话这条
    // 断言会在一个与后端无关的性质上红。
    const wire = (o: TextObject) => JSON.stringify(toExportObjects([o])[0])
    expect(wire(text())).not.toContain('font_family')

    const styled = JSON.parse(wire(text({ fontFamily: 'monospace' })))
    expect(styled).toMatchObject({ type: 'text', font_family: 'monospace' })
  })
})

describe('写回原图带标注', () => {
  it('字体族跟着标注一起写回；长度类字段缩放，族不缩放', () => {
    // 面板在画布上被放大到原生尺寸的两倍：字号跟着缩回去，族原样带走
    const p = panel({ w: 160, h: 120 })
    const out = collectPanelAnnotations([p], [text({ sizePt: 10, fontFamily: 'sans-serif' })])
    const objs = out.get('p1')!.objects
    expect(objs).toHaveLength(1)
    const t = objs[0]
    expect(t.type).toBe('text')
    if (t.type !== 'text') throw new Error('unreachable')
    expect(t.font_family).toBe('sans-serif')
    expect(t.size_pt).toBeCloseTo(5, 6)
  })
})

describe('老文档', () => {
  it('没有 fontFamily 的文档照常打开，且生效族就是默认族（不写一个值进去）', () => {
    const legacy = {
      schema: 2,
      name: 'fig',
      page: { w: 80, h: 60 },
      objects: [{ ...text() }],
      guides: [],
    }
    const project = migrateToProject(legacy)!
    const o = project.canvases[0].objects[0] as TextObject
    expect('fontFamily' in o).toBe(false)
    expect(effectiveCanvasFamily(o)).toBe(CANVAS_TEXT_DEFAULT_FAMILY)
  })
})
