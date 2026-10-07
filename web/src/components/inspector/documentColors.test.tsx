/**
 * 「文档颜色」只读文档里写下来、会画出来的颜色（`documentColorsOf`）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import type { Manifest } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { renderKeyOf } from '@/store/renderStore'
import { emptyProject, type PageSetup, type PanelObject, type TextObject } from '@/types/document'
import { normalizeHex } from '../ui/colorPalette'
import { colorOverrideKeysOf, documentColorsOf, useDocumentColors } from './documentColors'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const note = (over: Partial<TextObject> = {}): TextObject => ({
  id: 't1',
  type: 'text',
  text: '标注',
  sizePt: 9,
  bold: false,
  color: '#000000',
  align: 'left',
  x: 10,
  y: 10,
  w: 20,
  h: 8,
  ...over,
})

describe('documentColorsOf', () => {
  it('文字框的描边色也是文档颜色——只用在描边上的颜色不能漏（Codex #829）', () => {
    const colors = documentColorsOf({
      objects: [note({ bg: '#ffeedd', borderColor: '#13579b', borderPt: 0.5 })],
      page: { w: 150, h: 100, transparent: true },
    })
    expect(colors).toContain('#13579b')
    expect(colors).toContain('#ffeedd')
    expect(colors).toContain('#000000')
  })

  it('没有描边（borderColor 缺席 / null）不凭空添色', () => {
    const colors = documentColorsOf({
      objects: [note({ borderColor: null })],
      page: { w: 150, h: 100, transparent: true },
    })
    expect(colors).toEqual(['#000000'])
  })
})

/* ---------------- 图内 override：只收颜色属性（Codex #829 P2） ---------------- */

const panelWith = (overrides: PanelObject['overrides'], id = 'p1'): PanelObject =>
  ({
    id,
    type: 'panel',
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    x: 0,
    y: 0,
    w: 80,
    h: 60,
    overrides,
  }) as unknown as PanelObject

/** 与 engine/manifest.py 同形：标题的 `text` 是文本字段、`color` 是颜色字段 */
const manifest = {
  rev: 1,
  size_mm: [80, 60],
  elements: [
    {
      gid: 'axes_0.title',
      role: 'title',
      editable: [
        { prop: 'text', type: 'text', value: 'Kinetics' },
        { prop: 'color', type: 'color', value: '#000000' },
      ],
    },
  ],
} as unknown as Manifest

const page = { w: 150, h: 100, transparent: true } as PageSetup

describe('图内 override 只收颜色属性', () => {
  const overrides = [
    { gid: 'axes_0.title', prop: 'text', value: 'abc' },
    { gid: 'axes_0.title', prop: 'color', value: '#FF0000' },
  ]

  it('标题文字改成 "abc" 不是 #aabbcc；颜色字段的 override 照收', () => {
    const p = panelWith(overrides)
    // 先验落点：那条文字确实长得像色号
    expect(normalizeHex('abc')).toBe('#aabbcc')
    const keys = colorOverrideKeysOf([p], { byKey: { [renderKeyOf(p)]: { manifest } }, latest: {} })
    const set = new Set(keys.split('\n'))
    const colors = documentColorsOf({ objects: [p], page }, (q, gid, prop) =>
      set.has(`${q.id}\u0000${gid}\u0000${prop}`),
    )
    expect(colors).toEqual(['#ff0000'])
  })

  it('判据只认 manifest 的字段类型：manifest 缺席时一条都不猜', () => {
    const p = panelWith(overrides)
    expect(colorOverrideKeysOf([p], { byKey: {}, latest: {} })).toBe('')
  })

  it('这一版还没画出来时用同文件上一版的 manifest', () => {
    const p = panelWith(overrides)
    const keys = colorOverrideKeysOf([p], { byKey: { prev: { manifest } }, latest: { 'Fig1.pdf': 'prev' } })
    expect(keys.split('\n')).toEqual([`p1\u0000axes_0.title\u0000color`])
  })
})

/* ---------------- 一份排版的全部画布都算（Codex #829 P2） ---------------- */

describe('useDocumentColors 汇总整份排版', () => {
  let root: Root
  let host: HTMLDivElement
  let seen: string[] = []
  function Probe() {
    seen = useDocumentColors()
    return null
  }
  beforeEach(() => {
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    host.remove()
  })

  it('另一张画布上的颜色也在；激活画布以活跃 doc 为准，不读它的旧快照', async () => {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_doc_colors')
    useDocumentStore.getState().commit(literal('加文字'), (d) => {
      d.objects.push(note({ id: 'a', color: '#111111' }))
    })
    await act(async () => useDocumentStore.getState().addCanvas())
    // 现在激活的是新画布：在它上面放一个不同的颜色
    useDocumentStore.getState().commit(literal('加文字'), (d) => {
      d.objects.push(note({ id: 'b', color: '#222222' }))
    })
    const s = useDocumentStore.getState()
    expect(s.canvases.length).toBe(2)
    // 激活画布的快照是旧的（没有 #222222）：颜色必须来自活跃 doc
    expect(JSON.stringify(s.canvases.find((c) => c.id === s.activeCanvasId))).not.toContain('#222222')
    await act(async () => root.render(<Probe />))
    expect(seen).toContain('#111111')
    expect(seen).toContain('#222222')
  })
})
