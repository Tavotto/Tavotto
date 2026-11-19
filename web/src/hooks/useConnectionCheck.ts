import { useCallback, useEffect, useRef, useState } from 'react'
import { canCheckConnection } from '@/lib/connectionCheck'
import { currentConnection, requestCheckConnection, runConnectionCheck } from '@/store/connectAiStore'

/** 失败卡「检查连接」的四个相位：没点过 / 检查中 / 通了 / 仍不通（带原因） */
export type ConnectionCheckPhase = 'idle' | 'checking' | 'ok' | 'failed'

/**
 * 失败卡的「检查连接」：对刚才派活用的那条 Key 连接发 `/check`。
 * 那条不是 Key 类（订阅登录等，没有地址 + Key 可探）→ 照旧去连接弹窗，不进检查中。
 * 每一次点击都有结局：通了 / 仍不通 + 一个原因（调用方再给「修改连接」），绝不停在检查中。
 */
export function useConnectionCheck() {
  const [phase, setPhase] = useState<ConnectionCheckPhase>('idle')
  const [reason, setReason] = useState<string | null>(null)
  const alive = useRef(true)
  const running = useRef(false)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const check = useCallback(async () => {
    if (running.current) return
    const conn = currentConnection()
    if (!conn || !canCheckConnection(conn)) {
      requestCheckConnection()
      return
    }
    running.current = true
    setPhase('checking')
    try {
      const r = await runConnectionCheck(conn.id)
      if (!alive.current) return
      setReason(r.reason)
      setPhase(r.ok ? 'ok' : 'failed')
    } finally {
      running.current = false
    }
  }, [])

  return { phase, reason, check }
}
