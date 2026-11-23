import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { PRODUCT_NAME } from '@/lib/brand'
import { BRAND_MARK_BOX, BRAND_MARK_PATHS } from '../ui/brandMark.geometry'
import './foundingPlate.css'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.license.plate.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 刻字的名字只存本机（不上传、不进令牌 / 项目 / 诊断包）；与 `tavotto.locale` 同一类偏好存储 */
export const PLATE_NAME_KEY = 'tavotto.founding.plateName'
export const PLATE_NAME_MAX = 24
/** 创始名额总数（官网同值；只用于「NO. 037 / 200」的版面） */
const SEATS = 200

export function loadPlateName(): string {
  try {
    return (window.localStorage.getItem(PLATE_NAME_KEY) ?? '').slice(0, PLATE_NAME_MAX)
  } catch {
    return ''
  }
}
function savePlateName(name: string): void {
  try {
    if (name) window.localStorage.setItem(PLATE_NAME_KEY, name)
    else window.localStorage.removeItem(PLATE_NAME_KEY)
  } catch {
    /* 存不下就只在这次打开里有效 */
  }
}

/** 「YYYY · MM · DD」，按本机时区 */
export function plateDate(sinceSeconds: number): string {
  const d = new Date(sinceSeconds * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()} · ${p(d.getMonth() + 1)} · ${p(d.getDate())}`
}
export const plateSeat = (no: number): string => `NO. ${String(no).padStart(3, '0')} / ${SEATS}`

/**
 * 创始会员铭牌（ADR 0193 / 维护者 2026-11-23）：深空钛、蚀刻字；名字可点一下改（只存本机），没填时刻「早期同行者」。
 * 只在授权令牌带创始标记时渲染（`LicenseRow` 判断）；创始身份是永久的，过期了铭牌也在。
 */
export function FoundingPlate({ no, since }: { no: number; since: number }) {
  useTranslation('dialogs')
  const [name, setName] = useState(loadPlateName)
  const [editing, setEditing] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (editing) input.current?.select()
  }, [editing])

  const commit = (raw: string) => {
    const next = raw.trim().slice(0, PLATE_NAME_MAX)
    setName(next)
    savePlateName(next)
    setEditing(false)
  }
  const shown = name || st('defaultName')
  const label = st('label')
  const seat = plateSeat(no)

  return (
    <div
      className="tvp"
      data-founding-plate
      role="group"
      aria-label={`${label}: ${shown}, ${seat}`}
    >
      <div className="tvp-card">
        <span className="tvp-mark">{PRODUCT_NAME}</span>
        <span className="tvp-label">{label}</span>
        {editing ? (
          <input
            ref={input}
            className="tvp-name"
            data-founding-name-input
            defaultValue={name}
            maxLength={PLATE_NAME_MAX}
            placeholder={st('defaultName')}
            aria-label={st('nameLabel')}
            spellCheck={false}
            onBlur={(e) => commit(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commit(e.currentTarget.value)
              if (e.key === 'Escape') {
                e.stopPropagation()
                setEditing(false)
              }
            }}
          />
        ) : (
          <button
            type="button"
            className="tvp-name"
            data-founding-name
            data-default={name ? undefined : ''}
            title={st('nameTitle')}
            onClick={() => setEditing(true)}
          >
            {shown}
          </button>
        )}
        <span className="tvp-no" data-founding-seat>
          {seat} · {plateDate(since)}
        </span>
        <svg
          className="tvp-emblem"
          viewBox={`0 0 ${BRAND_MARK_BOX} ${BRAND_MARK_BOX}`}
          aria-hidden="true"
          fillRule="evenodd"
        >
          <defs>
            <linearGradient id="tvp-fm" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stopColor="#f8fbff" />
              <stop offset="0.48" stopColor="#dde3ee" />
              <stop offset="0.74" stopColor="#eff4fb" />
              <stop offset="1" stopColor="#b8c2d2" />
            </linearGradient>
            <linearGradient id="tvp-fa" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stopColor="#cfd6e3" />
              <stop offset="1" stopColor="#96a0b2" />
            </linearGradient>
          </defs>
          {BRAND_MARK_PATHS.map((p) => (
            <path
              key={p.role}
              d={p.d}
              fill={p.role === 'ink' ? 'url(#tvp-fm)' : 'url(#tvp-fa)'}
              stroke="rgb(255 255 255 / 0.45)"
              strokeWidth="0.9"
            />
          ))}
        </svg>
      </div>
    </div>
  )
}
