/**
 * 通知轨（`NotificationRail`）上「到时自己走」的两条——状态与操作提示——在指针停在上面、焦点落在它的
 * 按钮上时不走表；toast 卸载时把按住的原因一并放开；同一条 toast 换文字时原位换（宪法第二十三节）。
 *
 * 判据的主语是 **store 里那条消息还在不在**（`uiStore.status` / `useHintStore.current`），不是 DOM：
 * DOM 上的 toast 退场还要保活 90ms，拿它当主语会把「正在退场」读成「还在」。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  setProjectEnvironment: vi.fn(),
  prepareDiagSend: vi.fn(),
  startDiagSend: vi.fn(),
}))

vi.mock('@/lib/desktop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/desktop')>()),
  restartEngine: vi.fn(async () => true),
}))

import { NotificationRail } from '@/components/StatusBar'
import { restartEngine } from '@/lib/desktop'
import { useExitNoticeStore } from '@/store/exitNoticeStore'
import { literal, t } from '@/i18n'
import { prepareDiagSend, setProjectEnvironment, startDiagSend } from '@/lib/api'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useUnexpectedErrorStore } from '@/store/unexpectedErrorStore'
import { useWorkspaceStore } from '@/store/workspace'
import { ADOPTED_NOTICE_MS, useEnvStore } from '@/store/envStore'
import { useRenderStore } from '@/store/renderStore'
import { HINT_AUTO_DISMISS_MS, showHint, useHintStore } from '@/lib/onboarding/hints'
import { useOnboardingStore } from '@/store/onboardingStore'
import { STATUS_AUTO_DISMISS_MS, useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

const status = () => useUiStore.getState().status
const statusToast = () => container.querySelector<HTMLElement>('[data-status-live]')!.parentElement!.querySelector<HTMLElement>('[data-state]:not([data-onboarding-hint]):not([data-fast-edit-added-note])')
const hintToast = () => container.querySelector<HTMLElement>('[data-onboarding-hint]')

const pointer = (el: Element, type: 'pointerover' | 'pointerout') =>
  act(() => {
    el.dispatchEvent(new MouseEvent(type, { bubbles: true, relatedTarget: document.body }))
  })
const tick = (ms: number) => act(() => vi.advanceTimersByTime(ms))
const say = (text: string) => act(() => useUiStore.getState().setStatus(literal(text)))

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  useOnboardingStore.getState().resetHints()
  useHintStore.setState({ current: null, token: 0 })
  useUiStore.setState({ status: null, statusTone: 'info' })
  useEnvStore.getState().dismissAdoptedEnvironment()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<NotificationRail />))
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  act(() => useUiStore.getState().setStatus(null))
  useHintStore.getState().dismiss()
  vi.useRealTimers()
})

describe('状态 toast 的自动收起', () => {
  it('没人碰：到时自己走', () => {
    say('已导出')
    tick(STATUS_AUTO_DISMISS_MS - 1)
    expect(status()).not.toBeNull()
    tick(1)
    expect(status()).toBeNull()
  })

  it('指针停在上面不走表；离开后只数剩下的那截', () => {
    say('已导出')
    tick(1000)
    pointer(statusToast()!, 'pointerover')
    tick(60_000)
    expect(status(), '指针在上面的一分钟里不许消失').not.toBeNull()
    pointer(statusToast()!, 'pointerout')
    tick(STATUS_AUTO_DISMISS_MS - 1000 - 1)
    expect(status(), '离开后剩 3.5s，还差 1ms').not.toBeNull()
    tick(1)
    expect(status()).toBeNull()
  })

  it('指针停着时新状态顶掉旧状态：新的一条也不走', () => {
    say('已写回 fig3.py')
    pointer(statusToast()!, 'pointerover')
    say('已导出 figure_3.pdf')
    tick(60_000)
    expect(status()).not.toBeNull()
    pointer(statusToast()!, 'pointerout')
    tick(STATUS_AUTO_DISMISS_MS)
    expect(status()).toBeNull()
  })

  it('toast 卸载时放开按住的原因：指针停着时它被关掉，下一条状态照常走', () => {
    say('已导出')
    pointer(statusToast()!, 'pointerover')
    act(() => useUiStore.getState().setStatus(null))
    tick(200) // 退场保活 90ms 之后卸载
    expect(statusToast()).toBeNull()
    say('已保存')
    tick(STATUS_AUTO_DISMISS_MS)
    expect(status(), '上一条被关时指针还在上面，pointerleave 不会再来；不放开的话这条永远不走').toBeNull()
  })

  it('同一条 toast 换文字：原位换，DOM 文本是新字、旧句在 data-ghost 里', () => {
    say('已写回 fig3.py')
    say('已导出 figure_3.pdf')
    const box = statusToast()!.querySelector<HTMLElement>('.swap-text')!
    expect(box.textContent).toBe('已导出 figure_3.pdf')
    expect(box.dataset.ghost).toBe('已写回 fig3.py')
  })
})

describe('状态 toast 的语气（2026-10-07 审计 P0：此前非错误一律打 ✓，「正在构建…」也打勾）', () => {
  // 主语：此刻那条状态 toast 的 `data-status-tone`（图标由它一处取），不认图标类名
  const tone = () => statusToast()!.dataset.statusTone
  const shape = () => statusToast()!.querySelector('svg')!.getAttribute('class') ?? ''

  it('缺省是 info：中性图标，不打勾', () => {
    say('没有可撤销的操作')
    expect(tone()).toBe('info')
    expect(shape()).toContain('icon-info')
    expect(shape()).not.toContain('icon-check')
  })

  it('progress：转圈（与 Button 忙碌态同一个 LoaderCircle）', () => {
    act(() => useUiStore.getState().setStatus(literal('正在构建 a…'), 'progress'))
    expect(tone()).toBe('progress')
    expect(shape()).toContain('icon-loader-circle')
    expect(shape()).toContain('animate-spin')
  })

  it('done：打勾；三档都照常自己走、都进 polite 播报区', () => {
    act(() => useUiStore.getState().setStatus(literal('已导出'), 'done'))
    expect(tone()).toBe('done')
    expect(shape()).toContain('icon-check')
    expect(container.querySelector('[data-status-live]')!.textContent).toBe('已导出')
    tick(STATUS_AUTO_DISMISS_MS)
    expect(status()).toBeNull()
  })

  it('error 不变：叹号、常驻、走 assertive，不进 polite 播报区', () => {
    act(() => useUiStore.getState().setStatus(literal('无法保存'), 'error'))
    expect(tone()).toBe('error')
    expect(shape()).toContain('icon-circle-alert')
    expect(container.querySelector('[data-status-live]')!.textContent).toBe('')
    tick(STATUS_AUTO_DISMISS_MS * 2)
    expect(status()).not.toBeNull()
  })
})

describe('带动作的状态 toast（导出关窗后的结果等）', () => {
  it('非错误语气带动作：渲染按钮、点了执行；常驻不自己走；可手动关', () => {
    const run = vi.fn()
    act(() => useUiStore.getState().setStatus(literal('导出在等你'), 'info', { action: { label: literal('查看'), run } }))
    const btn = [...statusToast()!.querySelectorAll('button')].find((b) => b.textContent === '查看')!
    expect(btn).toBeTruthy()
    tick(STATUS_AUTO_DISMISS_MS * 2)
    expect(status()).not.toBeNull()
    act(() => btn.click())
    expect(run).toHaveBeenCalledTimes(1)
    expect(statusToast()!.querySelector('button[aria-label]:last-child')).toBeTruthy()
  })

  it('对照：不带动作的非错误 toast 照旧自己走', () => {
    act(() => useUiStore.getState().setStatus(literal('好了'), 'info'))
    tick(STATUS_AUTO_DISMISS_MS + 10)
    expect(status()).toBeNull()
  })

  it('被新的一句顶掉后动作随之清空（不留上一句的按钮）', () => {
    act(() => useUiStore.getState().setStatus(literal('A'), 'info', { action: { label: literal('查看'), run: vi.fn() } }))
    act(() => useUiStore.getState().setStatus(literal('B'), 'info'))
    expect(useUiStore.getState().statusAction).toBeNull()
  })
})

describe('操作提示的自动收起', () => {
  it('焦点落在它的 × 上不走表；焦点离开后接着数', () => {
    act(() => {
      showHint('multi_select')
    })
    expect(useHintStore.getState().current).toBe('multi_select')
    const close = hintToast()!.querySelector<HTMLButtonElement>('button')!
    act(() => close.focus())
    tick(HINT_AUTO_DISMISS_MS * 3)
    expect(useHintStore.getState().current, '焦点在 × 上的时候不许消失').toBe('multi_select')
    act(() => close.blur())
    tick(HINT_AUTO_DISMISS_MS - 1)
    expect(useHintStore.getState().current).toBe('multi_select')
    tick(1)
    expect(useHintStore.getState().current).toBeNull()
  })

  it('指针停在提示上不走表', () => {
    act(() => {
      showHint('multi_select')
    })
    pointer(hintToast()!, 'pointerover')
    tick(HINT_AUTO_DISMISS_MS * 3)
    expect(useHintStore.getState().current).toBe('multi_select')
    pointer(hintToast()!, 'pointerout')
    tick(HINT_AUTO_DISMISS_MS)
    expect(useHintStore.getState().current).toBeNull()
  })
})

// ------------------------------------------------------------------ 已改用你的环境（ADR 0079）

const adoptedToast = () => container.querySelector<HTMLElement>('[data-environment-adopted]')
const adopt = () =>
  act(() =>
    useEnvStore.getState().noteEnvironmentAdopted({ source: 'conda', label: 'lab', python_version: '3.12.4' }),
  )

describe('「已改用你的环境」', () => {
  it('说出改用了哪个；到时自己走，指针停在上面不走表', () => {
    adopt()
    const name = t('engine.userEnvSource_conda', { ns: 'errors', label: 'lab' })
    expect(adoptedToast()!.textContent).toContain(
      t('engine.userEnvAdopted', { ns: 'errors', name, version: '3.12.4' }),
    )
    pointer(adoptedToast()!, 'pointerover')
    tick(ADOPTED_NOTICE_MS * 3)
    expect(useEnvStore.getState().adoptedEnvironment).not.toBeNull()
    pointer(adoptedToast()!, 'pointerout')
    tick(ADOPTED_NOTICE_MS)
    expect(useEnvStore.getState().adoptedEnvironment).toBeNull()
  })

  it('「改回」= 本项目明确选回默认（PATCH python=null），每个在用的面板按原来的环境重建', async () => {
    vi.mocked(setProjectEnvironment).mockResolvedValue({ ok: true, project: { open: true } } as never)
    useEnvStore.setState({ env: { ok: true, project: { open: true } } as never })
    useRenderStore.setState({
      byKey: {
        a: { fileId: 'a.pdf', status: 'ready', stale: false, lastPatches: '[]', wantPatches: '[]' } as never,
        b: { fileId: 'b.pdf', status: 'ready', stale: false, lastPatches: '[]', wantPatches: '[]' } as never,
      },
      tracked: {},
    })
    adopt()
    const revert = [...adoptedToast()!.querySelectorAll('button')].find(
      (b) => b.textContent === t('engine.userEnvRevert', { ns: 'errors' }),
    )!
    await act(async () => revert.click())
    await act(async () => {})
    expect(setProjectEnvironment).toHaveBeenCalledWith(null, undefined)
    expect(useEnvStore.getState().adoptedEnvironment).toBeNull()
    const byKey = useRenderStore.getState().byKey
    expect(byKey.a.stale && byKey.b.stale, '所有在用的面板都要重建，不只是失败的').toBe(true)
    expect(status()?.key).toBe('engine.userEnvReverted')
    expect(useUiStore.getState().statusTone, '改回做成了打 ✓').toBe('done')
  })
})

/**
 * 通知轨的外形与顺序（2026-10-07 设计审计 §10.1；2026-10-13 错误也改成胶囊）：每一条都是单行胶囊；错误 =
 * danger 图标 + 「这一步没有完成」+ 原因一行（截断，全文在 title）+「复制详情」图标钮；**最新的一条最靠近底边**
 * （CSS order，DOM 顺序不动）。主语：`data-toast-layout` / `data-toast-tone` / `data-copy-details`；顺序认 style.order 的大小。
 */
