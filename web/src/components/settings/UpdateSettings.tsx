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
 * 「最新」这句话的作用域（审计 T48）。
 *
 * 界面上绝不能出现无条件的「已是最新版本」：那是**上一次检查的回答**，不是
 * 对发布状态的实时核验。两条不能合并的事实——
 *   * 从没查过（离线启动、`TAVOTTO_NO_UPDATE_CHECK`、后端 24h 节流下缓存也空）
 *     → 「不知道是不是最新」，不是「是最新」；
 *   * 查过了没有新版 → 只能说到那一刻为止。
 * 所以这里只有两句话，且都由**真实存在的时间戳**决定走哪一句：拿不到时间戳
 * 就说不知道，不补一个「刚刚」。
 */
function LastCheckVerdict({ checkedAtMs }: { checkedAtMs: number | null | undefined }) {
  return (
    <p
      // 结构性标记：判据认它，不去匹配那两句话的散文。用文案当判据的话，
      // 「不含另一句」在时间参数不同的时候是恒真的
      data-update-verdict={checkedAtMs ? 'checked' : 'unknown'}
      className="text-xs text-ink-3"
    >
      {checkedAtMs
        ? st('update.noUpdateAtLastCheck', { time: formatDateTime(checkedAtMs) })
        : st('update.latestUnknown')}
    </p>
  )
}

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
    applyFailed,
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
      <SettingRow label={st('update.autoCheck')}>
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
          {/* 失败必须看得出是失败：同一片灰色日志既当成功回执又当错误，
              用户读不出装没装上，也就不知道该不该再点一次那个按钮 */}
          {applyFailed && <InlineWarning tone="danger">{st('update.applyFailedRetry')}</InlineWarning>}
          {applyLog && (
            <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-sm bg-surface-2 p-1.5 font-mono text-xs text-ink-3">
              {applyLog}
            </pre>
          )}
        </div>
      ) : (
        status && !status.error && <LastCheckVerdict checkedAtMs={status.checked_at_ms} />
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
    desktopCheckedAtMs,
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
        status={st('update.lastChecked', {
          time: desktopCheckedAtMs
            ? formatDateTime(desktopCheckedAtMs)
            : st('update.neverChecked'),
        })}
      >
        <Button onClick={() => void checkDesktop()} disabled={busy}>
          {st(desktopPhase === 'checking' ? 'update.checking' : 'update.checkNow')}
        </Button>
      </SettingRow>

      {/* 查过、没有新版、也没有错误——只有这一种情况才轮得到那句话，而它说到
          的也只是那一刻。检查失败时不说（下面那条错误自己会讲），正在查时不说 */}
      {desktopChecked && !desktopUpdate && !desktopError && desktopPhase === 'idle' && (
        <LastCheckVerdict checkedAtMs={desktopCheckedAtMs} />
      )}

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
