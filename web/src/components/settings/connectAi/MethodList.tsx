import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyRound, Sparkles } from '@/components/ui/icons'
import { ICON_SIZE, IconSlot } from '@/components/ui/Icon'
import type { AiAgentCaps, AiAgentId, AiConnection, AiVendor, AiVendorMethod } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAiStore } from '@/store/aiStore'
import { StatusPill } from '../../ui/StatusPill'
import { OAuthLoginDialog } from '../OAuthLoginDialog'
import { useAgentStepAction } from './useAgentStepAction'
import { InstallActionButton } from './InstallActionButton'
import { agentStep } from './agentStep'
import { ConnectionManage } from './ConnectionManage'
import { cx } from './copy'
import { useLoginAfterInstall } from './useAgentInstall'
import { connectionErrorText, connectionForMethod, loginAgentOf, methodHint, methodName, vendorName } from './vendors'
import { refreshAndFind, useLoginFlow } from './useLoginFlow'
import { VendorKeyForm } from './VendorKeyForm'

/** 订阅登录方式背后的命令行 AI 还没装 / 打不开 / 被关了：这张卡不置灰，点开是「一句话 + 安装并登录」（应用内装，不要终端） */
function installableAgent(vendor: AiVendor, method: AiVendorMethod, agents: AiAgentCaps[] | undefined): AiAgentCaps | undefined {
  if (method.kind !== 'login' || method.available) return undefined
  const agent = agents?.find((a) => a.id === loginAgentOf(vendor, method))
  const action = agent ? agentStep(agent.state, agent.display_name).action : null
  return agent && action && action !== 'login' ? agent : undefined
}

/** 卡片第二行：可用 → 方式说明；命令行 AI 可在应用内装 / 重装 / 打开 → 说清「点这里就能装」；找不到 agent（老后端）才「需先安装」 */
function methodCaption(vendor: AiVendor, method: AiVendorMethod, agent: AiAgentCaps | undefined): string {
  if (method.available) return methodHint(vendor, method)
  if (!agent) return cx('needsInstall')
  const product = agent.display_name
  switch (agentStep(agent.state, product).action) {
    case 'install':
      return cx('step.installToEnable', { product })
    case 'reinstall':
      return cx('step.reinstallToEnable', { product })
    default:
      return cx('step.disabled')
  }
}

/** 选中某种方式之后摆在卡片下面的东西：Key 表单 / 设备码小对话框（订阅登录由 `LoginMethodRow` 自己摆） */
function MethodConnect({
  vendor,
  method,
  connection,
  onConnected,
  onCancel,
}: {
  vendor: AiVendor
  method: AiVendorMethod
  /** 这种方式已有的连接（error / needs_auth 的也算）：改 Key 要更新它，不是另建一条 */
  connection?: AiConnection
  onConnected: (c: AiConnection) => void
  onCancel: () => void
}) {
  if (method.kind === 'oauth') {
    return (
      <OAuthLoginDialog
        provider={vendor.logo}
        name={vendorName(vendor)}
        onClose={onCancel}
        onSignedIn={() => void refreshAndFind(vendor, method).then((r) => r.conn && onConnected(r.conn))}
      />
    )
  }
  return <VendorKeyForm vendor={vendor} method={method} connectionId={connection?.id} onConnected={onConnected} />
}

