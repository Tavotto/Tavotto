/**
 * 顶栏保存状态的三条安静纪律（2026-10-07 审计 P0）。自动保存防抖 1 s，此前标签
 * 「有未保存修改 → 正在保存… → 已存在本机」每秒翻一轮、宽度跟着变（推邻居）、每轮都经
 * aria-live 播报一次。钉：
 *   1. 进行中的那两句持续满 `SAVE_PENDING_REVEAL_MS` 才说；快的一轮里一直是落定那句；
 *   2. 宽度占位：所有会轮到的那几句同格叠放，换状态时这一格里的句子集合不变；
 *   3. 读屏只在进入「保存失败 / 外部冲突」时播报；标签本身不 aria-live。
 *
 * 主语：显示的是哪一档认 `data-save-shown`，文字认 `data-save-text`，播报区认 `data-save-live`
 * ——都是 data 钩子，不认 role / class。像素级的「邻居不动」jsdom 量不到，由 e2e/topbar-narrow 管。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { emptyProject, type TextObject } from '@/types/document'
import { useDocumentStore, type SaveState } from '@/store/documentStore'
import { SAVE_PENDING_REVEAL_MS, SaveStateLabel } from './TopBar'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const text: TextObject = {
  id: 't1', type: 'text', text: 'a', sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
}

let root: Root
let host: HTMLDivElement

const label = () => host.querySelector<HTMLElement>('[data-save-state]')!
const shown = () => label().dataset.saveShown
const visibleText = () => host.querySelector<HTMLElement>('[data-save-text]')!.textContent
const cell = () =>
  [...host.querySelector<HTMLElement>('[data-save-text]')!.parentElement!.children].map(
    (c) => c.textContent,
  )
const live = () => host.querySelector<HTMLElement>('[data-save-live]')!.textContent
const go = (saveState: SaveState) => act(() => useDocumentStore.setState({ saveState }))
const tick = (ms: number) => act(() => vi.advanceTimersByTime(ms))

beforeEach(async () => {
  localStorage.clear()
  globalThis.fetch = (async () => new Response('{}', { status: 404 })) as typeof fetch
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_save_label')
  useDocumentStore.getState().silent((d) => {
    d.objects.push(text)
  })
  // 真实流程里 saved 总伴随着一次落盘时间（afterWriteOk）；「从没落过盘」另有用例
  useDocumentStore.setState({ saveState: 'saved', lastPersisted: Date.now() })
  vi.useFakeTimers()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root.render(<SaveStateLabel />))
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  vi.useRealTimers()
})

describe('顶栏保存状态', () => {
  it('从没落过盘的新排版（Codex #821 P2）：第一次改动不等门槛，直接说进行中，不先声称「已存在本机」', () => {
    act(() => useDocumentStore.setState({ saveState: 'clean', lastPersisted: null }))
    go('dirty')
    expect(shown()).toBe('dirty')
    expect(visibleText()).not.toBe('已存在本机')
    go('saving')
    expect(shown()).toBe('saving')
    act(() => useDocumentStore.setState({ saveState: 'saved', lastPersisted: Date.now() }))
    expect(shown()).toBe('settled')
  })

  it('快的一轮（dirty → saving → saved 在门槛内走完）：一直是落定那句，不闪', () => {
    expect(shown()).toBe('settled')
    expect(visibleText()).toBe('已存在本机')
    go('dirty')
    tick(SAVE_PENDING_REVEAL_MS - 200)
    expect(shown()).toBe('settled')
    go('saving')
    tick(150)
    expect(shown(), 'dirty → saving 是连着的一段，计时不重来').toBe('settled')
    go('saved')
    tick(SAVE_PENDING_REVEAL_MS)
    expect(shown()).toBe('settled')
    expect(visibleText()).toBe('已存在本机')
  })

  it('慢的一轮：进行中满门槛才说，落定立刻回来', () => {
    go('dirty')
    tick(SAVE_PENDING_REVEAL_MS - 1)
    expect(shown()).toBe('settled')
    tick(1)
    expect(shown()).toBe('dirty')
    expect(visibleText()).toBe('有未保存修改')
    go('saving')
    expect(shown()).toBe('saving')
    expect(visibleText()).toBe('正在保存…')
    go('saved')
    expect(shown()).toBe('settled')
    expect(visibleText()).toBe('已存在本机')
  })

  it('宽度占位：无论哪一档，这一格里叠着的都是同样那几句', () => {
    const settledCell = [...cell()].sort()
    // 本机落定话的两种写法（saved 不带时间 / 快的一轮带上次落盘时间）都在占位里
    expect(settledCell.filter((c) => c?.startsWith('已存在本机'))).toHaveLength(2)
    expect(settledCell).toEqual(expect.arrayContaining(['已存在本机', '有未保存修改', '正在保存…']))
    go('dirty')
    tick(SAVE_PENDING_REVEAL_MS)
    expect([...cell()].sort()).toEqual(settledCell)
    go('saving')
    expect([...cell()].sort()).toEqual(settledCell)
  })

  it('读屏只在出事时说：平常一轮播报区是空的，进入保存失败 / 冲突才有字', () => {
    expect(label().hasAttribute('aria-live'), '标签本身不再 aria-live').toBe(false)
    go('dirty')
    tick(SAVE_PENDING_REVEAL_MS)
    expect(live()).toBe('')
    go('saving')
    expect(live()).toBe('')
    go('save_error')
    expect(shown()).toBe('save_error')
    expect(live()).toBe('保存失败')
    go('conflict')
    expect(live()).toBe('外部冲突')
    go('saved')
    expect(live()).toBe('')
  })

  it('出错不等门槛：立刻说', () => {
    go('save_error')
    expect(visibleText()).toBe('保存失败')
  })
})
