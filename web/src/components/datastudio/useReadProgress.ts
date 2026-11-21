import { useEffect, useState } from 'react'
import { aiDataProgress, type AiDataProgress } from '@/lib/api'
import { currentProjectId } from '@/lib/session'

const FIRST_MS = 300
const EVERY_MS = 400

/**
 * 大文件读表 / 出候选的真实进度：用这次请求的请求号轮询 `GET /api/ai/data-progress/<rid>`。
 * 有界：请求号没了（请求结束 / 换了一轮）或组件卸载就停；进入终态就停；一次请求失败就停（不重试，退回「没有进度」= 只有一句话）。
 * `none`（还没开始 / 小表 / 不认识）不算终态，接着问——但同样受上面三条约束。
 */
export function useReadProgress(rid: string | null): AiDataProgress | null {
  const [progress, setProgress] = useState<AiDataProgress | null>(null)
  useEffect(() => {
    setProgress(null)
    if (!rid) return
    let stopped = false
    let timer: number | undefined
    const pj = currentProjectId()
    const tick = async () => {
      let r: AiDataProgress
      try {
        r = await aiDataProgress(rid, pj)
      } catch {
        return
      }
      if (stopped) return
      setProgress(r)
      if (r.state === 'done' || r.state === 'cancelled' || r.state === 'failed') return
      timer = window.setTimeout(() => void tick(), EVERY_MS)
    }
    timer = window.setTimeout(() => void tick(), FIRST_MS)
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [rid])
  return progress
}
