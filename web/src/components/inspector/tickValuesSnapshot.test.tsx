/**
 * 「固定刻度值」输入框清空时的语义：**定格当前刻度**，不是提交一个「空」。
 *
 * 为什么要盯住：空列表的含义要到应用那一刻才由引擎解析成具体位置（脚本原样
 * 的那组），所以提交「空」会让画面当场跳一下——而用户按下删除键时想的是
 * 「就保持现在这个样子」。定格成真数字则所见即所得，而且这组值实打实进了
 * 文档，重开 / 写回 / 换台机器都是同一张图。
 *
 * 想让刻度重新跟着脚本走，那是「自动」档的事，不是清空的事。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { EditableField, EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { resetPreview } from '@/store/svgPreviewStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { ElementInspector } from './ElementInspector'

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField

/** x 主刻度组：模式=固定，当前刻度 5 / 10 / 15（manifest 的这个字段是实况回读） */
const ticksEl: ManifestElement = {
  gid: 'axes_0.xticks',
  role: 'ticks',
  label: 'x 刻度',
  bbox: [0.1, 0.9, 0.8, 0.05],
  draggable: false,
  editable: [
    f('major_mode', 'enum', 'fixed', { options: ['auto', 'step', 'fixed'] }),
    f('major_values', 'number_list', [5, 10, 15]),
  ],
}

const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [101.6, 76.2],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    ticksEl,
  ],
}

const panelOf = (): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 0, y: 0, w: 101.6, h: 76.2,
    fileId: 'Fig1.pdf', fileKind: 'pdf', nativeW: 101.6, nativeH: 76.2,
    script: 'fig.py', overrides: [{ gid: 'axes_0.xticks', prop: 'major_values', value: [5, 10, 15] }],
  }) as unknown as PanelObject

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}
const valuesOverride = () =>
  livePanel().overrides.find((o) => o.gid === 'axes_0.xticks' && o.prop === 'major_values')?.value

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

/** 那个数值列表输入框：按当前显示的内容找，不依赖界面语言 */
const listInput = (): HTMLInputElement => {
  const all = Array.from(host.querySelectorAll<HTMLInputElement>('input'))
  const hit = all.find((el) => /^\s*\d/.test(el.value) && el.value.includes(','))
  if (!hit) throw new Error(`找不到数值列表输入框（现有：${all.map((e) => e.value).join(' | ')}）`)
  return hit
}

/**
 * React 的 `onBlur` 挂的是原生 **focusout**（blur 本身不冒泡，合成事件靠
 * focusout 委托）。发 `new FocusEvent('blur')` 处理器根本不会跑——而那样写
 * 出来的用例会「通过」，因为它断言的是「没有写进文档」：什么都没发生自然
 * 也没写。假绿比红更糟。
 */
const blur = (el: HTMLElement) =>
  el.dispatchEvent(new FocusEvent('focusout', { bubbles: true }))

/** React 19 的 value tracker：必须经原生 setter 写值，否则 onChange 被跳过 */
function typeInto(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

beforeEach(async () => {
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: '<svg/>', warnings: [] })
  resetPreview()
  localStorage.clear()
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_ticklist')
  useDocumentStore.getState().commit({ key: 'x', ns: 'common' } as never, (d) => {
    d.objects.push(panelOf())
  })
  const panel = livePanel()
  useRenderStore.setState({
    byKey: {
      [renderKeyOf(panel)]: {
        fileId: 'Fig1.pdf', rev: 1, manifest, svg: '<svg/>', status: 'ready',
        error: null, code: '', module: '', traceback: '', warnings: [],
        lastPatches: JSON.stringify(panel.overrides),
      } as never,
    },
    latest: { 'Fig1.pdf': manifest } as never,
  })
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.xticks'] })

  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<Harness />)
  })
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('固定刻度值：清空 = 定格当前刻度', () => {
  it('清空再失焦不写「空」，文档里仍是那组数字', async () => {
    const input = listInput()
    expect(input.value).toBe('5, 10, 15')

    await act(async () => {
      typeInto(input, '')
      blur(input)
    })

    // 关键断言：绝不能把空列表写进文档——那条 patch 的含义要等引擎解析，
    // 画面会跳回脚本原样的那组
    expect(valuesOverride()).toEqual([5, 10, 15])
    expect(engineRender).not.toHaveBeenCalled()      // 值没变，不劳烦 matplotlib
  })

  it('清空之后输入框把定格下来的那组显示回去（不留一个空框）', async () => {
    const input = listInput()
    await act(async () => {
      typeInto(input, '')
      blur(input)
    })
    expect(input.value).toBe('5, 10, 15')
  })

  it('真的改了值照旧提交（快照只接管「清空」这一种输入）', async () => {
    const input = listInput()
    await act(async () => {
      typeInto(input, '0, 1, 2')
      blur(input)
    })
    expect(valuesOverride()).toEqual([0, 1, 2])
  })
})
