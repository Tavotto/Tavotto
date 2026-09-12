import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Plus, RefreshCw, TriangleAlert } from 'lucide-react'
import { Details, Summary } from '@/components/ui/Details'
import { ICON_SIZE } from '@/components/ui/Icon'
import {
  backendErrorText,
  deleteAiEndpoint,
  fetchAiInstallStatus,
  patchAiAgent,
  saveAiEndpoint,
  setAiEndpointActive,
  startAiInstall,
  type AiAgentCaps,
  type AiCapabilities,
  type AiInstallState,
} from '@/lib/api'
import { formatDateTime } from '@/i18n/format'
import { Button } from '../ui/Button'
import { Radio } from '../ui/Radio'
import { Dialog } from '../ui/Dialog'
import { TextInput } from '../ui/Input'
import { AgentIcon } from './AgentIcon'
import { ag, AgentStateBadge, agentVersionLabel } from './agentState'
import { CopyButton } from './CopyButton'
import { EndpointDialog } from './EndpointDialog'

/**
 * 概览里的一行「标签 / 值」。
 *
 * `name` 是 e2e 的稳定锚点（`data-agent-field`）：`label` 是会被文案改动
 * 重写的那句话，拿它定位等于每改一次文案就重新下一次赌注。
 */
const Field = ({
  name,
  label,
  children,
}: {
  name: string
  label: string
  children: React.ReactNode
}) => (
  <div data-agent-field={name} className="flex min-h-6 items-baseline gap-3">
    <span className="w-24 shrink-0 text-xs text-ink-3">{label}</span>
    <span className="min-w-0 flex-1 break-all text-xs text-ink-2">{children}</span>
  </div>
)

/** 分区：标题行左标题、右可选动作（如「添加服务」），下面是内容 */
const Section = ({
  title,
  action,
  className,
  children,
}: {
  title: string
  action?: React.ReactNode
  className?: string
  children: React.ReactNode
}) => (
  <section className={`flex flex-col gap-2 ${className ?? ''}`}>
    <div className="flex min-h-7 items-center justify-between gap-3">
      <h4 className="text-xs font-medium text-ink-2">{title}</h4>
      {action}
    </div>
    {children}
  </section>
)

/** `<details>` 折叠块：高级设置与诊断默认收起，一级页面不制造噪音 */
const Fold = ({
  name,
  summary,
  value,
  children,
}: {
  name: string
  summary: string
  /** 收起时显示在右侧的摘要值（如当前来源 / 是否已设置） */
  value?: string
  children: React.ReactNode
}) => (
  <Details data-agent-fold={name} className="border-b border-border last:border-b-0">
    <Summary className="flex min-h-9 cursor-default items-center gap-2.5 py-2 text-sm text-ink-2 hover:text-ink">
      <span className="min-w-0 flex-1">{summary}</span>
      {value ? <span className="max-w-[45%] truncate text-xs text-ink-3">{value}</span> : null}
    </Summary>
    <div className="flex flex-col gap-2 pb-3">{children}</div>
  </Details>
)

/**
 * 单个编码 Agent 的详情。
 *
 * 设置内容区里的**子页面**，不是第二层大模态框——设置本身已经是一个对话框，
 * 再叠一层的结果是两条 Esc 路径、两个焦点陷阱和一个越来越小的可视区。
 *
 * 版面顺序按「用得到的频率」排：先说清它现在什么状态，再是登录与模型，
 * 最后才是高级设置（自定义可执行文件 / 诊断）。手动填路径与第三方接口都在
 * 这一层，一级列表上一个输入框都没有。
 */
