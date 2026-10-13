/**
 * 意外错误的**唯一汇聚点**（ADR 0118 补充 2026-10-14）：window error / unhandledrejection（订阅 `diagnostics/detail.ts`
 * 那一份监听，不另装）、ErrorBoundary、引擎兜底 500（`lib/api.ts` 的 `setEngine500Handler`）都走 `reportUnexpectedError`。
 *
 * 顺序固定：分类（过滤结构化失败与噪声）→ **先记诊断事件 `ui.unexpected_error`**（吞异常；trace 里只有来源 / 内建错误名 /
 * message 的 hash）→ 补未捕获错误环（原文与栈，只随发送包走）→ 只有「发送问题反馈」开着（`diagSendStore.capability !== null`）
 * 才可能登记通知轨卡片。卡片只是一句话 + 打开现有确认框；**上传必须等用户在确认框点「发送」**。
 */
import { setEngine500Handler } from '@/lib/api'
import { errorMessageHash, recordDiagnosticEvent } from '@/diagnostics'
import { onUncaughtError, recordUncaughtDetail } from '@/diagnostics/detail'
import {
  UNEXPECTED_ERROR_NAMES,
  type UnexpectedErrorName,
  type UnexpectedErrorSource,
} from '@/diagnostics/types'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useUnexpectedErrorStore } from '@/store/unexpectedErrorStore'

/** 去重集合上限（指纹见 `fingerprintOf`）；满了淘汰最久没碰过的 */
export const MAX_FINGERPRINTS = 64
/** 每会话主动提示的总数上限；超了只记环不弹 */
export const MAX_PROMPTS_PER_SESSION = 5

export interface UnexpectedErrorInput {
  source: UnexpectedErrorSource
  /** 原始错误对象 / 拒绝原因（分类用；engine_500 没有） */
  error?: unknown
  /** window error 的 `ErrorEvent.message` */
  message?: string
  filename?: string
  /** boundary：React 的 componentStack */
  componentStack?: string
  /** engine_500：请求方法与未规整的 url */
  request?: { method: string; url: string }
}

export type UnexpectedErrorOutcome = 'ignored' | 'recorded' | 'prompted'

const BUILTIN_NAMES = new Set<string>(UNEXPECTED_ERROR_NAMES)

/** 内建错误类的原型表：原型不在表里 = 业务自己的错误类（`ApiError` / `EngineError` / `PendingConflict` …）= 结构化失败，各有各的卡 */
function builtinPrototypes(): object[] {
  const out: object[] = [Error.prototype]
  for (const C of [TypeError, RangeError, ReferenceError, SyntaxError, EvalError, URIError]) out.push(C.prototype)
  if (typeof DOMException !== 'undefined') out.push(DOMException.prototype)
  return out
}

function isStructuredFailure(e: unknown): boolean {
  if (!(e instanceof Error) && !(typeof DOMException !== 'undefined' && e instanceof DOMException)) return false
  return !builtinPrototypes().includes(Object.getPrototypeOf(e))
}

const IGNORED_MESSAGE = /^(ResizeObserver loop|Script error\.?$)/
/** pyodide / web worker 的报错不是主界面的意外（浏览器 playground 有自己的失败呈现） */
const WORKER_ORIGIN = /pyodide|\.worker\b|worker-[\w-]+\.js/i
/** 诊断 / 发送流程自身：靠栈里的文件名认（生产构建合并了文件名时靠 `diagSendStore.open` 兜底） */
const SELF_ORIGIN = /\/diagnostics\/|DiagnosticsSendDialog|diagSend/

function errorNameOf(e: unknown): UnexpectedErrorName {
  if (typeof DOMException !== 'undefined' && e instanceof DOMException) return e.name === 'AbortError' ? 'AbortError' : 'DOMException'
  const n = e instanceof Error ? e.name : ''
  return BUILTIN_NAMES.has(n) && n !== 'other' ? (n as UnexpectedErrorName) : 'other'
}

/** 不该提示的一类（返回 true = 忽略，连 trace 也不记）。只按**结构**判，不凭消息关键词猜严重度 */
function isIgnored(i: UnexpectedErrorInput): boolean {
  if (i.source === 'engine_500') return false
  const e = i.error
  if (isStructuredFailure(e)) return true
  if (errorNameOf(e) === 'AbortError') return true
  const message = e instanceof Error ? e.message : (i.message ?? '')
  if (IGNORED_MESSAGE.test(message) || IGNORED_MESSAGE.test(i.message ?? '')) return true
  if (i.filename && WORKER_ORIGIN.test(i.filename)) return true
  if (e instanceof Error && e.stack && WORKER_ORIGIN.test(e.stack)) return true
  // 没有 Error 对象的拒绝（取消令牌、`reject()`）不是可定位的意外
  if (i.source === 'unhandled_rejection' && !(e instanceof Error)) return true
  if (!(e instanceof Error) && !message) return true
  return false
}

