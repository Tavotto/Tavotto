/**
 * 「发送问题反馈」专用的 detail 载荷（bundle schema 8，ADR 0118 修订 2026-10-13）。
 *
 * 与老载荷（`frontend_state` / `interaction_trace`）**刻意不同**：那两样是匿名、无自由文本的结构性防线；这里是
 * 维护者要看的原文——console.error / warn 与未捕获错误的消息和调用栈、壳与 WebView 版本、显示环境、正在编辑的面板的
 * 样式改动。所以它**只走发送路径**（`buildSendPayload`）：导出路径（`buildDiagnosticPayload`）不带它，
 * 引擎的导出端点也根本不读这个键。脱敏（家目录 / 用户名 / 秘密）在引擎侧 `diagdetail.Scrubber` 统一做。
 *
 * 两个定长环（console 40、未捕获错误 30）是**全局**的：切项目不清（console 输出不属于某个项目），包里的
 * `frontend-console.json` 注明了这一点。包装 console 时吞掉自身的所有异常——诊断把一次 `console.error` 弄挂比没有诊断糟得多。
 */
import { isDesktop, getUpdateOutcome } from '@/lib/desktop'
import { currentLocale } from '@/i18n'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { buildDiagnosticPayload } from './index'
import type { DiagnosticPayload } from './types'

/** 确认框里的三个勾选项。默认全 false、不记忆；引擎侧白名单只认字面 true。 */
export interface DetailOptions {
  source: boolean
  crash_reports: boolean
  corrupt_sessions: boolean
}
export const NO_DETAIL_OPTIONS: DetailOptions = { source: false, crash_reports: false, corrupt_sessions: false }

export const CONSOLE_RING_CAPACITY = 40
export const ERROR_RING_CAPACITY = 30
const MAX_MESSAGE = 4000
const MAX_STACK = 8000

export interface RingRecord {
  t: number
  level: 'error' | 'warn' | 'unhandledrejection'
  message: string
  stack: string | null
}

const consoleRing: RingRecord[] = []
const errorRing: RingRecord[] = []

function push(ring: RingRecord[], cap: number, rec: RingRecord): void {
  ring.push(rec)
  if (ring.length > cap) ring.splice(0, ring.length - cap)
}

/** 有界序列化的上限：深度、每层键 / 元素数。累计输出到 MAX_MESSAGE 就停——不先全量 stringify（几 MB 的文档对象会拖慢编辑）。 */
const SHOW_MAX_DEPTH = 4
const SHOW_MAX_BREADTH = 50

/** 小的递归 writer：往 `out` 里追加，超出 `budget` 立刻停；超界处写 `…`。 */
function write(value: unknown, out: string[], budget: { left: number }, depth: number, seen: WeakSet<object>): void {
  if (budget.left <= 0) return
  const emit = (t: string) => {
    out.push(t.length > budget.left ? t.slice(0, budget.left) : t)
    budget.left -= t.length
  }
  if (value === null || value === undefined) return emit(String(value))
  const ty = typeof value
  if (ty === 'string') return emit(depth === 0 ? (value as string) : JSON.stringify((value as string).slice(0, 200)))
  if (ty === 'number' || ty === 'boolean' || ty === 'bigint') return emit(String(value))
  if (ty === 'function' || ty === 'symbol') return emit(`[${ty}]`)
  if (value instanceof Error) return emit(`${value.name}: ${value.message}`)
  const obj = value as object
  if (seen.has(obj)) return emit('[Circular]')
  if (depth >= SHOW_MAX_DEPTH) return emit('…')
  seen.add(obj)
  try {
    if (Array.isArray(obj)) {
      emit('[')
      for (let i = 0; i < obj.length && i < SHOW_MAX_BREADTH && budget.left > 0; i++) {
        if (i) emit(',')
        write(obj[i], out, budget, depth + 1, seen)
      }
      if (obj.length > SHOW_MAX_BREADTH) emit(',…')
      emit(']')
    } else {
      emit('{')
      let n = 0
      for (const k in obj) {
        if (!Object.prototype.hasOwnProperty.call(obj, k)) continue
        if (n >= SHOW_MAX_BREADTH || budget.left <= 0) {
          emit(',…')
          break
        }
        if (n++) emit(',')
        emit(JSON.stringify(k.slice(0, 80)) + ':')
        let v: unknown
        try {
          v = (obj as Record<string, unknown>)[k]
        } catch {
          v = '[unreadable]'
        }
        write(v, out, budget, depth + 1, seen)
      }
      emit('}')
    }
  } finally {
    seen.delete(obj)
  }
}

