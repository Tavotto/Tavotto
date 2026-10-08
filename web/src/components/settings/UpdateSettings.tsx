import { useEffect, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatMessage, t as translate } from '@/i18n'
import { formatDateTime } from '@/i18n/format'
import type { UpdateStatus } from '@/lib/api'
import { useUpdateStore } from '@/store/updateStore'
import { UpdateRestartError } from '../UpdateRestartError'
import { Button } from '../ui/Button'
import { FieldGroup, FormSection } from '../ui/FormSection'
import { ProgressBar } from '../ui/ProgressBar'
import { Toggle } from '../ui/Toggle'
import {
  DiagnosticDisclosure,
  DiagnosticItem,
  GroupNotice,
  SettingRow,
  settingRowLabelId,
} from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 「最新」这句话的作用域（审计 T48），**时刻与结论是同一句**（全面打磨 D35）。
 *
 * 界面上绝不能出现无条件的「已是最新版本」：那是**上一次检查的回答**，不是
 * 对发布状态的实时核验。两条不能合并的事实——
 *   * 从没查过（离线启动、`TAVOTTO_NO_UPDATE_CHECK`、后端 24h 节流下缓存也空）
 *     → 「不知道是不是最新」，不是「是最新」；
 *   * 查过了没有新版 → 只能说到那一刻为止（时刻本身就是这个边界）。
 * 都由**真实存在的时间戳**决定走哪一句：拿不到时间戳就说不知道，不补一个「刚刚」。
 *
 * 此前时刻在行的 `status` 上、结论在下面另起一段 `type-caption`，同一件事说两遍，
 * 中间还隔着错误条与「有新版本」那一段。现在它就是那一行的现状。
 */
function LastCheckStatus({
  checkedAtMs,
  settled,
}: {
  checkedAtMs: number | null | undefined
  /** 查过了、没有新版、也没有错误——只有这一种情况才轮得到「没有新版本」这句结论 */
  settled: boolean
}) {
  const unknown = !checkedAtMs
  return (
    <span
      // 结构性标记：判据认它，不去匹配那几句话的散文。用文案当判据的话，
      // 「不含另一句」在时间参数不同的时候是恒真的
      data-update-verdict={unknown ? 'unknown' : settled ? 'checked' : 'pending'}
    >
      {unknown
        ? st('update.lastCheckedUnknown')
        : settled
          ? st('update.lastCheckedNoUpdate', { time: formatDateTime(checkedAtMs) })
          : st('update.lastChecked', { time: formatDateTime(checkedAtMs) })}
    </span>
  )
}

/**
 * 「有新版本」：组里一条 accent（info）语气的 Notice——标题是「有新版本 x.y.z」，正文一句「当前 a.b.c」，
 * 右边是**这一页唯一的主动作**（32px 主按钮）；发行说明收在下面一行原地展开（2026-10-07 设计审计 §9.1：此前
 * 是一段打破行语法的内容 + 原样输出的 `<pre>`）。桌面 / 浏览器两条通道共用这一份壳。
 */
function UpdateAvailable({
  version,
  current,
  notes,
  action,
  children,
}: {
  version: string
  current: string
  notes?: string | null
  /** 主动作（lg primary）；装不了 / 装好了时不给 */
  action?: ReactNode
  /** Notice 正文里在「当前 x」之后的那段（重启提示、源码升级命令、下载进度……） */
  children?: ReactNode
}) {
  return (
    <>
      <GroupNotice
        tone="info"
        data-update-available
        title={
          <>
            {st('update.available')} <span className="font-mono">{version}</span>
          </>
        }
        action={action}
      >
        <span className="tabular-nums">{st('update.currentIs', { version: current })}</span>
        {children}
      </GroupNotice>
      {notes && (
        <DiagnosticDisclosure variant="row" title={st('update.notesTitle')} data-update-notes>
          <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-ink-2">
            {notes}
          </pre>
        </DiagnosticDisclosure>
      )}
    </>
  )
}

