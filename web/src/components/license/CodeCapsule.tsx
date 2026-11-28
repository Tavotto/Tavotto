import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { PRO_NAME } from '@/lib/brand'
import { ICON_SIZE } from '../ui/Icon'
import { LoaderCircle, Lock } from '../ui/icons'
import { TitaniumStar } from './ProRailMark'
import './codeCapsule.css'

/** 激活码的形状（ADR 0193）：`TVK-` + 12 位 Crockford base32，界面上分三组、每组 4 位 */
export const CODE_PREFIX = 'TVK'
export const CODE_LEN = 12
const GROUP = 4
/** 填满后停这么久再提交：让最后一个字落稳、看得见 */
const SETTLE_MS = 260
/** 整串粘贴时字依次落下的间隔 */
const STAGGER_MS = 28

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.license.${key}`, { ns: 'dialogs', pro: PRO_NAME, ...(values ?? {}) })

/**
 * 粘进来什么都认：去掉开头的 `TVK` 前缀（后面跟着分隔符，或整串超过 12 位时）、短横、空格，转大写，只留字母数字，最多 12 位。
 * 不校验字母表——认不认是服务端的事（前端不放第二份判据）。
 */
export function normalizeCode(raw: string): string {
  let s = raw.toUpperCase().replace(/[^0-9A-Z]/g, '')
  const head = raw.trimStart()
  if (s.startsWith(CODE_PREFIX) && (/^tvk[\s\-–—_:]/i.test(head) || s.length > CODE_LEN)) s = s.slice(CODE_PREFIX.length)
  return s.slice(0, CODE_LEN)
}

/** 交给 `activate()` 的标准写法：`TVK-XXXX-XXXX-XXXX` */
export const formatCode = (v: string) => `${CODE_PREFIX}-${v.slice(0, 4)}-${v.slice(4, 8)}-${v.slice(8, 12)}`

export type CapsulePhase = 'pill' | 'entry' | 'verifying' | 'done'

/**
 * 激活码胶囊（维护者 2026-11-28 定；学 sucodee「Code Input」的设计语言）：
 *
 *   pill（🔒 输入激活码）→ entry（TVK + 12 格，字从下面落进格子，光标框在格子间滑）→ 填满自动提交 → verifying（正在验证）
 *   → 失败回到 entry：「激活码不对」染危险色 + 抖一下（`bad`），别的失败不抖、码留着；
 *   → 成功 done（中性胶囊 + 钛星「已激活 Pro」；礼花由调用方 `burstFrom` 放）。
 *
 * 格子下面压着一个真 `<input data-license-input>`：粘贴、输入法、读屏、Enter / Esc 都靠它。
 * 形态由调用方给（它知道 busy / 结果），码由调用方存（失败后「再试一次」要用）。宽度跟着当前形态过渡。
 */
export function CodeCapsule({
  phase,
  value,
  onValueChange,
  onOpen,
  onSubmit,
  onCancel,
  bad = false,
  shakeKey = 0,
}: {
  phase: CapsulePhase
  value: string
  onValueChange: (v: string) => void
  onOpen: () => void
  onSubmit: () => void
  onCancel: () => void
  /** 「激活码不对」：格子染危险色，直到用户改了码 */
  bad?: boolean
  /** 每变一次抖一下（同一个错连按两次 Enter 也要再抖） */
  shakeKey?: number
}) {
  useTranslation('dialogs')
  const box = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const layers = useRef<Partial<Record<CapsulePhase, HTMLElement | null>>>({})
  const cells = useRef<(HTMLSpanElement | null)[]>([])
  const [caretX, setCaretX] = useState(0)
  const [ready, setReady] = useState(false)
  const [shaking, setShaking] = useState(false)
  /** 每个字落下的延迟（整串粘贴时依次落） */
  const delays = useRef<number[]>([])
  const prev = useRef(value)

  // 胶囊宽度 = 当前形态的自然宽度；第一次量完才开过渡（打开对话框时不从 0 长出来）
  useLayoutEffect(() => {
    const el = layers.current[phase]
    const cap = box.current
    if (!el || !cap) return
    cap.style.width = `${el.offsetWidth}px`
    if (!ready) requestAnimationFrame(() => setReady(true))
  }, [phase, ready])

  // 光标框跟着下一格走
  const at = Math.min(value.length, CODE_LEN - 1)
  useLayoutEffect(() => {
    setCaretX(cells.current[at]?.offsetLeft ?? 0)
  }, [at, phase])

  // 展开后焦点进输入框；失败回来也回到这里
  useEffect(() => {
    if (phase === 'entry') input.current?.focus({ preventScroll: true })
  }, [phase])

  // 填满自动提交一次（改了码才会再提交；失败后要重试用 Enter 或那颗按钮）
  const submitted = useRef<string | null>(null)
  useEffect(() => {
    if (phase !== 'entry' || value.length !== CODE_LEN || submitted.current === value) return
    const extra = Math.max(0, ...delays.current)
    const t = window.setTimeout(() => {
      submitted.current = value
      onSubmit()
    }, SETTLE_MS + extra)
    return () => window.clearTimeout(t)
  }, [phase, value, onSubmit])

  useEffect(() => {
    if (!shakeKey) return
    setShaking(true)
    const t = window.setTimeout(() => setShaking(false), 450)
    return () => window.clearTimeout(t)
  }, [shakeKey])

  const take = (next: string, stagger: boolean) => {
    const from = prev.current.length
    delays.current = Array.from({ length: CODE_LEN }, (_, i) => (stagger && i >= from ? (i - from) * STAGGER_MS : 0))
    prev.current = next
    if (next !== submitted.current) submitted.current = null
    onValueChange(next)
  }

  const off = (p: CapsulePhase) => (phase === p ? undefined : '')
  const full = value.length === CODE_LEN

  return (
    <div
      ref={box}
      className="code-capsule"
      data-license-capsule={phase}
      data-ready={ready || undefined}
      data-bad={(bad && phase === 'entry') || undefined}
      data-shake={shaking || undefined}
      data-full={full || undefined}
      data-done={phase === 'done' || undefined}
    >
      <button
        type="button"
        ref={(el) => {
          layers.current.pill = el
        }}
        data-capsule-layer
        data-capsule-pill
        data-off={off('pill')}
        aria-hidden={phase !== 'pill' || undefined}
        tabIndex={phase === 'pill' ? 0 : -1}
        data-license-action="enter"
        className="text-base font-medium text-ink"
        onClick={onOpen}
        onPaste={(e) => {
          e.preventDefault()
          onOpen()
          take(normalizeCode(e.clipboardData.getData('text')), true)
        }}
      >
        <Lock size={ICON_SIZE.sm} aria-hidden />
        {st('action.enter')}
      </button>

      <div
        ref={(el) => {
          layers.current.entry = el
        }}
        data-capsule-layer
        data-capsule-cells
        data-off={off('entry')}
        aria-hidden
      >
        <span className="capsule-pre text-sm">{CODE_PREFIX}</span>
        <span className="capsule-dash" />
        {Array.from({ length: CODE_LEN }, (_, i) => (
          <CellGroupPart key={i} i={i}>
            <span
              ref={(el) => {
                cells.current[i] = el
              }}
              className="capsule-cell text-lg font-medium"
              data-last={i === CODE_LEN - 1 || undefined}
              data-here={(i === at && !full) || undefined}
              data-capsule-cell={i}
            >
              {value[i] && (
                <span key={`${i}:${value[i]}`} className="capsule-ch" style={{ animationDelay: `${delays.current[i] ?? 0}ms` }}>
                  {value[i]}
                </span>
              )}
            </span>
          </CellGroupPart>
        ))}
        <span className="capsule-caret" data-at-end={at === CODE_LEN - 1 || undefined} style={{ transform: `translateX(${caretX}px)` }} />
      </div>

      <div
        ref={(el) => {
          layers.current.verifying = el
        }}
        data-capsule-layer
        data-capsule-verifying
        data-off={off('verifying')}
        aria-hidden={phase !== 'verifying' || undefined}
        className="text-base font-medium"
      >
        <LoaderCircle size={ICON_SIZE.sm} className="animate-spin text-ink-3" aria-hidden />
        <span className="text-shimmer">{st('verifying')}</span>
      </div>

      <div
        ref={(el) => {
          layers.current.done = el
        }}
        data-capsule-layer
        data-capsule-done
        data-off={off('done')}
        aria-hidden={phase !== 'done' || undefined}
        className="text-base font-medium text-ink"
      >
        {phase === 'done' && <TitaniumStar size={ICON_SIZE.md} />}
        {st('activated', { pro: PRO_NAME })}
      </div>

      {phase === 'entry' && (
        <input
          ref={input}
          data-license-input
          value={value}
          aria-label={st('codeLabel')}
          aria-invalid={bad || undefined}
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="characters"
          onChange={(e) => {
            const raw = e.target.value
            // 报错后再敲一个字：整串换成新打的（粘贴来的码，改一位用退格）
            const base = bad && raw.length === value.length + 1 ? raw.slice(-1) : raw
            const next = normalizeCode(base)
            take(next, next.length - value.length > 1)
          }}
          onSelect={(e) => {
            // 光标永远在末尾：格子只表达「下一位」
            const el = e.currentTarget
            const end = el.value.length
            if (el.selectionStart !== end || el.selectionEnd !== end) el.setSelectionRange(end, end)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              if (full) {
                submitted.current = value
                onSubmit()
              }
            } else if (e.key === 'Escape') {
              e.preventDefault()
              e.stopPropagation()
              onCancel()
            }
          }}
        />
      )}
    </div>
  )
}

/** 每组 4 格之间插一道短横 */
function CellGroupPart({ i, children }: { i: number; children: React.ReactNode }) {
  return (
    <>
      {i > 0 && i % GROUP === 0 && <span className="capsule-dash" />}
      {children}
    </>
  )
}
