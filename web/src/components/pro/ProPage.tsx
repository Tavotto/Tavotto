import { useEffect, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { formatDate } from '@/i18n/format'
import { PRODUCT_NAME } from '@/lib/brand'
import { licenseVisible, useLicenseStore } from '@/store/licenseStore'
import { FoundingPlate } from '../license/FoundingPlate'
import { LicenseRow } from '../settings/LicenseRow'
import { SettingRow, SettingValueRow } from '../settings/SettingRow'
import { FieldGroup, FormSection } from '../ui/FormSection'
import { ProgressBar } from '../ui/ProgressBar'

const ps = (key: string, values?: Record<string, unknown>) =>
  translate(`pro.${key}`, { ns: 'dialogs', ...(values ?? {}) })

const DAY_MS = 86_400_000
/** 档名 → 文案 key；不认识的档不显示这一行（不臆造名字） */
const TIER_KEYS = new Set(['year', 'season', 'sprint', 'team'])

/** 80% 起给一行灰字（ADR 0200 §二）；比例是 0–1 的已用量 */
const NEAR_END = 0.8

/**
 * 高级版页（维护者 2026-11-24）：一页三块。
 *   1. 创始会员的铭牌（只有创始会员有）；
 *   2. 通行证：第一行是原来设置里那一行（输入激活码 / 购买 / 续费 / 解绑 / 找回），下面补类型、剩余天数、设备名额
 *      （服务端没给就不显示这一行，不臆造）；
 *   3. 用量：内置 AI 一根细进度条（只显示比例，不显示 token 与钱），外部审阅一行字。
 * 未激活时用量两行都写「激活后可用」、不画空进度条；过期写「已到期」。入口没开（`PRODUCTION_LIVE = False`）时整页不存在。
 */
export function ProPage() {
  useTranslation('dialogs')
  const status = useLicenseStore((s) => s.status)
  const usage = useLicenseStore((s) => s.usage)
  const load = useLicenseStore((s) => s.load)
  const loadUsage = useLicenseStore((s) => s.loadUsage)
  const state = licenseVisible(status) ? status.state : 'none'

  useEffect(() => {
    if (status === null) void load()
  }, [status, load])
  useEffect(() => {
    if (state === 'active') void loadUsage()
  }, [state, loadUsage])

  if (!licenseVisible(status)) return null
  const active = state === 'active'
  const expired = state === 'expired'
  const daysLeft = status.exp ? Math.max(0, Math.ceil((status.exp * 1000 - Date.now()) / DAY_MS)) : null

  const rows: ReactNode[] = []
  if ((active || expired) && status.tier && TIER_KEYS.has(status.tier.key)) {
    rows.push(<SettingValueRow key="tier" data-pro-row="tier" label={ps('tier.label')} value={ps(`tier.${status.tier.key}`)} />)
  }
  if (active && daysLeft !== null) {
    rows.push(<SettingValueRow key="days" data-pro-row="days" label={ps('days.label')} value={ps('days.value', { count: daysLeft })} />)
  }
  if ((active || expired) && status.devices) {
    rows.push(
      <SettingValueRow
        key="devices"
        data-pro-row="devices"
        label={ps('devices.label')}
        value={ps('devices.value', { used: status.devices.used, limit: status.devices.limit })}
      />,
    )
  }

  const agentName = `${PRODUCT_NAME} Agent`
  const used = usage?.agent_used ?? null
  const reviewLeft = usage?.review_new_left ?? null
  const idle = ps(expired ? 'usage.expired' : 'usage.locked')

  return (
    <div className="flex flex-col gap-6" data-pro-page data-pro-state={state}>
      {status.founding && (
        <FoundingPlate
          no={status.founding.no}
          since={status.founding.since}
          name={status.founding.name}
          onName={(n) => void useLicenseStore.getState().setPlateName(n)}
        />
      )}
      <LicenseRow plate={false} label={ps('pass')} extra={rows.length ? rows : undefined} />
      <FormSection title={ps('usage.title')}>
        <FieldGroup>
          <SettingRow
            label={agentName}
            description={
              !active ? (
                <span data-pro-usage-note>{idle}</span>
              ) : used === null ? (
                <span data-pro-usage-note>{ps('usage.unknown')}</span>
              ) : used >= NEAR_END ? (
                <span data-pro-usage-note>{ps('usage.nearEnd')}</span>
              ) : undefined
            }
            data-pro-usage="agent"
          >
            {active && used !== null && (
              <div className="flex w-full items-center justify-end gap-3">
                <ProgressBar className="w-36 shrink-0" pct={Math.round(used * 100)} label={ps('usage.agentBar', { name: agentName })} />
                <span className="shrink-0 text-sm tabular-nums text-ink-2" data-pro-usage-pct>
                  {ps('usage.used', { pct: Math.round(used * 100) })}
                </span>
              </div>
            )}
          </SettingRow>
          <SettingRow
            label={ps('review.label')}
            description={
              active && reviewLeft !== null ? (
                <span data-pro-usage-note>{ps('review.left', { count: reviewLeft })}</span>
              ) : (
                <span data-pro-usage-note>{active ? ps('usage.unknown') : idle}</span>
              )
            }
            data-pro-usage="review"
          />
        </FieldGroup>
      </FormSection>
      {/* 到期日只在通行证那一块里说；这里不重复 */}
      {active && status.exp ? <span className="sr-only">{formatDate(status.exp * 1000)}</span> : null}
    </div>
  )
}
