/**
 * pair / rect 属性的**每一格都要有自己的可访问名**。
 *
 * 缺名字的后果不是"不好听"：辅助技术里两个框读出来都是"编辑框 80"
 * "编辑框 57.6"，用户不知道哪个是宽哪个是高。axe 的 `label` 规则按
 * **critical** 报——PR #214 新加的问题面板 a11y 用例在 Windows/webkit 上就是
 * 停在快速编辑、右栏摆着「图幅」时抓到这一对的。
 *
 * 单测钉在这里而不是只靠那条 e2e：e2e 要**恰好走到**那一屏才量得到，
 * 而这条缺陷属于控件本身，任何 pair/rect 属性都带着它。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { EditableField, Manifest, ManifestElement } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useInspectorPrefs } from '@/store/inspectorPrefs'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { ElementInspector } from './ElementInspector'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: vi.fn().mockResolvedValue({ rev: 2, manifest: null, svg: '', warnings: [] }),
}))

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField

const figureEl: ManifestElement = {
  gid: 'figure',
  role: 'figure',
  label: '整张图',
  bbox: [0, 0, 1, 1],
  draggable: false,
  // 真实形状取自 `engine/manifest.py`：figure 的 size_mm 是带 unit 的 pair
  editable: [f('size_mm', 'pair', [80, 57.6], { unit: 'mm' })],
}

const axesEl: ManifestElement = {
  gid: 'axes_0',
  role: 'axes',
  label: '子图 1',
  bbox: [0.1, 0.1, 0.8, 0.8],
  draggable: false,
  // position 是 rect（x/y/宽/高），xlim 是无单位的 pair（最小/最大）
  editable: [
    f('position', 'rect', [0.125, 0.11, 0.775, 0.77]),
    f('xlim', 'pair', [0, 10]),
  ],
}

const manifest: Manifest = {
  stem: 'A',
  size_mm: [80, 57.6],
  elements: [figureEl, axesEl],
}

const panelOf = (id: string, fileId: string): PanelObject =>
  ({
    id,
    type: 'panel',
    x: 0,
    y: 0,
    w: 80,
    h: 57.6,
    fileId,
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 57.6,
    script: 'fig.py',
    overrides: [],
  }) as unknown as PanelObject

let root: Root
let host: HTMLDivElement

function Harness({ id }: { id: string }) {
  const panel = useDocumentStore((s) => s.doc.objects.find((o) => o.id === id)) as PanelObject
  return (
    <TooltipProvider>
      <ElementInspector panel={panel} />
    </TooltipProvider>
  )
}

async function mount(gid: string) {
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: [gid] })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<Harness id="p1" />)
  })
}

/** 只看数字框：文本类控件另有自己的名字来源 */
const numberInputs = () =>
  Array.from(host.querySelectorAll('input')).filter((i) => i.type !== 'checkbox')

const names = () => numberInputs().map((i) => i.getAttribute('aria-label') ?? '')

beforeEach(async () => {
  localStorage.clear()
  document.body.innerHTML = ''
  useInspectorPrefs.setState({ moreOpen: {}, advancedOpen: {} })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_axis')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf('p1', 'A.pdf'))
  })
  const key = renderKeyOf(panelOf('p1', 'A.pdf'))
  useRenderStore.getState().patch(key, {
    fileId: 'A.pdf',
    manifest,
    svg: '<svg/>',
    rev: 1,
    status: 'ready',
    lastPatches: '[]',
  })
  useRenderStore.setState((s) => ({ latest: { ...s.latest, ['A.pdf']: key } }))
  useDocumentStore.setState({ past: [], future: [] })
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('pair / rect 的每一格都有自己的可访问名', () => {
  it('图幅：两个框分别是「宽」和「高」，都带单位', async () => {
    await mount('figure')
    const got = names()
    expect(got.length, '没渲染出那两个数字框，这条用例什么都量不到').toBe(2)
    for (const n of got) expect(n, '数字框没有可访问名（axe label，critical）').not.toBe('')
    // **两个名字必须不同。** 都挂同一个「图幅」也能骗过"非空"这条判据，
    // 而那样辅助技术里依旧分不出宽和高
    expect(new Set(got).size, '两个框叫同一个名字，等于没区分').toBe(2)
    expect(got[0]).toContain('宽')
    expect(got[1]).toContain('高')
    for (const n of got) expect(n, '带单位的属性要把单位说出来').toContain('(mm)')
  })

  it.each(['figure', 'axes_0'])(
    '%s：属性栏里没有一个数字框是无名的（axe `label` 查的就是这条）',
    async (gid) => {
      await mount(gid)
      const got = names()
      expect(got.length, '一个数字框都没渲染出来，这条用例什么都量不到').toBeGreaterThan(0)
      for (const n of got) expect(n, '数字框没有可访问名（axe label，critical）').not.toBe('')
      // 无单位的属性不许拼出一个空括号
      for (const n of got) expect(n).not.toContain('()')
    },
  )

  it('xlim（无单位 pair）：最小 / 最大', async () => {
    await mount('axes_0')
    const got = names()
    expect(got.some((n) => n.includes('最小')), `没有「最小」：${got.join(' | ')}`).toBe(true)
    expect(got.some((n) => n.includes('最大')), `没有「最大」：${got.join(' | ')}`).toBe(true)
  })
})
