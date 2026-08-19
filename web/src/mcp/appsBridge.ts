/**
 * MCP Apps 的 host 桥 —— **JSON-RPC over postMessage**，手写、零依赖。
 *
 * 协议取自 `@modelcontextprotocol/ext-apps`（MCP Apps SDK）的线上行为：
 * 消息就是**裸的 JSON-RPC 对象**直接 `window.parent.postMessage(msg, '*')`，
 * 收也是直接读 `event.data`，没有额外信封。
 *
 *   app → host  `ui/initialize` {appInfo, appCapabilities, protocolVersion}
 *               → {protocolVersion, hostInfo, hostCapabilities, hostContext}
 *   app → host  通知 `ui/notifications/initialized`
 *   host → app  通知 `ui/notifications/tool-input` / `tool-result`
 *                    / `tool-cancelled` / `host-context-changed`
 *   app → host  请求 `tools/call`、`ui/message`、`ui/request-display-mode`、
 *                    `ui/update-model-context`
 *   app → host  通知 `ui/notifications/size-changed`
 *
 * 为什么不引 SDK：那是个 npm 包，而这块画布要打成**单文件 HTML** 塞进 MCP
 * 资源里。协议这一层只有一百来行，手写比把整个 SDK 连同 zod 一起 inline 划算。
 *
 * `window.openai.*` **只在共享标准给不出结果时**才用，且必须 feature-detect
 * （见 `hostFallback`）——它是 ChatGPT 侧的兼容别名，不是标准。
 */

/** MCP Apps 的 UI 扩展协议版本（ext-apps 1.7.x 用的就是这个）。 */
export const UI_PROTOCOL_VERSION = '2026-01-26'

export interface ToolCallResult {
  content?: { type: string; text?: string }[]
  structuredContent?: Record<string, unknown>
  isError?: boolean
  _meta?: Record<string, unknown>
}

interface Pending {
  resolve: (v: unknown) => void
  reject: (e: Error) => void
  timer: number
}

type Listener = (params: unknown) => void

export class AppsBridge {
  private id = 0
  private pending = new Map<number, Pending>()
  private listeners = new Map<string, Set<Listener>>()
  private target: Window | null
  /** 握手结果；没有 host（本地开发直接开页面）时恒为 null */
  hostContext: Record<string, unknown> | null = null
  hostCapabilities: Record<string, unknown> | null = null
  connected = false

  constructor(target?: Window | null) {
    // 顶层窗口 = 没有 host（本地调试直接打开 HTML）。此时 target 为 null，
    // 一切请求立刻失败并给出可读原因，而不是永远挂着。
    this.target =
      target !== undefined ? target : window.parent !== window ? window.parent : null
    window.addEventListener('message', this.onMessage)
  }

  private onMessage = (ev: MessageEvent) => {
    const msg = ev.data
    if (!msg || typeof msg !== 'object' || msg.jsonrpc !== '2.0') return
    if (typeof msg.id === 'number' && ('result' in msg || 'error' in msg)) {
      const p = this.pending.get(msg.id)
      if (!p) return
      this.pending.delete(msg.id)
      window.clearTimeout(p.timer)
      if (msg.error) p.reject(new Error(msg.error.message ?? 'host 返回错误'))
      else p.resolve(msg.result)
      return
    }
    if (typeof msg.method === 'string') {
      // host 发来的请求（ping / teardown）：有 id 就得回，不回 host 会认为我们死了
      if (msg.id != null) {
        this.post({ jsonrpc: '2.0', id: msg.id, result: {} })
      }
      for (const fn of this.listeners.get(msg.method) ?? []) fn(msg.params)
    }
  }

  private post(msg: unknown): void {
    this.target?.postMessage(msg, '*')
  }

  on(method: string, fn: Listener): () => void {
    if (!this.listeners.has(method)) this.listeners.set(method, new Set())
    this.listeners.get(method)!.add(fn)
    return () => this.listeners.get(method)?.delete(fn)
  }

  notify(method: string, params?: unknown): void {
    this.post({ jsonrpc: '2.0', method, ...(params !== undefined ? { params } : {}) })
  }

