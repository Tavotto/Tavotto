import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { formatDate } from '@/i18n/format'
import { PRO_NAME } from '@/lib/brand'
import type { LicenseFailureCode } from '@/lib/api'
import { DURATION } from '@/lib/motion'
import { licenseVisible, useLicenseStore } from '@/store/licenseStore'
import { burstFrom } from '../license/burst'
import { CODE_LEN, CodeCapsule, formatCode, type CapsulePhase } from '../license/CodeCapsule'
import { FoundingPlate } from '../license/FoundingPlate'
import { Button } from '../ui/Button'
import { Reveal } from '../ui/Field'
import { ICON_SIZE } from '../ui/Icon'
import { ExternalLink } from '../ui/icons'
import { FieldGroup, FormSection } from '../ui/FormSection'
import { SettingRow } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.license.${key}`, { ns: 'dialogs', pro: PRO_NAME, ...(values ?? {}) })

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

/** 「已激活 Pro」那颗胶囊停多久再收起，换成已激活的行（礼花约 1 秒落完） */
const CELEBRATE_MS = 1700
/** 从 Pro 入口进来、还没激活：对话框落稳后再展开格子 */
const AUTO_ENTER_MS = 260

const dateOf = (expSeconds: number | null): string => (expSeconds ? formatDate(expSeconds * 1000) : '')

/**
 * 设置「关于与更新」里的**一行**授权入口（ADR 0193 §八）。
 *
 *   * 能力没开（正式安装、`PRODUCTION_LIVE = False`）：整块不渲染；
 *   * 未激活：一句灰字 + 下方一颗「🔒 输入激活码」胶囊（`license/CodeCapsule`，维护者 2026-11-28 定）：点它展开成
 *     TVK + 12 格，填满自动提交，收成「正在验证」；成功变「已激活 Pro」+ 一小捧礼花，停一会儿收起成已激活的行；
 *     `autoEnter`（Pro 页从入口打开时）= 不用再点，直接展开、光标就在格子里；
 *   * 已激活：「到期 · 邮箱提示」+ 「在这台电脑上解绑」；
 *   * 失败：一句人话换掉那句灰字（不用红、不上色块），按钮只有一颗。
 *
 * 没有令牌时 `status` 是纯本机读取，不联网；联网只在用户点按钮时。
 */
export function LicenseRow({
  plate = true,
  extra,
  label,
  title,
  renewable = false,
  autoEnter = false,
}: { plate?: boolean; extra?: ReactNode; label?: string; title?: string; renewable?: boolean; autoEnter?: boolean } = {}) {
  useTranslation('dialogs')
  const status = useLicenseStore((s) => s.status)
  const failure = useLicenseStore((s) => s.failure)
  const busy = useLicenseStore((s) => s.busy)
  const { load, activate, refresh, release, buy, setPlateName, clearFailure } = useLicenseStore.getState()
  const [entering, setEntering] = useState(false)
  /** 12 位码本体（不带 TVK 与短横）；提交时 `formatCode` 成 `TVK-XXXX-XXXX-XXXX` */
  const [code, setCode] = useState('')
  /** 上一次联网做的是哪一件：失败后的「重试」重做那一件，不拿刷新顶替解绑（Codex #2071 P2） */
  const [lastOp, setLastOp] = useState<'activate' | 'refresh' | 'release'>('refresh')
  /** 这一次激活刚成功：胶囊停在「已激活 Pro」上，礼花在放 */
  const [celebrating, setCelebrating] = useState(false)
  /** 庆祝结束、胶囊正在收起（Reveal 的退场那一下） */
  const [folding, setFolding] = useState(false)
  const [shakeKey, setShakeKey] = useState(0)
  const capsuleBox = useRef<HTMLDivElement>(null)
  const timers = useRef<number[]>([])
  const burst = useRef<(() => void) | null>(null)

  useEffect(() => {
    if (status === null) void load()
  }, [status, load])
  useEffect(
    () => () => {
      timers.current.forEach((t) => window.clearTimeout(t))
      burst.current?.()
    },
    [],
  )
  const canEnter = status?.enabled === true && (status.state === 'none' || status.state === 'revoked')
  // 从 Pro 入口进来还没激活：直接展开（只在挂载后这一次；失败态不自动展开）
  const autoEntered = useRef(false)
  useEffect(() => {
    if (!autoEnter || autoEntered.current || !canEnter || failure) return
    autoEntered.current = true
    const t = window.setTimeout(() => setEntering(true), AUTO_ENTER_MS)
    timers.current.push(t)
  }, [autoEnter, canEnter, failure])

  // 输入着的时候别处把授权弄成了 active（不是这一次提交）：收起输入，不放庆祝
  const activeNow = status?.enabled === true && status.state === 'active'
  useEffect(() => {
    if (activeNow && entering && !celebrating && !(busy || lastOp === 'activate')) setEntering(false)
  }, [activeNow, entering, celebrating, busy, lastOp])

  if (!licenseVisible(status)) return null

  const submit = async () => {
    if (code.length !== CODE_LEN || busy) return
    setLastOp('activate')
    if (await activate(formatCode(code))) {
      setCelebrating(true)
      timers.current.push(
        window.setTimeout(() => {
          const box = capsuleBox.current?.querySelector('[data-license-capsule]')
          if (box) burst.current = burstFrom(box)
        }, 140),
        window.setTimeout(() => setFolding(true), CELEBRATE_MS),
        window.setTimeout(() => {
          setFolding(false)
          setCelebrating(false)
          setEntering(false)
          setCode('')
        }, CELEBRATE_MS + DURATION.exit + 30),
      )
    } else if (useLicenseStore.getState().failure === 'invalid_code') {
      setShakeKey((k) => k + 1)
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
  // 激活一返回成功，状态就已经是 active：输入态里看到 active = 这一次激活成功了，胶囊留着、直接换成「已激活」
  // （不能等 setCelebrating——store 先更新的那一帧里胶囊会被卸掉、礼花找不到起点）
  const showCapsule = celebrating || entering || (!active && (state === 'none' || state === 'revoked'))
  const phase: CapsulePhase =
    celebrating || (entering && active) ? 'done' : !entering ? 'pill' : busy && lastOp === 'activate' ? 'verifying' : 'entry'
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
  else if (state === 'expired')
    line = (
      <>
        {st('state.expired', { date })}
        {hint && <span className="font-mono text-ink-3"> · {hint}</span>}
      </>
    )
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
  const buyButton = (
    <Button variant="ghost" loading={busy && lastOp !== 'activate'} onClick={() => void buy()} data-license-action="buy">
      {st('action.buy')}
      <ExternalLink size={ICON_SIZE.xs} className="text-ink-2" aria-hidden />
    </Button>
  )
  if (entering && !active) {
    // 输入中：格子就是输入框，填满自动提交；码不对时那句灰字就够了（改码即重提）。
    // 其余失败（网络 / 名额 / 限流……）给失败码那一颗按钮，「重试」= 用格子里的码再提交一次
    const kind = failure && failure !== 'invalid_code' ? FAILURE_ACTION[failure] : null
    control = kind ? (
      <Button variant="secondary" loading={busy} onClick={() => act(kind)} data-license-action={kind}>
        {st(`action.${kind}`)}
      </Button>
    ) : (
      buyButton
    )
  } else if (failure && !celebrating) {
    const kind = FAILURE_ACTION[failure]
    control = (
      <Button variant="secondary" loading={busy} onClick={() => act(kind)} data-license-action={kind}>
        {st(`action.${kind}`)}
      </Button>
    )
  } else if (active) {
    control = (
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          className="text-ink-2 hover:text-ink"
          loading={busy}
          title={st('releaseTitle')}
          onClick={() => void doRelease()}
          data-license-action="release"
        >
          {st('action.release')}
        </Button>
        {renewable && (
          <Button variant="secondary" loading={busy} onClick={() => void buy()} data-license-action="renew">
            {st('action.renew')}
          </Button>
        )}
      </div>
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
          {st('action.renewed')}
        </Button>
        <Button variant="primary" loading={busy} onClick={() => void buy()} data-license-action="renew">
          {st('action.renew')}
        </Button>
      </div>
    )
  } else {
    // 未激活 / 吊销（吊销之后买了新的码也在下面的胶囊里输入，Codex #2071 P2）：右边只剩「购买」，输入在下面那颗胶囊
    control = buyButton
  }

  return (
    <FormSection title={title} data-settings-anchor="about.license" data-license-state={failure ? `failed:${failure}` : state}>
      <FieldGroup>
        <SettingRow
          label={label ?? st('label', { pro: PRO_NAME })}
          description={<span data-license-line>{line}</span>}
          below={
            showCapsule ? (
              <Reveal open={!folding}>
                <div ref={capsuleBox} className="pt-1 pb-0.5">
                  <CodeCapsule
                    phase={phase}
                    value={code}
                    onValueChange={(v) => {
                      setCode(v)
                      if (failure) clearFailure()
                    }}
                    onOpen={enterCode}
                    onSubmit={() => void submit()}
                    onCancel={cancel}
                    bad={failure === 'invalid_code'}
                    shakeKey={shakeKey}
                  />
                </div>
              </Reveal>
            ) : plate && status.founding ? (
              <FoundingPlate
                no={status.founding.no}
                since={status.founding.since}
                name={status.founding.name}
                onName={(n) => void setPlateName(n)}
              />
            ) : undefined
          }
          control="auto"
        >
          {control}
        </SettingRow>
        {extra}
      </FieldGroup>
    </FormSection>
  )
}