export function AgentDetailView({
  agent,
  caps,
  onBack,
  onRefreshed,
}: {
  agent: AiAgentCaps
  caps: AiCapabilities
  onBack: () => void
  /** 任何改动之后重新拉能力（父级负责 loadCaps） */
  onRefreshed: (next?: AiCapabilities) => Promise<void> | void
}) {
  useTranslation('dialogs')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<null | { id?: string }>(null)

  /** 跑一个改动，然后重新拉能力。`fn` 自己就是重探测时不再多跑一次。 */
  const run = async (fn: () => Promise<AiCapabilities | unknown> | void) => {
    setError(null)
    setBusy(true)
    try {
      const done = fn()
      await done
      if (fn !== onRefreshed) await onRefreshed()
    } catch (e) {
      setError(backendErrorText(e))
    } finally {
      setBusy(false)
    }
  }

  const mine = (caps.endpoints ?? []).filter((e) => e.agent === agent.id)
  const activeId = agent.active_endpoint_id ?? ''
  const usingEndpoint = !!agent.active_endpoint_id
  const radioName = `endpoint-${agent.id}`
  const sourceLabel = agent.detection_source
    ? ag(`source.${agent.detection_source}`, { defaultValue: agent.detection_source })
    : ag('detail.none')
  /** 模型服务的一行：选中 = selected 轻 tint（第五节的三档），未选中只在 hover 时浮出 surface-hover */
  const optionClass = (selected: boolean) =>
    `flex min-h-7 items-center gap-3 rounded-sm px-2 py-1 ${selected ? 'bg-selected' : 'hover:bg-surface-hover'}`

  return (
    <div data-agent-detail={agent.id} className="flex flex-col gap-5">
      <div>
        {/* aria-label 与左侧导航的同名项区分开：读屏里两个「编码 Agent」
            听不出差别，用例也选不中正确的那个 */}
        <Button
          data-agent-back
          variant="ghost"
          size="sm"
          aria-label={ag('backAria')}
          onClick={onBack}
        >
          <ArrowLeft size={ICON_SIZE.sm} aria-hidden />
          {ag('backToList')}
        </Button>
      </div>

      {/* ---------------- 头部：身份 + 状态 + 重新检测 ---------------- */}
      <header className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3">
        <AgentIcon iconKey={agent.icon_key} size={36} />
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
            <h3 className="type-title">{agent.display_name}</h3>
            {/* 说版本号，不说内部包名（`codex-cli 0.151.0` 的前半截不是用户
                要认的东西，ADR 0038）。抽不出数字时才回原文——那时原文本身就是
                诊断材料。`--version` 的完整原话在下面的「诊断信息」里。 */}
            <span data-agent-field="version" className="break-all font-mono text-xs text-ink-3">
              {agentVersionLabel(agent.version) ?? agent.version ?? ag('detail.none')}
            </span>
          </div>
          <div className="mt-1 flex min-h-4 flex-wrap items-center gap-2 text-xs text-ink-3">
            <span data-agent-field="state" className="inline-flex">
              <AgentStateBadge state={agent.state} />
            </span>
            <span aria-hidden className="text-ink-faint">·</span>
            <span>
              {caps.checked_at_ms
                ? ag('lastChecked', { time: formatDateTime(caps.checked_at_ms) })
                : ag('detail.none')}
            </span>
          </div>
        </div>
        {/* 直接调 onRefreshed（父级会强制重探测）；套一层 run() 会让它
            跑两遍——每一遍都是两个真子进程 */}
        <Button
          data-agent-rescan
          variant="secondary"
          size="sm"
          loading={busy}
          onClick={() => void run(onRefreshed)}
        >
          <RefreshCw size={ICON_SIZE.sm} aria-hidden />
          {ag('rescan')}
        </Button>
      </header>

      {error && (
        <p role="alert" className="flex items-start gap-1.5 text-xs text-danger">
          <TriangleAlert size={ICON_SIZE.sm} aria-hidden className="mt-px shrink-0" />
          <span className="min-w-0 flex-1">{error}</span>
        </p>
      )}

      {/* ---------------- 一键安装（没装才给） ---------------- */}
      {!agent.installed && agent.install && (
        <Section title={ag('detail.install')} className="border-t border-border pt-4">
          <InstallPanel agent={agent} onRefreshed={onRefreshed} />
        </Section>
      )}

      {/* ---------------- 模型服务 ---------------- */}
      {agent.features.third_party_endpoints && (
        <Section
          title={ag('detail.modelService')}
          action={
            <Button variant="ghost" size="sm" onClick={() => setEditing({})}>
              <Plus size={ICON_SIZE.sm} aria-hidden />
              {ag('detail.addEndpoint')}
            </Button>
          }
        >
          <fieldset className="flex min-w-0 flex-col gap-0.5">
            <legend className="sr-only">
              {ag('detail.serviceAria', { name: agent.display_name })}
            </legend>
            <label className={optionClass(!usingEndpoint)}>
              <Radio
                name={radioName}
                checked={!usingEndpoint}
                onChange={() => void run(() => setAiEndpointActive(agent.id, ''))}
              />
              <span className="min-w-0 flex-1 text-sm text-ink">
                {ag('detail.useAgentLogin', { name: agent.display_name })}
              </span>
            </label>
            {mine.map((e) => {
              const selected = activeId === e.id
              return (
                <div key={e.id} className={optionClass(selected)}>
                  <label className="flex min-w-0 flex-1 items-center gap-3">
                    <Radio
                      name={radioName}
                      checked={selected}
                      onChange={() => void run(() => setAiEndpointActive(agent.id, e.id))}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm text-ink">{e.label}</span>
                      <span className="mt-0.5 flex min-w-0 items-baseline gap-1.5 text-xs text-ink-3">
                        <span className="min-w-0 truncate font-mono" title={e.base_url || undefined}>
                          {e.base_url || ag('detail.officialBaseUrl')}
                        </span>
                        {!e.has_key && (
                          <span className="shrink-0 text-warn">{ag('detail.noKeySuffix')}</span>
                        )}
                      </span>
                    </span>
                  </label>
                  <span className="flex shrink-0 items-center gap-0.5">
                    <Button variant="ghost" size="sm" onClick={() => setEditing({ id: e.id })}>
                      {ag('detail.edit')}
                    </Button>
                    <Button variant="danger" size="sm" onClick={() => void run(() => deleteAiEndpoint(e.id))}>
                      {ag('detail.delete')}
                    </Button>
                  </span>
                </div>
              )
            })}
          </fieldset>
        </Section>
      )}

      {/* ---------------- 高级设置 ---------------- */}
      <Section title={ag('detail.advanced')} className="border-t border-border pt-4">
        <div className="flex flex-col">
          <Fold name="overview" summary={ag('detail.overview')} value={sourceLabel}>
            <Field name="executable" label={ag('detail.executable')}>
              <span className="flex min-w-0 items-start gap-1">
                <span className="min-w-0 flex-1 break-all font-mono" title={agent.executable_path ?? undefined}>
                  {agent.executable_path ?? ag('detail.none')}
                </span>
                {agent.executable_path && (
                  <CopyButton text={agent.executable_path} label={ag('detail.copyPath')} />
                )}
              </span>
            </Field>
            <Field name="source" label={ag('detail.source')}>
              {sourceLabel}
            </Field>
            <Field name="checked-at" label={ag('detail.checkedAt')}>
              {caps.checked_at_ms ? formatDateTime(caps.checked_at_ms) : ag('detail.none')}
            </Field>
          </Fold>
          <Fold
            name="custom-executable"
            summary={ag('detail.customExecutable')}
            value={agent.path_override ? ag('detail.currentOverride') : ag('detail.autoDetected')}
          >
            <CustomExecutable agent={agent} onRefreshed={onRefreshed} />
          </Fold>
          <Fold name="diagnostics" summary={ag('detail.diagnostics')}>
            <Diagnostics agent={agent} />
          </Fold>
        </div>
      </Section>

      {editing && (
        <EndpointDialog
          agent={agent.id}
          agentLabel={agent.display_name}
          wireApi={agent.features.wire_api_selection}
          existing={caps.endpoints.find((e) => e.id === editing.id) ?? null}
          presets={(caps.presets ?? []).filter((p) => p.agent === agent.id)}
          onClose={() => setEditing(null)}
          onSave={(rec) => {
            setEditing(null)
            void run(() => saveAiEndpoint(rec))
          }}
        />
      )}
    </div>
  )
}

