import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { CircleCheck, CircleMinus, CircleX, Ellipsis, Unplug } from '@/components/ui/icons'
import { ICON_SIZE, IconSlot } from '@/components/ui/Icon'
import {
  HostShellError,
  hostErrorText,
  hostStepLabel,
  hostStepStateText,
  parseHostList,
  parseHostResult,
  type HostList,
  type HostRow,
  type HostResult,
  type HostStep,
} from '@/lib/hostConnect'
import { isDesktop, runCodexIntegration, runHostIntegration } from '@/lib/desktop'
import { parseCodexResult } from '@/lib/codexInstall'
import { BrandLogo } from './BrandLogo'
import { Button, IconButton } from '../ui/Button'
import { FieldGroup, FormSection } from '../ui/FormSection'
import { Dialog } from '../ui/Dialog'
import { Menu, MenuItem } from '../ui/Menu'
import { DiagnosticDisclosure, GroupNotice, SettingRow } from './SettingRow'

/**
 * 设置 →「接入 AI 编辑器」（用户 2026-10-27，与「连接 AI」并列的独立一组）。
 *
 * 每个宿主一行：没装的只写「未检测到」，不画按钮；装了的一个「接入」；接好的「✓ 已接入」+ ⋯（断开）。
 * **点一下就接好，不选文件夹**（用户 2026-11-04，ADR 0009 §1b）：范围由 MCP 服务端定——宿主报的项目目录优先，
 * 不报的宿主是本机任意位置，只拦凭据类文件（.env、私钥、凭据目录、浏览器登录数据）。
 *
 * **按钮背后是那条命令，不是第二套安装器**：`tavotto-cli hosts … --json`（`engine/hostconnect.py`），这里只渲染
 * 它那一行 JSON。失败显示翻译过的一句原因，逐步结论折叠在下面（与 Codex 那一行同一形状）。浏览器模式没有壳，
 * 整组不显示——**不画一个按不动的按钮**。
 */