function show(value: unknown): string {
  try {
    const out: string[] = []
    write(value, out, { left: MAX_MESSAGE }, 0, new WeakSet())
    return out.join('')
  } catch {
    return Object.prototype.toString.call(value)
  }
}

function formatArgs(args: unknown[]): { message: string; stack: string | null } {
  let stack: string | null = null
  for (const a of args) {
    if (a instanceof Error && a.stack) {
      stack = a.stack
      break
    }
  }
  const message = args.map(show).join(' ')
  return { message: message.slice(0, MAX_MESSAGE), stack: stack ? stack.slice(0, MAX_STACK) : null }
}

function callSiteStack(): string | null {
  try {
    const lines = (new Error().stack ?? '').split('\n')
    // 去掉第一行（Error）与本模块的包装帧
    return lines.slice(3).join('\n').slice(0, MAX_STACK) || null
  } catch {
    return null
  }
}

let installed: (() => void) | null = null
let recording = false

/**
 * 装上 console / 全局错误捕获。**幂等**；返回卸载函数（测试与热更新用）。
 * 包装函数里任何异常都吞掉，且保证原 console 方法一定被调用、`this` 与返回值不变。
 */
export function installConsoleCapture(): () => void {
  if (installed) return installed
  const c = console as unknown as Record<'error' | 'warn', (...a: unknown[]) => void>
  const originals = { error: c.error, warn: c.warn }
  for (const level of ['error', 'warn'] as const) {
    const orig = originals[level]
    c[level] = function (this: unknown, ...args: unknown[]) {
      if (!recording) {
        recording = true
        try {
          const f = formatArgs(args)
          push(consoleRing, CONSOLE_RING_CAPACITY, {
            t: Date.now(),
            level,
            message: f.message,
            stack: f.stack ?? callSiteStack(),
          })
        } catch {
          /* 诊断自己出错不许伤到 console */
        } finally {
          recording = false
        }
      }
      return orig.apply(this, args)
    }
  }
  const onError = (e: ErrorEvent) => {
    try {
      const err = e.error as unknown
      push(errorRing, ERROR_RING_CAPACITY, {
        t: Date.now(),
        level: 'error',
        message: (err instanceof Error ? `${err.name}: ${err.message}` : e.message || 'error').slice(0, MAX_MESSAGE),
        stack: err instanceof Error && err.stack ? err.stack.slice(0, MAX_STACK) : `${e.filename}:${e.lineno}:${e.colno}`,
      })
    } catch {
      /* 同上 */
    }
  }
  const onRejection = (e: PromiseRejectionEvent) => {
    try {
      const r = e.reason as unknown
      push(errorRing, ERROR_RING_CAPACITY, {
        t: Date.now(),
        level: 'unhandledrejection',
        message: show(r).slice(0, MAX_MESSAGE),
        stack: r instanceof Error && r.stack ? r.stack.slice(0, MAX_STACK) : null,
      })
    } catch {
      /* 同上 */
    }
  }
  window.addEventListener('error', onError)
  window.addEventListener('unhandledrejection', onRejection)
  installed = () => {
    // 只有 console 方法还是我们装的那一层时才还原，别踩掉后来者的包装
    c.error = originals.error
    c.warn = originals.warn
    window.removeEventListener('error', onError)
    window.removeEventListener('unhandledrejection', onRejection)
    installed = null
  }
  return installed
}

