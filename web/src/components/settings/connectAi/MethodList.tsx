import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyRound, Sparkles } from '@/components/ui/icons'
import { ICON_SIZE, IconSlot } from '@/components/ui/Icon'
import type { AiAgentCaps, AiAgentId, AiConnection, AiVendor, AiVendorMethod } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useAiStore } from '@/store/aiStore'
import { StatusPill } from '../../ui/StatusPill'
import { OAuthLoginDialog } from '../OAuthLoginDialog'
import { AgentStepAction } from './AgentStepAction'
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

/** 选中某种方式之后摆在卡片下面的东西：Key 表单 / 登录按钮与提示 / 设备码小对话框 */
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
  const flow = useLoginFlow(vendor, method, onConnected)
  // 点订阅登录这张卡 = 直接发起登录，不再多点一次「登录」；命令行 AI 还没装则先「安装并登录」，装好后自动接登录
  const { begin } = flow
  const agents = useAiStore((st) => st.caps?.agents)
  const toInstall = installableAgent(vendor, method, agents)
  const loginAgent = method.kind === 'login' ? loginAgentOf(vendor, method) : ''
  useLoginAfterInstall(loginAgent as AiAgentId, `method:${vendor.id}:${method.id}`, begin)
  useEffect(() => {
    if (method.kind === 'login' && !toInstall) begin()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  if (toInstall) {
    return (
      <div className="flex flex-col items-start gap-1">
        <AgentStepAction agent={toInstall} owner={`method:${vendor.id}:${method.id}`} />
      </div>
    )
  }
  if (method.kind === 'key') return <VendorKeyForm vendor={vendor} method={method} connectionId={connection?.id} onConnected={onConnected} />
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
  return (
    <div className="flex flex-col gap-1">
      <div>{flow.button}</div>
      {flow.hint}
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
  const agents = useAiStore((st) => st.caps?.agents)
  return (
    <div data-connect-methods={vendor.id} className="flex flex-col gap-2">
      {methods.map((m) => {
        const on = picked === m.id
        const Icon = m.kind === 'key' ? KeyRound : Sparkles
        const conn = connectionForMethod(connections, vendor, m)
        const ready = conn?.state === 'ready'
        // 已连的订阅类没有可管理的东西（只能在命令行里退出登录）：卡片只显示状态，不展开
        const installAgent = installableAgent(vendor, m, agents)
        const installable = !!installAgent
        const usable = m.available || installable
        const clickable = usable && !(ready && m.kind !== 'key')
        return (
          <div key={m.id} className="flex flex-col gap-2">
            <button
              type="button"
              data-connect-method={m.id}
              data-connect-method-kind={m.kind}
              data-connect-method-state={ready ? 'ready' : (conn?.state ?? 'none')}
              aria-pressed={on}
              disabled={!clickable}
              onClick={() => setPicked(on ? null : m.id)}
              className={cn(
                'focus-visible:focus-ring flex items-center gap-3 rounded-lg border px-3 py-2.5 text-left outline-none transition-colors',
                on ? 'border-ink-3 bg-surface-hover' : 'border-border bg-surface hover:bg-surface-hover',
                !usable && 'cursor-not-allowed opacity-40 hover:bg-surface',
                usable && !clickable && 'cursor-default hover:bg-surface',
              )}
            >
              {/* 无框 16px，坐在名字（第一行，text-base）的中线上 */}
              <IconSlot className="self-start text-base text-ink-2">
                <Icon size={ICON_SIZE.md} aria-hidden />
              </IconSlot>
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-base font-medium text-ink">{methodName(m)}</span>
                <span className="type-caption truncate">{methodCaption(vendor, m, installAgent)}</span>
              </span>
              {conn && (
                <StatusPill
                  tone={ready ? 'ok' : conn.state === 'needs_auth' ? 'warn' : 'danger'}
                  dot
                  data-connect-state={conn.state}
                >
                  {ready ? cx('connected') : conn.state === 'needs_auth' ? cx('needsAuth') : connectionErrorText(conn)}
                </StatusPill>
              )}
            </button>
            {on &&
              (ready && conn ? (
                <ConnectionManage vendor={vendor} connection={conn} method={m} />
              ) : (
                <MethodConnect vendor={vendor} method={m} connection={conn} onConnected={onConnected} onCancel={() => setPicked(null)} />
              ))}
          </div>
        )
      })}
    </div>
  )
}
