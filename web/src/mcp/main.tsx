import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { AppsBridge, hostFallback } from './appsBridge'
import { McpApp } from './McpApp'
import { installMcpTransport, seedSession, type OpenFigureResult } from './session'
import '@/index.css'

/**
 * MCP App 画布的入口。
 *
 * 生命周期：
 *   1. 装 MCP 传输（画布里的一切引擎往来都要走它，**必须在挂载之前**）；
 *   2. 与 host 握手（`ui/initialize`），失败也照常挂载——好告诉用户为什么空着；
 *   3. 等 `ui/notifications/tool-result` 送来 `magplot_open_figure` 的结果；
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
// 会打到一个不存在的 /api（iframe 里没有 Magplot 服务）
installMcpTransport(bridge)

/**
 * 只接受**完整的** open 结果。
 *
 * `magplot_apply_overrides` 的响应也挂着同一份 widget 资源，也就能用来初始化
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

    // MCP Apps 标准路径：host 把工具结果推过来
    const off = bridge.on('ui/notifications/tool-result', (params) => {
      const result = (params as { result?: { structuredContent?: unknown; _meta?: Record<string, unknown> } })
        ?.result
      accept(result?.structuredContent ?? (result?._meta?.widgetData as unknown))
    })

    void bridge.connect({ name: 'magplot-canvas', version: '1' }).then((ok) => {
      if (!ok) {
        setState('nohost')
        return
      }
      // 复杂编辑画布：inline 那点高度放不下图 + 属性页
      bridge.requestFullscreen()
      setState((s) => (s === 'ready' ? s : 'waiting'))
      // 兜底：某些 surface 只把结果挂在 window.openai 上，且在握手前就写好了
      const fb = hostFallback()
      if (fb.toolOutput) accept(fb.toolOutput)
    })

    return () => {
      off()
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
  const text =
    state === 'nohost'
      ? '这块画布要在支持 MCP Apps 的 Codex 里打开。没有 UI 的 host 里，同一套 magplot_* 工具也能完成打开 / 改图 / 预检 / 导出。'
      : state === 'connecting'
        ? '正在连接 Codex…'
        : '正在等待 magplot_open_figure 的结果…'
  return (
    <div className="flex h-full w-full items-center justify-center bg-bg p-6">
      <p className="max-w-md text-center text-[13px] leading-relaxed text-ink-2">{text}</p>
    </div>
  )
}

createRoot(rootEl).render(
  <StrictMode>
    <ErrorBoundary>
      <Boot />
    </ErrorBoundary>
  </StrictMode>,
)
