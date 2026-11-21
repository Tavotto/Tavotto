import { useEffect, useRef } from 'react'
import { hasUsableAi, readyConnections } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { useDataFigureStore } from '@/store/dataFigureStore'

/**
 * 「读懂数据」此刻不可用吗（左栏底部「连接 AI」胶囊的显示条件）：
 *  - 判据在后端：`/api/ai/data-files` 的 `understanding_available`（只认内置执行器能直接用的连接）；
 *  - 响应里没有这个字段（旧后端 / 还没取到）退回应用通用的 `hasUsableAi(caps)`；
 *  - 连接状态未知（`caps === null`）→ null，不显示，宁可晚出现。
 */
export function useUnderstandingUnavailable(): boolean | null {
  const caps = useAiStore((s) => s.caps)
  const available = useDataFigureStore((s) => s.understandingAvailable)
  if (caps === null) return null
  return available !== null ? !available : !hasUsableAi(caps)
}

/**
 * 连接变化的观察：挂在生命周期覆盖抽屉与临时标签的宿主上（`DataDraft` 在 App 里常驻，不依赖右栏 / 抽屉是否开着）。
 *  - 连接集合变了（`caps` 里可用连接的 id 变化）→ 重取清单（后端的「读懂数据可不可用」随清单回来，不另起轮询）；
 *  - 观察结果交给 store（`noteUnavailable`）：「曾经不可用」记在 store 里，宿主重新挂上时发现已变可用也会补跑一次。
 * 数据功能没进入过（清单 `idle`，开关关着）什么都不做。
 */
export function useConnectAiWatch(): void {
  const caps = useAiStore((s) => s.caps)
  const filesIdle = useDataFigureStore((s) => s.files.status === 'idle')
  const unavailable = useUnderstandingUnavailable()
  const readyKey = caps ? readyConnections(caps).map((c) => c.id).join('|') : null
  const lastKey = useRef<string | null>(null)
  useEffect(() => {
    if (readyKey === null || filesIdle) return
    if (lastKey.current !== null && lastKey.current !== readyKey) void useDataFigureStore.getState().loadFiles()
    lastKey.current = readyKey
  }, [readyKey, filesIdle])
  useEffect(() => {
    if (unavailable === null || filesIdle) return
    useDataFigureStore.getState().noteUnavailable(unavailable)
  }, [unavailable, filesIdle])
}
