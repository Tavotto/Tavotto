/**
 * Notice 原语（2026-10-16 UI 2.0「错误与警告」）：一行中性提示。
 * 判据全是正面形式：文字不染色、只有 attention 档（danger）图标染色、详情默认折叠且可复制、一个主动作、
 * 所有语气的 a11y role 不变。选择器认 data-*，类名断言只证明「走了哪一档 token」。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Button } from './Button'
import { Notice, NoticeReveal, type StatusTone } from './Notice'
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
const render = (node: React.ReactNode) => act(async () => root.render(<TooltipProvider>{node}</TooltipProvider>))
const q = <E extends Element = HTMLElement>(sel: string) => host.querySelector<E>(sel)
const T = 'x'
const NEXT = 'next line'
const TONES: StatusTone[] = ['neutral', 'info', 'ok', 'warn', 'danger']

describe('Notice：一行中性提示', () => {
  it.each(TONES)('tone=%s：底是中性 surface-hover，无边框 / 无彩色底，文字不染色', async (tone) => {
    await render(<Notice tone={tone} title="没有发出去">原因</Notice>)
    const n = q(`[data-notice="${tone}"]`)!
    expect(n.className).toContain('bg-surface-hover')
    expect(n.className).toContain('text-ink')
    // 正面形式：根上只允许 ink 系文字色 / 中性底，没有任何语义色 token、没有描边环
    expect(n.className).not.toMatch(/(?:danger|warn|ok|info)(?:-surface|-border|-content)?\b/)
    expect(n.className).not.toMatch(/\b(?:border|ring|inset-ring)\b/)
    // 标题与次要文字都没有自带语义色
    for (const el of n.querySelectorAll('p, div')) expect(el.className).not.toMatch(/text-(?:danger|warn|ok|info)/)
  })

  it.each(TONES)('tone=%s：只有 danger 档的图标染色（danger 色），其余 ink-3', async (tone) => {
    await render(<Notice tone={tone}>x</Notice>)
    const icon = q('[data-notice-icon]')!
    expect(icon).not.toBeNull()
    if (tone === 'danger') {
      expect(icon.getAttribute('data-notice-icon')).toBe('attention')
      expect(icon.getAttribute('class')).toContain('text-danger')
    } else {
      expect(icon.getAttribute('data-notice-icon')).toBe('')
      expect(icon.getAttribute('class')).toContain('text-ink-3')
      expect(icon.getAttribute('class')).not.toMatch(/text-(?:danger|warn|ok|info)\b/)
    }
  })

  it.each(TONES)('tone=%s：a11y role 不变（只有 danger 是 alert）', async (tone) => {
    await render(<Notice tone={tone}>x</Notice>)
    expect(q(`[data-notice="${tone}"]`)!.getAttribute('role')).toBe(tone === 'danger' ? 'alert' : null)
  })

  it('调用方显式传的 role 照旧生效（status / alert）', async () => {
    await render(<Notice tone="warn" role="status">x</Notice>)
    expect(q('[data-notice]')!.getAttribute('role')).toBe('status')
  })

  it('title + 字符串 children：同一行「主句 · 原因」，原因是次要灰字；title + 节点 children：另起一行的次要灰字；只有 children：就是主句（ink）', async () => {
    await render(
      <>
        <Notice data-a title="主句">原因</Notice>
        <Notice data-b title="主句"><span>{NEXT}</span></Notice>
        <Notice data-c>只有一句</Notice>
      </>,
    )
    expect(q('[data-a] p')!.textContent).toBe('主句 · 原因')
    expect(q('[data-a] span.text-ink-2')!.textContent).toBe(' · 原因')
    expect(q('[data-b] p')!.textContent).toBe('主句')
    expect(q('[data-b] div.text-ink-2')!.textContent).toBe(NEXT)
    expect(q('[data-c] div.text-ink-2, [data-c] span.text-ink-2')).toBeNull()
  })

  it('没有 detail 就没有「详情」钮；动作槽里是调用方给的那一个', async () => {
    const onClick = vi.fn()
    await render(<Notice action={<Button data-act onClick={onClick}>{T}</Button>}>x</Notice>)
    expect(q('[data-notice-detail-toggle]')).toBeNull()
    expect(host.querySelectorAll('[data-notice] button')).toHaveLength(1)
    await act(async () => q<HTMLButtonElement>('[data-act]')!.click())
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('详情默认折叠（不在 DOM 里）；点「详情」展开，aria-expanded 同步；再点收起', async () => {
    await render(<Notice tone="danger" detail="HTTP 500 · upstream timeout">没有发出去</Notice>)
    const toggle = q<HTMLButtonElement>('[data-notice-detail-toggle]')!
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(q('[data-notice-detail]')).toBeNull()
    expect(host.textContent).not.toContain('upstream timeout')
    await act(async () => toggle.click())
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(q('[data-notice-detail]')!.textContent).toContain('upstream timeout')
    await act(async () => toggle.click())
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
  })

  it('详情里的「复制」把它渲染出来的文字写进剪贴板，并给一个「已复制」回执', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    await render(
      <Notice tone="danger" detail={<>HTTP 500<br />request 3f9a</>}>没有发出去</Notice>,
    )
    await act(async () => q<HTMLButtonElement>('[data-notice-detail-toggle]')!.click())
    const copy = q<HTMLButtonElement>('[data-notice-copy]')!
    await act(async () => copy.click())
    expect(writeText).toHaveBeenCalledWith('HTTP 500request 3f9a')
    expect(copy.textContent).toBe('已复制')
  })
})

describe('NoticeReveal：退场保活期间旧子树不可交互', () => {
  it('open 变 false 后，旧按钮所在的子树 inert + aria-hidden；重新打开后撤掉', async () => {
    const T2 = 'old'
    const view = (open: boolean) => (
      <NoticeReveal open={open}>
        <Notice action={<Button data-old-action>{T2}</Button>}>{T2}</Notice>
      </NoticeReveal>
    )
    await render(view(true))
    expect(q('[data-reveal]')!.hasAttribute('inert')).toBe(false)
    await render(view(false))
    // 收起动画还在播：旧子树仍挂着，但已 inert，读屏也不念
    const closing = q('[data-reveal]')!
    expect(closing.getAttribute('data-state')).toBe('closed')
    expect(closing.hasAttribute('inert')).toBe(true)
    expect(closing.getAttribute('aria-hidden')).toBe('true')
    expect(closing.querySelector('[data-old-action]')).not.toBeNull()
    await render(view(true))
    expect(q('[data-reveal]')!.hasAttribute('inert')).toBe(false)
  })
})

describe('Notice：同一槽位换成另一条，详情状态不沿用', () => {
  it('展开并复制过一条后换成另一条：新的一条默认收起、没有「已复制」；换回来的字符串身份也算新的', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: () => Promise.resolve() }, configurable: true })
    const view = (title: string, detail: string) => <Notice title={title} detail={detail} />
    await render(view('first', 'detail one'))
    await act(async () => q<HTMLButtonElement>('[data-notice-detail-toggle]')!.click())
    await act(async () => q<HTMLButtonElement>('[data-notice-copy]')!.click())
    expect(q('[data-notice-copy]')!.textContent).toBe('已复制')
    await render(view('second', 'detail two'))
    expect(q('[data-notice-detail-toggle]')!.getAttribute('aria-expanded')).toBe('false')
    // 收起动画还在播时旧节点仍挂着（inert），所以认「收起态」而不是「不在 DOM」
    expect(q('[data-reveal]')?.getAttribute('data-state') ?? 'closed').toBe('closed')
    await act(async () => q<HTMLButtonElement>('[data-notice-detail-toggle]')!.click())
    expect(q('[data-notice-copy]')!.textContent).not.toBe('已复制')
  })

  it('detailKey 不变时，标题 / 详情的译文换了（切语言）展开状态保持', async () => {
    const view = (title: string, detail: string) => <Notice title={title} detailKey="same" detail={detail} />
    await render(view('Cancel failed', 'long english detail'))
    await act(async () => q<HTMLButtonElement>('[data-notice-detail-toggle]')!.click())
    await render(view('取消失败', '很长的中文详情'))
    expect(q('[data-notice-detail-toggle]')!.getAttribute('aria-expanded')).toBe('true')
    expect(q('[data-notice-detail]')!.textContent).toContain('很长的中文详情')
  })

  it('节点型 detail 靠 detailKey 区分', async () => {
    const view = (k: string) => <Notice title={T} detailKey={k} detail={<b>{k}</b>} />
    await render(view('a'))
    await act(async () => q<HTMLButtonElement>('[data-notice-detail-toggle]')!.click())
    expect(q('[data-notice-detail]')).not.toBeNull()
    await render(view('b'))
    expect(q('[data-notice-detail-toggle]')!.getAttribute('aria-expanded')).toBe('false')
    expect(q('[data-reveal]')?.getAttribute('data-state') ?? 'closed').toBe('closed')
  })
})