/** 登录按钮与提示摆在行下面；`begin` = 点开卡片就发起登录（装好后自动接登录时由行自己发起，不再来一次） */
function LoginConnect({ flow, begin }: { flow: ReturnType<typeof useLoginFlow>; begin: boolean }) {
  useEffect(() => {
    if (begin) flow.begin()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return (
    <div className="flex flex-col gap-1">
      <div>{flow.button}</div>
      {flow.hint}
    </div>
  )
}

interface RowProps {
  vendor: AiVendor
  method: AiVendorMethod
  connections: AiConnection[]
  onConnected: (c: AiConnection) => void
  picked: boolean
  onPick: (id: string | null) => void
}

const CARD_CLASS = 'flex items-center gap-3 rounded-lg border px-3 py-2.5 text-left'

/** 一张方式卡的内容（图标 + 方式名 + 一句说明 + 右侧槽），壳由调用方决定是可点的 button 还是容器 */
function MethodCardBody({ method, caption, right }: { method: AiVendorMethod; caption: string; right: ReactNode }) {
  const Icon = method.kind === 'key' ? KeyRound : Sparkles
  return (
    <>
      {/* 无框 16px，坐在名字（第一行，text-base）的中线上 */}
      <IconSlot className="self-start text-base text-ink-2">
        <Icon size={ICON_SIZE.md} aria-hidden />
      </IconSlot>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-base font-medium text-ink">{methodName(method)}</span>
        <span className="type-caption truncate">{caption}</span>
      </span>
      {right}
    </>
  )
}

function connPill(conn: AiConnection | undefined, ready: boolean) {
  return (
    conn && (
      <StatusPill tone={ready ? 'ok' : conn.state === 'needs_auth' ? 'warn' : 'danger'} dot data-connect-state={conn.state}>
        {ready ? cx('connected') : conn.state === 'needs_auth' ? cx('needsAuth') : connectionErrorText(conn)}
      </StatusPill>
    )
  )
}

/** Key / OAuth 方式的卡：点开展开表单（已连的 Key 类展开管理） */
function MethodRow({ vendor, method: m, connections, onConnected, picked: on, onPick }: RowProps) {
  const conn = connectionForMethod(connections, vendor, m)
  const ready = conn?.state === 'ready'
  const usable = m.available
  // 已连的非 Key 类（OAuth）没有可管理的东西（Change Key 对 OAuth 连接必 404）：卡片只显示状态，不展开
  const clickable = usable && !(ready && m.kind !== 'key')
  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        data-connect-method={m.id}
        data-connect-method-kind={m.kind}
        data-connect-method-state={ready ? 'ready' : (conn?.state ?? 'none')}
        aria-pressed={on}
        disabled={!clickable}
        onClick={() => onPick(on ? null : m.id)}
        className={cn(
          CARD_CLASS,
          'focus-visible:focus-ring outline-none transition-colors',
          on ? 'border-ink-3 bg-surface-hover' : 'border-border bg-surface hover:bg-surface-hover',
          !usable && 'cursor-not-allowed opacity-40 hover:bg-surface',
        )}
      >
        <MethodCardBody method={m} caption={methodCaption(vendor, m, undefined)} right={connPill(conn, ready)} />
      </button>
      {on &&
        (ready && conn ? (
          <ConnectionManage vendor={vendor} connection={conn} method={m} />
        ) : (
          <MethodConnect vendor={vendor} method={m} connection={conn} onConnected={onConnected} onCancel={() => onPick(null)} />
        ))}
    </div>
  )
}

/**
 * 订阅登录方式的卡。登录流程与「装好后自动接登录」都挂在这一行（常驻，不依赖安装按钮存活）：
 * 命令行 AI 可装 / 可重装 / 可打开且未连接时，壳是容器（不能在 button 里嵌按钮），动作按钮直接在右侧；
 * 其余状态与 Key 类卡一致（可点的 button / 已连接 / 置灰）。
 */
function LoginMethodRow({ vendor, method: m, connections, onConnected, picked: on, onPick }: RowProps) {
  const agents = useAiStore((st) => st.caps?.agents)
  const conn = connectionForMethod(connections, vendor, m)
  const ready = conn?.state === 'ready'
  const agentId = loginAgentOf(vendor, m) as AiAgentId
  const owner = `method:${vendor.id}:${m.id}`
  const flow = useLoginFlow(vendor, m, onConnected)
  const [autoLogin, setAutoLogin] = useState(false)
  useLoginAfterInstall(agentId, owner, () => {
    setAutoLogin(true)
    flow.begin()
  })
  const installAgent = installableAgent(vendor, m, agents)
  const act = useAgentStepAction(installAgent, agentId, owner)
  const showAction = !!installAgent && !ready
  const usable = m.available || !!installAgent
  // 已连的订阅类没有可管理的东西（只能在命令行里退出登录）：卡片只显示状态，不展开
  const clickable = usable && !ready
  const caption = methodCaption(vendor, m, installAgent)
  const dataAttrs = {
    'data-connect-method': m.id,
    'data-connect-method-kind': m.kind,
    'data-connect-method-state': ready ? 'ready' : (conn?.state ?? 'none'),
  }
  const body = (right: ReactNode) => <MethodCardBody method={m} caption={caption} right={right} />
  return (
    <div className="flex flex-col gap-2">
      {showAction && act.step?.action && act.step.action !== 'login' ? (
        <div {...dataAttrs} className={cn(CARD_CLASS, 'border-border bg-surface')}>
          {body(
            <InstallActionButton
              label={act.step.actionLabel ?? ''}
              running={act.running}
              progress={act.progress}
              onClick={act.onClick}
              action={act.step.action}
            />,
          )}
        </div>
      ) : (
        <button
          type="button"
          {...dataAttrs}
          aria-pressed={on}
          disabled={!clickable}
          onClick={() => onPick(on ? null : m.id)}
          className={cn(
            CARD_CLASS,
            'focus-visible:focus-ring outline-none transition-colors',
            on ? 'border-ink-3 bg-surface-hover' : 'border-border bg-surface hover:bg-surface-hover',
            !usable && 'cursor-not-allowed opacity-40 hover:bg-surface',
            usable && !clickable && 'cursor-default hover:bg-surface',
          )}
        >
          {body(connPill(conn, ready))}
        </button>
      )}
      {showAction && act.error && (
        <span role="alert" className="text-xs text-danger">
          {act.error}
        </span>
      )}
      {!ready && (on || autoLogin) && !showAction && <LoginConnect key={on ? 'pick' : 'auto'} flow={flow} begin={on && !autoLogin} />}
    </div>
  )
}

/**
 * 二级卡片：一家厂商有多种连接方式（API Key / 订阅登录 …）时，每种方式一张可点的选项卡
 * （图标 + 方式名 + 一句短说明）；不可用的置灰并给一句原因。
 * **状态落在每种方式上**：每张卡自己显示「已连接」或异常；点没连的卡，它下面展开该方式的表单 / 登录；
 * 点已连的 Key 类卡，展开管理（换 Key / 默认型号 / 断开）——所以一种连好了也不会堵住另一种的入口。
 */
export function MethodList({
  vendor,
  connections = [],
  onConnected,
}: {
  vendor: AiVendor
  connections?: AiConnection[]
  onConnected: (c: AiConnection) => void
}) {
  useTranslation('dialogs')
  const [picked, setPicked] = useState<string | null>(null)
  const methods = vendor.methods ?? []
  return (
    <div data-connect-methods={vendor.id} className="flex flex-col gap-2">
      {methods.map((m) =>
        m.kind === 'login' ? (
          <LoginMethodRow key={m.id} vendor={vendor} method={m} connections={connections} onConnected={onConnected} picked={picked === m.id} onPick={setPicked} />
        ) : (
          <MethodRow key={m.id} vendor={vendor} method={m} connections={connections} onConnected={onConnected} picked={picked === m.id} onPick={setPicked} />
        ),
      )}
    </div>
  )
}
