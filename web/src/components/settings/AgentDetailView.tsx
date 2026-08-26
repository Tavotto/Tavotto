import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft } from 'lucide-react'
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
import { Dialog } from '../ui/Dialog'
import { TextInput } from '../ui/Input'
import { AgentIcon } from './AgentIcon'
import { ag, AgentStateBadge } from './agentState'
import { EndpointDialog } from './EndpointDialog'

/** 概览里的一行「标签 / 值」 */
const Field = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div className="flex min-h-6 items-baseline gap-2">
    <span className="w-20 shrink-0 text-xs text-ink-3">{label}</span>
    <span className="min-w-0 flex-1 break-all text-xs text-ink-2">{children}</span>
  </div>
)

const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <section className="flex flex-col gap-1.5">
    <h4 className="text-xs font-medium text-ink-2">{title}</h4>
    {children}
  </section>
)

/** `<details>` 折叠块：高级设置与诊断默认收起，一级页面不制造噪音 */
const Fold = ({ summary, children }: { summary: string; children: React.ReactNode }) => (
  <details className="rounded-sm border border-border bg-surface px-2 py-1.5">
    <summary className="cursor-default text-xs text-ink-2 outline-none focus-visible:focus-ring">
      {summary}
    </summary>
    <div className="mt-1.5 flex flex-col gap-1.5">{children}</div>
  </details>
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
  const usingEndpoint = !!agent.active_endpoint_id

  return (
    <div className="flex flex-col gap-3">
      <div>
        {/* aria-label 与左侧导航的同名项区分开：读屏里两个「编码 Agent」
            听不出差别，用例也选不中正确的那个 */}
        <Button variant="ghost" size="sm" aria-label={ag('backAria')} onClick={onBack}>
          <ArrowLeft size={12} aria-hidden />
          {ag('backToList')}
        </Button>
      </div>

      <header className="flex items-center gap-3">
        <AgentIcon iconKey={agent.icon_key} size={40} />
        <div className="min-w-0">
          <h3 className="text-sm font-medium text-ink">{agent.display_name}</h3>
          <AgentStateBadge state={agent.state} className="mt-0.5" />
        </div>
      </header>

      {/* ---------------- 概览 ---------------- */}
      <Section title={ag('detail.overview')}>
        <div className="rounded-sm border border-border bg-surface p-2">
          <Field label={ag('detail.state')}>
            <AgentStateBadge state={agent.state} />
          </Field>
          <Field label={ag('detail.version')}>
            <span className="font-mono">{agent.version ?? ag('detail.none')}</span>
          </Field>
          <Field label={ag('detail.executable')}>
            <span className="font-mono" title={agent.executable_path ?? undefined}>
              {agent.executable_path ?? ag('detail.none')}
            </span>
          </Field>
          <Field label={ag('detail.source')}>
            {agent.detection_source
              ? ag(`source.${agent.detection_source}`, {
                  defaultValue: agent.detection_source,
                })
              : ag('detail.none')}
          </Field>
          <Field label={ag('detail.checkedAt')}>
            {caps.checked_at_ms ? formatDateTime(caps.checked_at_ms) : ag('detail.none')}
          </Field>
        </div>
        <div>
          {/* 直接调 onRefreshed（父级会强制重探测）；套一层 run() 会让它
              跑两遍——每一遍都是两个真子进程 */}
          <Button variant="outline" size="sm" loading={busy} onClick={() => void run(onRefreshed)}>
            {ag('rescan')}
          </Button>
        </div>
      </Section>

      {/* ---------------- 一键安装（没装才给） ---------------- */}
      {!agent.installed && agent.install && (
        <Section title={ag('detail.install')}>
          <InstallPanel agent={agent} onRefreshed={onRefreshed} />
        </Section>
      )}

      {/* ---------------- 登录与模型 ---------------- */}
      {agent.features.third_party_endpoints && (
        <Section title={ag('detail.loginAndModels')}>
          <fieldset className="flex flex-col gap-1 rounded-sm border border-border bg-surface p-2">
            <legend className="px-1 text-xs text-ink-3">{ag('detail.modelService')}</legend>
            <label className="flex items-center gap-1.5 text-xs text-ink-2">
              <input
                type="radio"
                name={`endpoint-${agent.id}`}
                checked={!usingEndpoint}
                onChange={() => void run(() => setAiEndpointActive(agent.id, ''))}
                className="accent-accent"
              />
              {ag('detail.useAgentLogin', { name: agent.display_name })}
            </label>
            <label className="flex items-center gap-1.5 text-xs text-ink-2">
              <input
                type="radio"
                name={`endpoint-${agent.id}`}
                checked={usingEndpoint}
                disabled={mine.length === 0}
                onChange={() => {
                  const first = mine[0]
                  if (first) void run(() => setAiEndpointActive(agent.id, first.id))
                }}
                className="accent-accent"
              />
              {ag('detail.useCustomService')}
            </label>
            {usingEndpoint && mine.length > 1 && (
              <label className="mt-1 flex items-center gap-2 text-xs text-ink-2">
                {ag('detail.service')}
                <select
                  value={agent.active_endpoint_id ?? ''}
                  onChange={(e) => void run(() => setAiEndpointActive(agent.id, e.target.value))}
                  aria-label={ag('detail.serviceAria', { name: agent.display_name })}
                  className="h-7 min-w-0 flex-1 rounded-sm border border-border bg-surface px-1.5 text-xs text-ink outline-none focus-visible:focus-ring"
                >
                  {mine.map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.label}
                      {e.has_key ? '' : ag('detail.noKeySuffix')}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {mine.length > 0 && (
              <ul className="mt-1 flex flex-col gap-0.5">
                {mine.map((e) => (
                  <li key={e.id} className="flex items-center gap-2">
                    <span
                      className="min-w-0 flex-1 truncate font-mono text-xs text-ink-3"
                      title={e.base_url}
                    >
                      {e.label} · {e.base_url || ag('detail.officialBaseUrl')}
                    </span>
                    <button
                      onClick={() => setEditing({ id: e.id })}
                      className="shrink-0 text-xs text-ink-3 outline-none hover:text-ink focus-visible:focus-ring"
                    >
                      {ag('detail.edit')}
                    </button>
                    <button
                      onClick={() => void run(() => deleteAiEndpoint(e.id))}
                      className="shrink-0 text-xs text-ink-3 outline-none hover:text-danger focus-visible:focus-ring"
                    >
                      {ag('detail.delete')}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-1">
              <Button variant="outline" size="sm" onClick={() => setEditing({})}>
                {ag('detail.addEndpoint')}
              </Button>
            </div>
          </fieldset>
        </Section>
      )}

      {/* ---------------- 高级设置 ---------------- */}
      <Section title={ag('detail.advanced')}>
        <Fold summary={ag('detail.customExecutable')}>
          <CustomExecutable agent={agent} onRefreshed={onRefreshed} />
        </Fold>
        <Fold summary={ag('detail.diagnostics')}>
          <Diagnostics agent={agent} />
        </Fold>
      </Section>

      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}

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
            variant="outline"
            size="sm"
            onClick={() => setDraft(agent.path_override ?? '')}
          >
            {ag('detail.useCustomExecutable')}
          </Button>
          {agent.path_override && (
            <Button
              variant="outline"
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
          <label className="flex items-center gap-2">
            <span className="w-20 shrink-0 text-xs text-ink-2">{ag('detail.customPath')}</span>
            <TextInput
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={ag('detail.pathPlaceholder')}
              className="flex-1 font-mono"
              spellCheck={false}
            />
          </label>
          <div className="flex items-center gap-1.5">
            <Button
              variant="outline"
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
  return (
    <div className="flex flex-col gap-1.5">
      <Field label={ag('detail.readiness')}>
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
        <details>
          <summary className="cursor-default text-xs text-ink-3 outline-none focus-visible:focus-ring">
            {ag('install.log')}
          </summary>
          <pre className="mt-0.5 max-h-40 overflow-auto whitespace-pre-wrap rounded-sm border border-border bg-surface p-1.5 font-mono text-xs text-ink-3">
            {live.log}
          </pre>
        </details>
      )}
      {confirming && (
        <Dialog
          open
          onOpenChange={(v) => !v && setConfirming(false)}
          title={ag('install.confirmTitle', { name: agent.display_name })}
          size="sm"
          footer={
            <>
              <Button variant="outline" size="md" onClick={() => setConfirming(false)}>
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