describe('通知轨：外形与先后', () => {
  it('普通状态与错误都是单行胶囊；错误的原因截断成一行、全文在 title，复制详情复制全文', async () => {
    say('已导出')
    expect(statusToast()!.dataset.toastLayout).toBe('pill')
    expect(statusToast()!.className).toContain('rounded-full')
    act(() => useUiStore.getState().setStatus(literal('写盘失败：/very/long/path/that/goes/on'), 'error'))
    const box = statusToast()!
    expect(box.dataset.toastLayout).toBe('pill')
    expect(box.dataset.toastTone).toBe('error')
    expect(box.className).toContain('rounded-full')
    expect(box.querySelector('[data-toast-bar]')).toBeNull()
    expect(box.textContent).toContain('写盘失败：/very/long/path/that/goes/on')
    expect(box.querySelector('[title="写盘失败：/very/long/path/that/goes/on"]')).not.toBeNull()
    // 截断要落在块级盒子上：行内 span 的 overflow / text-overflow 不生效（Codex #995）
    const clipped = box.querySelector<HTMLElement>('.truncate')!
    expect(clipped.classList.contains('block')).toBe(true)
    expect(clipped.parentElement!.className).toContain('min-w-0')
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    await act(async () => box.querySelector<HTMLButtonElement>('[data-copy-details]')!.click())
    expect(writeText).toHaveBeenCalledWith('写盘失败：/very/long/path/that/goes/on')
  })

  it('最新的一条最靠近底边：先有状态、后来提示 → 提示排在后面；再来一条状态 → 状态又到最后', () => {
    say('正在构建')
    act(() => {
      showHint('multi_select')
    })
    const orderOf = (el: HTMLElement | null) => Number(el!.style.order)
    expect(orderOf(hintToast())).toBeGreaterThan(orderOf(statusToast()))
    say('已导出')
    expect(orderOf(statusToast())).toBeGreaterThan(orderOf(hintToast()))
  })
})

