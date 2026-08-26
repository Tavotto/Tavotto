import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { formatDateTime } from '@/i18n/format'
import type { UpdateStatus } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useUpdateStore } from '@/store/updateStore'
import { Button } from '../ui/Button'
import { Toggle } from '../ui/Toggle'
import {
  DiagnosticDisclosure,
  DiagnosticItem,
  InlineWarning,
  SettingRow,
  SettingSection,
} from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 检查更新。保留：当前版本、自动检查开关、检查按钮、当前状态。
 * 安装方式、签名校验说明、升级命令进「技术详情」；**错误照旧常驻**。
 */
export function UpdateSettings() {
  useTranslation('dialogs')
  const {
    status,
    checking,
    applying,
    restartRequired,
    applyLog,
    checkError,
    check,
    apply,
    setAutoCheck,
  } = useUpdateStore()
  useEffect(() => {
    if (!status) void check(false)
  }, [status, check])

  const checkedAt = status?.checked_at_ms
    ? formatDateTime(status.checked_at_ms)
    : st('update.neverChecked')

  // 桌面模式：Python updater 整个停用（升级归 Tauri 层）
  if (status?.desktop) return <DesktopUpdateSettings status={status} />

  return (
    <SettingSection>
      <SettingRow label={st('update.currentVersion')}>
        <span className="font-mono text-xs text-ink">{status?.current ?? '…'}</span>
      </SettingRow>
      <SettingRow label={st('update.autoCheck')} help={st('update.autoCheckHint')}>
        <Toggle
          checked={status?.auto_check ?? true}
          onChange={(v) => void setAutoCheck(v)}
          aria-label={st('update.autoCheckAria')}
        />
      </SettingRow>

      <SettingRow label={st('update.check')} status={st('update.lastChecked', { time: checkedAt })}>
        <Button onClick={() => void check(true)} disabled={checking}>
          {st(checking ? 'update.checking' : 'update.checkNow')}
        </Button>
      </SettingRow>

      {status?.error && (
        <InlineWarning tone="danger">
          {/* code 有本地文案时按界面语言渲染；error 中文原文只作回退（issue #30） */}
          {status.code === 'update_check_failed'
            ? translate('update.checkFailed', {
                ns: 'errors',
                error: String(status.params?.error ?? ''),
              })
            : status.error}
        </InlineWarning>
      )}
      {checkError && <InlineWarning tone="danger">{checkError}</InlineWarning>}

      {status?.update_available ? (
        <div className="flex flex-col gap-2 rounded-md border border-border p-2.5">
          <p className="text-xs text-ink">
            {st('update.available')} <span className="font-mono">{status.latest}</span>
            <span className="ml-2 text-ink-3">
              {st('update.currentIs', { version: status.current })}
            </span>
          </p>
          {status.notes && (
            <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-ink-2">
              {status.notes}
            </pre>
          )}
          {restartRequired ? (
            <p className="text-xs text-ink-2">
              {st('update.restartBefore')}
              <strong className="font-medium text-ink">{st('update.restartStrong')}</strong>
              {st('update.restartAfter')}
            </p>
          ) : status.can_self_update ? (
            <div className="flex items-center gap-2">
              <Button variant="primary" onClick={() => void apply()} disabled={applying}>
                {st(applying ? 'update.upgrading' : 'update.downloadAndUpgrade')}
              </Button>
              <a
                href={status.html_url}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-accent hover:underline"
              >
                {st('update.releaseNotes')}
              </a>
            </div>
          ) : (
            <p className="text-xs text-ink-2">
              {st('update.sourceUpgrade')}{' '}
              <code className="font-mono">{status.upgrade_command}</code>
            </p>
          )}
          {applyLog && (
            <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-sm bg-surface-2 p-1.5 font-mono text-xs text-ink-3">
              {applyLog}
            </pre>
          )}
        </div>
      ) : (
        status && !status.error && <p className="text-xs text-ink-3">{st('update.upToDate')}</p>
      )}

      <DiagnosticDisclosure title={st('techDetails')}>
        <DiagnosticItem
          name={st('update.installMethod')}
          value={
            status?.method === 'pipx'
              ? 'pipx'
              : status?.method === 'source'
                ? st('update.methodSource')
                : 'pip'
          }
        />
        <p className="text-xs leading-relaxed text-ink-3">{st('update.channelNote')}</p>
      </DiagnosticDisclosure>
    </SettingSection>
  )
}

