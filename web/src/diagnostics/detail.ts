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
  level: 'error' | 'warn' | 'unhandledrejection' | 'boundary' | 'engine_500'
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

/**
 * 读对象属性但**绝不触发用户写的 getter**：沿原型链找到定义处的描述符，data 属性取值；accessor 只有引擎自带的
 * （原生函数，如新版 V8 的 `stack`）才调用，用户写的（子类把 `message` / `stack` 覆盖成 getter）返回 `GETTER`。
 */
const GETTER = Symbol('getter')
/**
 * 引擎原生的 `stack` accessor 的**身份白名单**：模块加载时从新建的 `Error` 上取出它的 getter（V8 里是同一个函数对象）。
 * 只有 `desc.get` 与白名单里的某个函数全等才调用；其余一切 accessor（用户写的、`bind` 过的、被 Proxy 包过的）都写
 * `[getter]`。（旧版靠 `toString` 含 `[native code]` 判，挡不住 bound getter。）
 */
const NATIVE_GETTERS: ReadonlySet<unknown> = (() => {
  const set = new Set<unknown>()
  try {
    let o: object | null = new Error('probe')
    for (let i = 0; o && i < 4; i++) {
      const d = Object.getOwnPropertyDescriptor(o, 'stack')
      if (d?.get) set.add(d.get)
      o = Object.getPrototypeOf(o)
    }
    const d = Object.getOwnPropertyDescriptor(Error.prototype, 'stack')
    if (d?.get) set.add(d.get)
  } catch {
    /* 取不到就没有白名单：所有 accessor 都不调，栈只剩 data 属性 */
  }
  return set
})()

function dataProp(obj: object, key: string): unknown {
  let o: object | null = obj
  for (let i = 0; o && i < 32; i++) {
    let d: PropertyDescriptor | undefined
    try {
      d = Object.getOwnPropertyDescriptor(o, key)
    } catch {
      return GETTER
    }
    if (d) {
      if ('value' in d) return d.value
      // 引擎自带的 accessor（新版 V8 的 `stack` 就是）按身份白名单放行；其余 accessor（用户写的 / bind 过的 / Proxy 包的）一律不调
      if (typeof d.get === 'function' && NATIVE_GETTERS.has(d.get)) {
        try {
          return d.get.call(obj)
        } catch {
          return GETTER
        }
      }
      return GETTER
    }
    try {
      o = Object.getPrototypeOf(o)
    } catch {
      return GETTER
    }
  }
  return undefined
}

function errorHead(err: Error): string {
  const name = dataProp(err, 'name')
  const message = dataProp(err, 'message')
  const str = (v: unknown) => (v === GETTER ? '[getter]' : typeof v === 'string' ? v : String(v ?? ''))
  return `${str(name)}: ${str(message)}`
}

function errorStack(err: Error): string | null {
  const stack = dataProp(err, 'stack')
  return typeof stack === 'string' && stack ? stack : null
}

/**
 * 是不是 Error：优先用 `Error.isError`（ES2026，内部槽位检查，**不触发 Proxy 的任何 trap**）；老 WebView 没有它时退回
 * `instanceof`（对 Proxy 会触发一次 getPrototypeOf trap——只在这些引擎上，且不会再往下读别的东西）。
 */
const nativeIsError = (Error as unknown as { isError?: (v: unknown) => boolean }).isError
const isErrorValue = (v: unknown): v is Error => (typeof nativeIsError === 'function' ? nativeIsError(v) : v instanceof Error)

/**
 * 业务侧声明「这是一棵纯数据」的对象树（调用方保证里面没有 Proxy / 带副作用的 accessor）。console 包装对**其余**
 * 对象一律只写 `[object]`：判断一个对象是不是纯数据（取原型、读描述符、`for...in`）本身就会触发 Proxy 的 trap
 * （immer draft 之类），所以不碰。原始值与 Error 照常记。
 */
