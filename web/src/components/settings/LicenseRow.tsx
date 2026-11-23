import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { formatDate } from '@/i18n/format'
import { PRODUCT_NAME } from '@/lib/brand'
import type { LicenseFailureCode } from '@/lib/api'
import { licenseVisible, useLicenseStore } from '@/store/licenseStore'
import { FoundingPlate } from '../license/FoundingPlate'
import { Button } from '../ui/Button'
import { ICON_SIZE } from '../ui/Icon'
import { ExternalLink } from '../ui/icons'
import { FieldGroup, FormSection } from '../ui/FormSection'
import { TextInput } from '../ui/Input'
import { SettingRow } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.license.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 失败码 → 那一句话下面的唯一一颗按钮做什么：再提交一次 / 去付款页 / 去续费 */
const FAILURE_ACTION: Record<LicenseFailureCode, 'retry' | 'buy' | 'renew'> = {
  invalid_code: 'retry',
  device_limit: 'retry',
  revoked: 'buy',
  expired: 'renew',
  price_unavailable: 'retry',
  rate_limited: 'retry',
  network: 'retry',
  server: 'retry',
  client_error: 'retry',
}

/** 码 → 一句话；不认识的码按「授权服务不可用」说 */
const failureText = (code: LicenseFailureCode): string =>
  st(`failure.${code}`, { defaultValue: st('failure.client_error') })

const dateOf = (expSeconds: number | null): string => (expSeconds ? formatDate(expSeconds * 1000) : '')

/**
 * 设置「关于与更新」里的**一行**授权入口（ADR 0193 §八）。
 *
 *   * 能力没开（正式安装、`PRODUCTION_LIVE = False`）：整块不渲染；
 *   * 未激活：一句灰字 + 「输入激活码」（点后行内出输入框，输入框在这一行自己的下方，不弹窗）；
 *   * 已激活：「到期 · 邮箱提示」+ 「在这台电脑上解绑」；
 *   * 失败：一句人话换掉那句灰字（不用红、不上色块），按钮只有一颗。
 *
 * 没有令牌时 `status` 是纯本机读取，不联网；联网只在用户点按钮时。
 */
