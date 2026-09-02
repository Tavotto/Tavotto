/**
 * 画布对象的右键菜单（Prompt 18）：
 *   右键选择逻辑（ObjectView）· 五种菜单形态按对象与选区给 · 每一项只调既有 action ·
 *   readiness 入口只打开接入中心 · 键盘 / 子菜单 / Esc / 点外部 / aria · 与 ContextBar 不同时出现。
 *
 * jsdom 没有布局：越界翻转（Radix `avoidCollisions`）在这里量不到，真浏览器那一遍看它。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { EngineRenderOptions, PanelCapability, PanelInfo } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { alignSelectedTo, changeZOrder } from '@/store/actions'
import { useArrangeStore } from '@/store/arrangeStore'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import {
  emptyProject,
  type ArrowObject,
  type CanvasObject,
  type PanelObject,
  type ShapeObject,
  type TextObject,
} from '@/types/document'
import { ContextBar } from './context-bar/ContextBar'
import { ObjectView } from './ObjectView'
import { QuickEdit } from './QuickEdit'
import { useQuickEdit } from './quickEditStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const engineRender = vi.fn()
const engineInvalidate = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
  engineInvalidate: (id: string) => engineInvalidate(id),
  fetchReadiness: () => Promise.reject(new Error('offline')),
}))

/* ------------------------------ fixtures ---------------------------------- */

const text = (id: string, over: Partial<TextObject> = {}): TextObject =>
  ({
    id,
    type: 'text',
    text: id,
    sizePt: 10,
    bold: false,
    color: '#000000',
    align: 'left',
    x: 10,
    y: 100,
    w: 30,
    h: 8,
    ...over,
  }) as TextObject

const panel = (id: string, over: Partial<PanelObject> = {}): PanelObject =>
  ({
    id,
    type: 'panel',
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    x: 0,
    y: 0,
    w: 40,
    h: 30,
    nativeW: 40,
    nativeH: 30,
    script: 'fig.py',
    overrides: [],
    ...over,
  }) as PanelObject

const arrow = (id: string): ArrowObject => ({
  id,
  type: 'arrow',
  x: 10,
  y: 20,
  w: 40,
  h: 10,
  start: { rx: 0, ry: 0.5 },
  end: { rx: 1, ry: 0.5 },
  strokePt: 1,
  color: '#111111',
  head: 'end',
})

const shape = (id: string): ShapeObject => ({
  id,
  type: 'shape',
  shape: 'rect',
  x: 10,
  y: 20,
  w: 40,
  h: 10,
  strokePt: 1,
  color: '#111111',
  fill: null,
})

const cap = (over: Partial<PanelCapability> = {}): PanelCapability => ({
  status: 'layout_only',
  reason_code: 'no_source_candidate',
  script: null,
  candidates: [],
  can_probe: false,
  can_manual_link: true,
  ...over,
})

const asset = (id: string, over: Partial<PanelInfo> = {}): PanelInfo => ({
  id,
  name: id,
  folder: '.',
  kind: 'pdf',
  native_w_mm: 40,
  native_h_mm: 30,
  mtime: 1,
  ...over,
})

const objs = () => useDocumentStore.getState().doc.objects
const byId = <T extends CanvasObject = CanvasObject>(id: string) => objs().find((o) => o.id === id) as T
const past = () => useDocumentStore.getState().past
const sel = () => useSelectionStore.getState().ids

let root: Root
let mountEl: HTMLDivElement

async function seed(items: CanvasObject[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_objmenu_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
}

async function mount(extra?: React.ReactNode) {
  mountEl = document.createElement('div')
  document.body.appendChild(mountEl)
  root = createRoot(mountEl)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <QuickEdit />
        {extra}
      </TooltipProvider>,
    )
  })
}

/** 打开对象菜单：模拟 ObjectView 右键之后的状态（选区已经保证含它） */
async function openOn(id: string, ids: string[] = [id]) {
  await act(async () => {
    useSelectionStore.getState().set(ids)
    useQuickEdit.getState().open({ kind: 'object', id }, 120, 80)
  })
  // Radix 的 DismissableLayer 在下一拍才挂上 pointerdown 监听
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
}

const menu = () => document.querySelector<HTMLElement>('[role="menu"][data-quick-menu]')
const items = () => [...document.querySelectorAll<HTMLElement>('[data-quick-item]')]
const item = (key: string) => document.querySelector<HTMLElement>(`[data-quick-item="${key}"]`)
const itemKeys = () => items().map((el) => el.dataset.quickItem)
const click = async (el: HTMLElement | null) => {
  if (!el) throw new Error('没有这一项')
  await act(async () => {
    el.click()
  })
}
// Radix 的方向键把「聚焦下一项」放在 setTimeout(0) 里（RovingFocusGroup），等一拍再看
const key = async (k: string, target: Element | null = document.activeElement) => {
  await act(async () => {
    target?.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }))
    await new Promise((r) => setTimeout(r, 0))
  })
}

