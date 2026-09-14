/**
 * 共享原语的键盘契约（2026-09-14 apple-design 审计 S1 / S3 / S4）。
 *
 * 这三条此前一条都没有：Segmented 每格一个 Tab 停靠点、方向键不动；Tabs 同病；
 * 对话框打开后焦点停在标题栏的关闭钮上（Radix 默认给内容里第一个可聚焦元素）。
 * 键盘行为在鼠标截图里看不出来，所以判据写在这里而不是靠眼睛。
 */
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Dialog } from './Dialog'
import { Segmented } from './Segmented'
import { Tab, TabList, TabPanel } from './Tabs'
import { TooltipProvider } from './Tooltip'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

const render = (node: React.ReactNode) => act(async () => root.render(node))
const key = (el: Element, key: string) =>
  act(async () => {
    el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
  })
const radios = () => [...host.querySelectorAll<HTMLButtonElement>('[role="radio"]')]

describe('Segmented：radiogroup 的键盘契约', () => {
  function Harness({ initial = 'b', disabled = [] as string[] }) {
    // 受控：onChange 真的改 value，方向键才能连续漫游
    const [v, setV] = useState(initial as string | null)
    return (
      <Segmented
        value={v}
        onChange={setV}
        ariaLabel="方向"
        items={['a', 'b', 'c', 'd'].map((x) => ({ value: x, label: x, disabled: disabled.includes(x) }))}
      />
    )
  }

  it('整组只有一个 Tab 停靠点：选中项', async () => {
    await render(<Harness />)
    expect(radios().map((r) => r.tabIndex)).toEqual([-1, 0, -1, -1])
  })

  it('没有选中项（多选取值不一）时停靠点是第一个可用项', async () => {
    await render(<Harness initial={null as unknown as string} disabled={['a']} />)
    expect(radios().map((r) => r.tabIndex)).toEqual([-1, 0, -1, -1])
  })

  it('→ 选中下一格并把焦点与停靠点一起挪；← 回来；Home / End 到首尾', async () => {
    await render(<Harness />)
    radios()[1].focus()
    await key(radios()[1], 'ArrowRight')
    expect(radios().map((r) => r.getAttribute('aria-checked'))).toEqual(['false', 'false', 'true', 'false'])
    expect(document.activeElement).toBe(radios()[2])
    expect(radios().map((r) => r.tabIndex)).toEqual([-1, -1, 0, -1])
    await key(radios()[2], 'ArrowLeft')
    expect(radios()[1].getAttribute('aria-checked')).toBe('true')
    await key(radios()[1], 'End')
    expect(radios()[3].getAttribute('aria-checked')).toBe('true')
    expect(document.activeElement).toBe(radios()[3])
    await key(radios()[3], 'Home')
    expect(radios()[0].getAttribute('aria-checked')).toBe('true')
  })

  it('方向键跳过禁用项；到边界不回绕', async () => {
    await render(<Harness initial="b" disabled={['c']} />)
    radios()[1].focus()
    await key(radios()[1], 'ArrowRight')
    expect(radios()[3].getAttribute('aria-checked')).toBe('true')
    await key(radios()[3], 'ArrowRight')
    expect(radios()[3].getAttribute('aria-checked')).toBe('true')
  })
})

describe('Tabs：tablist 的键盘契约', () => {
  function Harness() {
    const [tab, setTab] = useState('a')
    return (
      <>
        <TabList label="视图">
          {['a', 'b', 'c'].map((id) => (
            <Tab key={id} panelId={`p-${id}`} active={tab === id} onClick={() => setTab(id)}>
              {id}
            </Tab>
          ))}
        </TabList>
        <TabPanel id={`p-${tab}`}>{tab}</TabPanel>
      </>
    )
  }
  const tabs = () => [...host.querySelectorAll<HTMLButtonElement>('[role="tab"]')]

  it('只有当前页在 Tab 顺序里；→ 换页并把焦点带过去；End / Home 到首尾', async () => {
    await render(<Harness />)
    expect(tabs().map((t) => t.tabIndex)).toEqual([0, -1, -1])
    tabs()[0].focus()
    await key(tabs()[0], 'ArrowRight')
    expect(tabs().map((t) => t.getAttribute('aria-selected'))).toEqual(['false', 'true', 'false'])
    expect(document.activeElement).toBe(tabs()[1])
    expect(tabs().map((t) => t.tabIndex)).toEqual([-1, 0, -1])
    await key(tabs()[1], 'End')
    expect(tabs()[2].getAttribute('aria-selected')).toBe('true')
    await key(tabs()[2], 'Home')
    expect(tabs()[0].getAttribute('aria-selected')).toBe('true')
  })

  it('页签与内容区互相指着：aria-controls → tabpanel，tabpanel 由页签命名', async () => {
    await render(<Harness />)
    const panel = host.querySelector('[role="tabpanel"]')!
    expect(panel.id).toBe('p-a')
    expect(tabs()[0].getAttribute('aria-controls')).toBe('p-a')
    expect(tabs()[0].id).toBe('p-a-tab')
    expect(panel.getAttribute('aria-labelledby')).toBe('p-a-tab')
  })
})

describe('Dialog：打开后的初始焦点', () => {
  // 用变量而不是 JSX 字面量：i18n lint 会把 JSX 里的字符串字面量当成漏翻的文案
  const TITLE = 'Export'
  it('落在对话框容器本身，不是标题栏的关闭钮', async () => {
    const onOpenChange = vi.fn()
    await render(
      <TooltipProvider>
        <Dialog open onOpenChange={onOpenChange} title={TITLE} anchor="t">
          <input aria-label="filename" />
          <button type="button">{TITLE}</button>
        </Dialog>
      </TooltipProvider>,
    )
    // Radix 的 mount autofocus 在 effect 里跑；等一拍
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!
    expect(document.activeElement).toBe(dialog)
    expect(document.activeElement).not.toBe(document.querySelector('[data-dialog-close]'))
    // 容器可聚焦但不在 Tab 顺序里：下一下 Tab 才进第一个控件
    expect(dialog.tabIndex).toBe(-1)
    // 第一下 Tab 进的是正文第一个控件，不是关闭钮：关闭钮 DOM 排在正文之后（视觉仍在右上角）
    const close = document.querySelector('[data-dialog-close]')!
    const first = document.querySelector('input[aria-label="filename"]')!
    expect(first.compareDocumentPosition(close) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
