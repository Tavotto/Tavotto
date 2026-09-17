/**
 * 调度器的边界与两条新 API。行为本身（防抖合并 / 降质只给含图像面板 / `none` 占位 /
 * flush 定稿 / 布尔参数兼容）已由 `hooks/useEngineSync.test.ts` 逐条钉着——搬家时那些
 * 用例改的只是 import 路径，一条判据没动。这里只补搬家本身带来的两件事：
 *
 * 1. **依赖方向**：调度器不许 import `store/actions`——否则 actions ↔ useEngineSync 那个
 *    两节点环只是换成了 actions → scheduler → actions 的三节点环。判源码而不判行为：
 *    环在运行时没有任何症状。
 * 2. `cancelScheduledRender`：取消挂起的防抖渲染，但**不动** `wantPatches` 占位（那是
 *    同步器的跳过判据；清了它同步器会立刻替这次改动再发一次）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import type { PanelObject } from '@/types/document'
import { cancelScheduledRender, hasScheduledRender, requestRender } from './renderScheduler'

const schedulerSource = Object.values(
  import.meta.glob('/src/store/renderScheduler.ts', { query: '?raw', import: 'default', eager: true }),
)[0] as string

const hookSource = Object.values(
  import.meta.glob('/src/hooks/useEngineSync.ts', { query: '?raw', import: 'default', eager: true }),
)[0] as string

describe('依赖方向', () => {
  it('读到了两份源码（判据的前提）', () => {
    expect(schedulerSource).toContain('export function requestRender')
    expect(hookSource).toContain('export function useEngineSync')
  })

  it('调度器不 import store/actions（否则两节点环换成三节点环）', () => {
    expect(schedulerSource).not.toMatch(/from\s+['"]@\/store\/actions['"]/)
    expect(schedulerSource).not.toMatch(/from\s+['"]\.\/actions['"]/)
  })

  it('hook 文件不 import store/actions，也不再定义调度函数', () => {
    expect(hookSource).not.toMatch(/from\s+['"]@\/store\/actions['"]/)
    expect(hookSource).not.toMatch(/export function (requestRender|flushRender)\b/)
  })
})

function panel(id: string, fileId: string): PanelObject {
  return {
    id,
    type: 'panel',
    x: 0,
    y: 0,
    w: 40,
    h: 30,
    fileId,
    fileKind: 'pdf',
    nativeW: 40,
    nativeH: 30,
    script: 'fig.py',
    overrides: [{ gid: 'g0', prop: 'color', value: '#000' }],
  } as PanelObject
}

describe('cancelScheduledRender', () => {
  let calls = 0
  beforeEach(() => {
    vi.useFakeTimers()
    calls = 0
    useRenderStore.getState().clear()
    useRenderStore.setState({
      render: async () => {
        calls += 1
      },
    })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('取消之后防抖到点也不发；wantPatches 占位原样留着', () => {
    const p = panel('a', 'Fig1.pdf')
    requestRender(p, 'defer')
    expect(hasScheduledRender('a')).toBe(true)
    cancelScheduledRender('a')
    expect(hasScheduledRender('a')).toBe(false)
    vi.advanceTimersByTime(1000)
    expect(calls).toBe(0)
    expect(useRenderStore.getState().get(renderKeyOf(p)).wantPatches).toBe(JSON.stringify(p.overrides))
  })

  it('没挂计时器时取消是 no-op', () => {
    expect(() => cancelScheduledRender('nobody')).not.toThrow()
    expect(hasScheduledRender('nobody')).toBe(false)
  })
})