  /**
   * `signal` 不是可选的锦上添花：`renderStore` 给每次渲染挂了按脚本 cost 分级的
   * 看门狗（light 2min / medium 5min / heavy 15min），超时就 `ctrl.abort()`。
   * HTTP 那条路把 signal 转给 fetch，abort 当场 reject；这条路要是把它丢掉，
   * 看门狗对内嵌画布就完全不起作用——一次卡死的渲染会一直转到 request 自己
   * 那 15 分钟的兜底超时，中途用户点什么都取消不掉。
   */
  request<T = unknown>(
    method: string,
    params?: unknown,
    timeoutMs = 900_000,
    signal?: AbortSignal,
  ): Promise<T> {
    if (!this.target) {
      return Promise.reject(
        new Error('没有 MCP host（这块画布要在 Codex 里打开，不能直接开 HTML 文件）'),
      )
    }
    if (signal?.aborted) return Promise.reject(new DOMException('已取消', 'AbortError'))
    const id = ++this.id
    return new Promise<T>((resolve, reject) => {
      const done = () => {
        this.pending.delete(id)
        window.clearTimeout(timer)
        signal?.removeEventListener('abort', onAbort)
      }
      const onAbort = () => {
        done()
        reject(new DOMException('已取消', 'AbortError'))
      }
      const timer = window.setTimeout(() => {
        done()
        reject(new Error(`${method} 超时（${Math.round(timeoutMs / 1000)}s 没有响应）`))
      }, timeoutMs)
      signal?.addEventListener('abort', onAbort, { once: true })
      this.pending.set(id, {
        resolve: (v) => {
          done()
          ;(resolve as (x: unknown) => void)(v)
        },
        reject: (e) => {
          done()
          reject(e)
        },
        timer,
      })
      this.post({ jsonrpc: '2.0', id, method, ...(params !== undefined ? { params } : {}) })
    })
  }

  /** 握手。失败不抛给调用方——没有 host 时画布要能显示「这里得在 Codex 里打开」。 */
  async connect(appInfo: { name: string; version: string }): Promise<boolean> {
    if (!this.target) return false
    try {
      const res = (await this.request<Record<string, unknown>>(
        'ui/initialize',
        {
          appInfo,
          appCapabilities: {},
          protocolVersion: UI_PROTOCOL_VERSION,
        },
        20_000,
      )) as { hostContext?: Record<string, unknown>; hostCapabilities?: Record<string, unknown> }
      this.hostContext = res?.hostContext ?? null
      this.hostCapabilities = res?.hostCapabilities ?? null
      this.notify('ui/notifications/initialized', {})
      this.connected = true
      return true
    } catch {
      return false
    }
  }

  /** 调 server 上的工具。**画布与后端之间的唯一通道**（没有任何跨源请求）。 */
  callTool(
    name: string,
    args: Record<string, unknown>,
    timeoutMs?: number,
    signal?: AbortSignal,
  ): Promise<ToolCallResult> {
    return this.request<ToolCallResult>('tools/call', { name, arguments: args }, timeoutMs, signal)
  }

  /** 请求全屏：复杂编辑画布的主要形态（inline 那点高度放不下一张图 + 属性页）。 */
  requestFullscreen(): void {
    if (!this.target) return
    void this.request('ui/request-display-mode', { mode: 'fullscreen' }, 10_000).catch(() => {
      /* host 不支持就维持 inline，不是错误 */
    })
  }

  /** 让模型接着说话（「我改完了，帮我导出」这类）。 */
  sendMessage(text: string): void {
    if (!this.target) return
    void this.request('ui/message', {
      role: 'user',
      content: [{ type: 'text', text }],
    }).catch(() => {
      /* host 不支持就算了 */
    })
  }

  notifySize(): void {
    if (!this.target) return
    this.notify('ui/notifications/size-changed', {
      width: Math.ceil(window.innerWidth),
      height: Math.ceil(document.documentElement.scrollHeight),
    })
  }

  dispose(): void {
    window.removeEventListener('message', this.onMessage)
    for (const p of this.pending.values()) {
      window.clearTimeout(p.timer)
      p.reject(new Error('画布已卸载'))
    }
    this.pending.clear()
  }
}

/**
 * `window.openai.*` 的兼容读取 —— **只在标准路径拿不到东西时兜底**，且逐个
 * feature-detect。标准是 `ui/notifications/tool-result`；ChatGPT 会另外把同一份
 * 数据挂到 `window.openai.toolOutput`，某些 surface 上只有后者。
 */
export function hostFallback(): {
  toolOutput: Record<string, unknown> | null
  displayMode: string | null
} {
  const w = window as unknown as {
    openai?: { toolOutput?: unknown; displayMode?: unknown }
  }
  const api = w.openai
  if (!api || typeof api !== 'object') return { toolOutput: null, displayMode: null }
  return {
    toolOutput:
      api.toolOutput && typeof api.toolOutput === 'object'
        ? (api.toolOutput as Record<string, unknown>)
        : null,
    displayMode: typeof api.displayMode === 'string' ? api.displayMode : null,
  }
}