/**
 * 自定义可执行文件。
 *
 * **显式「验证并保存」**，不再靠失焦提交：打开设置再移走一次焦点就把用户存好
 * 的路径以「改成了空」的名义清掉（issue #89）。保存失败时草稿留着、后端那份
 * 有效设置一个字节没动；「恢复自动检测」同样是一次明确的点击。
 */
function CustomExecutable({
  agent,
  onRefreshed,
}: {
  agent: AiAgentCaps
  onRefreshed: (next?: AiCapabilities) => Promise<void> | void
}) {
  useTranslation('dialogs')
  // null = 没在编辑（显示当前值）；字符串 = 正在编辑的草稿
  const [draft, setDraft] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  /**
   * 提交一次改动。`submitted` 是**发起这次提交时草稿的原文**：
   * 保存 + 重探测要跑上几秒，期间用户完全可能已经接着改了。无条件
   * `setDraft(null)` 会把更新的草稿顶掉，所以只在草稿仍是这一次提交的那份
   * 时才收起编辑态（与 issue #89 那条「在途提交不顶掉新编辑」同一条纪律）。
   */
  const submit = async (value: string, submitted: string | null) => {
    setBusy(true)
    setError(null)
    try {
      await patchAiAgent(agent.id, { path_override: value })
      await onRefreshed()
      setDraft((cur) => (cur === submitted ? null : cur))
    } catch (e) {
      setError(backendErrorText(e))     // 失败保留正在编辑的值
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div>
        <p className="text-xs text-ink-3">
          {agent.path_override ? ag('detail.currentOverride') : ag('detail.autoDetected')}
        </p>
        <p
          className="truncate font-mono text-xs text-ink-2"
          title={agent.path_override ?? agent.executable_path ?? undefined}
        >
          {agent.path_override ?? agent.executable_path ?? ag('detail.none')}
        </p>
      </div>

      {draft === null ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            data-agent-custom-exe
            variant="secondary"
            size="sm"
            onClick={() => setDraft(agent.path_override ?? '')}
          >
            {ag('detail.useCustomExecutable')}
          </Button>
          {agent.path_override && (
            <Button
              variant="secondary"
              size="sm"
              loading={busy}
              onClick={() => void submit('', null)}
            >
              {ag('detail.returnToAuto')}
            </Button>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <label className="flex flex-col gap-1.5">
            <span className="text-xs text-ink-2">{ag('detail.customPath')}</span>
            <TextInput
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={ag('detail.pathPlaceholder')}
              className="w-full font-mono"
              spellCheck={false}
            />
          </label>
          <div className="flex items-center gap-1.5">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setDraft(null)
                setError(null)
              }}
            >
              {ag('detail.cancel')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={busy}
              disabled={!draft.trim()}
              onClick={() => void submit(draft.trim(), draft)}
            >
              {ag('detail.validateAndSave')}
            </Button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  )
}

/** 诊断折叠区：找过哪儿、第一个坏候选、就绪检查的结论。 */
function Diagnostics({ agent }: { agent: AiAgentCaps }) {
  useTranslation('dialogs')
  const d = agent.diagnostics
  /** 诊断文本（复制用）：状态 / 版本 / 路径 / 来源 / 就绪 / 找过的位置——不含账号信息 */
  const asText = () =>
    [
      `agent: ${agent.id}`,
      `state: ${agent.state}`,
      `version: ${agent.version ?? ''}`,
      `executable: ${agent.executable_path ?? ''}`,
      `source: ${agent.detection_source ?? ''}`,
      `readiness: ${d.readiness}${d.readiness_detail ? ` (${d.readiness_detail})` : ''}`,
      d.broken_path ? `broken_candidate: ${d.broken_path}` : '',
      ...d.searched.map((p) => `searched: ${p}`),
    ]
      .filter(Boolean)
      .join('\n')
  return (
    <div className="flex flex-col gap-1.5">
      <Field name="readiness" label={ag('detail.readiness')}>
        {ag(`readiness.${d.readiness}`)}
        {d.readiness_detail ? (
          <span className="ml-1 font-mono text-ink-3">{d.readiness_detail}</span>
        ) : null}
      </Field>
      {d.broken_path && (
        <div>
          <p className="text-xs text-ink-3">{ag('detail.brokenCandidate')}</p>
          <p className="truncate font-mono text-xs text-ink-3" title={d.broken_path}>
            {d.broken_path}
          </p>
        </div>
      )}
      {d.searched.length > 0 && (
        <div>
          <p className="text-xs text-ink-3">{ag('detail.searched')}</p>
          <ul className="mt-0.5 flex flex-col gap-0.5">
            {d.searched.map((p) => (
              <li key={p} className="truncate font-mono text-xs text-ink-3" title={p}>
                {p}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex items-center">
        <CopyButton text={asText} label={ag('detail.copyDiagnostics')} />
      </div>
    </div>
  )
}

/**
 * 一键安装：后台 `npm install -g <后端注册表写死的包名>`。
 *
 * 三条纪律：① 用户必须明确点，且**先看到将要运行的那条命令**；② 没有 npm
 * 时只引导去装 Node.js LTS，绝不代下载安装器；③ npm 说成了不算数——后端会
 * 重新真探测一次，起不来就如实说「装完还是不可用」。
 */
function InstallPanel({
  agent,
  onRefreshed,
}: {
  agent: AiAgentCaps
  onRefreshed: (next?: AiCapabilities) => Promise<void> | void
}) {
  useTranslation('dialogs')
  const info = agent.install
  const [state, setState] = useState<AiInstallState | null>(null)
  const [confirming, setConfirming] = useState(false)
  const live = state ?? (info && info.status !== 'idle' ? info : null)
  const running = live?.status === 'running'

  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const s = await fetchAiInstallStatus(agent.id)
          setState(s)
          if (s.status === 'done') await onRefreshed()
        } catch {
          /* 网络抖动：下一轮再问 */
        }
      })()
    }, 2000)
    return () => window.clearInterval(timer)
  }, [running, agent.id, onRefreshed])

  if (!info) return null
  const command = `npm install -g ${info.package ?? agent.id}`

  const begin = async () => {
    setConfirming(false)
    setState({ status: 'running' })
    try {
      setState(await startAiInstall(agent.id))
    } catch (e) {
      setState({ status: 'error', code: 'spawn_failed', log: String(e) })
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          loading={running}
          disabled={!info.available}
          onClick={() => setConfirming(true)}
        >
          {running ? ag('install.running') : ag('install.action', { name: agent.display_name })}
        </Button>
        <span className="min-w-0 truncate font-mono text-xs text-ink-3">{command}</span>
        <CopyButton text={command} label={ag('detail.copyCommand')} />
      </div>
      {!info.available && <p className="text-xs leading-relaxed text-ink-3">{ag('install.noNpm')}</p>}
      {live?.status === 'error' && (
        <p role="alert" className="text-xs text-danger">
          {ag(
            `install.error.${
              live.code === 'npm_missing' ||
              live.code === 'installed_but_not_found' ||
              live.code === 'timeout'
                ? live.code
                : 'other'
            }`,
          )}
        </p>
      )}
      {live?.log && (
        <Details>
          <Summary className="cursor-default text-xs text-ink-3">
            {ag('install.log')}
          </Summary>
          <pre className="mt-0.5 max-h-40 overflow-auto whitespace-pre-wrap rounded-sm border border-border bg-surface p-1.5 font-mono text-xs text-ink-3">
            {live.log}
          </pre>
        </Details>
      )}
      {confirming && (
        <Dialog
          open
          onOpenChange={(v) => !v && setConfirming(false)}
          title={ag('install.confirmTitle', { name: agent.display_name })}
          size="sm"
          footer={
            <>
              <Button variant="secondary" size="md" onClick={() => setConfirming(false)}>
                {ag('detail.cancel')}
              </Button>
              <Button variant="primary" size="md" onClick={() => void begin()}>
                {ag('install.confirmAction')}
              </Button>
            </>
          }
        >
          <div className="flex flex-col gap-2">
            <p className="text-xs leading-relaxed text-ink-2">{ag('install.confirmBody')}</p>
            <pre className="rounded-sm border border-border bg-surface-2 p-1.5 font-mono text-xs text-ink">
              {command}
            </pre>
          </div>
        </Dialog>
      )}
    </div>
  )
}