/**
 * 桌面版的更新器（Tauri）。**整个过程留在软件里**：检查 → 下载（带进度）→
 * 安装 → 重启，用户不用去 Releases 页面手动下载覆盖安装。
 *
 * 三条纪律与 pip 那条一致：
 *   * 不静默——每一步都要用户按一下；
 *   * 装完不等于生效，重启才算换版本；
 *   * 失败要说人话并留退路（更新器连不上时仍给 Releases 链接）。
 *
 * 安装包的签名由壳里的公钥校验，校验不过当场失败——这里不做「忽略签名」的口子。
 */
function DesktopUpdateSettings({ status }: { status: UpdateStatus }) {
  useTranslation('dialogs')
  const {
    desktopPhase,
    desktopUpdate,
    desktopProgress,
    desktopError,
    desktopChecked,
    checkDesktop,
    installDesktop,
    relaunch,
  } = useUpdateStore()
  useEffect(() => {
    if (!desktopChecked) void checkDesktop()
  }, [desktopChecked, checkDesktop])

  const busy = desktopPhase !== 'idle'
  const pct = desktopProgress === null ? null : Math.round(desktopProgress * 100)

  return (
    <SettingSection>
      <SettingRow label={st('update.currentVersion')}>
        <span className="font-mono text-xs text-ink">{status.current}</span>
      </SettingRow>

      <SettingRow
        label={st('update.check')}
        status={
          desktopChecked && !desktopUpdate && !desktopError ? st('update.upToDate') : undefined
        }
      >
        <Button onClick={() => void checkDesktop()} disabled={busy}>
          {st(desktopPhase === 'checking' ? 'update.checking' : 'update.checkNow')}
        </Button>
      </SettingRow>

      {desktopError && (
        <div className="flex flex-col gap-1">
          <InlineWarning tone="danger">{desktopError}</InlineWarning>
          <a
            href={status.releases_url}
            target="_blank"
            rel="noreferrer"
            className="text-xs text-accent hover:underline"
          >
            {st('update.manualDownload')}
          </a>
        </div>
      )}

      {desktopUpdate && (
        <div className="flex flex-col gap-2 rounded-md border border-border p-2.5">
          <p className="text-xs text-ink">
            {st('update.available')} <span className="font-mono">{desktopUpdate.version}</span>
            <span className="ml-2 text-ink-3">
              {st('update.currentIs', { version: status.current })}
            </span>
          </p>
          {desktopUpdate.notes && (
            <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-ink-2">
              {desktopUpdate.notes}
            </pre>
          )}

          {desktopPhase === 'installed' ? (
            <div className="flex items-center gap-2">
              <Button variant="primary" onClick={() => void relaunch()}>
                {st('update.relaunch')}
              </Button>
              <span className="text-xs text-ink-2">{st('update.installedHint')}</span>
            </div>
          ) : desktopPhase === 'downloading' ? (
            <div className="flex flex-col gap-1">
              {/* 拿不到 Content-Length 就走不确定态，不假装卡在某个百分比 */}
              <div
                role="progressbar"
                aria-label={st('update.downloadProgressAria')}
                aria-valuenow={pct ?? undefined}
                aria-valuemin={0}
                aria-valuemax={100}
                className="h-1 overflow-hidden rounded-full bg-surface-2"
              >
                <div
                  className={cn('h-full bg-accent', pct === null && 'w-1/3 animate-pulse')}
                  style={pct === null ? undefined : { width: `${pct}%` }}
                />
              </div>
              <span className="text-xs text-ink-3">
                {pct === null ? st('update.downloading') : st('update.downloadingPct', { pct })}
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Button variant="primary" onClick={() => void installDesktop()}>
                {st('update.downloadAndInstall')}
              </Button>
              <a
                href={status.releases_url}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-accent hover:underline"
              >
                {st('update.releaseNotes')}
              </a>
            </div>
          )}
        </div>
      )}

      <DiagnosticDisclosure title={st('techDetails')}>
        <p className="text-xs leading-relaxed text-ink-3">{st('update.signatureNote')}</p>
      </DiagnosticDisclosure>
    </SettingSection>
  )
}
