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
  const choice = useDataFigureStore((s) => s.understandingChoice)
  if (caps === null) return null
  // 后端给了「用哪个 AI」的对象：只有 `none`（没有任何可用的 AI）才出「连接 AI」胶囊；`off` / `unavailable` 是用户的选择，由图标说话
  if (choice) return choice.state === 'none'
  return available !== null ? !available : !hasUsableAi(caps)
}

/**
 * 此刻「读懂数据」没在用 AI 吗（宿主的「曾经没在用 → 现在在用了就重跑一次」的判据）：`none` / `off` / `unavailable` 都算没在用；
 * 没有后端对象（旧后端）退回 `useUnderstandingUnavailable`。连接状态未知 → null。
 */
export function useNotUsingAi(): boolean | null {
  const caps = useAiStore((s) => s.caps)
  const choice = useDataFigureStore((s) => s.understandingChoice)
  const unavailable = useUnderstandingUnavailable()
  if (caps === null) return null
  return choice ? choice.state !== 'on' : unavailable
}

/**
 * 连接变化的观察：挂在生命周期覆盖抽屉与临时标签的宿主上（`DataDraft` 在 App 里常驻，不依赖右栏 / 抽屉是否开着）。
 *  - 连接集合变了（`caps` 里可用连接的 id 变化）→ 重取「用哪个 AI」（后端有这个对象时用轻量的 `GET /api/ai/data-understanding`：只读本地配置与能力缓存，
 *    不带文件列表、不触发清单刷新；旧后端退回重取整份清单）；
 *  - **清单比连接状态先回来**（应用刚启动 / 后端刚重启）：后端判「读懂数据可不可用」只看已有的能力缓存（不为这个去探测），缓存要等第一次取连接状态
 *    才有，所以那一刻答的是「不可用」；而连接集合此后并没有变，上一条不会触发。于是：每拿到一份新的连接状态，只要后端还说不可用、这里却看到
 *    有可用的连接，就重取一次（一份连接状态最多一次；后端坚持不可用——比如只有不支持的订阅——也只在连接状态刷新时再问）。
 *    **只有后端说 `none` / `unavailable`（旧后端：不可用）才重取；用户关了（`off`）绝不重取**——否则每次连接状态刷新都多一次请求；
 *  - 观察结果交给 store（`noteUnavailable`）：「曾经不可用」记在 store 里，宿主重新挂上时发现已变可用也会补跑一次。
 * 数据功能没进入过（清单 `idle`，开关关着）什么都不做。
 */
export function useConnectAiWatch(): void {
  const caps = useAiStore((s) => s.caps)
  const filesIdle = useDataFigureStore((s) => s.files.status === 'idle')
  const unavailable = useNotUsingAi()
  const readyKey = caps ? readyConnections(caps).map((c) => c.id).join('|') : null
  const lastKey = useRef<string | null>(null)
  const hasChoice = useDataFigureStore((s) => s.understandingChoice !== null)
  // 后端说不可用、并且不是用户关的：有对象时只认 `none` / `unavailable`（`off` 不算）；旧后端认 `understandingAvailable === false`
  const backendSaysNo = useDataFigureStore((s) => (s.understandingChoice ? s.understandingChoice.state === 'none' || s.understandingChoice.state === 'unavailable' : s.understandingAvailable === false))
  const askedFor = useRef<unknown>(null)
  const reask = () => {
    const st = useDataFigureStore.getState()
    void (st.understandingChoice ? st.refreshUnderstandingChoice() : st.loadFiles())
  }
  useEffect(() => {
    if (!caps || filesIdle || !backendSaysNo || !readyKey || askedFor.current === caps) return
    askedFor.current = caps
    reask()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caps, filesIdle, backendSaysNo, readyKey, hasChoice])
  useEffect(() => {
    if (readyKey === null || filesIdle) return
    if (lastKey.current !== null && lastKey.current !== readyKey) reask()
    lastKey.current = readyKey
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readyKey, filesIdle])
  useEffect(() => {
    if (unavailable === null || filesIdle) return
    useDataFigureStore.getState().noteUnavailable(unavailable)
  }, [unavailable, filesIdle])
}