export function HostsPanel() {
  const { t } = useTranslation('dialogs')
  const tx = useCallback(
    (key: string, values?: Record<string, unknown>) => t(`settings.hosts.${key}`, values ?? {}),
    [t],
  )
  const desktop = isDesktop()
  const [list, setList] = useState<HostList | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [result, setResult] = useState<HostResult | null>(null)
  const [shellCode, setShellCode] = useState<{ host: string; code: string } | null>(null)
  const [announce, setAnnounce] = useState('')
  const [overwriteHost, setOverwriteHost] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setList(parseHostList(await runHostIntegration('list')))
      setListError(null)
    } catch (e) {
      setListError(e instanceof HostShellError ? e.code : 'spawn_failed')
    }
  }, [])

  useEffect(() => {
    if (desktop) void refresh()
  }, [desktop, refresh])

  if (!desktop) return null

  const label = (id: string) => list?.hosts.find((h) => h.id === id)?.label ?? id

  const run = async (action: 'connect' | 'disconnect', host: string) => {
    setBusy(host)
    setResult(null)
    setShellCode(null)
    try {
      const r = parseHostResult(await runHostIntegration(action, host))
      setResult(r)
      setAnnounce(r.ok ? tx('announce.ok') : tx('announce.failed'))
    } catch (e) {
      setShellCode({ host, code: e instanceof HostShellError ? e.code : 'spawn_failed' })
      setAnnounce(tx('announce.failed'))
    } finally {
      setBusy(null)
      void refresh()
    }
  }

  /** 「更新」：Codex 插件落后时，走 `tavotto codex upgrade`（与终端同一条通道）。失败显示翻译过的一句 */
  const upgradeCodex = async () => {
    setBusy('codex')
    setResult(null)
    setShellCode(null)
    try {
      const r = parseCodexResult(await runCodexIntegration('upgrade'))
      if (r.ok) {
        setAnnounce(tx('announce.ok'))
      } else {
        const code = r.error_code ?? r.steps.find((x) => !x.ok && x.error_code)?.error_code ?? 'other'
        setShellCode({ host: 'codex', code })
        setAnnounce(tx('announce.failed'))
      }
    } catch (e) {
      setShellCode({ host: 'codex', code: e instanceof Error ? e.message : 'spawn_failed' })
      setAnnounce(tx('announce.failed'))
    } finally {
      setBusy(null)
      void refresh()
    }
  }

  /** 「覆盖」：用户在确认框里点过才带 `--force-skill` */
  const overwriteSkill = async (host: string) => {
    setOverwriteHost(null)
    setBusy(host)
    setResult(null)
    setShellCode(null)
    try {
      const r = parseHostResult(await runHostIntegration('connect_force_skill', host))
      setResult(r)
      setAnnounce(r.ok ? tx('announce.ok') : tx('announce.failed'))
    } catch (e) {
      setShellCode({ host, code: e instanceof HostShellError ? e.code : 'spawn_failed' })
      setAnnounce(tx('announce.failed'))
    } finally {
      setBusy(null)
      void refresh()
    }
  }

  const statusFor = (id: string): string | undefined => {
    if (result?.host !== id || !result.ok) return undefined
    if (result.action === 'disconnect') return tx('disconnected')
    return result.state === 'needs_new_session'
      ? tx('doneNewSession', { host: label(id) })
      : tx('connected')
  }

  /** 已接入行的状态（稳定 `data-host-state`）：skill-modified / plugin-outdated / plugin / skill / builtin / connected */
  const hostState = (h: HostRow): string =>
    h.update
      ? 'plugin-outdated'
      : h.id === 'codex'
        ? h.version
          ? 'plugin'
          : 'connected'
        : h.skill === 'modified'
          ? 'skill-modified'
          : h.skill === 'builtin'
            ? 'builtin'
            : h.version
              ? 'skill'
              : 'connected'

  /** 有新版、技能改过：仍是已接入，但不算一切正常，不给绿色对勾 */
  const needsAttention = (h: HostRow): boolean => ['plugin-outdated', 'skill-modified'].includes(hostState(h))

  const connectedText = (h: HostRow): string => {
    switch (hostState(h)) {
      case 'plugin-outdated':
        return tx('pluginOutdated', { version: h.version })
      case 'plugin':
        return tx('connectedPlugin', { version: h.version })
      case 'skill-modified':
        return tx('skillKept')
      case 'builtin':
        return tx('connectedBuiltin')
      case 'skill':
        return tx('connectedSkill', { version: h.version })
      default:
        return tx('connected')
    }
  }

  return (
    <FormSection title={tx('title')}>
      <FieldGroup data-hosts-section>
        <p aria-live="polite" className="sr-only p-0">
          {announce}
        </p>
        {listError && <GroupNotice tone="danger">{hostErrorText(listError)}</GroupNotice>}
        {list?.hosts.map((h) => {
          const failure =
            shellCode?.host === h.id
              ? hostErrorText(shellCode.code, h.label)
              : result?.host === h.id && !result.ok
                ? hostErrorText(result.error_code, h.label)
                : null
          const steps = result?.host === h.id ? result.steps : []
          return (
            <div key={h.id} className="flex min-h-12 flex-col gap-2 [&>[data-setting-row]]:min-h-0" data-host-row={h.id}>
              <SettingRow
                label={h.label}
                leading={<BrandLogo logo={h.id} size={ICON_SIZE.md} />}
                status={statusFor(h.id)}>
                {!h.installed ? (
                  <span className="text-sm text-ink-3" data-host-missing>
                    {tx('notDetected')}
                  </span>
                ) : h.connected ? (
                  <>
                    <span
                      className={cn(
                        'flex items-center gap-1 text-sm',
                        needsAttention(h) ? 'text-ink-2' : 'text-ok',
                      )}
                      data-host-connected
                      data-host-state={hostState(h)}
                      data-host-version={h.version}>
                      {!needsAttention(h) && <CircleCheck size={ICON_SIZE.sm} aria-hidden />}
                      {connectedText(h)}
                    </span>
                    {h.update && (
                      <Button
                        variant="secondary"
                        size="sm"
                        loading={busy === h.id}
                        disabled={busy !== null}
                        data-host-update={h.id}
                        onClick={() => void upgradeCodex()}>
                        {busy === h.id ? tx('updating') : tx('update')}
                      </Button>
                    )}
                    {!h.update && h.skill === 'modified' && h.id !== 'codex' && (
                      <Button
                        variant="secondary"
                        size="sm"
                        loading={busy === h.id}
                        disabled={busy !== null}
                        data-host-overwrite={h.id}
                        onClick={() => setOverwriteHost(h.id)}>
                        {tx('overwrite')}
                      </Button>
                    )}
                    <Menu
                      align="end"
                      width={220}
                      trigger={
                        <IconButton label={tx('more', { host: h.label })} iconSize="sm" data-host-more>
                          <Ellipsis size={ICON_SIZE.sm} aria-hidden />
                        </IconButton>
                      }
                    >
                      <MenuItem
                        icon={Unplug}
                        disabled={busy !== null}
                        onSelect={() => void run('disconnect', h.id)}
                      >
                        {tx('disconnect')}
                      </MenuItem>
                    </Menu>
                  </>
                ) : (
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={busy === h.id}
                    disabled={busy !== null}
                    data-host-connect={h.id}
                    onClick={() => void run('connect', h.id)}
                  >
                    {busy === h.id ? tx('running') : failure ? tx('retry') : tx('connect')}
                  </Button>
                )}
              </SettingRow>
              {failure && <GroupNotice tone="danger">{failure}</GroupNotice>}
              {steps.length > 0 && (
                <DiagnosticDisclosure variant="row" title={tx('stepsTitle')} value="" data-host-steps>
                  <StepList steps={steps} />
                </DiagnosticDisclosure>
              )}
            </div>
          )
        })}
      </FieldGroup>
      <Dialog
        open={overwriteHost !== null}
        onOpenChange={(v) => !v && setOverwriteHost(null)}
        title={tx('overwriteTitle', { host: label(overwriteHost ?? '') })}
        size="sm"
        footer={{
          secondary: (
            <Button variant="secondary" size="lg" onClick={() => setOverwriteHost(null)} data-host-overwrite-cancel>
              {tx('cancel')}
            </Button>
          ),
          primary: (
            <Button
              variant="primary"
              size="lg"
              data-host-overwrite-confirm
              onClick={() => {
                if (overwriteHost) void overwriteSkill(overwriteHost)
              }}>
              {tx('overwrite')}
            </Button>
          ),
        }}>
        <p className="text-sm text-ink-2">{tx('overwriteBody')}</p>
      </Dialog>
    </FormSection>
  )
}

function StepList({ steps }: { steps: HostStep[] }) {
  return (
    <ul className="flex flex-col gap-1">
      {steps.map((s, i) => {
        const Icon = !s.ok ? CircleX : s.skipped ? CircleMinus : CircleCheck
        const tone = !s.ok ? 'text-danger' : s.skipped ? 'text-ink-3' : 'text-ok'
        return (
          <li key={`${s.step}-${i}`} className="flex items-start gap-1.5 text-sm">
            <IconSlot>
              <Icon size={ICON_SIZE.sm} className={tone} aria-hidden />
            </IconSlot>
            <span className="min-w-0">
              <span className="text-ink">{hostStepLabel(s.step)}</span>
              <span className="ml-1 text-ink-3">{hostStepStateText(s)}</span>
              {s.detail && <span className="ml-1 break-all font-mono text-xs text-ink-3">{s.detail}</span>}
            </span>
          </li>
        )
      })}
    </ul>
  )
}
