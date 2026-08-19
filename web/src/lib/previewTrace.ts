/**
 * 假实时预览的客户端计时（默认完全静默）。
 *
 * 后端的 `timings` 一个字段都不动——那是 worker 与控制面的账。这里补的是
 * 后端看不见的另一半：鼠标到首帧、松手到 commit、松手到请求发出、权威 SVG
 * 换上画布的时刻。少了这半边，「慢」只能靠感觉描述。
 *
 * 开关：`window.__MM_PREVIEW_TRACE__ = true`（或 localStorage 的同名键）。
 * 关着的时候一条 console 都不打、performance.mark 也不下——生产日志噪音
 * 是要还的，而这类计时九成时间没人看。
 */

export interface PreviewTiming {
  panelId: string
  /** performance.now()，session 建立那一刻 */
  preview_session_start: number
  /** 第一帧预览真正写进 DOM 的时刻（相对 session_start，毫秒） */
  preview_first_frame: number | null
  /** 这一轮合并后实际写 DOM 的帧数（不是 pointermove 次数） */
  preview_frame_count: number
  /** pointermove 次数：与 frame_count 的差就是 rAF 合并掉的量 */
  preview_move_count: number
  /** 正式 commit 开始（相对 session_start） */
  commit_start: number | null
  /** commit 到权威 SVG 换上画布（毫秒）——用户眼里「等了多久」的那个数 */
  commit_to_authority_ms: number | null
  /** 权威 SVG 被前端替换的时刻（相对 session_start） */
  authority_svg_replaced: number | null
}

const KEY = '__MM_PREVIEW_TRACE__'

export function traceEnabled(): boolean {
  if (typeof window === 'undefined') return false
  const w = window as unknown as Record<string, unknown>
  if (w[KEY] === true) return true
  try {
    return window.localStorage?.getItem(KEY) === '1'
  } catch {
    return false // 隐私模式 / 被禁用的 storage 不该把预览带崩
  }
}

export function now(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now()
}

export function newTiming(panelId: string): PreviewTiming {
  return {
    panelId,
    preview_session_start: now(),
    preview_first_frame: null,
    preview_frame_count: 0,
    preview_move_count: 0,
    commit_start: null,
    commit_to_authority_ms: null,
    authority_svg_replaced: null,
  }
}

function mark(name: string): void {
  if (!traceEnabled()) return
  try {
    performance.mark(name)
  } catch {
    /* performance.mark 在个别环境里可能不可用；计时不是功能，绝不因此报错 */
  }
}

export function traceFrame(t: PreviewTiming): void {
  t.preview_frame_count++
  if (t.preview_first_frame == null) {
    t.preview_first_frame = now() - t.preview_session_start
    mark('tavotto:preview_first_frame')
  }
}

export function traceCommit(t: PreviewTiming): void {
  t.commit_start = now() - t.preview_session_start
  mark('tavotto:preview_commit_start')
}

export function traceAuthority(t: PreviewTiming): void {
  const at = now() - t.preview_session_start
  t.authority_svg_replaced = at
  if (t.commit_start != null) t.commit_to_authority_ms = at - t.commit_start
  mark('tavotto:preview_authority_svg')
  report(t)
}

/** 最近若干次 session 的计时，调试时从控制台取（`__MM_PREVIEW_TIMINGS__`） */
const RING_MAX = 50
const ring: PreviewTiming[] = []

export function report(t: PreviewTiming): void {
  ring.push(t)
  if (ring.length > RING_MAX) ring.shift()
  if (typeof window !== 'undefined') {
    ;(window as unknown as Record<string, unknown>).__MM_PREVIEW_TIMINGS__ = ring
  }
  if (!traceEnabled()) return
  console.info(
    `[tavotto preview] ${t.panelId} 首帧 ${fmt(t.preview_first_frame)}ms · ` +
      `${t.preview_frame_count}/${t.preview_move_count} 帧 · ` +
      `commit→权威 ${fmt(t.commit_to_authority_ms)}ms`,
    t,
  )
}

export function previewTimings(): readonly PreviewTiming[] {
  return ring
}

export function clearPreviewTimings(): void {
  ring.length = 0
}

const fmt = (v: number | null) => (v == null ? '—' : v.toFixed(1))