/**
 * 检查更新。保留：自动检查开关、检查按钮、当前状态、有新版本时的主动作。
 * 安装方式、签名校验说明、升级命令进「技术详情」；**错误照旧常驻**（组内 danger Notice）。
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
    autoCheckSaving,
    autoCheckFailure,
  } = useUpdateStore()
  useEffect(() => {
    if (!status) void check(false)
  }, [status, check])

  // 桌面模式：Python updater 整个停用（升级归 Tauri 层）
  if (status?.desktop) return <DesktopUpdateSettings status={status} />

  return (
    <UpdatesGroup>
      {/* 标签自己就是那句说明（「每天自动检查」），不在下面再复述一遍（全面打磨 D35）；
          开关的名字用渲染那行可见文字的同一份，不另写一句同义的 */}
      <SettingRow
        data-update-auto
        label={st('update.autoCheck')}
        controlId="setting-update-auto"
        status={autoCheckSaving ? st('update.autoCheckSaving') : undefined}
      >
        <Toggle
          id="setting-update-auto"
          aria-labelledby={settingRowLabelId('setting-update-auto')}
          checked={status?.auto_check ?? true}
          disabled={autoCheckSaving || !status}
          onChange={(v) => void setAutoCheck(v)}
        />
      </SettingRow>

      {autoCheckFailure && (
        <GroupNotice
          tone="danger"
          data-update-auto-error
          action={
            <Button
              data-update-auto-retry
              variant="secondary"
              size="sm"
              onClick={() => void setAutoCheck(autoCheckFailure.value)}
              disabled={autoCheckSaving}
            >
              {st('update.autoCheckRetry')}
            </Button>
          }
        >
          {st('update.autoCheckSaveFailed')} {formatMessage(autoCheckFailure.message)}
        </GroupNotice>
      )}

      <SettingRow
        label={st('update.check')}
        status={
          <LastCheckStatus
            checkedAtMs={status?.checked_at_ms}
            settled={Boolean(status && !status.error && !status.update_available && !checkError)}
          />
        }
      >
        <Button variant="secondary" size="sm" onClick={() => void check(true)} disabled={checking}>
          {st(checking ? 'update.checking' : 'update.checkNow')}
        </Button>
      </SettingRow>

      {status?.error && (
        <GroupNotice tone="danger">
          {/* code 有本地文案时按界面语言渲染；error 中文原文只作回退（issue #30） */}
          {status.code === 'update_check_failed'
            ? translate('update.checkFailed', {
                ns: 'errors',
                error: String(status.params?.error ?? ''),
              })
            : status.error}
        </GroupNotice>
      )}
      {checkError && <GroupNotice tone="danger">{checkError}</GroupNotice>}

      {status?.update_available && (
        <UpdateAvailable
          version={status.latest ?? ''}
          current={status.current}
          notes={status.notes}
          action={
            !restartRequired && status.can_self_update ? (
              <Button variant="primary" size="lg" onClick={() => void apply()} disabled={applying}>
                {st(applying ? 'update.upgrading' : 'update.downloadAndUpgrade')}
              </Button>
            ) : undefined
          }
        >
          {restartRequired ? (
            <span className="mt-1 block">
              {st('update.restartBefore')}
              <strong className="font-medium">{st('update.restartStrong')}</strong>
              {st('update.restartAfter')}
            </span>
          ) : status.can_self_update ? (
            <a
              href={status.html_url}
              target="_blank"
              rel="noreferrer"
              className="mt-1 block w-fit underline underline-offset-2"
            >
              {st('update.releaseNotes')}
            </a>
          ) : (
            <span className="mt-1 block">
              {st('update.sourceUpgrade')} <code className="font-mono">{status.upgrade_command}</code>
            </span>
          )}
        </UpdateAvailable>
      )}
      {/* 失败必须看得出是失败：同一片灰色日志既当成功回执又当错误，
          用户读不出装没装上，也就不知道该不该再点一次那个按钮 */}
      {status?.update_available && applyFailed && (
        <GroupNotice tone="danger">{st('update.applyFailedRetry')}</GroupNotice>
      )}
      {status?.update_available && applyLog && (
        <div>
          <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-md bg-surface p-2 font-mono text-xs text-ink-3">
            {applyLog}
          </pre>
        </div>
      )}

      <DiagnosticDisclosure variant="row" title={st('techDetails')}>
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
        <p className="type-caption">{st('update.channelNote')}</p>
      </DiagnosticDisclosure>
    </UpdatesGroup>
  )
}

