/**
 * 改图助手在「还没发过任务」那一刻的信息布局（审计 T37）。
 *
 * 原来这块是分成两头的：面板正中一个空状态（图标 + 「描述想要的改动」 + 一段
 * 「助手会做什么」），起手式与输入框在最底下。要发一条请求得先在中间读一段、
 * 再把视线拉到底下——两处争同一份注意力，而只有底下那处是能动手的。
 *
 * 现在（2026-10-07 设计审计 §6.5 / §6.7）：正中是 EmptyState v2 + 至多三条可点的示例提示（chipsFor 的前三条，
 * 点一下填进输入框）；输入框**上方**的上下文带里一枚「● 目标 · 作用范围」chip，发送前不必点开任何东西就答得出
 * 「按下去会改什么」；模型与推理强度是输入框工具行上的两颗可见胶囊。
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

const q = <T extends Element = HTMLElement>(sel: string) => host.querySelector<T>(sel)
const qa = <T extends Element = HTMLElement>(sel: string) => Array.from(host.querySelectorAll<T>(sel))
const typeDraft = async (text: string) => {
  const box = q<HTMLTextAreaElement>('[data-ai-input]')!
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(box, text)
    box.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

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
  /**
   * 2026-09-14 二审 D2（部分收回审计 T37）：「助手会做什么」那一句是**空态**，放回滚动区正中；
   * 2026-10-07 §6.7：空态换成 EmptyState v2，那一句是它的说明（hint）。
   */
  it('说明是滚动区里的空态，不和输入框叠在底部', async () => {
    await mount({ withPanel: true })
    const empty = q('[data-ai-empty]')
    expect(empty, '找不到空态').toBeTruthy()
    expect(q('[data-ai-scroller]')!.contains(empty)).toBe(true)
    expect(empty!.querySelector('[data-empty-state]')!.textContent).toContain(ai('panel.emptyHint'))
    expect(empty!.contains(q('[data-ai-input]'))).toBe(false)
  })

  it('空态里至多三条示例提示，取自 chipsFor；点一下填进输入框', async () => {
    await mount({ withPanel: true })
    const examples = qa<HTMLButtonElement>('[data-ai-example]')
    expect(examples.map((b) => b.dataset.aiExample)).toEqual(['unifyFont', 'unifyLineWidth', 'checkMinFontSize'])
    expect(examples[0].textContent).toBe(ai('chip.unifyFont'))
    await act(async () => examples[0].click())
    expect(q<HTMLTextAreaElement>('[data-ai-input]')!.value).toBe(ai('chip.unifyFont'))
  })

  it('空态时输入框上方不再重复摆同一组起手式（它们在正中）', async () => {
    await mount({ withPanel: true })
    expect(q('[data-ai-chips]')).toBeNull()
  })

  it('选不到可编辑的图时，正中那个空状态照旧——那是真的没活可干', async () => {
    await mount({ withPanel: false })
    expect(q('[data-ai-scroller]')!.textContent).toContain(ai('panel.noPanelTitle'))
    expect(q('[data-ai-example]')).toBeNull()
    expect(q('[data-ai-context]'), '没有目标时没有上下文带').toBeNull()
  })
})

describe('输入框两态（§6.5）', () => {
  it('空着时是紧凑的单行胶囊、没有工具行；一有内容就展开成两行，工具行里是模型与推理强度两颗胶囊', async () => {
    useAiStore.setState({ caps: capsOf([agentCaps({ efforts: ['low', 'high'], default_effort: 'high' })]) })
    await mount({ withPanel: true })
    const composer = q('[data-ai-composer]')!
    expect(composer.dataset.layout).toBe('compact')
    expect(q('[data-ai-pill]')).toBeNull()
    await typeDraft('把图例移到左上角')
    expect(composer.dataset.layout).toBe('expanded')
    expect(q('[data-ai-pill="model"]')!.textContent).toContain('Codex')
    expect(q('[data-ai-pill="effort"]')!.textContent).toContain(ai('effortLabel.high'))
    // 同一个输入框节点换了格子，不是重建（焦点不丢）
    const box = q('[data-ai-input]')
    await typeDraft('')
    expect(composer.dataset.layout).toBe('compact')
    expect(q('[data-ai-input]')).toBe(box)
  })

  it('聚焦只加深边框，不用 accent 边', async () => {
    await mount({ withPanel: true })
    const cls = q('[data-ai-composer]')!.className
    expect(cls).toContain('focus-within:border-border-strong')
    expect(cls).not.toContain('border-accent')
    expect(q('[data-ai-input]')!.className).toContain('text-base')
  })
})

describe('发送前的作用范围摘要（上下文带）', () => {
  it('输入框上方的上下文带直说「目标 · 作用范围」', async () => {
    await mount({ withPanel: true })
    const band = q('[data-ai-context]')
    expect(band, '找不到上下文带').toBeTruthy()
    // 带在输入框上方：DOM 顺序在前，而且不在输入框里
    const composer = q('[data-ai-composer]')!
    expect(band!.compareDocumentPosition(composer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const chip = q('[data-ai-target]')!
    expect(chip.textContent).toContain('Fig1')
    expect(chip.textContent).toContain(ai('scope.figure'))
  })

  /**
   * 2026-09-15 全面打磨 L6：作用范围只说一次。判据数的是**出现次数**，不是「有没有」：
   * 留一处的实现与留两处的实现，后者同样能通过「包含范围名」那种写法。
   */
  it('作用范围只说一次：只有上下文带的 chip 写着它', async () => {
    await mount({ withPanel: true })
    const scope = ai('scope.figure')
    await typeDraft('x') // 展开工具行：胶囊也在场时照样只有一处
    const withScope = qa('button').filter((b) => b.textContent?.includes(scope))
    expect(withScope).toHaveLength(1)
    expect(withScope[0].hasAttribute('data-ai-target')).toBe(true)
  })

  /**
   * 打磨 A5：发送钮左边那枚常驻的 `⌘↵` 删了——同一句话已经在发送钮的气泡里。
   */
  it('输入框上不再常驻一枚快捷键键帽，快捷键仍在发送钮的提示里（A5）', async () => {
    await mount({ withPanel: true })
    expect(host.querySelector('kbd')).toBeNull()
    expect(host.textContent ?? '').not.toContain('↵')
    const send = q('[data-ai-send="send"]')
    expect(send, '找不到发送钮').toBeTruthy()
    expect(send!.getAttribute('aria-label')).toBe(ai('panel.sendAria'))
  })

  it('发送钮是 26px 的圆（不是方块），没有 scale 换形', async () => {
    await mount({ withPanel: true })
    const send = q('[data-ai-send]')!
    expect(send.className).toContain('rounded-full')
    expect(send.className).toContain('h-[26px]')
    expect(send.innerHTML).not.toContain('scale-50')
  })
})
