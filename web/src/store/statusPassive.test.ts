/**
 * 被动通知不顶掉用户要读的结果。
 *
 * 真实界面复现（ADR 0080 验收）：点「全部处理」→「已修复 8 项，可撤销。」→ 修复一提交
 * 就触发重渲染 → 30–50 ms 后 SSE `render.done` 把 toast 换成「渲染完成」，用户什么都
 * 没看到。后台通知（渲染完成 / 正在构建）因此是被动的：非被动 toast 挂着时不覆盖它。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { formatMessage, literal } from '@/i18n'
import { useUiStore } from './uiStore'

const shown = () => formatMessage(useUiStore.getState().status)

afterEach(() => useUiStore.getState().setStatus(null))

describe('被动通知', () => {
  it('非被动 toast 挂着时，被动通知不覆盖它', () => {
    const ui = useUiStore.getState()
    ui.setStatus(literal('已修复 8 项'))
    ui.setStatus(literal('渲染完成'), 'info', { passive: true })
    expect(shown()).toBe('已修复 8 项')
  })

  it('被动之间照常覆盖；没有 toast 时被动通知照常出现', () => {
    const ui = useUiStore.getState()
    ui.setStatus(literal('正在构建'), 'info', { passive: true })
    expect(shown()).toBe('正在构建')
    ui.setStatus(literal('渲染完成'), 'info', { passive: true })
    expect(shown()).toBe('渲染完成')
  })

  it('非被动总能覆盖被动的', () => {
    const ui = useUiStore.getState()
    ui.setStatus(literal('渲染完成'), 'info', { passive: true })
    ui.setStatus(literal('已修复 1 项'))
    expect(shown()).toBe('已修复 1 项')
  })
})
