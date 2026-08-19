/**
 * 成组对象里混入锁定成员时的移动语义。
 *
 * 背景：点中组内任意成员会把整组塞进选区（ObjectView 的 groupMates），但
 * 「这次谁会动」原先各自按 `!o.locked` 逐个过滤——锁的不动、没锁的动，组内间距
 * 被悄悄改掉，且无任何提示。裁决：组的意义是保持相对排布，**组内任一成员锁定
 * 即整组不可移动**，并给一条提示。见 docs/audit/2026-08-17-ux-audit.md。
 *
 * 鼠标拖动（startMoveDrag）与方向键微调（useKeyboard → nudgeSelected）走的是
 * 同一个 movableTargets，两条路径在这里都断言一遍。
 */
import { formatMessage, literal } from '@/i18n'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { beforeEach, describe, expect, it } from 'vitest'

import { nudgeSelected } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type ShapeObject } from '@/types/document'
import { startMoveDrag } from './interactions'

/* ------------------------------ 测试用对象 ------------------------------- */

const rect = (id: string, x: number, over: Partial<ShapeObject> = {}): ShapeObject => ({
  id,
  type: 'shape',
  shape: 'rect',
  x,
  y: 0,
  w: 10,
  h: 10,
  strokePt: 1,
  color: '#111111',
  fill: null,
  ...over,
})

/* ------------------------------ 指针事件桩 ------------------------------- */

const down = (clientX = 0, clientY = 0) =>
  ({ clientX, clientY, button: 0, stopPropagation() {} }) as unknown as ReactPointerEvent

/** jsdom 没有 PointerEvent 构造器，用同名 MouseEvent —— 监听器按事件名派发 */
const fire = (type: 'pointermove' | 'pointerup', clientX: number, clientY: number) =>
  window.dispatchEvent(new MouseEvent(type, { clientX, clientY, bubbles: true }))

/** mm → client px（zoom=1 / pan=0 / origin=0） */
const px = (mm: number) => mmToWorld(mm)

const byId = (id: string) => useDocumentStore.getState().doc.objects.find((o) => o.id === id)!
const status = () => formatMessage(useUiStore.getState().status)

/** 拖 dxMm 毫米：pointerdown → 两帧 pointermove → pointerup */
function drag(id: string, dxMm: number) {
  startMoveDrag(down(0, 0), id)
  fire('pointermove', px(dxMm) / 2, 0)
  fire('pointermove', px(dxMm), 0)
  fire('pointerup', px(dxMm), 0)
}

/* ------------------------------- 场景搭建 -------------------------------- */

beforeEach(async () => {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  // 关掉吸附：这里要的是位移原样落到 x/y，不掺吸附修正
  useUiStore.setState({ tool: 'select', snapEnabled: false, status: null, statusTone: 'info' })
  useSelectionStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_grouplock')
}, 20000)

/** 造一个组：members 里带 locked 的就是锁定成员 */
function makeGroup(gid: string, members: { id: string; x: number; locked?: boolean }[]) {
  useDocumentStore.getState().commit(literal('加组'), (d) => {
    for (const m of members) d.objects.push(rect(m.id, m.x, { groupId: gid, locked: m.locked }))
  })
}

/* -------------------------------- 用例 ----------------------------------- */

describe('组内含锁定成员 → 整组不可移动', () => {
  it('拖动：两成员一锁一未锁，两者都不动并给出提示', () => {
    makeGroup('g1', [{ id: 'a', x: 0 }, { id: 'b', x: 20, locked: true }])
    useSelectionStore.getState().set(['a', 'b'])

    drag('a', 30)

    expect(byId('a').x).toBe(0)
    expect(byId('b').x).toBe(20)
    expect(byId('a').y).toBe(0)
    expect(byId('b').y).toBe(0)
    expect(status()).toContain('组内有锁定对象')
    expect(status()).toContain('先解锁')
  })

  it('拖动：全员未锁的组照常整组一起走', () => {
    makeGroup('g1', [{ id: 'a', x: 0 }, { id: 'b', x: 20 }])
    useSelectionStore.getState().set(['a', 'b'])

    drag('a', 30)

    expect(byId('a').x).toBe(30)
    expect(byId('b').x).toBe(50)
    expect(status()).toBe('')
  })

  it('拖动：混选时只动可动的那组，并说明有组被跳过', () => {
    makeGroup('g1', [{ id: 'a', x: 0 }, { id: 'b', x: 20 }])
    makeGroup('g2', [{ id: 'c', x: 40 }, { id: 'd', x: 60, locked: true }])
    useSelectionStore.getState().set(['a', 'b', 'c', 'd'])

    drag('a', 30)

    expect(byId('a').x).toBe(30)
    expect(byId('b').x).toBe(50)
    expect(byId('c').x).toBe(40)
    expect(byId('d').x).toBe(60)
    expect(status()).toContain('已跳过 1 个组')
  })

  it('拖动：不成组的锁定对象维持原样——自己不动、别人照走', () => {
    useDocumentStore.getState().commit(literal('加对象'), (d) => {
      d.objects.push(rect('free', 0), rect('lock', 20, { locked: true }))
    })
    useSelectionStore.getState().set(['free', 'lock'])

    drag('free', 30)

    expect(byId('free').x).toBe(30)
    expect(byId('lock').x).toBe(20)
    expect(status()).toBe('')
  })

  it('方向键微调：同一规则——含锁组不动并提示，可动组照常走', () => {
    makeGroup('g1', [{ id: 'a', x: 0 }, { id: 'b', x: 20, locked: true }])
    useSelectionStore.getState().set(['a', 'b'])

    nudgeSelected(5, 0)

    expect(byId('a').x).toBe(0)
    expect(byId('b').x).toBe(20)
    expect(status()).toContain('先解锁')

    useUiStore.setState({ status: null })
    makeGroup('g2', [{ id: 'c', x: 40 }, { id: 'd', x: 60 }])
    useSelectionStore.getState().set(['c', 'd'])

    nudgeSelected(5, 0)

    expect(byId('c').x).toBe(45)
    expect(byId('d').x).toBe(65)
    expect(status()).toBe('')
  })

  it('方向键微调：混选只动可动组，并说明有组被跳过', () => {
    makeGroup('g1', [{ id: 'a', x: 0 }, { id: 'b', x: 20 }])
    makeGroup('g2', [{ id: 'c', x: 40 }, { id: 'd', x: 60, locked: true }])
    useSelectionStore.getState().set(['a', 'b', 'c', 'd'])

    nudgeSelected(5, 0)

    expect(byId('a').x).toBe(5)
    expect(byId('b').x).toBe(25)
    expect(byId('c').x).toBe(40)
    expect(byId('d').x).toBe(60)
    expect(status()).toContain('已跳过 1 个组')
  })
})
