import { StrictMode, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { applyLocale, t as translate } from '@/i18n'
import { normalizeLocale } from '@/i18n/locale'
import { AppsBridge, hostFallback } from './appsBridge'
import { resolveToolResult, WAITING_NOTICE_MS } from './boot'
import { McpApp } from './McpApp'
import { McpProviders } from './McpProviders'
import { installMcpTransport, seedSession, type OpenFigureResult } from './session'
import '@/index.css'

/**
 * MCP App 画布的入口。
 *
 * 生命周期：
 *   1. 装 MCP 传输（画布里的一切引擎往来都要走它，**必须在挂载之前**）；
 *   2. 与 host 握手（`ui/initialize`），失败也照常挂载——好告诉用户为什么空着；
 *   3. 等 `ui/notifications/tool-result` 送来 `tavotto_open_figure` 的结果；
 *      `window.openai.toolOutput` 是**兜底**（feature-detect，只在标准路径
 *      拿不到东西时看一眼）；
 *   4. 结果完整就直接用；只有把手就经 `tavotto_session_state` 取件（`boot.ts`
 *      写着三种来货各走哪条路）；空壳当场说出口；
 *   5. 把结果灌进既有 stores，挂 `McpApp`。
 *
 * 拿不到结果时**不猜、不自己发起 open**：这块画布是被某一次工具调用带出来的，
 * 没有那次调用的结果就说明 host 侧出了问题，编一张图出来只会更难查。
 */

const rootEl = document.getElementById('root')!
const bridge = new AppsBridge()
// 传输必须先装：store 一旦挂载就可能发渲染请求，那时候拿到默认的 HTTP 传输
// 会打到一个不存在的 /api（iframe 里没有 Tavotto 服务）
installMcpTransport(bridge)

type BootState = 'connecting' | 'waiting' | 'fetching' | 'ready' | 'nohost' | 'failed'
interface Failure {
  code: 'stripped' | 'fetch_failed'
  detail: string
}

function Boot() {
  const [open, setOpen] = useState<OpenFigureResult | null>(null)
  const [panelId, setPanelId] = useState<string | null>(null)
  const [state, setState] = useState<BootState>('connecting')
  const [failure, setFailure] = useState<Failure | null>(null)
  // 等太久了：不是失败，是把「还在等、可能等不来」摆出来（见 WAITING_NOTICE_MS）
  const [waitedLong, setWaitedLong] = useState(false)
  // 收到过几条 tool-result——诊断用：0 条与「收到了但不能用」是两种病
  const seen = useRef(0)

  useEffect(() => {
    let done = false
    let resolving = false
    const settle = (payload: OpenFigureResult) => {
      if (done) return
      done = true
      const { panelId: pid } = seedSession(payload)
      setOpen(payload)
      setPanelId(pid)
      setState('ready')
    }
    const accept = (params: unknown) => {
      // 一次只解一条：第二条到的时候第一条可能还在取件，两条都种就是两张图
      if (done || resolving) return
      resolving = true
      void resolveToolResult(bridge, params, () => setState('fetching')).then((res) => {
        resolving = false
        if (done) return
        if (res.ok) {
          settle(res.payload)
          return
        }
        setFailure({ code: res.code, detail: res.detail })
        setState('failed')
      })
    }

    // 画布跟随 **Codex host** 的界面语言（issue #30）：iframe 自己探测到的
    // navigator/localStorage 语言属于这台浏览器，不属于用户正对着的宿主界面。
    // 认不出的 locale 保持现状（探测链的结果），不硬扳。
    const applyHostLocale = (ctx: unknown) => {
      const tag =
        ctx && typeof ctx === 'object' && typeof (ctx as { locale?: unknown }).locale === 'string'
          ? ((ctx as { locale: string }).locale)
          : null
      const loc = normalizeLocale(tag)
      if (loc) void applyLocale(loc)
    }
    // host 中途切语言：hostContext 可能平铺在 params 里，也可能包一层
    const offLocale = bridge.on('ui/notifications/host-context-changed', (params) => {
      const p = params as { hostContext?: Record<string, unknown> } | null
      const ctx = (p?.hostContext ?? p ?? {}) as Record<string, unknown>
      bridge.hostContext = { ...(bridge.hostContext ?? {}), ...ctx }
      applyHostLocale(ctx)
    })

    // MCP Apps 标准路径：host 把工具结果推过来。params 就是 CallToolResult
    // （2026-01-26），老的 `{ result }` 包装在 boot.ts 里兼容
    const off = bridge.on('ui/notifications/tool-result', (params) => {
      seen.current += 1
      accept(params)
    })

    let notice: number | null = null
    void bridge.connect({ name: 'tavotto-canvas', version: '1' }).then((ok) => {
      if (!ok) {
        setState('nohost')
        return
      }
      // 握手响应里带的 hostContext：先于一切界面渲染把语言对齐到宿主
      applyHostLocale(bridge.hostContext)
      // 复杂编辑画布：inline 那点高度放不下图 + 属性页
      bridge.requestFullscreen()
      setState((s) => (s === 'connecting' ? 'waiting' : s))
      notice = window.setTimeout(() => setWaitedLong(true), WAITING_NOTICE_MS)
      // 兜底：某些 surface 只把结果挂在 window.openai 上，且在握手前就写好了
      const fb = hostFallback()
      if (fb.toolOutput) accept({ structuredContent: fb.toolOutput })
    })

    return () => {
      off()
      offLocale()
      if (notice !== null) window.clearTimeout(notice)
    }
  }, [])

  if (state === 'ready' && open && panelId) {
    return <McpApp bridge={bridge} open={open} panelId={panelId} />
  }
  // 走到这里 state 必然不是 ready（上面那个分支已经处理掉了），
  // 但 open/panelId 也可能还没到位——都归 Splash 说人话
  return (
    <Splash
      state={state === 'ready' ? 'waiting' : state}
      failure={failure}
      waitedLong={waitedLong}
      seen={seen.current}
    />
  )
}

function Splash({
  state,
  failure,
  waitedLong,
  seen,
}: {
  state: Exclude<BootState, 'ready'>
  failure: Failure | null
  waitedLong: boolean
  seen: number
}) {
  // 与 McpApp 同一个命名空间（`dialogs:mcp.*`）：这一屏以前是硬编码中文，
  // 英文 host 里连接 / 等待 / 无 host 三种状态全是中文
  const tr = (key: string, values?: Record<string, unknown>) =>
    translate(`mcp.${key}`, { ns: 'dialogs', ...values })
  let text: string
  let detail: string | null = null
  if (state === 'nohost') text = tr('splashNoHost')
  else if (state === 'connecting') text = tr('splashConnecting')
  else if (state === 'fetching') text = tr('splashFetching')
  else if (state === 'failed' && failure) {
    text =
      failure.code === 'stripped' ? tr('splashStripped') : tr('splashFetchFailed')
    detail = failure.detail
  } else if (waitedLong) {
    text = tr('splashWaitingLong', { seconds: Math.round(WAITING_NOTICE_MS / 1000) })
    detail = `tool-result notifications received: ${seen}`
  } else text = tr('splashWaiting')
  return (
    <div className="flex h-full w-full items-center justify-center bg-bg p-6">
      <div className="max-w-md text-center" data-boot-state={state}>
        <p className="text-base leading-relaxed text-ink-2">{text}</p>
        {detail && (
          // 诊断材料：原样、等宽、可选中——报 issue 时要贴的就是这一行
          <p
            className="mt-3 select-text break-all text-left font-mono text-xs text-ink-3"
            data-boot-detail
          >
            {detail}
          </p>
        )}
      </div>
    </div>
  )
}

createRoot(rootEl).render(
  <StrictMode>
    <ErrorBoundary>
      {/* 与桌面 / playground 入口一致：属性检查器会渲染 Radix Tooltip。 */}
      <McpProviders>
        <Boot />
      </McpProviders>
    </ErrorBoundary>
  </StrictMode>,
)
