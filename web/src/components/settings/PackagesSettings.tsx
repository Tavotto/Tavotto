import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import { formatDateTime } from '@/i18n/format'
import type { PackageOp, PackageProgress, UserPackage } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { currentProjectId } from '@/lib/session'
import { cn } from '@/lib/utils'
import { repairCodeMessage } from '../DependencyRepairCard'
import { useDepRepairStore } from '@/store/depRepairStore'
import { isPackageJobRunning, searchTerm, usePackageStore } from '@/store/packageStore'
import { askConfirm } from '@/store/uiStore'
import { Ellipsis, RotateCcw, Trash2 } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Button, IconButton } from '../ui/Button'
import { FieldGroup, FormSection } from '../ui/FormSection'
import { Menu, MenuItem } from '../ui/Menu'
import { Notice } from '../ui/Notice'
import { StatusPill } from '../ui/StatusPill'
import { ProgressBar } from '../ui/ProgressBar'
import { TextInput } from '../ui/Input'
import { Select } from '../ui/Select'
import { CopyButton } from './CopyButton'
import { DiagnosticDisclosure, SettingRow } from './SettingRow'

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
  const { data, loading, loadError, busy, errorCode, errorText, load } = usePackageStore()
  // 只看**这个项目的**作业（issue #309）：作业按所属项目存，A 起的安装在 B 的页面上
  // 没有进度条、没有取消按钮；切回 A 时按 job_id 接上
  const progress = usePackageStore((s) => s.progressFor(currentProjectId()))
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
    // 分区之间的间距由外壳统一给（`display: contents`）；这一页是管理页，信息架构不动，
    // 只把字级 / 按钮 / 折叠区收到与别的分区同一套
    <div data-packages-page className="contents">
      {!available && capability && (
        <p className="type-caption" data-packages-disabled>
          {capability.reason === 'no_project'
            ? pk('disabled.noProject', { product: PRODUCT_NAME })
            : capability.reason === 'managed_env_unavailable'
              ? pk('disabled.noBasePython')
              : pk('disabled.other')}
        </p>
      )}
      {loadError && !data && <Notice tone="danger">{loadError}</Notice>}

      {/* ---------------- 环境（先说现状，再给动作；2026-09-13 审计 B39） ---------------- */}
      <EnvironmentSection />

      {/* ---------------- 用户安装（这一页存在的理由） ---------------- */}
      <FormSection title={pk('userTitle')} data-settings-anchor="packages.install">
        <FieldGroup>
          {/* 安装一行（2026-10-07 设计审计 §9.1）：输入框铺满，「在 PyPI 查找」次级在中间，「安装」主按钮贴右
              ——回车仍是安装，主动作在表单的收尾处 */}
          <form
            data-packages-form
            className="flex flex-col gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              void install()
            }}
          >
            <div className="flex items-center gap-1.5">
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
                className="min-w-0 flex-1 font-mono"
              />
              {/* 出网的动作只有这一颗按钮。`type="button"` 是要紧的：留成 submit
                  的话回车会同时触发安装与查找，而回车该只做主动作。 */}
              <Button
                type="button"
                variant="secondary"
                size="sm"
                data-packages-lookup
                disabled={locked || !term}
                onClick={() => void usePackageStore.getState().runLookup(spec)}
              >
                {pk('search.action')}
              </Button>
              <Button type="submit" variant="primary" size="sm" disabled={locked || !spec.trim()}>
                {pk('install')}
              </Button>
            </div>
            {specError && <Notice tone="danger">{specError}</Notice>}
          </form>
          {/* 作业进度 / 结果：它说的是刚点下去的那次安装 / 升级 / 卸载，就是安装框下面紧挨着的一行、
              包表上面——反馈出现在用户点的地方（2026-10-07 设计审计 P0）。此前排在整张包表之后，
              包一多，点了「安装」页面上什么都没发生，进度在屏幕外 */}
          <JobPanel progress={progress} errorCode={errorCode} errorText={errorText} />
          <LookupPanel locked={locked} onInstall={start} />
        </FieldGroup>

        <FieldGroup data-packages-user-table>
          <PackageTable
            ariaLabel={pk('userTitle')}
            empty={loading && !data ? pk('loading') : term ? noMatch : pk('userEmpty')}
            rows={shownUser.map((u) => ({
              key: u.distribution,
              name: (
                <span className="flex min-w-0 flex-col">
                  <span className="truncate text-ink">{u.distribution}</span>
                  <span className="type-caption truncate">
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
        </FieldGroup>
      </FormSection>

      {/* ---------------- 背景材料：内置包 · 技术详情（两条原地展开的行） ---------------- */}
      {/* 它们是同一类东西（背景材料），收进同一组（全面打磨 D17，L4；2026-10-07 起是组里的两行） */}
      <FormSection title={pk('backgroundTitle')}>
        <FieldGroup data-settings-anchor="packages.builtin">
          {/* 内置那一份折叠为摘要（审计 T46）：条数在行尾，展开才是清单。
              它是"这个环境里本来就有什么"的背景，不是用户此刻要操作的东西。 */}
          <DiagnosticDisclosure
            variant="row"
            title={pk('builtinTitle')}
            value={
              term
                ? pk('search.builtinCountMatch', {
                    count: shownBuiltin.length,
                    total: allBuiltin.length,
                  })
                : pk('builtinCountMeta', { count: allBuiltin.length })
            }
          >
            <p className="type-caption">
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
                actions: <span className="text-sm text-ink-3">{pk('readOnly')}</span>,
              }))}
            />
          </DiagnosticDisclosure>

          {/* 「没有回滚」与快照份数是工程细节——它们解释的是**为什么**只能重建，不是用户此刻要做的事 */}
          <DiagnosticDisclosure variant="row" title={pk('techTitle')}>
            <p className="type-caption">{pk('snapshotDetail', { count: data?.snapshots ?? 0 })}</p>
            <p className="type-caption">{pk('search.privacyDetail')}</p>
            {data?.network?.proxy && <p className="type-caption">{pk('network.proxy')}</p>}
            {data?.network?.custom_index && <p className="type-caption">{pk('network.customIndex')}</p>}
          </DiagnosticDisclosure>
        </FieldGroup>
      </FormSection>
    </div>
  )
}

