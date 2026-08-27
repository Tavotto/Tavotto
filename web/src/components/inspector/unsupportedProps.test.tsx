/**
 * guard 的 reason 必须走到眼睛（#76）。
 *
 * PR #75 给多宿主色条加了 guard：不宣称 `orientation`，并在 manifest 上给出稳定
 * reason `multi_host_colorbar`。但前端完全不认识那个字段——`ManifestElement` 没
 * 声明它，inspector 只按 `editable` 建 UI，于是**方向开关就是消失了，没有任何
 * 解释**。这比「点了把排版弄坏」好（那是 guard 挡掉的那件事），但 guard 的完整
 * 形态是 `detect → guard/hide → reason → issue → 修`，reason 那一环当时只到
 * manifest。
 *
 * 三条判据：原因真的渲染出来了、按 code 翻译而不是透传英文、不认识的 code 有
 * 兜底而不是把 code 本身摆到界面上。
 */
import { literal, setLocale } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Manifest, ManifestElement } from '@/lib/api'
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

const cbarEl = (unsupported: ManifestElement['unsupported_props']): ManifestElement => ({
  gid: 'axes_1.colorbar',
  role: 'colorbar',
  label: '色条',
  bbox: [0.85, 0.1, 0.04, 0.8],
  draggable: false,
  editable: [{ prop: 'label', type: 'text', value: '强度' }],
  unsupported_props: unsupported,
})

const manifestOf = (el: ManifestElement): Manifest => ({
  stem: 'A',
  size_mm: [100, 80],
  elements: [
    { gid: 'figure', role: 'figure', label: '整张图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    el,
  ],
})

const panelOf = (): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 0, y: 0, w: 100, h: 80,
    fileId: 'A.pdf', fileKind: 'pdf', nativeW: 100, nativeH: 80,
    script: 'fig.py', overrides: [],
  }) as unknown as PanelObject

let root: Root
let host: HTMLDivElement

function Harness() {
  const panel = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return (
    <TooltipProvider>
      <ElementInspector panel={panel} />
    </TooltipProvider>
  )
}

async function mount(el: ManifestElement) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_unsupported')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf())
  })
  const key = renderKeyOf(panelOf())
  useRenderStore.getState().patch(key, {
    fileId: 'A.pdf', manifest: manifestOf(el), svg: '<svg/>', rev: 1,
    status: 'ready', lastPatches: '[]',
  })
  useRenderStore.setState((s) => ({ latest: { ...s.latest, 'A.pdf': key } }))
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: [el.gid] })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<Harness />)
  })
}

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
  useInspectorPrefs.setState({ moreOpen: {}, advancedOpen: {} })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  await setLocale('zh-CN')
})

describe('unsupported_props 的界面出口', () => {
  it('多宿主色条：方向那一项带着原因出现，而不是凭空消失', async () => {
    await mount(cbarEl([{ prop: 'orientation', reason: 'multi_host_colorbar', detail: { hosts: 2 } }]))
    const row = host.querySelector('[data-unsupported-prop="orientation"]')
    expect(row, 'orientation 那一项整个不见了——正是 #76 的现场').toBeTruthy()
    expect(row!.textContent).toContain('2')          // detail 进了插值
    expect(row!.textContent).toContain('色条')        // 按 code 翻出来的中文
    expect(row!.textContent).not.toContain('multi_host_colorbar')
  })

  it('换成英文：同一条 reason 出英文，不是透传 code', async () => {
    await setLocale('en-US')
    await mount(cbarEl([{ prop: 'orientation', reason: 'multi_host_colorbar', detail: { hosts: 3 } }]))
    const row = host.querySelector('[data-unsupported-prop="orientation"]')!
    expect(row.textContent).toContain('3')
    expect(row.textContent?.toLowerCase()).toContain('colorbar')
    expect(row.textContent).not.toContain('multi_host_colorbar')
  })

  it('不认识的 reason code 走通用兜底，不把 code 摆到界面上', async () => {
    await mount(cbarEl([{ prop: 'orientation', reason: 'some_future_reason_code' }]))
    const row = host.querySelector('[data-unsupported-prop="orientation"]')!
    expect(row.textContent).not.toContain('some_future_reason_code')
    expect(row.textContent?.trim().length).toBeGreaterThan(4)
  })

  it('没有 unsupported_props 时什么都不多出来', async () => {
    await mount(cbarEl(undefined))
    expect(host.querySelector('[data-unsupported-prop]')).toBeNull()
  })
})
