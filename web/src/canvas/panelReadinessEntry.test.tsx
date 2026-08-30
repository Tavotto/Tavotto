/**
 * 画布上那句「为什么不能编辑？」（Prompt 08 §九）。
 *
 * 用户在画布上选中一张没有编辑入口的图时，**问题就在他眼前**——他此刻要的
 * 不是一个动作，是一个解释。所以这个入口只做三件事：打开接入状态、滚到这张
 * 图、把焦点放上去。
 *
 * 它**什么都不改**：选择不动、脚本不跑、不切裁剪态。这一条是硬约束——一个
 * 写着"为什么"的按钮做出了副作用，用户此后不敢再点任何解释性入口。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ContextBar } from '@/canvas/ContextBar'
import { TooltipProvider } from '@/components/ui/Tooltip'
import type { PanelCapability, PanelInfo, ReadinessStatus } from '@/lib/api'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const capabilityOf = (status: ReadinessStatus): PanelCapability => ({
  status,
  reason_code: status === 'editable' ? 'registered_source' : 'no_source_candidate',
  script: status === 'editable' ? 'fig.py' : null,
  candidates: [],
  can_probe: false,
  can_manual_link: true,
})

const assetOf = (id: string, capability?: PanelCapability): PanelInfo => ({
  id,
  name: id,
  folder: '.',
  kind: 'pdf',
  native_w_mm: 80,
  native_h_mm: 60,
  mtime: 1,
  ...(capability ? { capability } : {}),
})

const panelObj = (over: Partial<PanelObject> = {}): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    fileId: 'Photo.pdf',
    x: 10,
    y: 10,
    w: 40,
    h: 30,
    nativeW: 80,
    nativeH: 60,
    overrides: [],
    ...over,
  }) as PanelObject

let root: Root

async function mount(obj: PanelObject, assets: PanelInfo[]) {
  useAssetStore.setState({
    panels: assets,
    byId: Object.fromEntries(assets.map((a) => [a.id, a])),
    loaded: true,
  })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_readiness_entry')
  useDocumentStore.getState().commit(literal('放一张图'), (d) => {
    d.objects.push(obj)
  })
  useDocumentStore.setState({ past: [], future: [] })

  const anchor = document.createElement('div')
  anchor.setAttribute('data-object-id', obj.id)
  document.body.appendChild(anchor)
  const mountEl = document.createElement('div')
  document.body.appendChild(mountEl)
  root = createRoot(mountEl)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ContextBar />
      </TooltipProvider>,
    )
  })
  await act(async () => {
    useSelectionStore.getState().set([obj.id])
  })
}

const whyButton = () =>
  [...document.querySelectorAll('button')].find((b) =>
    b.textContent?.includes('为什么不能编辑'),
  )

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
  useUiStore.setState({
    elementPanelId: null,
    selectedGids: [],
    editingTextId: null,
    cropTargetId: null,
    tool: 'select',
    layout: 'wide',
    leftOpen: false,
    rightOpen: false,
    registryOpen: false,
  })
  useProjectReadinessStore.getState().clear()
})

afterEach(async () => {
  await act(async () => root.unmount())
  useSelectionStore.getState().clear()
  document.body.innerHTML = ''
})

describe('入口出现的条件', () => {
  it('没有源脚本、且项目说它不可编辑 → 给出解释入口', async () => {
    await mount(panelObj(), [assetOf('Photo.pdf', capabilityOf('layout_only'))])
    expect(whyButton()).toBeTruthy()
  })

  it('可编辑的图不显示它（那里有的是「编辑图内元素」）', async () => {
    await mount(panelObj({ script: 'fig.py' }), [
      assetOf('Photo.pdf', capabilityOf('editable')),
    ])
    expect(whyButton()).toBeUndefined()
  })

  it('capability 还没到：不显示——「这一轮还不知道」不是一种可解释的状态', async () => {
    await mount(panelObj(), [assetOf('Photo.pdf')])
    expect(whyButton()).toBeUndefined()
  })

  it('派生同步还没跑完时不与「编辑图内元素」并排出现', async () => {
    // 文档里还记着 script（上一轮的），项目那边已经说它连不上了
    await mount(panelObj({ script: 'fig.py' }), [
      assetOf('Photo.pdf', capabilityOf('source_missing')),
    ])
    expect(whyButton()).toBeUndefined()
  })
})

describe('点下去只解释，什么都不改', () => {
  it('打开接入状态并聚焦到这张图', async () => {
    await mount(panelObj(), [assetOf('Photo.pdf', capabilityOf('layout_only'))])
    await act(async () => whyButton()!.click())
    expect(useUiStore.getState().registryOpen).toBe(true)
    expect(useProjectReadinessStore.getState().focusId).toBe('Photo.pdf')
  })

  it('选择一个字不动，也不进裁剪态', async () => {
    await mount(panelObj(), [assetOf('Photo.pdf', capabilityOf('layout_only'))])
    const before = [...useSelectionStore.getState().ids]
    await act(async () => whyButton()!.click())
    expect(useSelectionStore.getState().ids).toEqual(before)
    expect(useUiStore.getState().cropTargetId).toBeNull()
    expect(useUiStore.getState().elementPanelId).toBeNull()
  })

  it('文档一个字节都没改（解释不是编辑）', async () => {
    await mount(panelObj(), [assetOf('Photo.pdf', capabilityOf('layout_only'))])
    const before = useDocumentStore.getState().doc
    await act(async () => whyButton()!.click())
    expect(useDocumentStore.getState().doc).toBe(before)
    expect(useDocumentStore.getState().past).toHaveLength(0)
  })
})
