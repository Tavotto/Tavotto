import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CircleCheck, CircleMinus, CircleX, Ellipsis, FolderOpen, Unplug } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import {
  HostShellError,
  hostErrorText,
  hostStepLabel,
  hostStepStateText,
  parseHostList,
  parseHostResult,
  type HostList,
  type HostResult,
  type HostStep,
} from '@/lib/hostConnect'
import { isDesktop, pickDirectory, runHostIntegration } from '@/lib/desktop'
import { Button, IconButton } from '../ui/Button'
import { FieldGroup, FormSection } from '../ui/FormSection'
import { Menu, MenuItem } from '../ui/Menu'
import { DiagnosticDisclosure, GroupNotice, SettingRow } from './SettingRow'

/**
 * 设置 →「接入 AI 编辑器」（用户 2026-10-27，与「连接 AI」并列的独立一组）。
 *
 * 每个宿主一行：没装的只写「未检测到」，不画按钮；装了的一个「接入」；接好的「✓ 已接入」+ ⋯（更改文件夹 · 断开）。
 * 第一次接入先选项目文件夹（所有宿主共用，主目录 / 磁盘根由引擎拒绝）。
 *
 * **按钮背后是那条命令，不是第二套安装器**：`tavotto-cli hosts … --json`（`engine/hostconnect.py`），这里只渲染
 * 它那一行 JSON。失败显示翻译过的一句原因，逐步结论折叠在下面（与 Codex 那一行同一形状）。浏览器模式没有壳，
 * 整组不显示——**不画一个按不动的按钮**。
 */
/** 失败原因是文件夹本身时，下一次点按钮先弹选择框 */
const FOLDER_ERRORS = ['project_root_rejected', 'project_root_missing']

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

  const run = async (action: 'connect' | 'disconnect', host: string, askFolder = false) => {
    let root: string | undefined
    if (action === 'connect' && (askFolder || !list?.project_root)) {
      const picked = await pickDirectory(tx('pickFolderTitle', { host: label(host) }))
      if (!picked) return
      root = picked
    }
    setBusy(host)
    setResult(null)
    setShellCode(null)
    try {
      const r = parseHostResult(await runHostIntegration(action, host, root))
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
          // 记住的文件夹失效（被移走 / 删掉 / 含分隔符被拒）：重试先让用户换一个文件夹，否则会一直提交那个坏的
          // （Codex #1456 P2）
          const folderProblem =
            result?.host === h.id &&
            !result.ok &&
            FOLDER_ERRORS.includes(result.error_code ?? '')
          return (
            <div key={h.id} className="contents" data-host-row={h.id}>
              <SettingRow label={h.label} status={statusFor(h.id)}>
                {!h.installed ? (
                  <span className="text-sm text-ink-3" data-host-missing>
                    {tx('notDetected')}
                  </span>
                ) : h.connected ? (
                  <>
                    <span className="flex items-center gap-1 text-sm text-ok" data-host-connected>
                      <CircleCheck size={ICON_SIZE.sm} aria-hidden />
                      {tx('connected')}
                    </span>
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
                        icon={FolderOpen}
                        disabled={busy !== null}
                        onSelect={() => void run('connect', h.id, true)}
                      >
                        {tx('changeFolder')}
                      </MenuItem>
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
                    onClick={() => void run('connect', h.id, folderProblem)}
                  >
                    {busy === h.id
                      ? tx('running')
                      : folderProblem
                        ? tx('chooseFolder')
                        : failure
                          ? tx('retry')
                          : tx('connect')}
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
            <Icon size={ICON_SIZE.sm} className={`mt-0.5 shrink-0 ${tone}`} aria-hidden />
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