export function LicenseRow() {
  useTranslation('dialogs')
  const status = useLicenseStore((s) => s.status)
  const failure = useLicenseStore((s) => s.failure)
  const busy = useLicenseStore((s) => s.busy)
  const { load, activate, refresh, release, buy, clearFailure } = useLicenseStore.getState()
  const [entering, setEntering] = useState(false)
  const [code, setCode] = useState('')
  /** 上一次联网做的是哪一件：失败后的「重试」重做那一件，不拿刷新顶替解绑（Codex #2071 P2） */
  const [lastOp, setLastOp] = useState<'activate' | 'refresh' | 'release'>('refresh')
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (status === null) void load()
  }, [status, load])
  useEffect(() => {
    if (entering) input.current?.focus()
  }, [entering])

  if (!licenseVisible(status)) return null

  const submit = async () => {
    if (!code.trim()) return
    setLastOp('activate')
    if (await activate(code)) {
      setEntering(false)
      setCode('')
    }
  }
  const doRefresh = () => {
    setLastOp('refresh')
    return refresh()
  }
  const doRelease = () => {
    setLastOp('release')
    return release()
  }
  const act = (kind: 'retry' | 'buy' | 'renew') => {
    if (kind !== 'retry') return buy()
    if (entering) return submit()
    return lastOp === 'release' ? doRelease() : doRefresh()
  }
  const enterCode = () => {
    clearFailure()
    setEntering(true)
  }

  const state = status.state
  const active = state === 'active'
  const date = dateOf(status.exp)
  const hint = status.email_hint

  // 那一句灰字：失败优先，其次按状态
  let line: React.ReactNode
  if (failure) line = failureText(failure)
  else if (active)
    line = (
      <>
        {st('until', { date })}
        {hint && <span className="font-mono text-ink-3"> · {hint}</span>}
      </>
    )
  else if (state === 'expired') line = st('state.expired', { date })
  else if (state === 'revoked') line = st('state.revoked')
  else if (state === 'lapsed') line = st('state.lapsed')
  else line = st('none')

  // 右边的那一颗按钮
  const cancel = () => {
    setEntering(false)
    setCode('')
    clearFailure()
  }
  let control: React.ReactNode
  if (entering && !active) {
    control = (
      <div className="flex items-center gap-2">
        <Button variant="ghost" onClick={cancel} data-license-action="cancel">
          {st('action.cancel')}
        </Button>
        <Button variant="primary" loading={busy} disabled={!code.trim()} onClick={() => void submit()} data-license-action="activate">
          {st('action.activate')}
        </Button>
      </div>
    )
  } else if (failure) {
    const kind = FAILURE_ACTION[failure]
    control = (
      <Button variant="secondary" loading={busy} onClick={() => act(kind)} data-license-action={kind}>
        {st(`action.${kind}`)}
      </Button>
    )
  } else if (active) {
    control = (
      <Button
        variant="ghost"
        loading={busy}
        title={st('releaseTitle')}
        onClick={() => void doRelease()}
        data-license-action="release"
      >
        {st('action.release')}
      </Button>
    )
  } else if (state === 'lapsed') {
    control = (
      <Button variant="secondary" loading={busy} onClick={() => void doRefresh()} data-license-action="refresh">
        {st('action.refresh')}
      </Button>
    )
  } else if (state === 'expired') {
    // 续费之后点「刷新」取回新的到期日；后端对过期的令牌照样能刷新（Codex #2071 P2）
    control = (
      <div className="flex items-center gap-2">
        <Button variant="ghost" loading={busy} onClick={() => void doRefresh()} data-license-action="refresh">
          {st('action.refresh')}
        </Button>
        <Button variant="secondary" loading={busy} onClick={() => void buy()} data-license-action="renew">
          {st('action.renew')}
        </Button>
      </div>
    )
  } else if (state === 'revoked') {
    // 吊销之后要重新激活：买了新的码能在这里输入（Codex #2071 P2）
    control = (
      <div className="flex items-center gap-2">
        <Button variant="ghost" loading={busy} onClick={() => void buy()} data-license-action="buy">
          {st('action.buy')}
          <ExternalLink size={ICON_SIZE.xs} className="text-ink-2" aria-hidden />
        </Button>
        <Button variant="secondary" onClick={enterCode} data-license-action="enter">
          {st('action.enter')}
        </Button>
      </div>
    )
  } else {
    control = (
      <div className="flex items-center gap-2">
        <Button variant="ghost" loading={busy} onClick={() => void buy()} data-license-action="buy">
          {st('action.buy')}
          <ExternalLink size={ICON_SIZE.xs} className="text-ink-2" aria-hidden />
        </Button>
        <Button
          variant="secondary"
          onClick={enterCode}
          data-license-action="enter"
        >
          {st('action.enter')}
        </Button>
      </div>
    )
  }

  return (
    <FormSection data-settings-anchor="about.license" data-license-state={failure ? `failed:${failure}` : state}>
      <FieldGroup>
        <SettingRow
          label={st('label')}
          description={<span data-license-line>{line}</span>}
          below={
            entering && !active ? (
              <TextInput
                ref={input}
                value={code}
                onChange={(e) => {
                  setCode(e.target.value)
                  if (failure) clearFailure()
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void submit()
                  if (e.key === 'Escape') {
                    e.stopPropagation()
                    cancel()
                  }
                }}
                placeholder="TVK-XXXX-XXXX-XXXX"
                aria-label={st('codeLabel')}
                data-license-input
                spellCheck={false}
                autoComplete="off"
                className="max-w-xs font-mono text-xs tracking-wider"
              />
            ) : status.founding ? (
              <div className="mt-1 flex flex-wrap items-end gap-x-5 gap-y-2">
                <FoundingPlate no={status.founding.no} since={status.founding.since} />
                <span className="type-caption text-ink-3">{st('plate.thanks', { product: PRODUCT_NAME })}</span>
              </div>
            ) : undefined
          }
          control="auto"
        >
          {control}
        </SettingRow>
      </FieldGroup>
    </FormSection>
  )
}
