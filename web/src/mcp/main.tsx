import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { applyLocale, t as translate } from '@/i18n'
import { normalizeLocale } from '@/i18n/locale'
import { AppsBridge, hostFallback } from './appsBridge'
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
 *   4. 把结果灌进既有 stores，挂 `McpApp`。
 *
 * 拿不到结果时**不猜、不自己发起 open**：这块画布是被某一次工具调用带出来的，
 * 没有那次调用的结果就说明 host 侧出了问题，编一张图出来只会更难查。
 */

const rootEl = document.getElementById('root')!
const bridge = new AppsBridge()
// 传输必须先装：store 一旦挂载就可能发渲染请求，那时候拿到默认的 HTTP 传输
// 会打到一个不存在的 /api（iframe 里没有 Tavotto 服务）
installMcpTransport(bridge)

/**
 * 只接受**完整的** open 结果。
 *
 * `tavotto_apply_overrides` 的响应也挂着同一份 widget 资源，也就能用来初始化
 * 一个新 iframe——而它带着 `session_id` 与 `manifest`，只看这两项的话会被
 * 当成 open 结果收下。可它没有 `profile` / `project` / `script`：`McpApp`
 * 一读 `open.profile.profile_id` 就当场崩掉；就算把那次读取包起来，用
 * `overrides: []` 去 seed 也会把已经应用的整份 patch 集忘干净——画布看起来
 * 正常，用户的修改却已经不在账本里了。
 */
function isOpenResult(v: unknown): v is OpenFigureResult {
  const o = v as OpenFigureResult | null
  if (!o || typeof o !== 'object') return false
  return (
    typeof o.session_id === 'string' &&
    !!o.manifest &&
    typeof o.project === 'string' &&
    typeof o.stem === 'string' &&
    typeof o.script === 'string' &&
    !!o.profile &&
    typeof o.profile.profile_id === 'string'
  )
}

function Boot() {
  const [open, setOpen] = useState<OpenFigureResult | null>(null)
  const [panelId, setPanelId] = useState<string | null>(null)
  const [state, setState] = useState<'connecting' | 'waiting' | 'ready' | 'nohost'>('connecting')

  useEffect(() => {
    let done = false
    const accept = (payload: unknown) => {
      if (done || !isOpenResult(payload)) return
      done = true
      const { panelId: pid } = seedSession(payload)
      setOpen(payload)
      setPanelId(pid)
      setState('ready')
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

    // MCP Apps 标准路径：host 把工具结果推过来
    const off = bridge.on('ui/notifications/tool-result', (params) => {
      // MCP Apps 2026-01-26 sends CallToolResult directly as notification params.
      // Keep the older { result: CallToolResult } wrapper as a compatibility path;
      // treating it as the standard shape made Codex complete ui/initialize yet leave
      // the canvas forever on "waiting for tavotto_open_figure".
      const envelope = params as {
        structuredContent?: unknown
        _meta?: Record<string, unknown>
        result?: { structuredContent?: unknown; _meta?: Record<string, unknown> }
      }
      const result = envelope?.result ?? envelope
      accept(result?.structuredContent ?? (result?._meta?.widgetData as unknown))
    })

    void bridge.connect({ name: 'tavotto-canvas', version: '1' }).then((ok) => {
      if (!ok) {
        setState('nohost')
        return
      }
      // 握手响应里带的 hostContext：先于一切界面渲染把语言对齐到宿主
      applyHostLocale(bridge.hostContext)
      // 复杂编辑画布：inline 那点高度放不下图 + 属性页
      bridge.requestFullscreen()
      setState((s) => (s === 'ready' ? s : 'waiting'))
      // 兜底：某些 surface 只把结果挂在 window.openai 上，且在握手前就写好了
      const fb = hostFallback()
      if (fb.toolOutput) accept(fb.toolOutput)
    })

    return () => {
      off()
      offLocale()
    }
  }, [])

  if (state === 'ready' && open && panelId) {
    return <McpApp bridge={bridge} open={open} panelId={panelId} />
  }
  // 走到这里 state 必然不是 ready（上面那个分支已经处理掉了），
  // 但 open/panelId 也可能还没到位——都归 Splash 说人话
  return <Splash state={state === 'ready' ? 'waiting' : state} />
}

function Splash({ state }: { state: 'connecting' | 'waiting' | 'nohost' }) {
  // 与 McpApp 同一个命名空间（`dialogs:mcp.*`）：这一屏以前是硬编码中文，
  // 英文 host 里连接 / 等待 / 无 host 三种状态全是中文
  const key = state === 'nohost' ? 'splashNoHost' : state === 'connecting' ? 'splashConnecting' : 'splashWaiting'
  const text = translate(`mcp.${key}`, { ns: 'dialogs' })
  return (
    <div className="flex h-full w-full items-center justify-center bg-bg p-6">
      <p className="max-w-md text-center text-[13px] leading-relaxed text-ink-2">{text}</p>
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
