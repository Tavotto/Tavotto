import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { PRODUCT_NAME } from '@/lib/brand'
import { TextInput } from '../ui/Input'
import { LOGO_ACCENT, LOGO_MAIN } from './plate/plateLogo'
import { attachHand, reducedMotion, type Apply } from './plate/plateHand'
import './plate/foundingPlate.css'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.license.plate.${key}`, { ns: 'dialogs', ...(values ?? {}) })

export const PLATE_NAME_MAX = 24
/** 创始名额总数（官网同值；只用于「NO. 037 / 200」的版面） */
const SEATS = 200

/** 「YYYY · MM · DD」，按本机时区 */
export function plateDate(sinceSeconds: number): string {
  const d = new Date(sinceSeconds * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()} · ${p(d.getMonth() + 1)} · ${p(d.getDate())}`
}
export const plateSeat = (no: number): string => `NO. ${String(no).padStart(3, '0')} / ${SEATS}`

/** 一枚徽记：镜面银渐变 + 蚀刻的上缘暗切（与官网同一份 SVG，id 用 useId 区分多实例） */
function Emblem({ cls, tag }: { cls: string; tag: string }) {
  return (
    <svg className={cls} viewBox="0 0 128 128" aria-hidden="true">
      <defs>
        <linearGradient id={`${tag}-m`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#f8fbff" />
          <stop offset="0.48" stopColor="#dde3ee" />
          <stop offset="0.74" stopColor="#eff4fb" />
          <stop offset="1" stopColor="#b8c2d2" />
        </linearGradient>
        <linearGradient id={`${tag}-a`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#cfd6e3" />
          <stop offset="1" stopColor="#96a0b2" />
        </linearGradient>
        <filter id={`${tag}-i`}>
          <feOffset dy="2" />
          <feGaussianBlur stdDeviation="1.4" result="b" />
          <feComposite in="SourceGraphic" in2="b" operator="out" result="i" />
          <feColorMatrix in="i" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0.45 0" result="s" />
          <feMerge>
            <feMergeNode in="SourceGraphic" />
            <feMergeNode in="s" />
          </feMerge>
        </filter>
      </defs>
      <g filter={`url(#${tag}-i)`} fillRule="evenodd" stroke="rgb(255 255 255 / 0.45)" strokeWidth="0.9">
        <path d={LOGO_MAIN} fill={`url(#${tag}-m)`} />
        <path d={LOGO_ACCENT} fill={`url(#${tag}-a)`} />
      </g>
    </svg>
  )
}

/**
 * 创始会员铭牌（ADR 0193 / 维护者 2026-11-23）：与官网 `FoundingPlate.astro` 同一块牌——深空钛、微倒角、蚀刻字，
 * 正面（徽、创始会员、名字、席位号、日期、镜面银标志）与背面（居中标志、发行日期、席位号）；拖一下带惯性翻面，方向键也能转。
 *
 * 渲染：先是 CSS 版（同版式、同样能手动翻），铭牌进了视野、没开「减少动态」、真能建出 WebGL 时才动态 import three，
 * 换成 WebGL 版（金属环境反光）；加载或建上下文失败就一直是 CSS 版。三个文件（Hand / Faces / Gl）逐字移植自官网。
 *
 * 名字：没刻时刻「早期同行者」；铭牌下面一个小字按钮刻 / 改（名字由 `onName` 写本机，铭牌这边不碰存储）。
 */
export function FoundingPlate({
  no,
  since,
  name,
  onName,
}: {
  no: number
  since: number
  /** 已刻的名字（引擎里的本机文件）；没刻 = 空串 */
  name: string
  onName: (name: string) => void
}) {
  useTranslation('dialogs')
  const tag = useId().replace(/:/g, '')
  const root = useRef<HTMLDivElement>(null)
  const card = useRef<HTMLDivElement>(null)
  const apply = useRef<Apply>(() => {})
  const hand = useRef<ReturnType<typeof attachHand> | null>(null)
  const [editing, setEditing] = useState(false)

  const shown = name || st('defaultName')
  const label = st('label')
  const seat = plateSeat(no)
  const date = plateDate(since)

  // 手：只装一次。没有 WebGL 时它直接转 CSS 卡
  useEffect(() => {
    const el = root.current
    if (!el) return
    apply.current = (spin, tx, ty) => {
      if (card.current) card.current.style.transform = `rotateX(${-tx}rad) rotateY(${spin + ty}rad)`
    }
    const h = attachHand(el, apply)
    hand.current = h
    return () => {
      h.dispose()
      hand.current = null
    }
  }, [])

  // WebGL：进视野才加载 three；刻字 / 席位 / 日期变了就整块重画
  useEffect(() => {
    const el = root.current
    if (!el || reducedMotion() || !('IntersectionObserver' in window)) return
    const ac = new AbortController()
    let off: (() => void) | null = null
    const data = { tone: 'titanium', name: shown, label, serial: seat, date }
    const io = new IntersectionObserver(
      (entries) => {
        if (!entries.some((e) => e.isIntersecting)) return
        io.disconnect()
        void import('./plate/plateGl')
          .then((m) => m.bootGl(el, data, apply, () => hand.current?.wake(), ac.signal))
          .then((dispose) => {
            if (ac.signal.aborted) dispose?.()
            else off = dispose
          })
          .catch(() => {
            /* 加载失败：一直是 CSS 版 */
          })
      },
      { rootMargin: '150px' },
    )
    io.observe(el)
    return () => {
      ac.abort()
      io.disconnect()
      off?.()
      // 回到 CSS 卡的转法；下一次 boot 成功会再换回去
      apply.current = (spin, tx, ty) => {
        if (card.current) card.current.style.transform = `rotateX(${-tx}rad) rotateY(${spin + ty}rad)`
      }
      if (card.current) card.current.style.transform = ''
    }
  }, [shown, label, seat, date])

  const commit = (raw: string) => {
    const next = raw.trim().slice(0, PLATE_NAME_MAX)
    setEditing(false)
    if (next !== name) onName(next)
  }

  let action: ReactNode
  if (editing) {
    action = (
      <TextInput
        autoFocus
        defaultValue={name}
        maxLength={PLATE_NAME_MAX}
        placeholder={st('defaultName')}
        aria-label={st('nameLabel')}
        data-founding-name-input
        spellCheck={false}
        className="w-48 text-center"
        onFocus={(e) => e.currentTarget.select()}
        onBlur={(e) => commit(e.currentTarget.value)}
        onKeyDown={(e) => {
          // 输入法选字时的回车不是提交
          if (e.nativeEvent.isComposing || e.keyCode === 229) return
          if (e.key === 'Enter') commit(e.currentTarget.value)
          if (e.key === 'Escape') {
            e.stopPropagation()
            setEditing(false)
          }
        }}
      />
    )
  } else {
    action = (
      <button
        type="button"
        data-founding-name
        data-default={name ? undefined : ''}
        title={st('nameTitle')}
        onClick={() => setEditing(true)}
        className="type-caption rounded-sm px-1 text-ink-3 underline decoration-current/35 underline-offset-[3px] hover:text-ink hover:decoration-current focus-visible:focus-ring outline-none"
      >
        {name ? st('rename') : st('engrave')}
      </button>
    )
  }

  return (
    <div className="flex flex-col items-center gap-4 pb-4 pt-2" data-founding-plate>
      <div
        ref={root}
        className="fp"
        data-fp
        data-tone="titanium"
        tabIndex={0}
        role="img"
        aria-label={`${label}: ${shown}, ${seat}`}
        title={st('turnTitle')}
      >
        <div className="fp-stage">
          <div className="fp-gl" aria-hidden="true" />
          <div ref={card} className="fp-card">
            <div className="fp-face fp-front">
              <span className="fp-mark">{PRODUCT_NAME.toUpperCase()}</span>
              <span className="fp-label">{label}</span>
              <b className="fp-name" data-founding-shown>
                {shown}
              </b>
              <span className="fp-no" data-founding-seat>
                {seat}
              </span>
              <Emblem cls="fp-anchor" tag={`${tag}-f`} />
            </div>
            <div className="fp-face fp-back">
              <Emblem cls="fp-emblem" tag={`${tag}-b`} />
              <span className="fp-issued">{st('issued')}</span>
              <b className="fp-date" data-founding-date>
                {date}
              </b>
              <span className="fp-no">{seat}</span>
            </div>
          </div>
        </div>
      </div>
      {action}
    </div>
  )
}