/** 「更新」一组（关于页里版本在上、检查更新紧随其后，再是隐私） */
function UpdatesGroup({ children }: { children: ReactNode }) {
  return (
    <FormSection title={st('update.title')} data-settings-anchor="about.updates">
      <FieldGroup>{children}</FieldGroup>
    </FormSection>
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
 * **自动检查那一行两条通道都有**（2026-10-07 设计审计 §9.1：桌面 / 浏览器同形）：桌面版每次启动都检查一次、
 * 目前没有应用内关闭开关（后端的 updater 在桌面壳里停用、存不下这个偏好）——所以开关画成开着、停用，原因就在
 * 行内，而不是这一行在桌面版上整个消失、让人以为桌面版不检查。
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
    relaunching,
    relaunchFailed,
  } = useUpdateStore()
  useEffect(() => {
    if (!desktopChecked) void checkDesktop()
  }, [desktopChecked, checkDesktop])

  const busy = desktopPhase !== 'idle'
  const pct = desktopProgress === null ? null : Math.round(desktopProgress * 100)

  return (
    <UpdatesGroup>
      <SettingRow
        data-update-auto
        label={st('update.autoCheckDesktop')}
        controlId="setting-update-auto"
        status={st('update.autoCheckDesktopReason')}
      >
        <Toggle
          id="setting-update-auto"
          aria-labelledby={settingRowLabelId('setting-update-auto')}
          checked
          disabled
          onChange={() => {}}
        />
      </SettingRow>

      <SettingRow
        label={st('update.check')}
        status={
          <LastCheckStatus
            checkedAtMs={desktopCheckedAtMs}
            settled={
              desktopChecked && !desktopUpdate && !desktopError && desktopPhase === 'idle'
            }
          />
        }
      >
        <Button variant="secondary" size="sm" onClick={() => void checkDesktop()} disabled={busy}>
          {st(desktopPhase === 'checking' ? 'update.checking' : 'update.checkNow')}
        </Button>
      </SettingRow>

      {desktopError && !relaunchFailed && (
        <GroupNotice tone="danger">
          {desktopError}
          <a
            href={status.releases_url}
            target="_blank"
            rel="noreferrer"
            className="mt-1 block w-fit underline underline-offset-2"
          >
            {st('update.manualDownload')}
          </a>
        </GroupNotice>
      )}

      {desktopUpdate && (
        <UpdateAvailable
          version={desktopUpdate.version}
          current={status.current}
          notes={desktopUpdate.notes}
          action={
            desktopPhase === 'installed' ? (
              <Button
                data-update-relaunch
                variant="primary"
                size="lg"
                onClick={() => void relaunch()}
                loading={relaunching}
              >
                {st('update.relaunch')}
              </Button>
            ) : desktopPhase === 'downloading' ? undefined : (
              <Button variant="primary" size="lg" onClick={() => void installDesktop()}>
                {st('update.downloadAndInstall')}
              </Button>
            )
          }
        >
          {desktopPhase === 'installed' ? (
            <span className="mt-1 block">{st('update.installedHint')}</span>
          ) : desktopPhase === 'downloading' ? (
            <span className="mt-2 flex flex-col gap-1">
              {/* 拿不到 Content-Length 就走不确定态，不假装卡在某个百分比 */}
              <ProgressBar pct={pct} label={st('update.downloadProgressAria')} />
              <span className="tabular-nums">
                {pct === null ? st('update.downloading') : st('update.downloadingPct', { pct })}
              </span>
            </span>
          ) : (
            <a
              href={status.releases_url}
              target="_blank"
              rel="noreferrer"
              className="mt-1 block w-fit underline underline-offset-2"
            >
              {st('update.releaseNotes')}
            </a>
          )}
        </UpdateAvailable>
      )}
      {desktopUpdate && desktopPhase === 'installed' && relaunchFailed && (
        <div>
          <UpdateRestartError detail={desktopError} />
        </div>
      )}

      <DiagnosticDisclosure variant="row" title={st('techDetails')}>
        <p className="type-caption">{st('update.signatureNote')}</p>
      </DiagnosticDisclosure>
    </UpdatesGroup>
  )
}