beforeEach(async () => {
  localStorage.clear()
  document.body.innerHTML = ''
  if (!('ResizeObserver' in globalThis)) {
    class RO {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    Object.defineProperty(globalThis, 'ResizeObserver', { value: RO, configurable: true, writable: true })
  }
  engineRender.mockReset()
  engineInvalidate.mockReset()
  engineRender.mockResolvedValue({ rev: 1, manifest: { elements: [] }, warnings: [] })
  engineInvalidate.mockResolvedValue({ invalidated: true })
  useRenderStore.getState().clear()
  useAssetStore.setState({ byId: {} })
  useUiStore.setState({
    elementPanelId: null,
    selectedGids: [],
    editingTextId: null,
    cropTargetId: null,
    tool: 'select',
    layout: 'wide',
    leftOpen: false,
    rightOpen: false,
    rightTab: 'assistant',
    registryOpen: false,
    confirm: null,
    status: null,
  })
  useProjectReadinessStore.setState({ focusId: null })
  useArrangeStore.getState().setAlignRef('selection')
  useInteractionStore.getState().end()
  useSelectionStore.getState().clear()
  useQuickEdit.getState().close()
})

afterEach(async () => {
  await act(async () => {
    useQuickEdit.getState().close()
    root?.unmount()
  })
  useSelectionStore.getState().clear()
  document.body.innerHTML = ''
})

/* -------------------------------------------------------------------------- */
/*  右键选择逻辑（ObjectView.onContextMenu）                                    */
/* -------------------------------------------------------------------------- */

describe('右键先保证目标在选区里', () => {
  const rightClick = async (id: string) => {
    const el = document.querySelector<HTMLElement>(`[data-object-id="${id}"]`)!
    await act(async () => {
      el.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }))
    })
  }

  beforeEach(async () => {
    await seed([
      text('t1'),
      text('t2'),
      text('t3'),
      text('g1', { groupId: 'G' }),
      text('g2', { groupId: 'G' }),
      text('L', { locked: true }),
      panel('p1'),
    ])
    await mount(
      <>
        {objs().map((o) => (
          <ObjectView key={o.id} obj={o} />
        ))}
      </>,
    )
  })

  it('未选对象右键 → 它成为选区，菜单开在它上面', async () => {
    await rightClick('t2')
    expect(sel()).toEqual(['t2'])
    expect(useQuickEdit.getState().target).toEqual({ kind: 'object', id: 't2' })
  })

  it('已选单对象右键 → 选区保持', async () => {
    useSelectionStore.getState().set(['t1'])
    await rightClick('t1')
    expect(sel()).toEqual(['t1'])
  })

  it('多选中的对象右键 → 整个多选保持（顺序也不变）', async () => {
    useSelectionStore.getState().set(['t3', 't1', 't2'])
    await rightClick('t1')
    expect(sel()).toEqual(['t3', 't1', 't2'])
    expect(menu()?.dataset.quickMenu).toBe('multi')
  })

  it('多选外对象右键 → 切到该对象', async () => {
    useSelectionStore.getState().set(['t1', 't2'])
    await rightClick('t3')
    expect(sel()).toEqual(['t3'])
  })

  it('组成员右键 → 整组进选区，菜单按多选给', async () => {
    await rightClick('g2')
    expect(sel()).toEqual(['g1', 'g2'])
    expect(menu()?.dataset.quickMenu).toBe('multi')
  })

  it('锁定对象在画布上不吃指针（既有产品规则：从图层树解锁），右键到不了它', async () => {
    expect(document.querySelector<HTMLElement>('[data-object-id="L"]')!.style.pointerEvents).toBe('none')
    expect(document.querySelector<HTMLElement>('[data-object-id="t1"]')!.style.pointerEvents).toBe('')
  })

  it('图内编辑态里右键别的对象 = 退出编辑态回到画布层（与左键一条路）', async () => {
    useUiStore.getState().setElementPanel('p1')
    await rightClick('t1')
    expect(useUiStore.getState().elementPanelId).toBeNull()
    expect(sel()).toEqual(['t1'])
  })

  it('图内编辑态里 shift 混排进选区的标注：右键它不退编辑态、选区不动', async () => {
    useUiStore.getState().setElementPanel('p1')
    useSelectionStore.getState().set(['t1', 't2'])
    await rightClick('t2')
    expect(useUiStore.getState().elementPanelId).toBe('p1')
    expect(sel()).toEqual(['t1', 't2'])
  })
})

/* -------------------------------------------------------------------------- */
/*  面板                                                                       */
/* -------------------------------------------------------------------------- */

