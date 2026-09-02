/**
 * 本地活动信号（Session 17 建，Session 21 扩）：kind 闭集、payload 隐私边界、
 * 订阅 / 退订、监听者抛错不影响发射方。
 */
import { describe, expect, it } from 'vitest'
import {
  ACTIVITY_EVENT,
  ACTIVITY_KINDS,
  ACTIVITY_PAYLOAD_KEYS,
  emitActivity,
  onActivity,
  type ActivityDetail,
  type ActivityKind,
} from './activity'

/** 每种 kind 一个样本——新增 kind 而不加样本时，下面的 exhaustiveness 编译红 */
const SAMPLES: Record<ActivityKind, ActivityDetail> = {
  'selection.aligned': { kind: 'selection.aligned', mode: 'top', ref: 'page', count: 2 },
  'selection.grouped': { kind: 'selection.grouped', count: 2 },
  'selection.ungrouped': { kind: 'selection.ungrouped', count: 2 },
  'project.opened': { kind: 'project.opened', tutorial: true },
  'workspace.mode_changed': { kind: 'workspace.mode_changed', mode: 'fast_edit' },
  'figure.opened_fast_edit': { kind: 'figure.opened_fast_edit', outcome: 'editing' },
  'figure.element_edit_entered': { kind: 'figure.element_edit_entered' },
  'selection.changed': { kind: 'selection.changed', count: 1 },
  'element.selection_changed': { kind: 'element.selection_changed', count: 1 },
  'element.property_changed': { kind: 'element.property_changed', prop: 'fontsize' },
  'history.pushed': { kind: 'history.pushed', label: 'history.setProp' },
  'problems.opened': { kind: 'problems.opened' },
  'problem.focused': { kind: 'problem.focused', ok: true, mode: 'fast_edit', field: 'focused' },
  'export.dialog_opened': { kind: 'export.dialog_opened' },
  'export.scope_changed': { kind: 'export.scope_changed', scope: 'original' },
  'figure.added_to_layout': { kind: 'figure.added_to_layout', outcome: 'added' },
  'menu.opened': { kind: 'menu.opened', menu: 'panel' },
  'document.saved': { kind: 'document.saved' },
}

describe('kind 闭集', () => {
  it('ACTIVITY_KINDS 与样本表一一对应、无重复', () => {
    expect([...ACTIVITY_KINDS].sort()).toEqual(Object.keys(SAMPLES).sort())
    expect(new Set(ACTIVITY_KINDS).size).toBe(ACTIVITY_KINDS.length)
  })

  it('每种样本的字段都在白名单里：没有 id / gid / name / path / text', () => {
    for (const d of Object.values(SAMPLES)) {
      for (const k of Object.keys(d)) expect(ACTIVITY_PAYLOAD_KEYS.has(k), `${d.kind}.${k}`).toBe(true)
    }
    for (const banned of ['id', 'gid', 'objectId', 'fileId', 'name', 'path', 'text', 'value', 'stem']) {
      expect(ACTIVITY_PAYLOAD_KEYS.has(banned)).toBe(false)
    }
  })
})

describe('发射与订阅', () => {
  it('emit → 订阅者收到同一份 detail；退订后不再收到', () => {
    const got: ActivityDetail[] = []
    const off = onActivity((d) => got.push(d))
    emitActivity(SAMPLES['export.scope_changed'])
    off()
    emitActivity(SAMPLES['document.saved'])
    expect(got).toEqual([SAMPLES['export.scope_changed']])
  })

  it('没有 detail.kind 的杂事件被过滤', () => {
    const got: ActivityDetail[] = []
    const off = onActivity((d) => got.push(d))
    window.dispatchEvent(new CustomEvent(ACTIVITY_EVENT, { detail: { foo: 1 } }))
    window.dispatchEvent(new Event(ACTIVITY_EVENT))
    off()
    expect(got).toEqual([])
  })

  it('监听者抛错不冒回发射方', () => {
    const off = onActivity(() => {
      throw new Error('boom')
    })
    // jsdom 把监听者的异常报到 window.onerror，不会打断派发方；这里接住它免得
    // vitest 当成未处理错误
    const onErr = (e: ErrorEvent) => e.preventDefault()
    window.addEventListener('error', onErr)
    expect(() => emitActivity(SAMPLES['problems.opened'])).not.toThrow()
    window.removeEventListener('error', onErr)
    off()
  })
})
