import { EngineError } from '@/lib/api'
import type { AppsBridge } from './appsBridge'
import { fetchSessionState, isOpenResult, sessionIdOf, type OpenFigureResult } from './session'

/**
 * 画布启动时怎么从 host 递来的 `ui/notifications/tool-result` 走到「有一张图可画」。
 *
 * 三种来货（issue #457）：
 *
 *   * **完整的 open 结果** —— 直接种进 stores（08-24 验收过的那条路，小图都走它）；
 *   * **只有把手**（`session_id` 在、`manifest` 不在）—— server 按宿主对工具结果
 *     事件副本的 1 MiB 上限省略过 manifest / SVG，或者 host 用 apply 的结果起了
 *     一块新 iframe。画布自己发 `tools/call` 把会话状态拉回来：那条路宿主直接
 *     代理、不截断、不进模型上下文；
 *   * **空壳** —— `structuredContent` 是 null / 缺失 / 没有 session_id。这是宿主
 *     把结果清空之后递来的（老 server 的大图结果超过上限时就是这样），或者
 *     host 的接线坏了。画布**当场说出口**，而不是再等下去：等是等不来的。
 *
 * 拿不到会话时**不猜、不自己发起 open**：这块画布是被某一次工具调用带出来的，
 * 没有那次调用的会话就说明 host 侧出了问题，编一张图出来只会更难查。
 */
export type ToolResultClass =
  | { kind: 'open'; payload: OpenFigureResult }
  | { kind: 'handle'; sessionId: string }
  | { kind: 'unusable'; detail: string }

interface Envelope {
  content?: unknown
  structuredContent?: unknown
  isError?: unknown
  _meta?: unknown
  /** 旧 fake host 用过的包装形状（`{ result: CallToolResult }`），留作兼容。 */
  result?: Envelope
}

/** MCP Apps 2026-01-26 把 `CallToolResult` 直接当 params 发；老包装也认。 */
export function unwrapEnvelope(params: unknown): Envelope | null {
  const env = params as Envelope | null
  if (!env || typeof env !== 'object') return null
  return env.result && typeof env.result === 'object' ? env.result : env
}

export function classifyToolResult(params: unknown): ToolResultClass {
  const result = unwrapEnvelope(params)
  const body = result?.structuredContent
  if (isOpenResult(body)) return { kind: 'open', payload: body }
  const sessionId = sessionIdOf(body)
  if (sessionId) return { kind: 'handle', sessionId }
  return { kind: 'unusable', detail: describeUnusable(params, result) }
}

/**
 * 空壳长什么样——这是**诊断材料**，不翻译：报 issue 时要原样贴出来。
 * 只描述形状，不复述内容：content 只取开头 160 字（宿主清空结果时那里是一段
 * 截断的 JSON 预览，开头就足以认出来）。
 */
function describeUnusable(params: unknown, result: Envelope | null): string {
  if (!result) return `params: ${params === null ? 'null' : typeof params}`
  const parts: string[] = []
  const sc = result.structuredContent
  if (sc === undefined) parts.push('structuredContent: missing')
  else if (sc === null) parts.push('structuredContent: null')
  else if (typeof sc === 'object') {
    parts.push(`structuredContent keys: [${Object.keys(sc as object).join(', ')}]`)
  } else parts.push(`structuredContent: ${typeof sc}`)
  parts.push(`_meta: ${result._meta == null ? String(result._meta) : 'object'}`)
  if (result.isError) parts.push('isError: true')
  const content = Array.isArray(result.content) ? result.content : []
  const text = content
    .map((c) => (c && typeof c === 'object' ? (c as { text?: unknown }).text : undefined))
    .find((t): t is string => typeof t === 'string')
  if (text !== undefined) {
    const head = text.length > 160 ? `${text.slice(0, 160)}…` : text
    parts.push(`content[0].text (${text.length} chars): ${JSON.stringify(head)}`)
  } else parts.push(`content: ${content.length} block(s), no text`)
  return parts.join('; ')
}

export type Resolution =
  | { ok: true; payload: OpenFigureResult; fetched: boolean }
  | { ok: false; code: 'stripped' | 'fetch_failed'; detail: string }

/**
 * 从一条 tool-result 走到可种的负载。`onFetching` 在真的要去取件时调一次，
 * 好让启动屏把「正在取回会话状态」摆出来（大图的取件是百 KB 级）。
 */
export async function resolveToolResult(
  bridge: Pick<AppsBridge, 'callTool'>,
  params: unknown,
  onFetching?: () => void,
): Promise<Resolution> {
  const cls = classifyToolResult(params)
  if (cls.kind === 'open') return { ok: true, payload: cls.payload, fetched: false }
  if (cls.kind === 'unusable') return { ok: false, code: 'stripped', detail: cls.detail }
  onFetching?.()
  try {
    return { ok: true, payload: await fetchSessionState(bridge, cls.sessionId), fetched: true }
  } catch (err) {
    const code = err instanceof EngineError && err.code ? `${err.code}: ` : ''
    const message = err instanceof Error ? err.message : String(err)
    return {
      ok: false,
      code: 'fetch_failed',
      detail: `tavotto_session_state(${cls.sessionId}) → ${code}${message}`,
    }
  }
}

/**
 * 握手成功之后多久没等到 tool-result 就把「还在等、以及为什么可能等不来」
 * 摆出来。**不是放弃**：之后到的结果照常收——有的 host 在工具开始时就建
 * iframe，heavy 的脚本要跑几分钟。30 秒是 Codex 的形状：它在工具返回时才建
 * iframe，结果本该随握手立刻到。
 */
export const WAITING_NOTICE_MS = 30_000
