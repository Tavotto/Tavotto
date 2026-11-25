import { useEffect, useRef, useState, type ReactNode } from 'react'
import {
  backendErrorText,
  cancelSiwcFlow,
  fetchSiwcFlow,
  siwcCodexFallback,
  startSiwcLogin,
  type AiConnection,
  type AiSiwcFlow,
  type AiVendor,
  type AiVendorMethod,
} from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { Button } from '../../ui/Button'
import { cx } from './copy'
import { refreshAndFind, useLoginFlow } from './useLoginFlow'

/** 轮询间隔：浏览器里点完「允许」到这边看到结果，最多慢这么久 */
const POLL_MS = 1000

type Phase = 'idle' | 'starting' | 'waiting' | 'done'

/** 结束但没连上时的一句话（流程状态 / 原因码 → 文案；每个键写成字面量，i18n 检查才数得到） */
function endedText(flow: AiSiwcFlow): string | null {
  switch (flow.state) {
    case 'plan_disabled':
      return cx('siwc.planDisabled')
    case 'denied':
    case 'cancelled':
      return null // 用户自己取消的：不报错，回到按钮
    case 'expired':
      return cx('siwc.expired')
    default:
      return cx('siwc.failed')
  }
}

/**
 * 「Continue with ChatGPT」一行的两个部件：按钮与它下面的一句提示（ADR 0209 §五）。
 * 点按钮 → 后端起本机回调、给授权地址 → 系统浏览器打开 → 轮询流程状态；登录成功后重读 capabilities，
 * 这种方式对应的连接 ready 了就调 `onConnected`。按钮文案是官方推荐的那一串，产品名不翻译。
 */
export function useSiwcLogin(
  vendor: AiVendor,
  method: AiVendorMethod,
  onConnected: (c: AiConnection) => void,
): { button: ReactNode; hint: ReactNode; begin: () => void } {
  const [phase, setPhase] = useState<Phase>('idle')
  // 失败选择题的第二个选项（维护者 2026-11-25「失败时给选择题」）：本机装了 Codex 才出现，点了走原来的 Codex 官方登录
  const codexInstalled = useAiStore((st) => !!st.caps?.agents.find((a) => a.id === 'codex')?.installed)
  const codexMethod: AiVendorMethod = { id: 'openai:login', kind: 'login', agent: 'codex', connection_id: 'sub:codex', available: codexInstalled }
  const codex = useLoginFlow(vendor, codexMethod, onConnected)
  const [usingCodex, setUsingCodex] = useState(false)
  const [flow, setFlow] = useState<AiSiwcFlow | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | null>(null)
  const alive = useRef(true)
  // 尝试代次：cancel() 与新的 begin() 都让旧的一次作废，慢回来的启动响应据此丢弃
  const gen = useRef(0)

  const stopPolling = () => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = null
  }

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      stopPolling()
    }
  }, [])

  const poll = (id: string) => {
    timer.current = window.setTimeout(async () => {
      let f: AiSiwcFlow
      try {
        f = await fetchSiwcFlow(id)
      } catch {
        if (alive.current) poll(id) // 读不到（后端忙 / 网络抖）：接着等，流程自己有 10 分钟时限
        return
      }
      if (!alive.current) return
      setFlow(f)
      if (f.state === 'waiting' || f.state === 'exchanging') {
        poll(id)
        return
      }
      setPhase('done')
      if (f.state === 'signed_in') {
        const r = await refreshAndFind(vendor, method)
        if (!alive.current) return
        if (r.conn) onConnected(r.conn)
        else setError(cx('siwc.failed'))
        return
      }
      setError(endedText(f))
    }, POLL_MS)
  }

  const begin = async () => {
    const mine = ++gen.current
    stopPolling()
    setError(null)
    setUsingCodex(false) // 上一轮失败后改用过 Codex：再点 ChatGPT 就回到 SIWC 自己的提示
    setPhase('starting')
    try {
      const r = await startSiwcLogin()
      if (!alive.current) return
      if (mine !== gen.current) {
        // 启动期间已取消或又开了新一轮：不开浏览器、不改状态，把后端刚起的那个流程撤掉
        if (r.flow_id) void cancelSiwcFlow(r.flow_id).catch(() => undefined)
        return
      }
      setFlow(r.flow)
      setPhase('waiting')
      window.open(r.flow.authorize_url, '_blank', 'noreferrer')
      poll(r.flow_id)
    } catch (e) {
      if (!alive.current || mine !== gen.current) return
      setPhase('idle')
      setError(backendErrorText(e))
    }
  }

  const switchToCodex = async () => {
    try {
      await siwcCodexFallback()
    } catch {
      // 记不下来也照样让他登 Codex：最坏只是登完后列表里暂时看不到那一行
    }
    if (!alive.current) return
    setError(null)
    setUsingCodex(true)
    codex.begin()
  }

  const cancel = () => {
    gen.current++
    stopPolling()
    if (flow) void cancelSiwcFlow(flow.flow_id).catch(() => undefined)
    setPhase('idle')
    setFlow(null)
  }

  const waiting = phase === 'starting' || phase === 'waiting'
  const button = waiting ? (
    <Button variant="ghost" size="sm" data-connect-action="siwc-cancel" onClick={cancel}>
      {cx('siwc.cancel')}
    </Button>
  ) : (
    <Button variant="secondary" size="sm" data-connect-action="siwc" data-connect-login={phase} onClick={() => void begin()}>
      {cx('siwc.continue')}
    </Button>
  )

  // 失败（登录没完成 / 超时 / 没允许订阅额度 / 起不来）：一句话 + 选择题——重试就是右侧那个「Continue with ChatGPT」（不再摆一个
  // 同义的「再试一次」），另一个选项「改用 Codex 登录」只在本机装了 Codex 时出现；同列表里下面还有 API Key，不让新手卡住
  const choice = error ? (
    // 「改用 Codex 登录」与「重新打开」同一种文字链接（下划线），紧跟在那句后面、贴着这一行（agy 审稿：看得出能点、归属清楚）
    <div role="alert" data-connect-login-hint="failed" className="-mt-1 flex flex-wrap items-baseline gap-x-2.5 gap-y-1 text-xs">
      <span className="text-danger">{error}</span>
      {codexInstalled && (
        <button
          type="button"
          data-connect-action="siwc-use-codex"
          onClick={() => void switchToCodex()}
          className="focus-visible:focus-ring rounded-sm text-ink-2 underline underline-offset-2 outline-none hover:text-ink"
        >
          {cx('siwc.useCodex')}
        </button>
      )}
    </div>
  ) : null

  const hint = usingCodex ? (
    <div className="flex flex-col gap-1">
      <div>{codex.button}</div>
      {codex.hint}
    </div>
  ) : phase === 'waiting' && flow ? (
      <p data-connect-login-hint className="type-caption">
        {cx('siwc.browserHint')}{' '}
        <a href={flow.authorize_url} target="_blank" rel="noreferrer" className="underline underline-offset-2">
          {cx('siwc.openAgain')}
        </a>
      </p>
    ) : (
      choice
    )

  return { button, hint, begin: () => void begin() }
}