describe('可编辑面板', () => {
  beforeEach(async () => {
    await seed([panel('p1', { overrides: [{ gid: 'axes_0.title', prop: 'text', value: 'A' }] }), panel('p2')])
    await mount()
  })

  it('菜单结构：编辑 / 重建 / 裁剪 / 适应 / 恢复 / 全部属性 ── 副本 / 锁 / 隐藏 / 层级 ── 删除', async () => {
    await openOn('p1')
    expect(menu()?.dataset.quickMenu).toBe('panel')
    expect(itemKeys()).toEqual([
      'edit-elements',
      'rebuild',
      'crop',
      'fit',
      'reset-overrides',
      'open-inspector',
      'duplicate',
      'lock',
      'hide',
      'z-order',
      'delete',
    ])
    expect(document.querySelector('[data-quick-heading]')?.textContent).toBe('Fig1')
  })

  it('没有 override 就没有「恢复图内修改」', async () => {
    await openOn('p2')
    expect(item('reset-overrides')).toBeNull()
    expect(itemKeys()).toContain('rebuild')
  })

  it('编辑图内元素 → enterElementEdit（进编辑态、菜单关掉）', async () => {
    await openOn('p2')
    await click(item('edit-elements'))
    expect(useUiStore.getState().elementPanelId).toBe('p2')
    expect(useQuickEdit.getState().target).toBeNull()
    expect(menu()).toBeNull()
  })

  it('重新构建 → 作废这张图的会话再按当前 overrides 渲染；文档与历史不动', async () => {
    await openOn('p1')
    const before = JSON.stringify(objs())
    await click(item('rebuild'))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(engineInvalidate).toHaveBeenCalledWith('Fig1.pdf')
    expect(engineRender).toHaveBeenCalledTimes(1)
    expect(engineRender.mock.calls[0][1]).toEqual([{ gid: 'axes_0.title', prop: 'text', value: 'A' }])
    expect(JSON.stringify(objs())).toBe(before)
    expect(past()).toHaveLength(0)
    expect(useUiStore.getState().status?.key).toBe('status.panelRebuilt')
  })

  it('裁剪 → 进裁剪态', async () => {
    await openOn('p2')
    await click(item('crop'))
    expect(useUiStore.getState().cropTargetId).toBe('p2')
  })

  it('旋转过的面板：裁剪 disabled 且常驻原因（不是静默无反应）', async () => {
    useDocumentStore.getState().commit(literal('转'), (d) => {
      const o = d.objects.find((x) => x.id === 'p2') as PanelObject
      o.rotation = 90
    })
    await openOn('p2')
    const crop = item('crop')!
    expect(crop.getAttribute('aria-disabled')).toBe('true')
    expect(crop.textContent).toContain('旋转')
    await click(crop)
    expect(useUiStore.getState().cropTargetId).toBeNull()
  })

  it('适应内容 → fitPanels：一条历史', async () => {
    useDocumentStore.getState().commit(literal('拉长'), (d) => {
      const o = d.objects.find((x) => x.id === 'p2') as PanelObject
      o.w = 80
      o.h = 30
    })
    useDocumentStore.setState({ past: [], future: [] })
    await openOn('p2')
    await click(item('fit'))
    expect(past()).toHaveLength(1)
    expect(past()[0].label.key).toBe('history.fitPanel')
    const p = byId<PanelObject>('p2')
    expect(p.w / p.h).toBeCloseTo(40 / 30, 5)
  })

  it('恢复图内修改 → 先问；确认后清空、可撤销、同文件的另一个实例不动', async () => {
    useDocumentStore.getState().commit(literal('给 p2 也加一条'), (d) => {
      const o = d.objects.find((x) => x.id === 'p2') as PanelObject
      o.overrides = [{ gid: 'axes_0.title', prop: 'text', value: 'B' }]
    })
    useDocumentStore.setState({ past: [], future: [] })
    await openOn('p1')
    expect(item('reset-overrides')?.textContent).toContain('1')
    await click(item('reset-overrides'))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(menu()).toBeNull()
    const req = useUiStore.getState().confirm
    expect(req?.title.key).toBe('confirm.resetOverridesTitle')
    expect(byId<PanelObject>('p1').overrides).toHaveLength(1) // 还没点头
    await act(async () => {
      useUiStore.getState().setConfirm(null)
      req!.resolve(true)
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(byId<PanelObject>('p1').overrides).toEqual([])
    expect(byId<PanelObject>('p2').overrides).toHaveLength(1)
    expect(past()).toHaveLength(1)
    await act(async () => {
      useDocumentStore.getState().undo()
    })
    expect(byId<PanelObject>('p1').overrides).toHaveLength(1)
  })

  it('打开全部属性 → 选区不动、右栏切到属性页、菜单关掉', async () => {
    await openOn('p1')
    await click(item('open-inspector'))
    expect(sel()).toEqual(['p1'])
    expect(useUiStore.getState().rightTab).toBe('properties')
    expect(useUiStore.getState().rightOpen).toBe(true)
    expect(menu()).toBeNull()
  })

  it('窄屏：打开全部属性把右栏铺开', async () => {
    useUiStore.setState({ layout: 'narrow', rightOpen: false })
    await openOn('p1')
    await click(item('open-inspector'))
    expect(useUiStore.getState().rightOpen).toBe(true)
  })
})

describe('仅排版面板', () => {
  beforeEach(async () => {
    await seed([panel('p1', { script: null })])
    await mount()
  })

  it('layout_only：为什么不能编辑？/ 连接源脚本 / 裁剪 / 适应 / 全部属性；没有编辑与重建', async () => {
    useAssetStore.setState({ byId: { 'Fig1.pdf': asset('Fig1.pdf', { capability: cap() }) } })
    await openOn('p1')
    expect(menu()?.dataset.quickMenu).toBe('panel-layout-only')
    expect(itemKeys()).toEqual([
      'why-not-editable',
      'connect-source',
      'crop',
      'fit',
      'open-inspector',
      'duplicate',
      'lock',
      'hide',
      'z-order',
      'delete',
    ])
  })

  it.each(['conflict', 'source_missing', 'needs_probe', 'auto_linkable'] as const)(
    '%s：统一叫「为什么不能编辑？」，落点是接入中心聚焦这张图；不跑脚本、不改选区、不进裁剪',
    async (status) => {
      useAssetStore.setState({
        byId: { 'Fig1.pdf': asset('Fig1.pdf', { capability: cap({ status, can_probe: true }) }) },
      })
      await openOn('p1')
      const why = item('why-not-editable')!
      expect(why.textContent).toBe('为什么不能编辑？')
      await click(why)
      expect(useUiStore.getState().registryOpen).toBe(true)
      expect(useProjectReadinessStore.getState().focusId).toBe('Fig1.pdf')
      expect(sel()).toEqual(['p1'])
      expect(useUiStore.getState().cropTargetId).toBeNull()
      expect(engineRender).not.toHaveBeenCalled()
      expect(engineInvalidate).not.toHaveBeenCalled()
    },
  )

  it('连接源脚本：只在有得连（可试运行 / 可手工关联）时出现，落点同样是接入中心', async () => {
    useAssetStore.setState({
      byId: { 'Fig1.pdf': asset('Fig1.pdf', { capability: cap({ can_manual_link: false, can_probe: false }) }) },
    })
    await openOn('p1')
    expect(item('why-not-editable')).not.toBeNull()
    expect(item('connect-source')).toBeNull()
    await act(async () => useQuickEdit.getState().close())
    useAssetStore.setState({
      byId: { 'Fig1.pdf': asset('Fig1.pdf', { capability: cap({ status: 'needs_probe', can_probe: true }) }) },
    })
    await openOn('p1')
    await click(item('connect-source'))
    expect(useUiStore.getState().registryOpen).toBe(true)
    expect(useProjectReadinessStore.getState().focusId).toBe('Fig1.pdf')
  })

  it('capability 缺席（这一轮还不知道）：什么都不说，不补一个 layout_only', async () => {
    useAssetStore.setState({ byId: { 'Fig1.pdf': asset('Fig1.pdf') } })
    await openOn('p1')
    expect(item('why-not-editable')).toBeNull()
    expect(item('connect-source')).toBeNull()
    expect(item('crop')).not.toBeNull()
  })

  it('readiness 打开失败（取不到报告）也保留选区与菜单目标', async () => {
    useAssetStore.setState({ byId: { 'Fig1.pdf': asset('Fig1.pdf', { capability: cap() }) } })
    await openOn('p1')
    await click(item('why-not-editable'))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(useProjectReadinessStore.getState().error).toBe('offline')
    expect(useUiStore.getState().registryOpen).toBe(true)
    expect(sel()).toEqual(['p1'])
  })
})

/* -------------------------------------------------------------------------- */
/*  文字 / 箭头 / 形状                                                          */
/* -------------------------------------------------------------------------- */

describe('文字 / 箭头 / 形状', () => {
  beforeEach(async () => {
    await seed([text('t1'), arrow('a1'), shape('s1')])
    await mount()
  })

  it('文字：编辑文字 / 全部属性 ── 副本 / 锁 / 隐藏 / 层级 ── 删除；编辑文字 → setEditingText', async () => {
    await openOn('t1')
    expect(menu()?.dataset.quickMenu).toBe('text')
    expect(itemKeys()).toEqual(['edit-text', 'open-inspector', 'duplicate', 'lock', 'hide', 'z-order', 'delete'])
    await click(item('edit-text'))
    expect(useUiStore.getState().editingTextId).toBe('t1')
  })

  it.each(['a1', 's1'])('%s：全部属性 ── 副本 / 锁 / 隐藏 / 层级 ── 删除（颜色 / 线宽留给 ContextBar）', async (id) => {
    await openOn(id)
    expect(menu()?.dataset.quickMenu).toBe('mark')
    expect(itemKeys()).toEqual(['open-inspector', 'duplicate', 'lock', 'hide', 'z-order', 'delete'])
    expect(menu()?.querySelector('input')).toBeNull()
  })

  it('创建副本 → duplicateSelected：副本成为选区，一条历史', async () => {
    await openOn('t1')
    await click(item('duplicate'))
    expect(objs()).toHaveLength(4)
    expect(past()).toHaveLength(1)
    expect(past()[0].label.key).toBe('history.duplicateObjects')
    expect(sel()).toHaveLength(1)
    expect(sel()[0]).not.toBe('t1')
  })

  it('锁定 → 下次菜单是解锁；选区保持', async () => {
    await openOn('t1')
    await click(item('lock'))
    expect(byId('t1').locked).toBe(true)
    expect(sel()).toEqual(['t1'])
    await openOn('t1')
    expect(item('lock')).toBeNull()
    await click(item('unlock'))
    expect(byId('t1').locked).toBe(false)
  })

  it('隐藏 → toggleHidden，一条历史', async () => {
    await openOn('t1')
    await click(item('hide'))
    expect(byId('t1').hidden).toBe(true)
    expect(past()[0].label.key).toBe('history.hideObject')
  })

  it('排列层级子菜单：四项带快捷键，每一项调 changeZOrder（标签与直接调 action 逐字相同）', async () => {
    await openOn('t1')
    const sub = item('z-order')!
    expect(sub.getAttribute('aria-haspopup')).toBe('menu')
    expect(sub.getAttribute('aria-expanded')).toBe('false')
    await act(async () => {
      sub.focus()
    })
    await key('ArrowRight', sub)
    expect(sub.getAttribute('aria-expanded')).toBe('true')
    expect(itemKeys().filter((k) => k?.startsWith('z-') && k !== 'z-order')).toEqual([
      'z-top',
      'z-up',
      'z-down',
      'z-bottom',
    ])
    expect(item('z-top')?.textContent).toContain(']')
    await click(item('z-top'))
    expect(objs().at(-1)?.id).toBe('t1')
    expect(past()).toHaveLength(1)
    // 与直接调 action 同一条历史标签
    const viaMenu = past()[0].label
    useSelectionStore.getState().set(['t1'])
    changeZOrder('top')
    useDocumentStore.getState().commit(literal('挪一下'), (d) => {
      d.objects.reverse()
    })
    useSelectionStore.getState().set(['t1'])
    changeZOrder('top')
    expect(past().at(-1)!.label).toEqual(viaMenu)
  })

  it('删除 → deleteSelected：对象没了、菜单关掉', async () => {
    await openOn('a1')
    await click(item('delete'))
    expect(byId('a1')).toBeUndefined()
    expect(menu()).toBeNull()
    expect(past()[0].label.key).toBe('history.deleteObject')
  })
})

/* -------------------------------------------------------------------------- */
/*  多选                                                                       */
/* -------------------------------------------------------------------------- */

describe('多选', () => {
  beforeEach(async () => {
    await seed([
      text('t1', { x: 10, y: 100, w: 30 }),
      text('t2', { x: 50, y: 120, w: 20 }),
      text('t3', { x: 90, y: 140, w: 10 }),
      text('g1', { groupId: 'G', x: 200 }),
      text('g2', { groupId: 'G', x: 240 }),
    ])
    await mount()
  })

  it('结构：已选 N 个 · 对齐与分布 > · 等宽 / 等高 · 成组 · 排列属性 ── 副本 / 锁 / 隐藏 / 层级 ── 删除 N 个', async () => {
    await openOn('t2', ['t1', 't2', 't3'])
    expect(menu()?.dataset.quickMenu).toBe('multi')
    expect(menu()?.dataset.quickMenuCount).toBe('3')
    expect(document.querySelector('[data-quick-heading]')?.textContent).toBe('已选 3 个对象')
    expect(itemKeys()).toEqual([
      'arrange',
      'align-samew',
      'align-sameh',
      'group',
      'open-arrange',
      'duplicate',
      'lock',
      'hide',
      'z-order',
      'delete',
    ])
    expect(item('delete')?.textContent).toContain('3')
    expect(item('lock')?.textContent).toContain('3')
    expect(sel()).toEqual(['t1', 't2', 't3'])
  })

  it('对齐与分布子菜单：显示当前参照，六向 + 两分布；点左对齐 = alignSelectedTo（同一条历史标签）', async () => {
    await openOn('t2', ['t1', 't2', 't3'])
    const sub = item('arrange')!
    await act(async () => sub.focus())
    await key('ArrowRight', sub)
    expect(document.querySelector('[data-quick-arrange-ref]')?.getAttribute('data-quick-arrange-ref')).toBe('selection')
    expect(document.querySelector('[data-quick-arrange-ref]')?.textContent).toContain('选区')
    expect(itemKeys().filter((k) => k?.startsWith('align-'))).toEqual([
      'align-samew',
      'align-sameh',
      'align-left',
      'align-hcenter',
      'align-right',
      'align-top',
      'align-vcenter',
      'align-bottom',
      'align-hdist',
      'align-vdist',
    ])
    await click(item('align-left'))
    expect(objs().slice(0, 3).map((o) => o.x)).toEqual([10, 10, 10])
    expect(past()).toHaveLength(1)
    const viaMenu = past()[0].label
    useDocumentStore.getState().commit(literal('挪'), (d) => {
      d.objects[1].x = 70
    })
    useSelectionStore.getState().set(['t1', 't2', 't3'])
    alignSelectedTo('left', 'selection')
    expect(past().at(-1)!.label).toEqual(viaMenu)
  })

  it('参照读 arrangeStore：属性页 / 浮动栏切到「画布」，菜单里当场是「画布」', async () => {
    useArrangeStore.getState().setAlignRef('page')
    await openOn('t2', ['t1', 't2', 't3'])
    const sub = item('arrange')!
    await act(async () => sub.focus())
    await key('ArrowRight', sub)
    expect(document.querySelector('[data-quick-arrange-ref]')?.getAttribute('data-quick-arrange-ref')).toBe('page')
    await click(item('align-right'))
    const pw = useDocumentStore.getState().doc.page.w
    expect(objs().slice(0, 3).map((o) => o.x + o.w)).toEqual([pw, pw, pw])
  })

  it('两个对象：分布 disabled 且带原因；点了不动', async () => {
    await openOn('t2', ['t1', 't2'])
    const sub = item('arrange')!
    await act(async () => sub.focus())
    await key('ArrowRight', sub)
    const h = item('align-hdist')!
    expect(h.getAttribute('aria-disabled')).toBe('true')
    expect(h.textContent).toContain('3')
    expect(item('align-left')?.getAttribute('aria-disabled')).toBeNull()
    await click(h)
    expect(past()).toHaveLength(0)
  })

  it('等宽 / 等高 → 同一个 action', async () => {
    await openOn('t3', ['t1', 't2', 't3'])
    await click(item('align-samew'))
    // 参照是选区：等宽 = 选区包围盒的宽（10 … 100 → 90）
    expect(objs().slice(0, 3).map((o) => o.w)).toEqual([90, 90, 90])
    expect(past()[0].label.key).toBe('history.alignWithRef')
  })

  it('成组 → groupSelected；再右键整组：只有取消成组（不再给成组）', async () => {
    await openOn('t2', ['t1', 't2'])
    await click(item('group'))
    expect(byId('t1').groupId).toBeDefined()
    expect(byId('t1').groupId).toBe(byId('t2').groupId)
    expect(past()[0].label.key).toBe('history.group')
    await openOn('t1', ['t1', 't2'])
    expect(item('group')).toBeNull()
    expect(item('ungroup')).not.toBeNull()
    await click(item('ungroup'))
    expect(byId('t1').groupId).toBeUndefined()
    expect(past().at(-1)!.label.key).toBe('history.ungroup')
  })

  it('混合选区（组 + 散对象）：成组与取消成组都给，不替用户猜', async () => {
    await openOn('t1', ['g1', 'g2', 't1'])
    expect(item('group')).not.toBeNull()
    expect(item('ungroup')).not.toBeNull()
  })

  it('打开排列属性 → 属性页，选区不动', async () => {
    await openOn('t2', ['t1', 't2', 't3'])
    await click(item('open-arrange'))
    expect(useUiStore.getState().rightTab).toBe('properties')
    expect(sel()).toEqual(['t1', 't2', 't3'])
  })

  it('创建副本保留多选与成组语义：副本整组成为新选区、组 id 是新的', async () => {
    await openOn('g1', ['g1', 'g2'])
    await click(item('duplicate'))
    expect(objs()).toHaveLength(7)
    expect(sel()).toHaveLength(2)
    const copies = sel().map((id) => byId(id))
    expect(copies[0].groupId).toBe(copies[1].groupId)
    expect(copies[0].groupId).not.toBe('G')
  })

  it('批量锁定：一条历史；混合时两项都给、文案说清', async () => {
    await openOn('t2', ['t1', 't2', 't3'])
    await click(item('lock'))
    expect(objs().slice(0, 3).every((o) => o.locked)).toBe(true)
    expect(past()).toHaveLength(1)
    expect(past()[0].label).toMatchObject({ key: 'history.lockObjects', values: { count: 3 } })
    expect(sel()).toEqual(['t1', 't2', 't3'])
    // 混合：解锁一个再看
    useDocumentStore.getState().commit(literal('解一个'), (d) => {
      d.objects[0].locked = false
    })
    await openOn('t2', ['t1', 't2', 't3'])
    expect(item('lock')?.textContent).toBe('锁定全部')
    expect(item('unlock')?.textContent).toBe('解锁全部')
    await click(item('unlock'))
    expect(objs().slice(0, 3).every((o) => !o.locked)).toBe(true)
  })

  it('批量隐藏：一条历史、撤销整批回来', async () => {
    await openOn('t2', ['t1', 't2', 't3'])
    await click(item('hide'))
    expect(objs().slice(0, 3).every((o) => o.hidden)).toBe(true)
    expect(past()).toHaveLength(1)
    await act(async () => useDocumentStore.getState().undo())
    expect(objs().slice(0, 3).every((o) => !o.hidden)).toBe(true)
  })

  it('层级动作作用于整个选区（action 语义不变）', async () => {
    await openOn('t1', ['t1', 't2'])
    const sub = item('z-order')!
    await act(async () => sub.focus())
    await key('ArrowRight', sub)
    await click(item('z-top'))
    expect(objs().slice(-2).map((o) => o.id)).toEqual(['t1', 't2'])
  })

  it('删除 N 个 → deleteSelected 作用于整个选区', async () => {
    await openOn('t2', ['t1', 't2', 't3'])
    await click(item('delete'))
    expect(objs().map((o) => o.id)).toEqual(['g1', 'g2'])
    expect(past()[0].label).toMatchObject({ key: 'history.deleteObjects', values: { count: 3 } })
  })
})

/* -------------------------------------------------------------------------- */
/*  键盘 / 关闭 / 无障碍 / 与 ContextBar                                        */
/* -------------------------------------------------------------------------- */

describe('键盘、关闭与无障碍', () => {
  beforeEach(async () => {
    await seed([text('t1'), text('t2')])
    await mount(<ContextBar />)
  })

  it('role=menu + aria-label；每一项是 menuitem；子菜单触发项 aria-haspopup', async () => {
    await openOn('t1')
    const m = menu()!
    expect(m.getAttribute('aria-label')).toBe('对象菜单')
    for (const el of items()) expect(el.getAttribute('role')).toBe('menuitem')
    expect(item('z-order')?.getAttribute('aria-haspopup')).toBe('menu')
  })

  it('打开即拿到焦点；↓ 走到第一项，再 ↓ 到下一项，↑ 回来；Home / End', async () => {
    await openOn('t1')
    expect(menu()!.contains(document.activeElement)).toBe(true)
    await key('ArrowDown', menu())
    expect(document.activeElement).toBe(item('edit-text'))
    await key('ArrowDown')
    expect(document.activeElement).toBe(item('open-inspector'))
    await key('ArrowUp')
    expect(document.activeElement).toBe(item('edit-text'))
    await key('End')
    expect(document.activeElement).toBe(item('delete'))
    await key('Home')
    expect(document.activeElement).toBe(item('edit-text'))
  })

  it('Enter 激活聚焦项', async () => {
    await openOn('t1')
    await key('ArrowDown', menu())
    await key('Enter')
    expect(useUiStore.getState().editingTextId).toBe('t1')
    expect(menu()).toBeNull()
  })

  it('子菜单：→ 打开并进入，← 收回；Esc 只关子菜单', async () => {
    await openOn('t1')
    await key('ArrowDown', menu())
    await key('End')
    await key('ArrowUp')
    expect(document.activeElement).toBe(item('z-order'))
    await key('ArrowRight')
    expect(item('z-order')?.getAttribute('aria-expanded')).toBe('true')
    expect(item('z-top')).not.toBeNull()
    await key('ArrowDown', item('z-top')!.parentElement)
    expect([item('z-top'), item('z-up')]).toContain(document.activeElement)
    await key('ArrowLeft')
    expect(item('z-top')).toBeNull()
    expect(document.activeElement).toBe(item('z-order'))
    expect(menu()).not.toBeNull()
  })

  it('子菜单开着时按 Esc：整个菜单关掉（Radix 语义），事件同样不冒到全局、选区不动', async () => {
    await openOn('t1')
    await key('ArrowDown', menu())
    await key('End')
    await key('ArrowUp')
    await key('ArrowRight')
    expect(item('z-top')).not.toBeNull()
    const seen = vi.fn()
    window.addEventListener('keydown', seen)
    await key('Escape')
    window.removeEventListener('keydown', seen)
    expect(menu()).toBeNull()
    expect(seen).not.toHaveBeenCalled()
    expect(sel()).toEqual(['t1'])
  })

  it('Esc 关掉菜单，选区不动，而且不冒到全局快捷键（否则 Esc 会清空选区）', async () => {
    await openOn('t1')
    const seen = vi.fn()
    window.addEventListener('keydown', seen)
    await key('Escape')
    window.removeEventListener('keydown', seen)
    expect(menu()).toBeNull()
    expect(useQuickEdit.getState().target).toBeNull()
    expect(sel()).toEqual(['t1'])
    expect(seen).not.toHaveBeenCalled()
  })

  it('菜单里按字母（首字母跳转）不冒到全局：不会切换绘制工具', async () => {
    await openOn('t1')
    const seen = vi.fn()
    window.addEventListener('keydown', seen)
    await key('r', menu())
    window.removeEventListener('keydown', seen)
    expect(seen).not.toHaveBeenCalled()
    expect(useUiStore.getState().tool).toBe('select')
  })

  it('点菜单外面 → 关掉；事件照常落到画布（在另一个对象上右键直接开它的菜单）', async () => {
    await openOn('t1')
    await act(async () => {
      document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
    })
    expect(menu()).toBeNull()
    expect(useQuickEdit.getState().target).toBeNull()
    // 同一个「外面」若是另一个对象的右键：新菜单直接开在它上面
    await openOn('t2')
    expect(useQuickEdit.getState().target).toEqual({ kind: 'object', id: 't2' })
    expect(menu()?.querySelector('[data-quick-heading]')?.textContent).toBe('t2')
  })

  it('滚轮 / 窗口失焦 → 关掉（锚点失效了，关掉比跟随更诚实）', async () => {
    await openOn('t1')
    await act(async () => {
      window.dispatchEvent(new WheelEvent('wheel'))
    })
    expect(menu()).toBeNull()
    await openOn('t1')
    await act(async () => {
      window.dispatchEvent(new Event('blur'))
    })
    expect(menu()).toBeNull()
  })

  it('关闭后焦点还给打开前的元素（还活着才还），不落在零尺寸锚上', async () => {
    const btn = document.createElement('button')
    document.body.appendChild(btn)
    btn.focus()
    await openOn('t1')
    expect(document.activeElement).not.toBe(btn)
    await key('Escape')
    expect(document.activeElement).toBe(btn)
  })

  it('菜单开着时 ContextBar 让位，关掉后回来', async () => {
    await act(async () => useSelectionStore.getState().set(['t1']))
    expect(document.querySelector('[data-context-bar]')).not.toBeNull()
    await openOn('t1')
    expect(document.querySelector('[data-context-bar]')).toBeNull()
    await key('Escape')
    expect(document.querySelector('[data-context-bar]')).not.toBeNull()
  })

  it('目标对象被删（撤销 / 外部删除）→ 菜单自己关掉', async () => {
    await openOn('t1')
    await act(async () => {
      useDocumentStore.getState().commit(literal('删'), (d) => {
        d.objects = d.objects.filter((o) => o.id !== 't1')
      })
    })
    expect(menu()).toBeNull()
    expect(useQuickEdit.getState().target).toBeNull()
  })

  it('动作抛异常：菜单照样关掉，异常继续往外抛', async () => {
    await openOn('t1')
    const spy = vi.spyOn(useUiStore.getState(), 'setEditingText').mockImplementation(() => {
      throw new Error('boom')
    })
    useUiStore.setState({ setEditingText: spy })
    let thrown: unknown = null
    const onError = (e: ErrorEvent) => {
      thrown = e.error
      e.preventDefault()
    }
    window.addEventListener('error', onError)
    await act(async () => {
      try {
        item('edit-text')!.click()
      } catch (e) {
        thrown = e
      }
    })
    window.removeEventListener('error', onError)
    expect(String(thrown)).toContain('boom')
    expect(useQuickEdit.getState().target).toBeNull()
  })
})

/* -------------------------------------------------------------------------- */
/*  图内元素弹层没有回归                                                         */
/* -------------------------------------------------------------------------- */

describe('图内元素的弹层（dialog）外壳不回归', () => {
  beforeEach(async () => {
    await seed([panel('p1', { overrides: [{ gid: 'axes_0.title', prop: 'fontsize', value: 12 }] })])
    const p = byId<PanelObject>('p1')
    const { seedExactRender } = await import('@/test/renderFixtures')
    seedExactRender(p, {
      stem: 'Fig1',
      size_mm: [40, 30],
      elements: [
        {
          gid: 'axes_0.title',
          role: 'title',
          label: '标题',
          group: 'axes',
          bbox: [0.1, 0.1, 0.5, 0.2],
          editable: [
            { prop: 'text', type: 'text', value: 'Hi' },
            { prop: 'visible', type: 'bool', value: true },
          ],
        },
      ],
    } as never)
    await mount()
  })

  const openElement = async () => {
    await act(async () => {
      useQuickEdit.getState().open({ kind: 'element', panelId: 'p1', gid: 'axes_0.title' }, 50, 50)
    })
  }

  it('仍是 role=dialog，含文字输入框，带「恢复此元素修改（1）」', async () => {
    await openElement()
    const dlg = document.querySelector('[role="dialog"]')!
    expect(dlg).not.toBeNull()
    expect(dlg.querySelector('textarea')).not.toBeNull()
    expect(document.querySelector('[data-quick-item="reset-element"]')?.textContent).toContain('1')
    await click(document.querySelector<HTMLElement>('[data-quick-item="reset-element"]'))
    expect(byId<PanelObject>('p1').overrides).toEqual([])
    expect(past().at(-1)!.label.key).toBe('element.resetElement')
  })

  it('Select 的 portal 不算点在外面；点别处才关；Esc 关', async () => {
    await openElement()
    const portal = document.createElement('div')
    portal.setAttribute('data-radix-popper-content-wrapper', '')
    document.body.appendChild(portal)
    await act(async () => {
      portal.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
    })
    expect(document.querySelector('[role="dialog"]')).not.toBeNull()
    await act(async () => {
      document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
    })
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    await openElement()
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })
})
