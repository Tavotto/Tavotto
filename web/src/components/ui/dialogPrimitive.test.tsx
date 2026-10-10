/**
 * Dialog 原语的 2026-10-07 契约（宪法第五节 / 第二十六节）：宽度五档、页脚三槽、Esc = 安全答案、
 * 栈底才画遮罩；以及两个调用方（ConfirmDialog / CloseGuardDialog 的确认框那一侧）的 Esc 与危险键形态。
 * 选择器只认 data-*（web/AGENTS.md）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { msg } from '@/i18n'
import { askConfirm, useUiStore } from '@/store/uiStore'
import { ConfirmDialog } from '../ConfirmDialog'
import { Button } from './Button'
import { Dialog } from './Dialog'
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
  useUiStore.getState().setConfirm(null)
})

const render = (node: React.ReactNode) => act(async () => root.render(<TooltipProvider>{node}</TooltipProvider>))
const escape = () =>
  act(async () => {
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
  })
const TITLE = 'T'
const BODY = 'body'
const dialogEl = (anchor: string) => document.querySelector<HTMLElement>(`[data-dialog="${anchor}"]`)

describe('Dialog：宽度五档', () => {
  it.each([
    ['sm', '400px'],
    ['md', '480px'],
    ['lg', '560px'],
    ['xl', '760px'],
    ['shell', '1000px'],
  ] as const)('size=%s → %s', async (size, width) => {
    await render(
      <Dialog open onOpenChange={() => {}} title={TITLE} size={size} anchor="w">
        <p>{BODY}</p>
      </Dialog>,
    )
    expect(dialogEl('w')!.style.width).toBe(width)
  })

  it('外壳是 rounded-panel；shell 尺寸自带 shell 外壳（正文不滚、不带内边距）', async () => {
    await render(
      <Dialog open onOpenChange={() => {}} title={TITLE} size="shell" anchor="s">
        <p data-probe>{BODY}</p>
      </Dialog>,
    )
    const d = dialogEl('s')!
    expect(d.className).toContain('rounded-panel')
    const body = d.querySelector('[data-probe]')!.parentElement!
    expect(body.className).not.toContain('overflow-y-auto')
  })
})

describe('Dialog：页脚三槽', () => {
  it('[start] …… [secondary] [primary]：DOM 顺序就是视觉顺序，中间一个弹性空位', async () => {
    await render(
      <Dialog
        open
        onOpenChange={() => {}}
        title={TITLE}
        anchor="f"
        footer={{
          start: <Button data-slot-btn="start">{BODY}</Button>,
          secondary: <Button data-slot-btn="secondary">{BODY}</Button>,
          primary: <Button data-slot-btn="primary">{BODY}</Button>,
        }}
      >
        <p>{BODY}</p>
      </Dialog>,
    )
    const footer = dialogEl('f')!.querySelector('[data-dialog-footer]')!
    const order = [...footer.querySelectorAll('[data-slot-btn]')].map((b) => b.getAttribute('data-slot-btn'))
    expect(order).toEqual(['start', 'secondary', 'primary'])
    expect(footer.querySelector('.flex-1')).not.toBeNull()
    // 默认外壳里页脚叠在正文滚动区底边（内容从它底下滑过）
    expect(footer.getAttribute('data-floating')).toBe('true')
    expect(footer.className).toContain("z-sticky")
  })

  it('旧写法（一段 ReactNode）照旧可用：整段右对齐', async () => {
    await render(
      <Dialog open onOpenChange={() => {}} title={TITLE} anchor="o" footer={<Button data-old-footer>{BODY}</Button>}>
        <p>{BODY}</p>
      </Dialog>,
    )
    const btn = dialogEl('o')!.querySelector('[data-dialog-footer] [data-old-footer]')!
    expect(btn.parentElement!.className).toContain('justify-end')
  })

  it('有 status 区时页脚落在它下面、不吸附在正文里', async () => {
    await render(
      <Dialog open onOpenChange={() => {}} title={TITLE} anchor="st" status={<p>{BODY}</p>} footer={{ primary: <Button>{BODY}</Button> }}>
        <p>{BODY}</p>
      </Dialog>,
    )
    const d = dialogEl('st')!
    const status = d.querySelector('[data-dialog-status]')!
    const footer = d.querySelector('[data-dialog-footer]')!
    expect(status.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(footer.hasAttribute('data-floating')).toBe(false)
  })
})

describe('Dialog：status 出现 / 消失时页脚不重挂载', () => {
  it('页脚按钮是同一个节点（焦点在它上面时不会掉到 body；ExportDialog 的「开始导出 → 完成」靠这一条）', async () => {
    const ui = (st: boolean) => (
      <Dialog open onOpenChange={() => {}} title={TITLE} anchor="keep" status={st ? <p>{BODY}</p> : null} footer={{ primary: <Button data-keep>{BODY}</Button> }}>
        <p>{BODY}</p>
      </Dialog>
    )
    await render(ui(false))
    const before = document.querySelector('[data-keep]')
    await render(ui(true))
    expect(document.querySelector('[data-keep]')).toBe(before)
  })
})

describe('Dialog：Esc = 安全答案', () => {
  it('blockDismiss + onEscape：Esc 调安全答案，不调 onOpenChange', async () => {
    const onEscape = vi.fn()
    const onOpenChange = vi.fn()
    await render(
      <Dialog open onOpenChange={onOpenChange} title={TITLE} blockDismiss onEscape={onEscape} anchor="e">
        <p>{BODY}</p>
      </Dialog>,
    )
    await escape()
    expect(onEscape).toHaveBeenCalledTimes(1)
    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it('busy 时 Esc 什么都不做（即使给了 onEscape）', async () => {
    const onEscape = vi.fn()
    await render(
      <Dialog open onOpenChange={() => {}} title={TITLE} busy onEscape={onEscape} anchor="b">
        <p>{BODY}</p>
      </Dialog>,
    )
    await escape()
    expect(onEscape).not.toHaveBeenCalled()
  })

  it('对照组：只有 blockDismiss、没有 onEscape 时 Esc 不算回答', async () => {
    const onOpenChange = vi.fn()
    await render(
      <Dialog open onOpenChange={onOpenChange} title={TITLE} blockDismiss anchor="c">
        <p>{BODY}</p>
      </Dialog>,
    )
    await escape()
    expect(onOpenChange).not.toHaveBeenCalled()
  })
})

// Codex #833（comment 4211499735）：关闭时的焦点归还只在焦点还在这层里（层卸掉后落在 body）时做；
// 关的同时焦点已被交给这层之外的元素（命令面板的命令打开了一个就地表面）就不抢回来
describe('Dialog：关闭时的焦点归还', () => {
  const settle = () => act(async () => new Promise((r) => setTimeout(r, 20)))
  function Probe({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange} title={TITLE} anchor="f">
        <input data-probe-input />
      </Dialog>
    )
  }

  it('Esc 关：焦点还给打开前的元素', async () => {
    const opener = document.createElement('button')
    document.body.appendChild(opener)
    opener.focus()
    let open = true
    const set = (v: boolean) => {
      open = v
      void render(<Probe open={open} onOpenChange={set} />)
    }
    await render(<Probe open onOpenChange={set} />)
    expect(dialogEl('f')!.contains(document.activeElement)).toBe(true)
    await escape()
    await settle()
    expect(open).toBe(false)
    expect(document.activeElement).toBe(opener)
    opener.remove()
  })

  // CI 满载时命令面板的归还用例撞见过 body：Radix 的归还排在 Presence 卸载之后的一个 setTimeout 里，
  // 那之间焦点悬空。这里把定时器整个扣住，证明归还不靠它——关上的那次提交里焦点就已经回到打开者
  it('关上的那次提交里就还回去，不等 Radix 那个延后的定时器', async () => {
    const opener = document.createElement('button')
    document.body.appendChild(opener)
    opener.focus()
    await render(<Probe open onOpenChange={() => {}} />)
    expect(dialogEl('f')!.contains(document.activeElement)).toBe(true)
    const held: (() => void)[] = []
    const spy = vi.spyOn(globalThis, 'setTimeout').mockImplementation(((fn: () => void) => {
      held.push(fn)
      return 0
    }) as unknown as typeof setTimeout)
    try {
      await render(<Probe open={false} onOpenChange={() => {}} />)
      expect(document.activeElement).toBe(opener)
    } finally {
      spy.mockRestore()
    }
    await act(async () => held.forEach((fn) => fn()))
    expect(document.activeElement).toBe(opener)
    opener.remove()
  })

  it('关的同时焦点交给了这层之外的元素：不抢回打开前的元素', async () => {
    const opener = document.createElement('button')
    const target = document.createElement('input')
    document.body.append(opener, target)
    opener.focus()
    await render(<Probe open onOpenChange={() => {}} />)
    await render(<Probe open={false} onOpenChange={() => {}} />)
    target.focus()
    await settle()
    expect(document.activeElement).toBe(target)
    opener.remove()
    target.remove()
  })

  // Codex #833（erwanjun 复核 8a349482）：退场动画没放完又打开，Presence 留着同一个 Content、不重挂载，
  // 挂载时的初始焦点不再跑。jsdom 没有 CSS 动画：按 data-state 报 animationName，Presence 才会等 animationend
  describe('退场中被重新打开（同一个 Content）', () => {
    let spy: { mockRestore: () => void }
    beforeEach(() => {
      const real = window.getComputedStyle
      spy = vi.spyOn(window, 'getComputedStyle').mockImplementation((el: Element, pseudo?: string | null) => {
        const styles = real(el, pseudo)
        if (!(el instanceof HTMLElement) || !el.hasAttribute('data-dialog')) return styles
        return new Proxy(styles, {
          get: (t, p) =>
            p === 'animationName' ? (el.getAttribute('data-state') === 'closed' ? 'pop-out' : 'pop-in') : Reflect.get(t, p),
        })
      })
    })
    afterEach(() => spy.mockRestore())

    it('焦点回到层里（初始落点：容器），不留在被模态层 aria-hidden 的打开者上；再关仍还给打开者', async () => {
      const opener = document.createElement('button')
      document.body.appendChild(opener)
      opener.focus()
      await render(<Probe open onOpenChange={() => {}} />)
      const content = dialogEl('f')!
      expect(document.activeElement).toBe(content)
      await render(<Probe open={false} onOpenChange={() => {}} />)
      expect(dialogEl('f'), '退场中仍是同一个 Content').toBe(content)
      expect(document.activeElement).toBe(opener)
      await render(<Probe open onOpenChange={() => {}} />)
      expect(dialogEl('f')).toBe(content)
      expect(opener.closest('[aria-hidden="true"]')).not.toBeNull()
      expect(document.activeElement).toBe(content)
      await render(<Probe open={false} onOpenChange={() => {}} />)
      expect(document.activeElement).toBe(opener)
      opener.remove()
    })

    it('重开之前焦点已交给层外一个没被藏起来的元素：不抢', async () => {
      const opener = document.createElement('button')
      document.body.appendChild(opener)
      opener.focus()
      await render(<Probe open onOpenChange={() => {}} />)
      const content = dialogEl('f')!
      await render(<Probe open={false} onOpenChange={() => {}} />)
      // 模态层挂上之后才出现的表面（hideOthers 只藏挂载那一刻已有的兄弟）
      const target = document.createElement('input')
      document.body.appendChild(target)
      target.focus()
      await render(<Probe open onOpenChange={() => {}} />)
      expect(dialogEl('f')).toBe(content)
      expect(target.closest('[aria-hidden="true"], [inert]')).toBeNull()
      expect(document.activeElement).toBe(target)
      opener.remove()
      target.remove()
    })
  })
})

describe('Dialog：遮罩只由栈底那个画', () => {
  it('两层叠开：只有先开的那层带 data-dialog-scrim；上层关掉后下层仍画', async () => {
    const two = (top: boolean) => (
      <>
        <Dialog open onOpenChange={() => {}} title={TITLE} anchor="bottom">
          <p>{BODY}</p>
        </Dialog>
        <Dialog open={top} onOpenChange={() => {}} title={TITLE} anchor="top">
          <p>{BODY}</p>
        </Dialog>
      </>
    )
    await render(two(true))
    expect(document.querySelectorAll('[data-dialog-scrim]')).toHaveLength(1)
    await render(two(false))
    expect(document.querySelectorAll('[data-dialog-scrim]')).toHaveLength(1)
  })

  it('栈底那层被 covered 时，盖着它的那层接过遮罩', async () => {
    await render(
      <>
        <Dialog open covered onOpenChange={() => {}} title={TITLE} anchor="under">
          <p>{BODY}</p>
        </Dialog>
        <Dialog open onOpenChange={() => {}} title={TITLE} anchor="over">
          <p>{BODY}</p>
        </Dialog>
      </>,
    )
    const scrims = document.querySelectorAll('[data-dialog-scrim]')
    expect(scrims).toHaveLength(1)
    expect(scrims[0].className).not.toContain('invisible')
  })
})

describe('ConfirmDialog：Esc = 取消、危险确认是浅底危险胶囊、页脚 32px', () => {
  it('Esc 回答 false 并关掉', async () => {
    await render(<ConfirmDialog />)
    let answer: boolean | undefined
    await act(async () => {
      void askConfirm({ title: msg('actions.delete', undefined, 'common'), body: msg('actions.delete', undefined, 'common'), danger: true }).then((v) => (answer = v))
    })
    expect(dialogEl('confirm')).not.toBeNull()
    await escape()
    await act(async () => {})
    expect(answer).toBe(false)
    expect(useUiStore.getState().confirm).toBeNull()
  })

  it('danger 的确认键是 danger-tinted + lg，不是红字 ghost；取消也是 lg', async () => {
    await render(<ConfirmDialog />)
    await act(async () => {
      void askConfirm({ title: msg('actions.delete', undefined, 'common'), body: msg('actions.delete', undefined, 'common'), danger: true })
    })
    const ok = document.querySelector<HTMLButtonElement>('[data-confirm-ok]')!
    expect(ok.getAttribute('data-variant')).toBe('danger-tinted')
    expect(ok.className).toContain('h-8')
    expect(document.querySelector('[data-confirm-cancel]')!.className).toContain('h-8')
  })
})
