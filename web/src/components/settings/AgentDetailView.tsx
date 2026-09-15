import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Plus, RefreshCw, TriangleAlert } from '@/components/ui/icons'
import { Details, Summary } from '@/components/ui/Details'
import { DiagnosticDisclosure, DiagnosticItem, SettingSection } from './SettingRow'
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
import { t as translate } from '@/i18n'
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
 * 这一页曾经自带三套只此一页的原语（全面打磨 D05）：分区标题 11/500/ink-2、
 * 字段行 96px 标签 + 11px 值、`<details>` 折叠头 36 高 + hairline。同一屏上于是
 * 出现 15 / 12 / 11 三档标题，与别的设置页读不成同一个产品。现在三样都换成设置页
 * 共用的那份——`SettingSection`（标题 type-section，动作在标题行右侧）、
 * `DiagnosticItem`（名左 / 值右）、`DiagnosticDisclosure`（28 高 chevron 折叠头，
 * 摘要值放在 `action` 槽里）。
 *
 * e2e 的稳定锚点 `data-agent-field` / `data-agent-fold` 原样保留：它们指的是
 * 「哪个字段 / 哪个折叠区」，不是「用哪个组件画的」。
 */
/** 折叠头右侧那截摘要值（当前来源 / 有没有设过），收起时也看得见 */
const FoldValue = ({ value }: { value?: string }) =>
  value ? <span className="type-meta max-w-[45%] truncate">{value}</span> : null

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
  // 正在就地确认删除的接口（2026-09-14 二审 A2）：这一行的「编辑 · 删除」换成
  // 「删除「x」？ [删除] [取消]」，Esc / 焦点离开 = 取消。此前一下就发 DELETE——
  // 密钥不回显，删错了没法找回；样式删除与终止会话都有一次确认，这里不该例外。
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  // Esc 只收起确认，不连设置窗口一起关：Radix 的 Dialog 在 document 的捕获阶段听 Esc，
  // 元素上的 onKeyDown 拦不住它；挂在 window 的捕获监听排在 document 之前，才拦得住。
  useEffect(() => {
    if (confirmDelete == null) return
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key !== 'Escape') return
      ev.preventDefault()
      ev.stopImmediatePropagation()
      setConfirmDelete(null)
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [confirmDelete])

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
    // `group`：行尾的「编辑 · 删除」指到这一行才浮出（全面打磨 D21）——它们此前常驻，
    // 一列服务行右边挂着一排重复的钮，比服务名本身还密
  const optionClass = (selected: boolean) =>
    `group flex min-h-7 items-center gap-3 rounded-sm px-2 py-1 ${selected ? 'bg-selected' : 'hover:bg-surface-hover'}`

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
          {/* 头部只留状态（全面打磨 D20）：「最近检测 …」此前在列表页首、这里、以及
              「概览」折叠里出现三次；它是排障材料，留在概览一处就够 */}
          <div className="mt-1 flex min-h-4 flex-wrap items-center gap-2 text-xs text-ink-3">
            <span data-agent-field="state" className="inline-flex">
              <AgentStateBadge state={agent.state} />
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
        <SettingSection title={ag('detail.install')} className="border-t border-border pt-4">
          <InstallPanel agent={agent} onRefreshed={onRefreshed} />
        </SettingSection>
      )}

      {/* ---------------- 模型服务 ---------------- */}
      {agent.features.third_party_endpoints && (
        <SettingSection
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
                  {confirmDelete === e.id ? (
                    <span
                      role="group"
                      aria-label={ag('detail.deleteConfirm', { label: e.label })}
                      data-endpoint-delete-confirm={e.id}
                      className="flex shrink-0 items-center gap-1"
                      onBlur={(ev) => {
                        // 焦点离开整组（不是在两颗钮之间移动）就当作取消
                        if (!ev.currentTarget.contains(ev.relatedTarget as Node | null))
                          setConfirmDelete(null)
                      }}
                    >
                      <span className="text-xs text-ink-2">
                        {ag('detail.deleteConfirm', { label: e.label })}
                      </span>
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => {
                          setConfirmDelete(null)
                          void run(() => deleteAiEndpoint(e.id))
                        }}
                      >
                        {ag('detail.delete')}
                      </Button>
                      {/* 焦点落在「取消」：Enter 是安全的那一边（危险操作的默认键） */}
                      <Button autoFocus variant="secondary" size="sm" onClick={() => setConfirmDelete(null)}>
                        {translate('actions.cancel')}
                      </Button>
                    </span>
                  ) : (
                    <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity duration-fast group-hover:opacity-100 group-focus-within:opacity-100">
                      <Button variant="ghost" size="sm" onClick={() => setEditing({ id: e.id })}>
                        {ag('detail.edit')}
                      </Button>
                      <Button variant="danger" size="sm" onClick={() => setConfirmDelete(e.id)}>
                        {ag('detail.delete')}
                      </Button>
                    </span>
                  )}
                </div>
              )
            })}
          </fieldset>
        </SettingSection>
      )}

      {/* ---------------- 高级设置 ---------------- */}
      <SettingSection title={ag('detail.advanced')} className="gap-1.5 border-t border-border pt-4">
        <DiagnosticDisclosure
          data-agent-fold="overview"
          title={ag('detail.overview')}
          action={<FoldValue value={sourceLabel} />}
        >
          <DiagnosticItem
            data-agent-field="executable"
            name={ag('detail.executable')}
            value={
              <span className="flex min-w-0 items-start justify-end gap-1">
                <span className="min-w-0 break-all" title={agent.executable_path ?? undefined}>
                  {agent.executable_path ?? ag('detail.none')}
                </span>
                {agent.executable_path && (
                  <CopyButton
                    text={agent.executable_path}
                    label={ag('detail.copyPath')}
                    appearance="icon"
                  />
                )}
              </span>
            }
          />
          <DiagnosticItem data-agent-field="source" name={ag('detail.source')} value={sourceLabel} />
          <DiagnosticItem
            data-agent-field="checked-at"
            name={ag('detail.checkedAt')}
            value={caps.checked_at_ms ? formatDateTime(caps.checked_at_ms) : ag('detail.none')}
          />
        </DiagnosticDisclosure>
        <DiagnosticDisclosure
          data-agent-fold="custom-executable"
          title={ag('detail.customExecutable')}
          action={
            <FoldValue
              value={agent.path_override ? ag('detail.currentOverride') : ag('detail.autoDetected')}
            />
          }
        >
          <CustomExecutable agent={agent} onRefreshed={onRefreshed} />
        </DiagnosticDisclosure>
        <DiagnosticDisclosure data-agent-fold="diagnostics" title={ag('detail.diagnostics')}>
          <Diagnostics agent={agent} />
        </DiagnosticDisclosure>
      </SettingSection>

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
      <DiagnosticItem
        data-agent-field="readiness"
        name={ag('detail.readiness')}
        value={
          <>
            {ag(`readiness.${d.readiness}`)}
            {d.readiness_detail ? <span className="ml-1">{d.readiness_detail}</span> : null}
          </>
        }
      />
      {d.broken_path && (
        <div className="min-w-0">
          <p className="text-xs text-ink-3">{ag('detail.brokenCandidate')}</p>
          <p className="truncate font-mono text-xs text-ink-3" title={d.broken_path}>
            {d.broken_path}
          </p>
        </div>
      )}
      {d.searched.length > 0 && (
        <div className="min-w-0">
          <p className="text-xs text-ink-3">{ag('detail.searched')}</p>
          <ul className="mt-0.5 flex min-w-0 flex-col gap-0.5">
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
        {/* 安装是这一段的动作，不是一行字（全面打磨 D19）：不传 variant 时 Button 是
            ghost——无边无底的「安装 Codex」旁边跟着一段 mono 命令与一颗复制钮，四样
            东西同权重，看不出哪个是该点的 */}
        <Button
          variant="secondary"
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
