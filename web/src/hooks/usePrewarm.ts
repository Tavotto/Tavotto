/**
 * 项目打开且首屏稳定后，为画布上第一张带脚本的图在后台预热渲染会话（ADR 0159）。
 *
 * 每个项目一次；首屏稳定 = 文档就位后再等 `PREWARM_DELAY_MS` 且浏览器空闲。文档在等待期间又变了就重新计时。
 * 失败静默——预热是旁路，用户点图时没有预热也只是回到原来的冷启动。
 */
import { useEffect } from 'react'
import { postEnginePrewarm } from '@/lib/api'
import { pickPrewarmTarget } from '@/lib/prewarm'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { useRenderStore } from '@/store/renderStore'

export const PREWARM_DELAY_MS = 1500
const done = new Set<string>()

export function usePrewarm(): void {
  const phase = useProjectStore((s) => s.phase)
  const projectId = useProjectStore((s) => s.project?.id ?? null)
  const objects = useDocumentStore((s) => s.doc.objects)

  useEffect(() => {
    if (phase !== 'open' || !projectId || done.has(projectId)) return
    if (pickPrewarmTarget(objects, {}) === null) return
    let idle: number | undefined
    const timer = window.setTimeout(() => {
      const fire = () => {
        const target = pickPrewarmTarget(
          useDocumentStore.getState().doc.objects,
          useRenderStore.getState().building,
        )
        if (target === null || done.has(projectId)) return
        done.add(projectId)
        void postEnginePrewarm(target)
      }
      if (typeof window.requestIdleCallback === 'function') idle = window.requestIdleCallback(fire, { timeout: 3000 })
      else fire()
    }, PREWARM_DELAY_MS)
    return () => {
      window.clearTimeout(timer)
      if (idle !== undefined && typeof window.cancelIdleCallback === 'function') window.cancelIdleCallback(idle)
    }
  }, [phase, projectId, objects])
}

/** 测试用：忘掉「已预热」记录。 */
export function resetPrewarmForTest(): void {
  done.clear()
}
