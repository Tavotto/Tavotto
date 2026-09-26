/**
 * 目标身份对不上的 override 走已有的「清除失效修改」出口（ADR 0083，QA SCI-03-B1）。
 *
 * 引擎不应用它、报一条 warning；界面上它要能被看见、能被清掉——不造新 UI，
 * 与「gid 整个没了」的孤儿 override 同一个出口。对照：身份对得上的不出现。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Manifest } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject, type PanelOverride } from '@/types/document'
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

const ALPHA = 'l1:aaaaaaaaaaaaaaaa'
const BETA = 'l1:bbbbbbbbbbbbbbbb'

/** 脚本改过之后：lines_0 现在是 beta。 */
const manifest: Manifest = {
  stem: 'A',
  size_mm: [100, 80],
  elements: [
    { gid: 'figure', role: 'figure', label: '整张图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    {
      gid: 'axes_0.lines_0',
      role: 'line',
      label: '曲线 “beta”',
      identity: BETA,
      bbox: [0.1, 0.1, 0.5, 0.5],
      editable: [{ prop: 'color', type: 'color', value: '#2ca02c' }],
      draggable: false,
    },
  ],
}

const panelOf = (overrides: PanelOverride[]): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 0, y: 0, w: 100, h: 80,
    fileId: 'A.pdf', fileKind: 'pdf', nativeW: 100, nativeH: 80,
    script: 'fig.py', overrides,
  }) as unknown as PanelObject

let root: Root

function Harness() {
  const panel = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return (
    <TooltipProvider>
      <ElementInspector panel={panel} />
    </TooltipProvider>
  )
}

async function mount(overrides: PanelOverride[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_stale_identity')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf(overrides))
  })
  seedExactRender(panelOf(overrides), manifest)
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: [] })
  const host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<Harness />)
  })
}

const clearButton = () =>
  [...document.querySelectorAll('button')].find((b) => b.textContent?.includes('清除失效修改'))

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
})

describe('目标身份对不上的 override = 失效修改', () => {
  it('写给 alpha 的编辑、gid 现在指向 beta：出现在「清除失效修改」里，点了就清掉', async () => {
    await mount([{ gid: 'axes_0.lines_0', prop: 'color', value: '#ff00ff', identity: ALPHA }])
    const btn = clearButton()
    expect(btn).toBeTruthy()
    await act(async () => {
      btn!.click()
    })
    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).overrides).toEqual([])
  })

  it('对照：身份对得上 / 不带身份（旧文档）都不算失效', async () => {
    await mount([
      { gid: 'axes_0.lines_0', prop: 'color', value: '#ff00ff', identity: BETA },
      { gid: 'axes_0.lines_0', prop: 'linewidth', value: 3 },
    ])
    expect(clearButton()).toBeUndefined()
  })
})