/**
 * 页首的环境组：**这个项目的** Tavotto 环境现在什么状态、是不是正在用它、重建入口。
 *
 * 名字里的「这个项目的」不是修辞（审计 T46 / T47）：受管环境在
 * `<data_dir>/environments/<项目指纹>/`，**每个项目一个**；而诊断页上那句
 * 「{{product}} 自带的渲染环境」说的是随安装包附带、只读、不能 pip 的另一个。
 *
 * 形态是一行标准的 `SettingRow`（2026-09-13 审计 B39）：标题 + 现状（版本 · 在不在用）在标题列；控件列是一枚
 * `StatusPill`（就绪 / 未完成 / 尚未创建，2026-10-07 设计审计 §9.1：状态一律胶囊、带字）+ ⋯，「重建环境…」在 ⋯ 里
 * ——它是高影响、低频的动作，先确认、说清会删什么、重装什么；菜单项第二行就是那句说明。
 */
function EnvironmentSection() {
  useTranslation('dialogs')
  const env = usePackageStore((s) => s.data?.environment)
  const capability = usePackageStore((s) => s.data?.capability)
  // 本项目的重建还没结束时起不了第二次（别的项目的重建不挡这里，#606）
  const rebuildBusy = useDepRepairStore((s) => s.busy || s.rebuildRunningFor(currentProjectId()))
  const rebuildManaged = useDepRepairStore((s) => s.rebuildManaged)
  if (!capability || capability.reason === 'no_project') return null
  const exists = !!env?.exists
  const status = exists
    ? [pk('env.python', { version: env?.python_version || '?' }), env?.in_use ? pk('env.inUse') : pk('env.notInUse')].join(
        ' · ',
      )
    : pk('env.notCreated')
  const rebuild = async () => {
    const ok = await askConfirm({
      title: msg('settings.packages.confirm.rebuildTitle', undefined, 'dialogs'),
      body: msg('settings.packages.confirm.rebuildBody', undefined, 'dialogs'),
      confirmLabel: msg('settings.packages.confirm.rebuildAction', undefined, 'dialogs'),
      danger: true,
    })
    if (ok) await rebuildManaged()
  }
  return (
    <FormSection title={pk('envSection')}>
      <FieldGroup data-packages-env>
        <SettingRow
          label={pk('envTitle', { product: PRODUCT_NAME })}
          status={status}
          data-settings-anchor="packages.env"
        >
          {exists ? (
            <StatusPill tone={env?.state === 'ready' ? 'ok' : 'warn'} dot data-packages-env-state={env?.state}>
              {env?.state === 'ready' ? pk('env.ready') : pk('env.incomplete')}
            </StatusPill>
          ) : (
            <StatusPill tone="neutral" data-packages-env-state="missing">
              {pk('env.notCreatedShort')}
            </StatusPill>
          )}
          {exists && (
            <Menu
              align="end"
              width={260}
              trigger={
                <IconButton label={pk('env.actions')} iconSize="sm" data-packages-env-menu>
                  <Ellipsis size={ICON_SIZE.sm} aria-hidden />
                </IconButton>
              }
            >
              <MenuItem
                icon={RotateCcw}
                danger
                disabled={rebuildBusy}
                hint={pk('env.rebuildDesc')}
                onSelect={() => void rebuild()}
              >
                {pk('env.rebuild')}
              </MenuItem>
            </Menu>
          )}
        </SettingRow>
      </FieldGroup>
    </FormSection>
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
        className="text-sm text-ink-2"
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
        className="flex flex-col gap-1.5"
        role="status"
        aria-live="polite"
      >
        <div className="flex items-start gap-2">
          <span className="min-w-0 flex-1 text-sm text-danger-content">
            {pk('search.failed', { name: lookup.query })}
            {message ? ` ${message}` : ''}
          </span>
          {dismiss}
        </div>
        {lookup.code === 'package_lookup_not_found' && (
          <p className="type-caption">{pk('search.exactOnly')}</p>
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
      className="flex flex-col gap-1.5"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-start gap-2">
        <span className="min-w-0 flex-1">
          <span className="font-mono text-sm text-ink">{found.name}</span>
          <span className="ml-2 text-sm text-ink-2">
            {pk('search.latest', { version: found.latest })}
          </span>
          <span className="type-caption block">{pk(`search.source.${found.source}`)}</span>
          {found.installed && (
            <span className="type-caption block">
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
        ? 'text-danger-content'
        : status === 'changed'
          ? 'text-warn-content'
          : 'text-ink-3'
  return (
    <span className={cn('flex flex-col text-sm', tone)}>
      <span>{status ? pk(`status.${status}`) : pk('status.unknown')}</span>
      {detail && <span className="type-caption">{detail}</span>}
    </span>
  )
}

/**
 * 用户包那一行的动作（2026-10-07 设计审计 §9.1）：**一颗** ghost「升级」（缺失时是「重新安装」）+ ⋯（卸载，危险项）。
 * 此前每行两颗等重的 secondary 胶囊并排，最常点的与最危险的长得一样。内置 / 被保护的只读。
 */
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
  if (pkg.protected) return <span className="text-sm text-ink-3">{pk('protected')}</span>
  return (
    <span className="flex items-center justify-end gap-0.5">
      {pkg.status === 'missing' ? (
        <Button
          variant="ghost"
          size="sm"
          disabled={locked}
          onClick={() => void onAction('install', `${pkg.distribution}${pkg.requested_specifier}`)}
        >
          {pk('reinstall')}
        </Button>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          disabled={locked}
          aria-label={pk('updateAria', { name: pkg.distribution })}
          onClick={() => void onAction('update', pkg.distribution)}
        >
          {pk('update')}
        </Button>
      )}
      <Menu
        align="end"
        width={180}
        trigger={
          <IconButton
            label={pk('moreAria', { name: pkg.distribution })}
            iconSize="sm"
            disabled={locked}
            data-package-more={pkg.distribution}
          >
            <Ellipsis size={ICON_SIZE.sm} aria-hidden />
          </IconButton>
        }
      >
        <MenuItem
          icon={Trash2}
          danger
          data-package-uninstall={pkg.distribution}
          onSelect={() => void onAction('uninstall', pkg.distribution)}
        >
          {pk('uninstall')}
        </MenuItem>
      </Menu>
    </span>
  )
}

/**
 * 一张四列的小表：名称 / 版本 / 状态 / 操作。真 `<table>`，读屏能按列读。坐在 `FieldGroup` 里（组给底、圆角与
 * 12 / 16 的内边距——行线因此天然左右各内缩 16，与组里别的行同一条线），行 36px。
 */
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
  if (!rows.length) return <p className="type-caption">{empty}</p>
  const edge = 'first:pl-0 last:pr-0'
  return (
    <div className="overflow-x-auto">
      <table aria-label={ariaLabel} className="w-full table-fixed border-collapse text-sm">
        <thead>
          <tr className="text-left text-ink-3">
            <th scope="col" className={cn('h-8 w-[38%] px-2 align-top font-medium', edge)}>
              {pk('col.name')}
            </th>
            <th scope="col" className={cn('h-8 w-[17%] px-2 align-top font-medium', edge)}>
              {pk('col.version')}
            </th>
            <th scope="col" className={cn('h-8 w-[20%] px-2 align-top font-medium', edge)}>
              {pk('col.status')}
            </th>
            <th scope="col" className={cn('h-8 px-2 text-right align-top font-medium', edge)}>
              {pk('col.actions')}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-t border-border align-middle">
              <td className={cn('h-9 min-w-0 px-2 py-1', edge)}>{r.name}</td>
              <td className={cn('h-9 px-2 py-1 font-mono text-xs text-ink-3', edge)}>{r.version}</td>
              <td className={cn('h-9 px-2 py-1', edge)}>{r.status}</td>
              <td className={cn('h-9 px-2 py-1 text-right', edge)}>{r.actions}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * 作业一行：状态一句 + 不确定态进度条 + 取消；结束后是结果或错误（danger Notice）。
 * 日志折叠、可复制；错误文案来自 `repairCodeMessage`（给下一步，不是退出码）。
 * 它是安装组里紧跟安装框的一行（不再是一块 surface-2 的面板）：进度条的轨道是 `border` 档，坐在组底上也看得见。
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
  const dismissJob = usePackageStore((s) => s.dismissJob)
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
    <div data-packages-job className="flex flex-col gap-2" role="status" aria-live="polite">
      <div className="flex min-h-7 items-center gap-2">
        <span
          className={cn(
            'min-w-0 flex-1 text-sm',
            progress?.state === 'failed' ? 'text-danger-content' : running ? 'text-ink' : 'text-ink-2',
          )}
        >
          {stateText}
        </span>
        {running && (
          <Button variant="secondary" size="sm" onClick={() => void cancel()}>
            {pk('job.cancel')}
          </Button>
        )}
        {!running && (progress || failure) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              clearError()
              dismissJob()
            }}
          >
            {pk('job.dismiss')}
          </Button>
        )}
      </div>
      {/* 包管理器不报百分比：不确定态，来回扫（轨道 border 档） */}
      {running && <ProgressBar pct={null} label={pk('job.progressAria')} data-packages-progress />}
      {failure && (
        <Notice tone="danger">
          {failure}
          {errorText && repairCodeMessage(errorCode) && (
            <span className="ml-1 font-mono text-xs">{errorCode}</span>
          )}
        </Notice>
      )}
      {progress?.log && (
        <DiagnosticDisclosure
          title={pk('job.log')}
          action={<CopyButton text={progress.log} label={pk('job.copyLog')} />}
        >
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-ink-3">
            {progress.log}
          </pre>
        </DiagnosticDisclosure>
      )}
    </div>
  )
}
