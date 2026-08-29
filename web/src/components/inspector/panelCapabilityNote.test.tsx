/**
 * 属性栏里那条非阻塞说明（Prompt 08 §九后半）。
 *
 * 与画布上的「为什么不能编辑？」是**同一份事实的两个出口**：一个在用户的手
 * 边（画布），一个在他找属性的地方（右栏）。两处都读 `PanelInfo.capability`，
 * 都不自己判一遍。
 *
 * 它是说明不是故障：`layout_only` 的图照旧能排版、裁剪、标注和导出，所以这里
 * 既没有 `role="alert"`，也不用警示色。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
  fetchReadiness: vi.fn().mockResolvedValue(null),
}))

import type { PanelCapability, PanelInfo, ReadinessStatus } from '@/lib/api'
import { PanelCapabilityNote } from '@/components/inspector/PanelSection'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const capabilityOf = (status: ReadinessStatus): PanelCapability => ({
  status,
  reason_code:
    status === 'source_missing'
      ? 'registered_script_missing'
      : status === 'editable'
        ? 'registered_source'
        : 'no_source_candidate',
  script: status === 'editable' || status === 'source_missing' ? 'fig.py' : null,
  candidates: [],
  can_probe: false,
  can_manual_link: true,
})

const asset = (capability?: PanelCapability): PanelInfo => ({
  id: 'Photo.pdf',
  name: 'Photo.pdf',
  folder: '.',
  kind: 'pdf',
  native_w_mm: 80,
  native_h_mm: 60,
  mtime: 1,
  ...(capability ? { capability } : {}),
})

const panel = (over: Partial<PanelObject> = {}): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    fileId: 'Photo.pdf',
    x: 0, y: 0, w: 40, h: 30,
    nativeW: 80, nativeH: 60,
    overrides: [],
    ...over,
  }) as PanelObject

let host: HTMLElement
let root: Root

async function mount(obj: PanelObject, a: PanelInfo) {
  useAssetStore.setState({ panels: [a], byId: { [a.id]: a }, loaded: true })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <PanelCapabilityNote panel={obj} />
      </TooltipProvider>,
    )
  })
}

beforeEach(() => {
  localStorage.clear()
  useUiStore.setState({ registryOpen: false })
  useProjectReadinessStore.getState().clear()
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

it('仅排版：说清它还能做什么，并给出查看入口', async () => {
  await mount(panel(), asset(capabilityOf('layout_only')))
  expect(host.textContent).toContain('仅排版')
  expect(host.textContent).toMatch(/排版|裁剪|导出/)
  expect(host.querySelector('[role="alert"]')).toBeNull()
  expect(host.querySelector('button')).not.toBeNull()
})

it('源脚本丢失用的是它自己那句话，不是「仅排版」那句', async () => {
  await mount(panel(), asset(capabilityOf('source_missing')))
  expect(host.textContent).toContain('源脚本丢失')
  expect(host.textContent).toContain('fig.py')
})

it('可编辑：不说话', async () => {
  await mount(panel({ script: 'fig.py' }), asset(capabilityOf('editable')))
  expect(host.textContent).toBe('')
})

it('capability 还没到：不说话（不替后端补一个默认状态）', async () => {
  await mount(panel(), asset())
  expect(host.textContent).toBe('')
})

it('入口打开接入状态并聚焦到这张图', async () => {
  await mount(panel(), asset(capabilityOf('layout_only')))
  await act(async () => host.querySelector('button')!.click())
  expect(useUiStore.getState().registryOpen).toBe(true)
  expect(useProjectReadinessStore.getState().focusId).toBe('Photo.pdf')
})
