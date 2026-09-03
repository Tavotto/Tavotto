import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import { formatDateTime } from '@/i18n/format'
import type { PackageOp, PackageProgress, UserPackage } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { cn } from '@/lib/utils'
import { repairCodeMessage } from '../DependencyRepairCard'
import { useDepRepairStore } from '@/store/depRepairStore'
import { isPackageJobRunning, usePackageStore } from '@/store/packageStore'
import { askConfirm } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { TextInput } from '../ui/Input'
import { CopyButton } from './CopyButton'
import { DiagnosticDisclosure, InlineWarning, SettingSection } from './SettingRow'

/** 本页文案在 dialogs:settings.packages.* 下 */
const pk = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.packages.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 设置 → 包管理（ADR 0038）。
 *
 * 只操作**这个项目的 Tavotto 受管环境**——系统 Python、用户自己的 `.venv`、
 * 内置渲染 runtime 都不在这一页上可改。两份清单：
 *
 *   * **内置**：基础栈 + 它的依赖闭包 + pip（后端按目标环境现算），只读；
 *   * **用户安装**：账上记着的、Tavotto 往这个环境里装过的，可安装 / 升级 / 卸载。
 *
 * 每个动作都是「形成作业 → 执行」两步（`packageStore.plan` / `run`）；卸载在
 * 中间多一次确认，账上有别的包依赖它时按危险操作问。进度按 state 换文案，
 * 日志折叠可复制；错误给下一步而不只是退出码（`repairCodeMessage`）。
 * 没有回滚（pip 没有事务）——这句话常驻在页面上，并说清每次改动前后都留了快照。
 */