describe('「出了点意外」卡（PR A）', () => {
  const unexpectedCard = () => container.querySelector<HTMLElement>('[data-unexpected-error-card]')
  const toasts = () => container.querySelectorAll('[data-toast-layout]')
  const showCard = () => act(() => void useUnexpectedErrorStore.getState().show())

  beforeEach(() => {
    useDiagSendStore.setState({ capability: { enabled: true }, open: false })
    useUnexpectedErrorStore.getState().dismiss()
    vi.mocked(prepareDiagSend).mockClear()
    vi.mocked(startDiagSend).mockClear()
  })
  afterEach(() => {
    act(() => useUnexpectedErrorStore.getState().dismiss())
    useDiagSendStore.setState({ capability: null, open: false })
    useWorkspaceStore.setState({ addedForEdit: null })
  })

  it('一句固定的话 + 一个主动作 + 关闭 ×；error 语气；不带错误原文，也没有「复制详情」', () => {
    showCard()
    const el = unexpectedCard()!
    expect(el.dataset.toastTone).toBe('error')
    expect(el.textContent).toContain(t('unexpectedError.text', { ns: 'workspace' }))
    const buttons = el.querySelectorAll('button')
    expect(buttons).toHaveLength(2) // 主动作 + 关闭
    expect(buttons[0].textContent).toBe(t('settings.diagnostics.send.cardButton', { ns: 'dialogs' }))
    expect(el.querySelector('[data-copy-details]')).toBeNull()
  })

  it('点主动作只 setOpen(true)（并收起这张卡）；prepare / start 的调用次数为 0', () => {
    showCard()
    act(() => unexpectedCard()!.querySelectorAll('button')[0].click())
    expect(useDiagSendStore.getState().open).toBe(true)
    expect(useUnexpectedErrorStore.getState().card).toBeNull()
    expect(prepareDiagSend).not.toHaveBeenCalled()
    expect(startDiagSend).not.toHaveBeenCalled()
  })

  it('× 关掉：卡片 store 清空', () => {
    showCard()
    act(() => unexpectedCard()!.querySelectorAll('button')[1].click())
    expect(useUnexpectedErrorStore.getState().card).toBeNull()
  })

  it('发送功能关着：就算 store 里有卡也不渲染（不增加任何新界面）', () => {
    useDiagSendStore.setState({ capability: null })
    showCard()
    expect(unexpectedCard()).toBeNull()
    expect(toasts()).toHaveLength(0)
  })

  it('仲裁：高于提示类——状态 + 意外卡占满两条时操作提示让位；状态从不让位', () => {
    act(() => {
      showHint('multi_select')
    })
    expect(hintToast()).not.toBeNull()
    showCard()
    expect(unexpectedCard()).not.toBeNull()
    expect(hintToast(), '提示 + 卡 = 两条，都在').not.toBeNull()
    say('已导出')
    tick(200) // 让位的那条退场保活 90ms
    expect(toasts()).toHaveLength(2)
    expect(unexpectedCard(), '卡不让位').not.toBeNull()
    expect(hintToast(), '三者同在：提示第一个让').toBeNull()
  })

  it('仲裁：低于「刚为编辑加入」——加入说明 + 状态已占满两条时，意外卡让位；状态走后才出现', () => {
    useWorkspaceStore.setState({ addedForEdit: 'p1', activePanelId: 'p1', addedForEditDepth: 0 })
    say('正在构建')
    showCard()
    expect(container.querySelector('[data-fast-edit-added-note]')).not.toBeNull()
    expect(unexpectedCard(), '加入说明 + 状态 = 两条，意外卡让位').toBeNull()
    act(() => useUiStore.getState().setStatus(null))
    tick(200)
    expect(unexpectedCard()).not.toBeNull()
  })

  it('最新的一条最靠近底边：卡出现在已有状态之后 → 排在状态后面', () => {
    say('正在构建')
    showCard()
    const statusEl = container.querySelector<HTMLElement>('[data-status-tone]')!
    expect(Number(unexpectedCard()!.style.order)).toBeGreaterThan(Number(statusEl.style.order))
  })
})

