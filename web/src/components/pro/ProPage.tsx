import { useEffect, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { formatDate } from '@/i18n/format'
import { currentLocale } from '@/i18n'
import { cn } from '@/lib/utils'
import { PRODUCT_NAME, PRO_NAME, premiumPageUrl } from '@/lib/brand'
import { licenseVisible, useLicenseStore } from '@/store/licenseStore'
import { FoundingPlate } from '../license/FoundingPlate'
import { LicenseRow } from '../settings/LicenseRow'
import { SettingRow, SettingValueRow } from '../settings/SettingRow'
import { FieldGroup, FormSection } from '../ui/FormSection'
import { ICON_SIZE } from '../ui/Icon'
import { ExternalLink } from '../ui/icons'
import { ProgressBar } from '../ui/ProgressBar'

const ps = (key: string, values?: Record<string, unknown>) =>
  translate(`pro.${key}`, { ns: 'dialogs', pro: PRO_NAME, ...(values ?? {}) })

const DAY_MS = 86_400_000
/** 档名 → 文案 key；不认识的档不显示这一行（不臆造名字） */
const TIER_KEYS = new Set(['year', 'season', 'sprint', 'team'])

/** 80% 起给一行灰字（ADR 0200 §二）；比例是 0–1 的已用量 */
const NEAR_END = 0.8

/**
 * Pro 页（维护者 2026-11-24）：一页三块。
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
  const account = useLicenseStore((s) => s.account)
  const loadAccount = useLicenseStore((s) => s.loadAccount)
  const load = useLicenseStore((s) => s.load)
  const loadUsage = useLicenseStore((s) => s.loadUsage)
  const state = licenseVisible(status) ? status.state : 'none'

  useEffect(() => {
    if (status === null) void load()
  }, [status, load])
  useEffect(() => {
    if (state === 'active' || state === 'expired') void loadAccount()
    if (state === 'active') void loadUsage()
  }, [state, loadAccount, loadUsage])

  if (!licenseVisible(status)) return null
  const active = state === 'active'
  const expired = state === 'expired'
  const daysLeft = status.exp ? Math.max(0, Math.ceil((status.exp * 1000 - Date.now()) / DAY_MS)) : null

  const tierName = (active || expired) && account && TIER_KEYS.has(account.tier.key) ? ps(`tier.${account.tier.key}`) : undefined
  const rows: ReactNode[] = []
  if (active && daysLeft !== null) {
    rows.push(<SettingValueRow key="days" data-pro-row="days" label={ps('days.label')} value={ps('days.value', { count: daysLeft })} />)
  }
  if ((active || expired) && account) {
    rows.push(
      <SettingValueRow
        key="devices"
        data-pro-row="devices"
        label={ps('devices.label')}
        value={ps('devices.value', { used: account.devices.used, limit: account.devices.limit })}
      />,
    )
  }

  const agentName = `${PRODUCT_NAME} Agent`
  const used = usage?.agent_used ?? null
  const reviewLeft = usage?.review_new_left ?? null
  const lockedNote = () => ps('usage.expired')

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
      <LicenseRow
        plate={false}
        title={state === 'none' ? undefined : ps('pass')}
        label={tierName ?? (state === 'none' ? ps('pass') : undefined)}
        renewable={active}
        extra={rows.length ? rows : undefined}
      />
      {state === 'none' || state === 'revoked' || state === 'lapsed' ? (
        <p className="type-caption" data-pro-learn>
          {ps('learn.line', { pro: PRO_NAME })}{' '}
          <button
            type="button"
            onClick={() => window.open(premiumPageUrl(currentLocale()), '_blank', 'noopener,noreferrer')}
            className="rounded-sm text-ink-2 underline decoration-current/35 underline-offset-[3px] hover:text-ink hover:decoration-current focus-visible:focus-ring outline-none"
          >
            {ps('learn.link', { pro: PRO_NAME })}
            <ExternalLink size={ICON_SIZE.xs} className="ml-1 inline" aria-hidden />
          </button>
        </p>
      ) : (
      <FormSection title={ps('usage.title')}>
        <FieldGroup>
          <SettingRow
            label={agentName}
            description={
              !active ? (
                <span data-pro-usage-note>{lockedNote()}</span>
              ) : used === null ? (
                <span data-pro-usage-note>{ps('usage.unknown')}</span>
              ) : used >= NEAR_END ? (
                <span data-pro-usage-note>{ps('usage.nearEnd')}</span>
              ) : undefined
            }
            data-pro-usage="agent"
            control="auto"
          >
            {active && used !== null && (
              <div className="flex w-full items-center justify-end gap-3">
                <ProgressBar className="w-36 shrink-0" pct={Math.round(used * 100)} label={ps('usage.agentBar', { name: agentName })} />
                <span className={cn('shrink-0 text-sm tabular-nums', used >= NEAR_END ? 'font-medium text-ink' : 'text-ink-2')} data-pro-usage-pct>
                  {ps('usage.used', { pct: Math.round(used * 100) })}
                </span>
              </div>
            )}
          </SettingRow>
          <SettingRow
            label={ps('review.label')}
            description={!active ? <span data-pro-usage-note>{lockedNote()}</span> : undefined}
            data-pro-usage="review"
          >
            {active && (
              <span className="block w-full text-right text-sm tabular-nums text-ink-2" data-pro-usage-note>
                {reviewLeft !== null ? ps('review.left', { count: reviewLeft }) : ps('usage.unknown')}
              </span>
            )}
          </SettingRow>
        </FieldGroup>
      </FormSection>
      )}
      {/* 到期日只在通行证那一块里说；这里不重复 */}
      {active && status.exp ? <span className="sr-only">{formatDate(status.exp * 1000)}</span> : null}
    </div>
  )
}