/** 路由规则：去掉查询串，把非「纯小写词」的段（项目 id / 文件名 / 数字）规整成 `:id`——既不带用户内容，也让同一路由同一指纹 */
export function routeRule(url: string): string {
  let path = url
  try {
    path = new URL(url, 'http://local.invalid').pathname
  } catch {
    path = url.split(/[?#]/)[0]
  }
  return path
    .split('/')
    .map((seg) => (seg === '' || /^[a-z][a-z_-]{0,23}$/.test(seg) ? seg : ':id'))
    .join('/')
}

interface Classified {
  name: UnexpectedErrorName
  /** 进未捕获错误环 / 取 hash 的文本 */
  message: string
  fingerprint: string
  /** 栈 / componentStack：仅 boundary、engine_500 需要自己补环（window 来源 detail.ts 已记） */
  stack: string | null
}

function classify(i: UnexpectedErrorInput): Classified {
  if (i.source === 'engine_500') {
    const method = i.request?.method ?? 'GET'
    const route = routeRule(i.request?.url ?? '')
    const message = `engine internal_error ${method} ${route}`
    return { name: 'other', message, fingerprint: `engine_500|${method} ${route}`, stack: null }
  }
  const e = i.error
  const name = errorNameOf(e)
  let message = ''
  let stack: string | null = null
  if (e instanceof Error) {
    message = `${e.name}: ${e.message}`
    stack = e.stack ?? null
  } else {
    message = i.message ?? ''
  }
  if (i.componentStack) stack = `${stack ?? ''}\n\ncomponentStack:${i.componentStack}`.trim()
  return { name, message, fingerprint: `${i.source}|${name}|${errorMessageHash(message)}`, stack }
}

/* -------------------------------- 会话状态 -------------------------------- */

/** 指纹 → 先后（Map 的插入序即 LRU 序：命中时删了再插） */
const seen = new Map<string, true>()
let prompts = 0

function touch(fp: string): boolean {
  const hit = seen.delete(fp)
  seen.set(fp, true)
  while (seen.size > MAX_FINGERPRINTS) seen.delete(seen.keys().next().value as string)
  return hit
}

/** 此刻能不能弹卡：开关、来源、是否已在发送流程里。false 时仍记环 */
function mayPrompt(i: UnexpectedErrorInput, stack: string | null): boolean {
  if (useDiagSendStore.getState().capability === null) return false
  // ErrorBoundary 来的：App 树（含通知轨）已经死了，崩溃页自己有入口
  if (i.source === 'boundary') return false
  // 发送流程自身抛的错（防递归）：对话框开着、或栈指回诊断模块
  if (useDiagSendStore.getState().open) return false
  if (stack && SELF_ORIGIN.test(stack)) return false
  return true
}

/**
 * 汇报一条意外错误。**永不抛**。返回值只给测试：`ignored`（分类过滤，什么都没记）、`recorded`（记了环，没弹）、
 * `prompted`（登记了通知轨卡片）。
 */
export function reportUnexpectedError(input: UnexpectedErrorInput): UnexpectedErrorOutcome {
  try {
    if (isIgnored(input)) return 'ignored'
    const c = classify(input)
    // 一律先记：trace 里没有 message / stack 原文，只有闭集枚举 + hash
    recordDiagnosticEvent({
      type: 'ui.unexpected_error',
      source: input.source,
      error_name: c.name,
      message_hash: errorMessageHash(c.message),
    })
    // window 来源 detail.ts 已经记过环；ErrorBoundary / 引擎 500 在这里补一条（带原文，随发送包走）
    if (input.source === 'boundary' || input.source === 'engine_500') {
      recordUncaughtDetail(input.source, c.message, c.stack)
    }
    if (!mayPrompt(input, c.stack ?? (input.error instanceof Error ? (input.error.stack ?? null) : null))) return 'recorded'
    if (touch(c.fingerprint)) return 'recorded' // 同一指纹本会话已经考虑过（弹过或被上界挡过），不再弹
    if (prompts >= MAX_PROMPTS_PER_SESSION) return 'recorded'
    if (!useUnexpectedErrorStore.getState().show()) return 'recorded' // 轨上已有一张
    prompts += 1
    return 'prompted'
  } catch {
    return 'recorded'
  }
}

/** 装上来源：订阅 detail.ts 的未捕获错误 + 引擎 500 出口。幂等；返回卸载函数 */
let installed: (() => void) | null = null
export function installUnexpectedErrorCapture(): () => void {
  if (installed) return installed
  const off = onUncaughtError((n) =>
    reportUnexpectedError({ source: n.source, error: n.error, message: n.message, filename: n.filename }),
  )
  setEngine500Handler((request) => reportUnexpectedError({ source: 'engine_500', request }))
  installed = () => {
    off()
    setEngine500Handler(null)
    installed = null
  }
  return installed
}

/** 仅供测试 */
export function __resetUnexpectedErrors(): void {
  seen.clear()
  prompts = 0
  useUnexpectedErrorStore.getState().dismiss()
}
export const __unexpectedErrorState = () => ({ fingerprints: seen.size, prompts })