export function PackagesSettings() {
  useTranslation('dialogs')
  const { data, loading, loadError, progress, busy, errorCode, errorText, load } =
    usePackageStore()
  const [spec, setSpec] = useState('')
  const [specError, setSpecError] = useState<string | null>(null)

  useEffect(() => {
    void load()
  }, [load])

  // SSE 断了时的补拉：作业在跑就每两秒问一次
  const running = isPackageJobRunning(progress)
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => void usePackageStore.getState().poll(), 2000)
    return () => window.clearInterval(timer)
  }, [running])

  const capability = data?.capability
  const available = !!capability?.available
  const locked = !available || running || busy || !!data?.busy

  const start = async (op: PackageOp, target: string) => {
    const api = usePackageStore.getState()
    const job = await api.plan(op, target)
    if (!job) return false
    if (op === 'uninstall') {
      const ok = await askConfirm({
        title: msg('settings.packages.confirm.uninstallTitle', { name: job.distribution }, 'dialogs'),
        body: job.dependents.length
          ? msg(
              'settings.packages.confirm.uninstallBodyDependents',
              { name: job.distribution, dependents: job.dependents.join('、') },
              'dialogs',
            )
          : msg('settings.packages.confirm.uninstallBody', { name: job.distribution }, 'dialogs'),
        confirmLabel: msg('settings.packages.confirm.uninstallAction', undefined, 'dialogs'),
        danger: true,
      })
      if (!ok) return false
    }
    return api.run(job.job_id)
  }

  const install = async () => {
    const value = spec.trim()
    // 客户端先挡一次形状（与后端同一条语法的**子集**：无空格、无路径分隔符、
    // 无 URL）；真正的判据在后端 `depresolve.parse_requirement`，这里只是让
    // 明显写错的不必跑一个请求
    if (!value || /[\s/\\@;[\]$&|`"']/.test(value) || value.startsWith('-')) {
      setSpecError(pk('specInvalid'))
      return
    }
    setSpecError(null)
    if (await start('install', value)) setSpec('')
  }

  return (
    <div data-packages-page className="flex flex-col gap-4">
      {/* ---------------- 环境与能力 ---------------- */}
      <EnvironmentLine />

      {!available && capability && (
        <p className="text-xs leading-relaxed text-ink-2" data-packages-disabled>
          {capability.reason === 'no_project'
            ? pk('disabled.noProject', { product: PRODUCT_NAME })
            : capability.reason === 'managed_env_unavailable'
              ? pk('disabled.noBasePython')
              : pk('disabled.other')}
        </p>
      )}
      {loadError && !data && <InlineWarning tone="danger">{loadError}</InlineWarning>}

      {/* ---------------- 内置包 ---------------- */}
      <SettingSection title={pk('builtinTitle')}>
        <p className="text-xs text-ink-3">
          {data?.builtin_source === 'managed_env'
            ? pk('builtinFromManaged', { product: PRODUCT_NAME })
            : data?.builtin_source === 'bundled_runtime'
              ? pk('builtinFromBundled', { product: PRODUCT_NAME })
              : pk('builtinPlanned')}
        </p>
        <PackageTable
          ariaLabel={pk('builtinTitle')}
          empty={loading && !data ? pk('loading') : pk('builtinEmpty')}
          rows={(data?.builtin ?? []).map((b) => ({
            key: b.name,
            name: b.name,
            version: b.version || '—',
            status: <StatusText status={b.status} />,
            actions: <span className="text-xs text-ink-3">{pk('readOnly')}</span>,
          }))}
        />
      </SettingSection>

      {/* ---------------- 用户安装 ---------------- */}
      <SettingSection title={pk('userTitle')}>
        <form
          className="flex items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault()
            void install()
          }}
        >
          <TextInput
            value={spec}
            onChange={(e) => {
              setSpec(e.target.value)
              setSpecError(null)
            }}
            placeholder={pk('specPlaceholder')}
            aria-label={pk('specAria')}
            aria-invalid={specError ? true : undefined}
            disabled={locked}
            spellCheck={false}
            className="flex-1 font-mono"
          />
          <Button type="submit" variant="primary" size="sm" disabled={locked || !spec.trim()}>
            {pk('install')}
          </Button>
        </form>
        {specError && (
          <p role="alert" className="text-xs text-danger">
            {specError}
          </p>
        )}
        <p className="text-xs text-ink-3">
          {pk('networkNote')}
          {data?.network?.proxy ? ` · ${pk('network.proxy')}` : ''}
          {data?.network?.custom_index ? ` · ${pk('network.customIndex')}` : ''}
        </p>

        <PackageTable
          ariaLabel={pk('userTitle')}
          empty={loading && !data ? pk('loading') : pk('userEmpty')}
          rows={(data?.user ?? []).map((u) => ({
            key: u.distribution,
            name: (
              <span className="flex min-w-0 flex-col">
                <span className="truncate">{u.distribution}</span>
                <span className="truncate text-[11px] text-ink-3">
                  {pk(`reason.${u.reason === 'user_requested' ? 'user' : 'repair'}`)}
                  {u.requested_specifier ? ` · ${u.distribution}${u.requested_specifier}` : ''}
                  {u.installed_at ? ` · ${formatDateTime(u.installed_at * 1000)}` : ''}
                </span>
              </span>
            ),
            version: u.installed_version || u.recorded_version || '—',
            status: (
              <StatusText
                status={u.status}
                detail={
                  u.status === 'changed' && u.recorded_version
                    ? pk('status.changedDetail', { recorded: u.recorded_version })
                    : undefined
                }
              />
            ),
            actions: <UserActions pkg={u} locked={locked} onAction={start} />,
          }))}
        />
      </SettingSection>

      {/* ---------------- 作业进度 / 结果 ---------------- */}
      <JobPanel progress={progress} errorCode={errorCode} errorText={errorText} />

      {/* 没有回滚这件事要说出来（ADR 0019 §八）；快照是修复时的对照 */}
      <p className="text-xs leading-relaxed text-ink-3">
        {pk('rollbackNote', { count: data?.snapshots ?? 0 })}
      </p>
    </div>
  )
}

/** 一行：这个项目的 Tavotto 环境现在什么状态、是不是正在用它、重建入口。 */
function EnvironmentLine() {
  useTranslation('dialogs')
  const env = usePackageStore((s) => s.data?.environment)
  const capability = usePackageStore((s) => s.data?.capability)
  const rebuildBusy = useDepRepairStore((s) => s.busy)
  const rebuildManaged = useDepRepairStore((s) => s.rebuildManaged)
  if (!capability || capability.reason === 'no_project') return null
  const exists = !!env?.exists
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs" data-packages-env>
      <span className="font-medium text-ink">{pk('envTitle', { product: PRODUCT_NAME })}</span>
      {exists ? (
        <>
          <span className="font-mono text-ink-2">
            {pk('env.python', { version: env?.python_version || '?' })}
          </span>
          <span className="text-ink-3">
            {env?.state === 'ready' ? pk('env.ready') : pk('env.incomplete')}
          </span>
          <span className="text-ink-3">{env?.in_use ? pk('env.inUse') : pk('env.notInUse')}</span>
          <Button variant="outline" size="sm" disabled={rebuildBusy} onClick={() => void rebuildManaged()}>
            {pk('env.rebuild')}
          </Button>
        </>
      ) : (
        <span className="text-ink-3">{pk('env.notCreated')}</span>
      )}
    </div>
  )
}

function StatusText({ status, detail }: { status: string; detail?: string }) {
  useTranslation('dialogs')
  const tone =
    status === 'installed'
      ? 'text-ink-2'
      : status === 'missing'
        ? 'text-danger'
        : status === 'changed'
          ? 'text-warn'
          : 'text-ink-3'
  return (
    <span className={cn('flex flex-col text-xs', tone)}>
      <span>{status ? pk(`status.${status}`) : pk('status.unknown')}</span>
      {detail && <span className="text-[11px] text-ink-3">{detail}</span>}
    </span>
  )
}

/** 用户包那一行的动作。内置 / 被保护的只读；缺失的给「重新安装」。 */
function UserActions({
  pkg,
  locked,
  onAction,
}: {
  pkg: UserPackage
  locked: boolean
  onAction: (op: PackageOp, target: string) => Promise<boolean>
}) {
  useTranslation('dialogs')
  if (pkg.protected) return <span className="text-xs text-ink-3">{pk('protected')}</span>
  return (
    <span className="flex items-center justify-end gap-1">
      {pkg.status === 'missing' ? (
        <Button
          variant="outline"
          size="sm"
          disabled={locked}
          onClick={() => void onAction('install', `${pkg.distribution}${pkg.requested_specifier}`)}
        >
          {pk('reinstall')}
        </Button>
      ) : (
        <Button
          variant="outline"
          size="sm"
          disabled={locked}
          aria-label={pk('updateAria', { name: pkg.distribution })}
          onClick={() => void onAction('update', pkg.distribution)}
        >
          {pk('update')}
        </Button>
      )}
      <Button
        variant="outline"
        size="sm"
        disabled={locked}
        aria-label={pk('uninstallAria', { name: pkg.distribution })}
        onClick={() => void onAction('uninstall', pkg.distribution)}
      >
        {pk('uninstall')}
      </Button>
    </span>
  )
}

/** 一张四列的小表：名称 / 版本 / 状态 / 操作。真 `<table>`，读屏能按列读。 */
function PackageTable({
  ariaLabel,
  rows,
  empty,
}: {
  ariaLabel: string
  rows: { key: string; name: ReactNode; version: ReactNode; status: ReactNode; actions: ReactNode }[]
  empty: string
}) {
  useTranslation('dialogs')
  if (!rows.length) return <p className="text-xs text-ink-3">{empty}</p>
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table aria-label={ariaLabel} className="w-full table-fixed border-collapse text-xs">
        <thead>
          <tr className="text-left text-ink-3">
            <th scope="col" className="w-[38%] px-2 py-1 font-medium">
              {pk('col.name')}
            </th>
            <th scope="col" className="w-[17%] px-2 py-1 font-medium">
              {pk('col.version')}
            </th>
            <th scope="col" className="w-[20%] px-2 py-1 font-medium">
              {pk('col.status')}
            </th>
            <th scope="col" className="px-2 py-1 text-right font-medium">
              {pk('col.actions')}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-t border-border align-middle">
              <td className="min-w-0 px-2 py-1.5 text-ink">{r.name}</td>
              <td className="px-2 py-1.5 font-mono text-ink-2">{r.version}</td>
              <td className="px-2 py-1.5">{r.status}</td>
              <td className="px-2 py-1.5 text-right">{r.actions}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * 作业面板：一行状态 + 不确定态进度条 + 取消；结束后是结果或错误。
 * 日志折叠、可复制；错误文案来自 `repairCodeMessage`（给下一步，不是退出码）。
 */
function JobPanel({
  progress,
  errorCode,
  errorText,
}: {
  progress: PackageProgress | null
  errorCode: string
  errorText: string
}) {
  useTranslation('dialogs')
  const cancel = usePackageStore((s) => s.cancel)
  const clearError = usePackageStore((s) => s.clearError)
  const running = isPackageJobRunning(progress)
  const failure = errorCode || errorText ? (repairCodeMessage(errorCode) ?? errorText) : null
  if (!progress && !failure) return null

  const opName = progress?.op ? pk(`op.${progress.op}`) : ''
  const target = progress?.requirement || progress?.distribution || ''
  const stateText = progress
    ? progress.state === 'done'
      ? pk('job.done', { op: opName, name: progress.result?.distribution ?? target, version: progress.result?.version ?? '' })
      : progress.state === 'cancelled'
        ? pk('job.cancelled', { op: opName, name: target })
        : progress.state === 'failed'
          ? pk('job.failed', { op: opName, name: target })
          : pk(`job.${progress.state}`, { op: opName, name: target })
    : ''

  return (
    <div
      data-packages-job
      className="flex flex-col gap-1.5 rounded-md border border-border p-2.5"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-2">
        <span className={cn('min-w-0 flex-1 text-xs', progress?.state === 'failed' ? 'text-danger' : 'text-ink')}>
          {stateText}
        </span>
        {running && (
          <Button variant="outline" size="sm" onClick={() => void cancel()}>
            {pk('job.cancel')}
          </Button>
        )}
        {!running && (progress || failure) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              clearError()
              usePackageStore.setState({ progress: null })
            }}
          >
            {pk('job.dismiss')}
          </Button>
        )}
      </div>
      {running && (
        <div
          role="progressbar"
          aria-label={pk('job.progressAria')}
          className="h-1 overflow-hidden rounded-full bg-surface-2"
        >
          <div className="h-full w-1/3 animate-pulse bg-accent" />
        </div>
      )}
      {failure && (
        <InlineWarning tone="danger">
          {failure}
          {errorText && repairCodeMessage(errorCode) && (
            <span className="ml-1 font-mono text-[11px] text-ink-3">{errorCode}</span>
          )}
        </InlineWarning>
      )}
      {progress?.log && (
        <DiagnosticDisclosure
          title={pk('job.log')}
          action={<CopyButton text={progress.log} label={pk('job.copyLog')} />}
        >
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-ink-3">
            {progress.log}
          </pre>
        </DiagnosticDisclosure>
      )}
    </div>
  )
}