const PLAIN_DATA = new WeakSet<object>()
export function markPlainData<T extends object>(tree: T): T {
  PLAIN_DATA.add(tree)
  return tree
}

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
  if (isErrorValue(value)) return emit(errorHead(value))
  const obj = value as object
  if (depth === 0 && !PLAIN_DATA.has(obj)) return emit('[object]') // WeakSet.has 不触发 trap
  if (seen.has(obj)) return emit('[Circular]')
  if (depth >= SHOW_MAX_DEPTH) return emit('…')
  seen.add(obj)
  try {
    if (Array.isArray(obj)) {
      emit('[')
      for (let i = 0; i < obj.length && i < SHOW_MAX_BREADTH && budget.left > 0; i++) {
        if (i) emit(',')
        const d = Object.getOwnPropertyDescriptor(obj, i)
        if (!d) emit('[object]')
        else if (!('value' in d)) emit('[getter]')
        else write(d.value, out, budget, depth + 1, seen)
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
        // 绝不触发 getter（有副作用 / 读布局的 accessor）：先看属性描述符，accessor 只写占位
        let desc: PropertyDescriptor | undefined
        try {
          desc = Object.getOwnPropertyDescriptor(obj, k)
        } catch {
          desc = undefined
        }
        if (!desc) emit('[object]')
        else if (!('value' in desc)) emit('[getter]')
        else write(desc.value, out, budget, depth + 1, seen)
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
    if (isErrorValue(a)) {
      const st = errorStack(a)
      if (st) {
        stack = st
        break
      }
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

/**
 * 未捕获错误的**旁路订阅点**（PR A：意外错误卡）。window error / unhandledrejection 全应用只在下面装一份监听，
 * 想知道「刚刚又有一条」的人（`lib/unexpectedErrors.ts`）在这里订阅，**不得**自己再 addEventListener。
 * 回调拿到的是原始对象（用于分类），回调里的异常一律吞掉；订阅者不得依赖它同步完成。
 */
export interface UncaughtNotice {
  source: 'window_error' | 'unhandled_rejection'
  /** `ErrorEvent.error` / `PromiseRejectionEvent.reason`，可能不是 Error */
  error: unknown
  message: string
  /** 仅 window error：脚本地址（分类用，不落盘） */
  filename: string
}
const uncaughtListeners = new Set<(n: UncaughtNotice) => void>()
export function onUncaughtError(cb: (n: UncaughtNotice) => void): () => void {
  uncaughtListeners.add(cb)
  return () => void uncaughtListeners.delete(cb)
}
function notifyUncaught(n: UncaughtNotice): void {
  for (const cb of Array.from(uncaughtListeners)) {
    try {
      cb(n)
    } catch {
      /* 订阅者出错不许伤到别的订阅者与页面 */
    }
  }
}

/**
 * 往未捕获错误环里补一条**不是 window 事件**的记录：ErrorBoundary 捕获的渲染错误（React 不再把它们冒到 window）、
 * 引擎 500 `internal_error`。消息与调用栈原文只在这个环里，随发送包走；导出路径与 trace 都不带。
 */
export function recordUncaughtDetail(level: 'boundary' | 'engine_500', message: string, stack: string | null): void {
  try {
    push(errorRing, ERROR_RING_CAPACITY, {
      t: Date.now(),
      level,
      message: message.slice(0, MAX_MESSAGE),
      stack: stack ? stack.slice(0, MAX_STACK) : null,
    })
  } catch {
    /* 同上 */
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
        message: (isErrorValue(err) ? errorHead(err) : e.message || 'error').slice(0, MAX_MESSAGE),
        stack: (isErrorValue(err) ? errorStack(err)?.slice(0, MAX_STACK) : null) ?? `${e.filename}:${e.lineno}:${e.colno}`,
      })
    } catch {
      /* 同上 */
    }
    notifyUncaught({
      source: 'window_error',
      error: e.error as unknown,
      message: typeof e.message === 'string' ? e.message : '',
      filename: typeof e.filename === 'string' ? e.filename : '',
    })
  }
  const onRejection = (e: PromiseRejectionEvent) => {
    try {
      const r = e.reason as unknown
      push(errorRing, ERROR_RING_CAPACITY, {
        t: Date.now(),
        level: 'unhandledrejection',
        message: show(r).slice(0, MAX_MESSAGE),
        stack: (isErrorValue(r) ? errorStack(r)?.slice(0, MAX_STACK) : null) ?? null,
      })
    } catch {
      /* 同上 */
    }
    notifyUncaught({ source: 'unhandled_rejection', error: e.reason as unknown, message: '', filename: '' })
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

export const MAX_OVERRIDE_PANELS = 3

/**
 * 焦点面板：正在编辑的那张；没有就取选中的面板，**主选（最后选中的那张）排第一**，其余按文档顺序——
 * 引擎把 `overrides[0]` 当焦点面板去探解释器 / 执行配置 / cwd，丢了主选顺序会探错面板。
 */
function pickFocusedPanels(): PanelObject[] {
  const objects = useDocumentStore.getState().doc.objects
  const panels = objects.filter((o): o is PanelObject => o.type === 'panel')
  const editing = useUiStore.getState().elementPanelId
  if (editing) return panels.filter((p) => p.id === editing)
  const ids = useSelectionStore.getState().ids
  const picked = panels.filter((p) => ids.includes(p.id))
  const primary = ids.findLast((id) => picked.some((p) => p.id === id))
  const first = picked.find((p) => p.id === primary)
  return first ? [first, ...picked.filter((p) => p !== first)] : picked
}

/** 焦点面板总数里超出数量上限（3）而没发的张数：引擎记进 overrides 的 index */
export function overridesOmitted(): number {
  const picked = pickFocusedPanels()
  return Math.max(0, picked.length - MAX_OVERRIDE_PANELS)
}

/** 正在编辑的面板（没有就取选中的面板）的样式改动——「这张图现在被改成了什么样」的热态，至多 3 张。 */
export function collectFocusedOverrides(): Record<string, unknown>[] {
  const picked = pickFocusedPanels()
  return picked.slice(0, MAX_OVERRIDE_PANELS).map((p) => ({
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

/** 请求体预算：引擎对发送载荷的上限是 1 MiB（`MAX_SEND_REQUEST_BYTES`），超了整份被丢；留出余量。 */
export const SEND_PAYLOAD_BUDGET = 900 * 1024

const bytesOf = (v: unknown): number => new TextEncoder().encode(JSON.stringify(v)).length

type Rec = { message: string; stack: string | null }

/**
 * 最坏情形下（40 条 console 各 4000+8000 字符、30 条错误、3 张面板各 400 条 overrides，再加 300 条轨迹）载荷会超预算：
 * 在前端按固定顺序**先截 detail 再说**，并把截过什么记进 `client_truncated`（引擎原样带进 `detail-frontend-console.json`）。
 * 顺序：栈与消息缩短 → 丢最旧的 console → 丢最旧的错误 → overrides 每面板留 100 条 → 丢面板。老载荷（状态 / 轨迹）不动。
 */
export function fitDetailToBudget(payload: SendPayload, budget = SEND_PAYLOAD_BUDGET): SendPayload {
  // 纯函数：下面的截短只作用在拷贝上。console / errors 环里的记录对象与 store 里的 overrides 数组都是全局的，
  // 原地改写会把证据永久缩短（下一份报告悄悄变短且没有 client_truncated）
  const src = payload.detail as {
    console?: Rec[]
    errors?: Rec[]
    overrides?: { overrides?: unknown[] }[]
  }
  const d = {
    ...(payload.detail as Record<string, unknown>),
    console: src.console?.map((r) => ({ ...r })),
    errors: src.errors?.map((r) => ({ ...r })),
    overrides: src.overrides?.map((p) => ({ ...p })),
  } as typeof src & { client_truncated?: string[] }
  payload = { ...payload, detail: d as unknown as Record<string, unknown> }
  const cut: string[] = []
  const over = () => bytesOf(payload) > budget
  const note = (what: string) => {
    if (!cut.includes(what)) cut.push(what)
  }
  const shorten = (rs?: Rec[]) =>
    rs?.forEach((r) => {
      r.message = r.message.slice(0, 1500)
      if (r.stack) r.stack = r.stack.slice(0, 2000)
    })
  if (over()) {
    shorten(d.console)
    shorten(d.errors)
    note('stack_text')
  }
  while (over() && (d.console?.length ?? 0) > 0) {
    d.console!.shift()
    note('console')
  }
  while (over() && (d.errors?.length ?? 0) > 0) {
    d.errors!.shift()
    note('errors')
  }
  if (over()) {
    d.overrides?.forEach((p) => {
      if (p.overrides && p.overrides.length > 100) p.overrides = p.overrides.slice(0, 100)
    })
    note('overrides')
  }
  while (over() && (d.overrides?.length ?? 0) > 0) {
    d.overrides!.pop()
    note('overrides')
  }
  if (cut.length) d.client_truncated = cut
  return payload
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
  return fitDetailToBudget({
    ...base,
    detail_options: { ...NO_DETAIL_OPTIONS, ...options },
    detail: {
      console: readConsoleRing(),
      errors: readErrorRing(),
      display: guard(collectDisplayEnv, {}),
      shell,
      overrides: guard(collectFocusedOverrides, []),
      overrides_omitted: guard(overridesOmitted, 0),
    },
  })
}