export const readConsoleRing = (): RingRecord[] => consoleRing.slice()
export const readErrorRing = (): RingRecord[] => errorRing.slice()
/** 仅供测试 */
export function __resetDetailRings(): void {
  consoleRing.length = 0
  errorRing.length = 0
}

/** 显示环境：事实而非内容。 */
export function collectDisplayEnv(): Record<string, unknown> {
  const root = typeof document !== 'undefined' ? document.documentElement : null
  const explicit = root?.getAttribute('data-theme')
  const prefersDark =
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-color-scheme: dark)').matches
      : false
  const scr = typeof screen !== 'undefined' ? screen : null
  return {
    device_pixel_ratio: window.devicePixelRatio,
    window_width: window.innerWidth,
    window_height: window.innerHeight,
    screen_width: scr?.width,
    screen_height: scr?.height,
    screen_avail_width: scr?.availWidth,
    screen_avail_height: scr?.availHeight,
    color_depth: scr?.colorDepth,
    visual_viewport_scale: window.visualViewport?.scale,
    language: currentLocale(),
    languages: Array.from(navigator.languages ?? []).slice(0, 8),
    theme: explicit === 'light' || explicit === 'dark' ? explicit : 'system',
    resolved_theme: explicit === 'light' || explicit === 'dark' ? explicit : prefersDark ? 'dark' : 'light',
  }
}

/** 桌面壳与 WebView 版本；网页模式只有 UA。取不到的字段不写（引擎侧如实标 unknown）。 */
export async function collectShellInfo(): Promise<Record<string, unknown>> {
  const out: Record<string, unknown> = {
    desktop: isDesktop(),
    user_agent: navigator.userAgent,
    platform: navigator.platform,
    update: getUpdateOutcome(),
  }
  const ua = /(Chrome|Chromium|Edg|Version)\/([\d.]+)/.exec(navigator.userAgent)
  if (ua) out.webview_version = `${ua[1]}/${ua[2]}`
  if (isDesktop()) {
    try {
      const app = await import('@tauri-apps/api/app')
      out.app_version = await app.getVersion()
      out.tauri_version = await app.getTauriVersion()
    } catch {
      /* 老壳 / ACL 不放行：留空，不影响备包 */
    }
  }
  return out
}

/** 正在编辑的面板（没有就取选中的面板）的样式改动——「这张图现在被改成了什么样」的热态，至多 3 张。 */
export function collectFocusedOverrides(): Record<string, unknown>[] {
  const objects = useDocumentStore.getState().doc.objects
  const panels = objects.filter((o): o is PanelObject => o.type === 'panel')
  const editing = useUiStore.getState().elementPanelId
  const picked = editing
    ? panels.filter((p) => p.id === editing)
    : panels.filter((p) => useSelectionStore.getState().ids.includes(p.id))
  return picked.slice(0, 3).map((p) => ({
    panel: p.fileId,
    kind: p.fileKind,
    script: p.script ?? null,
    overrides: p.overrides,
  }))
}

export type SendPayload = DiagnosticPayload & {
  detail_options: DetailOptions
  detail: Record<string, unknown>
}

/**
 * 发送路径的载荷 = 老载荷 + 勾选项 + detail。导出路径不调它。
 * 采集的任何一块失败只让那一块缺席，不让备包失败。
 */
export async function buildSendPayload(options: DetailOptions): Promise<SendPayload> {
  const base = buildDiagnosticPayload()
  const guard = <T,>(fn: () => T, fallback: T): T => {
    try {
      return fn()
    } catch {
      return fallback
    }
  }
  let shell: Record<string, unknown> = {}
  try {
    shell = await collectShellInfo()
  } catch {
    /* 同上 */
  }
  return {
    ...base,
    detail_options: { ...NO_DETAIL_OPTIONS, ...options },
    detail: {
      console: readConsoleRing(),
      errors: readErrorRing(),
      display: guard(collectDisplayEnv, {}),
      shell,
      overrides: guard(collectFocusedOverrides, []),
    },
  }
}
