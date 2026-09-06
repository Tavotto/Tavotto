/**
 * 改图助手在「还没发过任务」那一刻的信息布局（审计 T37）。
 *
 * 原来这块是分成两头的：面板正中一个空状态（图标 + 「描述想要的改动」 + 一段
 * 「助手会做什么」），起手式与输入框在最底下。要发一条请求得先在中间读一段、
 * 再把视线拉到底下——两处争同一份注意力，而只有底下那处是能动手的。
 *
 * 现在：正中留白，说明与起手式都贴着输入框；输入框旁那颗按钮直说「作用于：
 * 当前范围」，发送前不必点开任何东西就答得出「按下去会改什么」。
 *
 * 「选不到可编辑的图」是另一回事——那是真正的空状态，仍然留在正中。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { agentCaps, capsOf } from '@/components/settings/testCaps'
import { useAiStore } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { AssistantPanel } from './AiPanel'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const ai = (k: string, v?: Record<string, unknown>) => t(k, { ns: 'ai', ...(v ?? {}) })

const panel = (): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 0, y: 0, w: 100, h: 75,
    fileId: 'Fig1.pdf', fileKind: 'pdf', nativeW: 100, nativeH: 75,
    name: 'Fig1', script: '/tmp/figs/fig1.py', overrides: [],
  }) as unknown as PanelObject

let root: Root
let host: HTMLDivElement

/** 有没有选中一张可编辑的图，是两条完全不同的路径 */
async function mount({ withPanel }: { withPanel: boolean }) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_assistant')
  if (withPanel) {
    useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [panel()] } }) as never)
    useSelectionStore.setState({ ids: ['p1'] } as never)
  } else {
    useSelectionStore.setState({ ids: [] } as never)
  }
  useUiStore.setState({ elementPanelId: null, selectedGids: [] } as never)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <AssistantPanel />
      </TooltipProvider>,
    )
  })
}

const textOf = () => host.textContent ?? ''
const buttons = () => Array.from(host.querySelectorAll('button'))

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
  useAiStore.setState({
    sessions: [],
    scope: 'figure',
    caps: capsOf([agentCaps()]),
    agent: 'codex',
    models: {},
    efforts: {},
  })
})

afterEach(async () => {
  await act(async () => root?.unmount())
})

describe('还没发过任务时的信息布局', () => {
  it('说明贴着输入框，不再占着面板正中', async () => {
    await mount({ withPanel: true })
    // 那一句还在（用户仍然读得到助手会做什么）……
    expect(textOf()).toContain(ai('panel.emptyHint'))
    // ……但它和输入框在同一块里，而不是滚动区中间那个空状态
    const hint = Array.from(host.querySelectorAll('p')).find(
      (p) => p.textContent === ai('panel.emptyHint'),
    )
    expect(hint, '找不到那句说明').toBeTruthy()
    const box = host.querySelector('textarea')
    expect(box, '找不到输入框').toBeTruthy()
    expect(
      hint!.parentElement!.contains(box!),
      '说明和输入框不在同一块里 —— 注意力又被扯成两处',
    ).toBe(true)
  })

  it('正中不再摆「描述想要的改动」那个空状态', async () => {
    await mount({ withPanel: true })
    const scroller = host.querySelector('.overflow-y-auto')!
    expect(scroller.textContent?.trim(), '滚动区里还有东西在跟输入框抢注意力').toBe('')
  })

  it('起手式仍然在输入框上方，点一下填进输入框', async () => {
    await mount({ withPanel: true })
    const chip = buttons().find((b) => b.textContent === ai('chip.unifyFont'))
    expect(chip, '起手式不见了').toBeTruthy()
    await act(async () => chip!.click())
    expect((host.querySelector('textarea') as HTMLTextAreaElement).value).toBe(
      ai('chip.unifyFont'),
    )
  })

  it('选不到可编辑的图时，正中那个空状态照旧——那是真的没活可干', async () => {
    await mount({ withPanel: false })
    const scroller = host.querySelector('.overflow-y-auto')!
    expect(scroller.textContent).toContain(ai('panel.noPanelTitle'))
  })
})

describe('发送前的作用范围摘要', () => {
  it('输入框旁那颗按钮直说「作用于：…」，不是光一个范围名', async () => {
    await mount({ withPanel: true })
    const btn = buttons().find((b) =>
      b.getAttribute('aria-label') === ai('panel.scopeAndAgent'),
    )
    expect(btn, '找不到作用范围按钮').toBeTruthy()
    expect(btn!.textContent).toContain(ai('panel.actsOn', { scope: ai('scope.figure') }))
    // 交给谁执行也写在同一行上
    expect(btn!.textContent).toContain('Codex')
  })
})
