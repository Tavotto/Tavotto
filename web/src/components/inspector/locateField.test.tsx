/**
 * 问题面板「定位」的落点要在真的属性页里找得到、看得见（2026-10-23 用户反馈）：
 *
 *   1. 字段收在「更多」里（图例的「标题字号」）：定位要把那一折打开，焦点落到那个字段上——
 *      此前只选中了图例、焦点哪儿也没落，用户顺手改了首屏的字号（管图例项、不管标题），提醒还在；
 *   2. 从一个元素定位到另一个文字元素的「字号」：旧属性页（图例）也有一个 `fontsize`，此前按
 *      `data-prop` 取第一个，焦点先落到图例的字号上，新页一挂载又被「内容」框选中即聚焦抢走——
 *      用户看到的是定位到了「内容」。落点要认主人（`data-inspector-gid`），等新页挂上再落；
 *   3. 落点那一行亮一下（`data-locate-flash`），不是只有输入框自己的焦点边。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { EditableField, Manifest, ManifestElement } from '@/lib/api'
import { focusObject } from '@/lib/issueFocus'
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

const LEGEND = 'axes_0.legend'
const TITLE = 'axes_0.legend.title'

/** 字段形状照引擎 `_legend_fields` / `_text_fields`（标题字号在「样式」组，属于「更多」） */
const legendEl: ManifestElement = {
  gid: LEGEND,
  role: 'legend',
  label: '图例',
  bbox: [0.1, 0.6, 0.3, 0.3],
  draggable: true,
  editable: [
    f('loc', 'enum', 'best', { options: ['best', 'upper right', 'lower left'] }),
    f('fontsize', 'number', 7, { min: 3, max: 24, step: 0.5, unit: 'pt' }),
    f('frameon', 'bool', true),
    f('visible', 'bool', true),
    f('title', 'text', 'Series', { group: '样式' }),
    f('title_fontsize', 'number', 4.5, { min: 3, max: 24, step: 0.5, unit: 'pt', group: '样式' }),
  ],
}

const titleEl: ManifestElement = {
  gid: TITLE,
  role: 'legend_text',
  label: '图例标题 “Series”',
  bbox: [0.12, 0.62, 0.1, 0.04],
  draggable: false,
  editable: [
    f('text', 'text', 'Series'),
    f('fontfamily', 'enum', 'sans-serif', { options: ['sans-serif', 'serif'] }),
    f('fontsize', 'number', 4.5, { min: 1, max: 72, step: 0.5, unit: 'pt' }),
    f('weight', 'enum', 'normal', { options: ['normal', 'bold'] }),
    f('style', 'enum', 'normal', { options: ['normal', 'italic'] }),
    f('color', 'color', '#000000'),
  ],
}

const manifest: Manifest = {
  stem: 'A.pdf',
  size_mm: [100, 80],
  elements: [
    { gid: 'figure', role: 'figure', label: '整张图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    legendEl,
    titleEl,
    // 有图例项时图例页首屏才有图例卡的「字号」（`data-prop="fontsize"`）——那正是会被认错的同名字段
    { ...titleEl, gid: 'axes_0.legend.texts_0', label: '图例项 “sin”', editable: titleEl.editable.map((x) => (x.prop === 'text' ? { ...x, value: 'sin' } : x.prop === 'fontsize' ? { ...x, value: 7 } : x)) },
  ],
}

const panel: PanelObject = {
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
  script: 'fig.py',
  overrides: [],
} as unknown as PanelObject

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

async function mount(gid: string) {
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: [gid] })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<Harness />)
  })
}

/** 让 `focusField` 的逐帧重试跑完（每帧之间让 React 提交一次） */
async function settleFrames(n = 6) {
  for (let i = 0; i < n; i++) {
    await act(async () => {
      await new Promise((r) => requestAnimationFrame(() => r(null)))
    })
  }
}

const ref = (gid: string) => ({
  documentId: 'd_locate',
  canvasId: useDocumentStore.getState().activeCanvasId,
  objectId: 'p1',
  gid,
})

const fieldHost = (prop: string, gid: string) =>
  Array.from(host.querySelectorAll<HTMLElement>(`[data-prop="${prop}"]`)).find(
    (h) => (h.getAttribute('data-gid') ?? h.closest('[data-inspector-gid]')?.getAttribute('data-inspector-gid')) === gid,
  )

beforeEach(async () => {
  localStorage.clear()
  document.body.innerHTML = ''
  useInspectorPrefs.setState({ moreOpen: {}, advancedOpen: {}, foldOpen: {} })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  useUiStore.setState({ fieldLocate: null, issueHighlight: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_locate')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(structuredClone(panel))
  })
  const key = renderKeyOf(panel)
  useRenderStore.getState().patch(key, {
    fileId: 'A.pdf',
    manifest,
    svg: '<svg/>',
    rev: 1,
    status: 'ready',
    lastPatches: '[]',
  })
  useRenderStore.setState((s) => ({ latest: { ...s.latest, 'A.pdf': key } }))
  useDocumentStore.setState({ past: [], future: [] })
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  document.body.innerHTML = ''
})

describe('定位落点在真的属性页里', () => {
  it('收在「更多」里的字段：定位打开那一折，焦点落到「标题字号」上，那一行亮起', async () => {
    await mount(LEGEND)
    expect(fieldHost('title_fontsize', LEGEND)).toBeUndefined()
    await act(async () => {
      focusObject(ref(LEGEND), 'title_fontsize')
    })
    await settleFrames()
    const row = fieldHost('title_fontsize', LEGEND)
    expect(row).toBeDefined()
    expect(row!.contains(document.activeElement)).toBe(true)
    expect(row!.hasAttribute('data-locate-flash')).toBe(true)
    expect(useUiStore.getState().fieldLocate).toBeNull()
  })

  it('换到文字元素定位「字号」：不落到上一个元素的同名字段，最后停在标题的「字号」上、不在「内容」里', async () => {
    await mount(LEGEND)
    // 前提：图例页上确有一个同名的「字号」——没有它这条用例量不到「认主人」
    expect(host.querySelector('[data-prop="fontsize"]')).not.toBeNull()
    await act(async () => {
      focusObject(ref(TITLE), 'fontsize')
    })
    await settleFrames()
    const size = fieldHost('fontsize', TITLE)
    expect(size).toBeDefined()
    expect(size!.contains(document.activeElement)).toBe(true)
    expect(document.activeElement?.tagName).not.toBe('TEXTAREA')
  })

  it('不是定位来的（点选文字元素）：选中即可打字的便利照旧', async () => {
    await mount(TITLE)
    expect(document.activeElement?.tagName).toBe('TEXTAREA')
  })
})
