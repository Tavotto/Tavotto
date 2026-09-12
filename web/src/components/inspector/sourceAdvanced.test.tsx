/**
 * 「源文件与高级」折叠区（审计 T32 的后半段）。
 *
 * 恢复动作（「恢复此元素 · n 项」「恢复整张图 · m 项」）与「修改保存在哪里」
 * 2026-09-12 起住在身份头的脚本行里，由 `sourceRow.test.tsx` 看护。这里剩下的：
 *   1. 折叠区里**只有**会动磁盘的那一组（「原始文件」：写回 / 历史 / 同步），
 *      没有恢复按钮——「只改文档」与「会动磁盘」的边界现在是两个位置，不是两个组头；
 *   2. 精确名词（gid）不再常驻，收在「技术详情」里。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { engineRender, type EditableField, type Manifest, type ManifestElement } from '@/lib/api'
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
  engineRender: vi.fn(),
}))

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField

const titleEl: ManifestElement = {
  gid: 'axes_0.title',
  role: 'title',
  label: '标题 “A”',
  bbox: [0.1, 0.0, 0.8, 0.1],
  draggable: false,
  editable: [
    f('text', 'text', 'A'),
    f('fontsize', 'number', 9, { min: 4, max: 40, step: 0.5 }),
    f('color', 'color', '#000000'),
  ],
}

const manifest: Manifest = {
  stem: 'A.pdf',
  size_mm: [100, 80],
  elements: [
    { gid: 'figure', role: 'figure', label: '整张图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    titleEl,
  ],
}

const panel = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 100,
    h: 80,
    fileId: 'A.pdf',
    fileKind: 'pdf',
    nativeW: 100,
    nativeH: 80,
    script: 'figs/fig.py',
    overrides: [
      { gid: 'axes_0.title', prop: 'fontsize', value: 11 },
      { gid: 'axes_0.title', prop: 'color', value: '#ff0000' },
      { gid: 'axes_0.xlabel', prop: 'fontsize', value: 8 },
    ],
  }) as unknown as PanelObject

let root: Root
let host: HTMLDivElement

function Harness() {
  const p = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return (
    <TooltipProvider>
      <ElementInspector panel={p} />
    </TooltipProvider>
  )
}

async function mount() {
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.title'] })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<Harness />)
  })
  // 「源文件与高级」默认收起：先展开
  await act(async () => {
    buttons().find((b) => b.textContent?.includes('源文件与高级'))!.click()
  })
}

const buttons = () => Array.from(host.querySelectorAll('button'))
const buttonByText = (text: string) => buttons().find((b) => b.textContent?.trim().startsWith(text))

beforeEach(async () => {
  localStorage.clear()
  document.body.innerHTML = ''
  // 交互引发的重渲染回同一份 manifest：检查器不会因为「等引擎」而整屏换掉
  vi.mocked(engineRender).mockResolvedValue({ rev: 2, manifest, svg: '<svg/>', warnings: [] } as never)
  useInspectorPrefs.setState({ moreOpen: {}, advancedOpen: {} })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_source_advanced')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panel())
  })
  // 现在这组 overrides 与清空之后那组，两个变体都算「已渲染」
  for (const variant of [panel(), { ...panel(), overrides: [] }]) {
    const key = renderKeyOf(variant)
    useRenderStore.getState().patch(key, {
      fileId: 'A.pdf',
      manifest,
      svg: '<svg/>',
      rev: 1,
      status: 'ready',
      lastPatches: '[]',
    })
    useRenderStore.setState((s) => ({ latest: { ...s.latest, 'A.pdf': key } }))
  }
  useDocumentStore.setState({ past: [], future: [] })
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  document.body.innerHTML = ''
})

describe('源文件与高级：只剩会动磁盘的那一组', () => {
  it('折叠区里没有恢复按钮；「原始文件」组头之下是写回按钮', async () => {
    await mount()
    const fold = host.querySelector('[data-source-advanced]')!
    const names = Array.from(fold.querySelectorAll('button')).map((b) => b.textContent?.trim() ?? '')
    expect(names.some((t) => t.startsWith('恢复'))).toBe(false)
    const heads = Array.from(fold.querySelectorAll('p')).map((p) => p.textContent?.trim())
    expect(heads).toContain('原始文件')
    expect(heads).not.toContain('恢复')
    const fileHead = Array.from(fold.querySelectorAll('p')).find((p) => p.textContent === '原始文件')!
    const writeBack = buttonByText('写回原始文件')!
    expect(fileHead.compareDocumentPosition(writeBack) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('gid 不常驻：收在「技术详情」里，默认收起', async () => {
    await mount()
    const details = host.querySelector('details')
    expect(details, '没有技术详情折叠').toBeTruthy()
    expect(details!.open).toBe(false)
    expect(details!.textContent).toContain('axes_0.title')
    // 折叠之外没有第二处 gid
    const outside = Array.from(host.querySelectorAll('p')).filter(
      (p) => p.textContent?.includes('axes_0.title') && !details!.contains(p),
    )
    expect(outside).toEqual([])
  })
})
