/**
 * 项目打开且首屏稳定后，为画布上第一张带脚本的图在后台预热渲染会话（ADR 0159）。
 *
 * 每个项目一次；首屏稳定 = 文档就位后再等 `PREWARM_DELAY_MS` 且浏览器空闲。文档在等待期间又变了就重新计时。
 * 失败静默——预热是旁路，用户点图时没有预热也只是回到原来的冷启动。
 *
 * **挂成树尾不画任何东西的叶子（`<PrewarmSync />`），且不经 React 订阅文档**：宿主订阅了什么，它下面整棵树
 * 就跟着重画什么（见 `useEngineDocumentSync` 的注释）。文档变化用 store.subscribe 听，预热一旦触发
 * 或该项目已做过就立刻退订。
 */
import { useEffect } from 'react'
import { postEnginePrewarm } from '@/lib/api'
import { pickPrewarmTarget } from '@/lib/prewarm'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { useRenderStore } from '@/store/renderStore'

export const PREWARM_DELAY_MS = 1500
const done = new Set<string>()

/** 为这个项目布防：返回清理函数（退订 + 撤销待发的计时）。 */
export function armPrewarm(projectId: string): () => void {
  if (done.has(projectId)) return () => {}
  let timer: number | undefined
  let idle: number | undefined
  let unsubscribe: (() => void) | undefined
  const cancel = () => {
    if (timer !== undefined) window.clearTimeout(timer)
    if (idle !== undefined && typeof window.cancelIdleCallback === 'function') window.cancelIdleCallback(idle)
    timer = idle = undefined
  }
  const stop = () => {
    cancel()
    unsubscribe?.()
    unsubscribe = undefined
  }
  const fire = () => {
    const target = pickPrewarmTarget(useDocumentStore.getState().doc.objects, useRenderStore.getState().building)
    if (target === null || done.has(projectId)) return
    done.add(projectId)
    stop()
    void postEnginePrewarm(target)
  }
  const schedule = () => {
    cancel()
    if (pickPrewarmTarget(useDocumentStore.getState().doc.objects, {}) === null) return
    timer = window.setTimeout(() => {
      if (typeof window.requestIdleCallback === 'function') idle = window.requestIdleCallback(fire, { timeout: 3000 })
      else fire()
    }, PREWARM_DELAY_MS)
  }
  let lastObjects = useDocumentStore.getState().doc.objects
  unsubscribe = useDocumentStore.subscribe((s) => {
    if (s.doc.objects === lastObjects) return
    lastObjects = s.doc.objects
    schedule()
  })
  schedule()
  return stop
}

export function PrewarmSync(): null {
  const phase = useProjectStore((s) => s.phase)
  const projectId = useProjectStore((s) => s.project?.id ?? null)
  useEffect(() => {
    if (phase !== 'open' || !projectId) return
    return armPrewarm(projectId)
  }, [phase, projectId])
  return null
}

/** 测试用：忘掉「已预热」记录。 */
export function resetPrewarmForTest(): void {
  done.clear()
}