describe('引擎退出两张卡（ADR 0129）', () => {
  const engineCard = () => container.querySelector<HTMLElement>('[data-engine-exited-card]')
  const previousCard = () => container.querySelector<HTMLElement>('[data-previous-exit-card]')
  const toasts = () => container.querySelectorAll('[data-toast-layout]')

  beforeEach(() => {
    useDiagSendStore.setState({ capability: { enabled: true }, open: false })
    useExitNoticeStore.setState({ engineExit: null, restarting: false, previous: null })
    vi.mocked(prepareDiagSend).mockClear()
    vi.mocked(startDiagSend).mockClear()
    vi.mocked(restartEngine).mockClear()
  })
  afterEach(() => {
    act(() => useExitNoticeStore.setState({ engineExit: null, restarting: false, previous: null }))
    useDiagSendStore.setState({ capability: null, open: false })
  })

  it('修复卡：一句话 + 一个主按钮「重新启动」；折叠的一句只在 title；没有发送入口', () => {
    act(() => useExitNoticeStore.getState().setEngineExit('nonzero'))
    const el = engineCard()!
    expect(el.textContent).toContain(t('exitNotice.engine.text', { ns: 'workspace' }))
    const buttons = el.querySelectorAll('button')
    expect(buttons).toHaveLength(1)
    expect(buttons[0].textContent).toBe(t('exitNotice.engine.restart', { ns: 'workspace' }))
    expect(el.title).toBe(t('exitNotice.engine.note', { ns: 'workspace' }))
    expect(el.textContent).not.toContain(t('settings.diagnostics.send.cardButton', { ns: 'dialogs' }))
  })

  it('不受发送开关控制：开关 null 时修复卡照出，下次启动卡不出', () => {
    useDiagSendStore.setState({ capability: null })
    act(() => {
      useExitNoticeStore.getState().setEngineExit('signal')
      useExitNoticeStore.getState().showPrevious('app_unclean')
    })
    expect(engineCard()).not.toBeNull()
    expect(previousCard()).toBeNull()
  })

  it('点「重新启动」调 restartEngine，且转圈期间重复点击无效', async () => {
    let finish!: (v: boolean) => void
    vi.mocked(restartEngine).mockReturnValueOnce(new Promise<boolean>((r) => (finish = r)))
    act(() => useExitNoticeStore.getState().setEngineExit('zero'))
    await act(async () => engineCard()!.querySelector('button')!.click())
    await act(async () => engineCard()!.querySelector('button')!.click())
    expect(restartEngine).toHaveBeenCalledTimes(1)
    expect(useExitNoticeStore.getState().restarting).toBe(true)
    await act(async () => finish(false)) // 壳没动手：按钮还给用户
    expect(useExitNoticeStore.getState().restarting).toBe(false)
    expect(prepareDiagSend).not.toHaveBeenCalled()
    expect(startDiagSend).not.toHaveBeenCalled()
  })

  it('下次启动卡：一句话 + 「发送问题反馈」+ ×；点主动作只 setOpen(true)，prepare / start 为 0', () => {
    act(() => useExitNoticeStore.getState().showPrevious('engine_exited'))
    const el = previousCard()!
    expect(el.textContent).toContain(t('exitNotice.previous.text', { ns: 'workspace' }))
    const buttons = el.querySelectorAll('button')
    expect(buttons).toHaveLength(2)
    expect(buttons[0].textContent).toBe(t('settings.diagnostics.send.cardButton', { ns: 'dialogs' }))
    act(() => buttons[0].click())
    expect(useDiagSendStore.getState().open).toBe(true)
    expect(useExitNoticeStore.getState().previous).toBeNull()
    expect(prepareDiagSend).not.toHaveBeenCalled()
    expect(startDiagSend).not.toHaveBeenCalled()
  })

  it('previous 为空时没有卡；× 关掉只清 store', () => {
    expect(previousCard()).toBeNull()
    act(() => useExitNoticeStore.getState().showPrevious('app_unclean'))
    act(() => previousCard()!.querySelectorAll('button')[1].click())
    expect(useExitNoticeStore.getState().previous).toBeNull()
    expect(useDiagSendStore.getState().open).toBe(false)
  })

  it('仲裁：修复卡从不让位——它 + 状态占满两条时，下次启动卡与提示让位', () => {
    act(() => {
      useExitNoticeStore.getState().showPrevious('app_unclean')
      useExitNoticeStore.getState().setEngineExit('nonzero')
    })
    expect(previousCard(), '修复卡 + 下次启动卡 = 两条').not.toBeNull()
    act(() => useUiStore.getState().setStatus(literal('正在构建')))
    tick(200)
    expect(engineCard()).not.toBeNull()
    expect(previousCard(), '修复卡 + 状态已满两条').toBeNull()
    expect(toasts()).toHaveLength(2)
  })
})
