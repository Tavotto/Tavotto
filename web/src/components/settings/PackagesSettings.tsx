import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import { formatDateTime } from '@/i18n/format'
import type { PackageOp, PackageProgress, UserPackage } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { cn } from '@/lib/utils'
import { repairCodeMessage } from '../DependencyRepairCard'
import { useDepRepairStore } from '@/store/depRepairStore'
import { isPackageJobRunning, searchTerm, usePackageStore } from '@/store/packageStore'
import { askConfirm } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { TextInput } from '../ui/Input'
import { Select } from '../ui/Select'
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
 *
 * **版面顺序就是使用顺序**（审计 T46）：安装入口 + 用户自己的包在最上面，
 * 首屏一句「装坏了可以重建」；内置那 14 条与网络 / 快照那些工程细节折叠在下面。
 * 修改前首屏被内置清单与反复出现的「已安装 / 只读」占满，用户要装一个包得先
 * 滚过它们。
 *
 * 首屏那句恢复说明**只说重建做什么**：重建是按 `environment.json` 上记的包重装
 * 一遍，与 snapshots 目录里那些 `pip freeze` 无关——旧文案把两件事写成一件
 * （「用重建恢复到快照记录的状态」），而 snapshots 根本没有任何读回路径。
 *
 * **查找是两层，且只有第二层出网**（ADR 0038 的 2026-09-07 修订，审计 T46）：
 *
 *   * 第一层**纯前端**：输入框里的名字即时过滤两份清单。「我是不是已经装过了」
 *     不该为此发一个请求，更不该出网。
 *   * 第二层是用户**点「在 PyPI 查找」**才发生的一次请求。界面上没有任何随输入
 *     自动触发的路径——出网必须是一个看得见的动作，否则用户在设置页里打字这件
 *     事就悄悄变成了对外发送。
 *
 * 输入框只有一个：它同时是安装规范与搜索词。**安装用的是原串**（`lmfit>=1.3`
 * 就是他想装的东西），过滤与查找用的是 `searchTerm()` 切掉约束之后的名字。
 * 回车仍然是「安装」——那是这个表单一直以来的主动作，查找有自己的按钮。
 *
 * 结果卡上的安装走的是**同一条**安装流程（`start('install', …)`），不复制第二
 * 条路径。选「最新版」时交给 pip 的是**裸包名**而不是 `==<那个版本号>`：安装
 * argv 带 `--only-binary=:all:`，钉死一个只有 sdist 的版本会直接失败，而裸名字
 * 让 pip 自己挑最新的、有轮子的那一版。选了具体版本才是明确的钉住。
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

  // 第一层搜索：纯前端过滤，一个请求都不发。判据用 PEP 503 归一后的子串——
  // 用户输 `Scikit_Learn` 时该匹配到清单上的 `scikit-learn`
  const term = searchTerm(spec)
  const key = term.replace(/[-_.]+/g, '-').toLowerCase()
  const match = (name: string) => !key || name.replace(/[-_.]+/g, '-').toLowerCase().includes(key)
  const allUser = data?.user ?? []
  const allBuiltin = data?.builtin ?? []
  const shownUser = allUser.filter((u) => match(u.distribution))
  const shownBuiltin = allBuiltin.filter((b) => match(b.name))
  const noMatch = pk('search.noMatch', { term })

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

      {/* ---------------- 用户安装（置顶：这一页存在的理由） ---------------- */}
      <SettingSection title={pk('userTitle')}>
        {/* 安装目标就在安装入口旁边，不必去别处对照 */}
        <EnvironmentLine />
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
          {/* 出网的动作只有这一颗按钮。`type="button"` 是要紧的：留成 submit
              的话回车会同时触发安装与查找，而回车该只做主动作。 */}
          <Button
            type="button"
            variant="outline"
            size="sm"
            data-packages-lookup
            disabled={locked || !term}
            onClick={() => void usePackageStore.getState().runLookup(spec)}
          >
            {pk('search.action')}
          </Button>
        </form>
        {specError && (
          <p role="alert" className="text-xs text-danger">
            {specError}
          </p>
        )}
        <p className="text-xs text-ink-3">{pk('networkNote')}</p>
        {/* 「点了会发生什么」就近说清：那颗按钮会出网。**为什么打字不出网**是
            工程细节，折在下面的技术详情里——首屏留给此刻要做的事（审计 T46 的
            那条纪律，`settingsDisclosure.test.tsx` 数首屏长文的段数）。 */}
        <p className="text-xs text-ink-3">{pk('search.networkNote')}</p>

        <LookupPanel locked={locked} onInstall={start} />

        <PackageTable
          ariaLabel={pk('userTitle')}
          empty={loading && !data ? pk('loading') : term ? noMatch : pk('userEmpty')}
          rows={shownUser.map((u) => ({
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

      {/* ---------------- 内置包 ---------------- */}
      {/* 内置那一份折叠为摘要（审计 T46）：条数在标题上，展开才是清单。
          它是"这个环境里本来就有什么"的背景，不是用户此刻要操作的东西。 */}
      <DiagnosticDisclosure
        title={
          term
            ? pk('search.builtinTitleMatch', {
                count: shownBuiltin.length,
                total: allBuiltin.length,
              })
            : pk('builtinTitleCount', { count: allBuiltin.length })
        }
      >
        <p className="text-xs text-ink-3">
          {data?.builtin_source === 'managed_env'
            ? pk('builtinFromManaged', { product: PRODUCT_NAME })
            : data?.builtin_source === 'bundled_runtime'
              ? pk('builtinFromBundled', { product: PRODUCT_NAME })
              : pk('builtinPlanned')}
        </p>
        <PackageTable
          ariaLabel={pk('builtinTitle')}
          empty={loading && !data ? pk('loading') : term ? noMatch : pk('builtinEmpty')}
          rows={shownBuiltin.map((b) => ({
            key: b.name,
            name: b.name,
            version: b.version || '—',
            status: <StatusText status={b.status} />,
            actions: <span className="text-xs text-ink-3">{pk('readOnly')}</span>,
          }))}
        />
      </DiagnosticDisclosure>

      {/* ---------------- 作业进度 / 结果 ---------------- */}
      <JobPanel progress={progress} errorCode={errorCode} errorText={errorText} />

      {/* 一句话说清失败后怎么办（审计 T46）。「没有回滚」与快照份数是工程细节，
          折在下面——它们解释的是**为什么**只能重建，不是用户此刻要做的事。 */}
      <p className="text-xs leading-relaxed text-ink-3">{pk('recoveryNote')}</p>

      <DiagnosticDisclosure title={pk('techTitle')}>
        <p className="text-xs leading-relaxed text-ink-3">
          {pk('snapshotDetail', { count: data?.snapshots ?? 0 })}
        </p>
        <p className="text-xs leading-relaxed text-ink-3">{pk('search.privacyDetail')}</p>
        {data?.network?.proxy && (
          <p className="text-xs text-ink-3">{pk('network.proxy')}</p>
        )}
        {data?.network?.custom_index && (
          <p className="text-xs text-ink-3">{pk('network.customIndex')}</p>
        )}
      </DiagnosticDisclosure>
    </div>
  )
}

/**
 * 一行：**这个项目的** Tavotto 环境现在什么状态、是不是正在用它、重建入口。
 *
 * 名字里的「这个项目的」不是修辞（审计 T46 / T47）：受管环境在
 * `<data_dir>/environments/<项目指纹>/`，**每个项目一个**；而诊断页上那句
 * 「{{product}} 自带的渲染环境」说的是随安装包附带、只读、不能 pip 的另一个。
 * 两者以前都叫「Tavotto 环境」，于是「尚未创建」与「matplotlib 3.11.1」会同时
 * 出现在两页上，看起来像自相矛盾。
 */
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
      <span className="text-ink-3">{pk('envTarget')}</span>
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

/** 选「最新版」的哨兵值。PEP 440 的版本号里不可能出现 `@`，撞不上真版本。 */
const LATEST = '@latest'

/**
 * 「在 PyPI 查找」的那一块：查找中 / 找到了 / 没找到 / 出网失败。
 *
 * **它是这一页上唯一一处出网结果的落点**，所以措辞要说清三件事：查的是哪个
 * 名字、答案来自哪种源（官方 / 你配的镜像 / 我们也不知道）、下一步做什么。
 *
 * 找不到时**不猜相近的名字**：PyPI 早就没有全文搜索 API，而「猜一个像的」
 * 正是抢注攻击的入口。界面上把这条限制直说出来，免得用户以为是搜索坏了。
 */
function LookupPanel({
  locked,
  onInstall,
}: {
  locked: boolean
  onInstall: (op: PackageOp, target: string) => Promise<boolean>
}) {
  useTranslation('dialogs')
  const lookup = usePackageStore((s) => s.lookup)
  const clearLookup = usePackageStore((s) => s.clearLookup)
  const [version, setVersion] = useState<string>(LATEST)
  const found = lookup.status === 'found' ? lookup.result : null

  // 换了一个包就回到「最新版」。不重置的话，上一个包选过的 1.2.3 会留在这里，
  // 而新包多半没有那个版本——下拉显示空白，安装却会带着一个别的包的版本号。
  useEffect(() => setVersion(LATEST), [found?.name])

  if (lookup.status === 'idle') return null

  const dismiss = (
    <Button variant="ghost" size="sm" onClick={clearLookup}>
      {pk('job.dismiss')}
    </Button>
  )

  if (lookup.status === 'loading') {
    return (
      <div
        data-packages-lookup-panel="loading"
        className="rounded-md border border-border p-2.5 text-xs text-ink-2"
        role="status"
        aria-live="polite"
      >
        {pk('search.loading', { name: lookup.query })}
      </div>
    )
  }

  if (!found) {
    // 四档 code 各有一句「下一步做什么」，与缺包修复共用同一张表；表里没有
    // 时退回后端原文（老前端 / curl 那条回退路径在界面上也成立）
    const message = repairCodeMessage(lookup.code) ?? lookup.text
    return (
      <div
        data-packages-lookup-panel="error"
        className="flex flex-col gap-1.5 rounded-md border border-border p-2.5"
        role="status"
        aria-live="polite"
      >
        <div className="flex items-start gap-2">
          <span className="min-w-0 flex-1 text-xs text-danger">
            {pk('search.failed', { name: lookup.query })}
            {message ? ` ${message}` : ''}
          </span>
          {dismiss}
        </div>
        {lookup.code === 'package_lookup_not_found' && (
          <p className="text-xs leading-relaxed text-ink-3">{pk('search.exactOnly')}</p>
        )}
      </div>
    )
  }

  const options = [
    { value: LATEST, label: pk('search.latestOption', { version: found.latest }) },
    ...found.versions.map((v) => ({ value: v, label: v })),
  ]
  // 选最新版 → 交给 pip **裸包名**：安装 argv 带 `--only-binary=:all:`，钉死一个
  // 只有 sdist 的版本会当场失败，裸名字让 pip 挑最新的、有轮子的那一版。
  const spec = version === LATEST ? found.name : `${found.name}==${version}`

  return (
    <div
      data-packages-lookup-panel="found"
      className="flex flex-col gap-1.5 rounded-md border border-border p-2.5"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-start gap-2">
        <span className="min-w-0 flex-1">
          <span className="font-mono text-xs text-ink">{found.name}</span>
          <span className="ml-2 text-xs text-ink-2">
            {pk('search.latest', { version: found.latest })}
          </span>
          <span className="block text-[11px] text-ink-3">{pk(`search.source.${found.source}`)}</span>
          {found.installed && (
            <span className="block text-[11px] text-ink-3">
              {pk('search.alreadyInstalled', { version: found.installed })}
            </span>
          )}
        </span>
        {dismiss}
      </div>
      <div className="flex items-center gap-1.5">
        <Select
          value={version}
          onChange={setVersion}
          options={options}
          ariaLabel={pk('search.versionAria', { name: found.name })}
          className="w-56"
        />
        <Button
          variant="primary"
          size="sm"
          disabled={locked}
          data-packages-lookup-install
          onClick={() => {
            void onInstall('install', spec).then((ok) => {
              if (ok) clearLookup()
            })
          }}
        >
          {pk('search.installHere')}
        </Button>
      </div>
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
