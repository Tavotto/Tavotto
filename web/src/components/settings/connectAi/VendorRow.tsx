import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronRight } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import type { AiAgentId, AiConnection, AiVendor } from '@/lib/api'
import { actionForReason, canCheckConnection, connActionLabel, isCheckableError, probeReasonText, type ConnAction } from '@/lib/connectionCheck'
import { cn } from '@/lib/utils'
import { useAiStore } from '@/store/aiStore'
import { requestConnectAi, requestEditConnection, runConnectionCheck } from '@/store/connectAiStore'
import { Button } from '../../ui/Button'
import { StatusPill } from '../../ui/StatusPill'
import { BrandLogo } from '../BrandLogo'
import { OAuthLoginDialog } from '../OAuthLoginDialog'
import { cx } from './copy'
import { MethodList } from './MethodList'
import { connectionErrorText, loginAgentOf, methodsOf, vendorName } from './vendors'
import { AgentStepAction } from './AgentStepAction'
import { useLoginAfterInstall } from './useAgentInstall'
import { refreshAndFind, useLoginFlow } from './useLoginFlow'
import { VendorKeyForm } from './VendorKeyForm'

/**
 * 「连接 AI」列表的一行（连接弹窗与设置页共用）：`[logo] 厂商名 ……… [状态 / 按钮]`。
 *
 * 只有一种连接方式的厂商：右侧一个动作，按方式的类型定——Key 类「连接」（就地展开 Key 表单）、订阅类「登录」
 * （沿用官方登录进度与「我已登录」）、账号登录类「登录」（设备码小对话框）。
 * 有多种方式（API Key + 订阅登录等）的厂商：「连接」打开二级卡片让用户自己选——弹窗里由 `onChooseMethods`
 * 换成二级页，设置页（没传它）就在行下展开同一份卡片。已连接显示「已连接」；设置页里已连接的 Key 类行可展开
 * （`manage`：换 Key / 默认型号 / 断开）。连好之后重读 capabilities，再调 `onConnected`。
 */
export function VendorRow({
  vendor,
  connection,
  connections,
  onConnected,
  manage,
  onChooseMethods,
  className,
}: {
  vendor: AiVendor
  connection?: AiConnection
  /** 这家厂商的全部连接（多方式厂商按方式各自看状态）；没给就只有 `connection` 那一条 */
  connections?: AiConnection[]
  onConnected: (c: AiConnection) => void
  /** 设置页：已连接时点行展开的管理区 */
  manage?: (c: AiConnection) => ReactNode
  /** 弹窗：多种方式时进二级页 */
  onChooseMethods?: (v: AiVendor) => void
  className?: string
}) {
  useTranslation('dialogs')
  const [open, setOpen] = useState(false)
  const [oauth, setOauth] = useState(false)
  const methods = methodsOf(vendor)
  const multi = methods.length > 1
  const method = methods[0]
  const flow = useLoginFlow(vendor, method, onConnected)
  useLoginAfterInstall(loginAgentOf(vendor, method) as AiAgentId, `row:${vendor.id}`, flow.begin)
  const loginAgent = useAiStore((st) => (method.kind === 'login' ? st.caps?.agents.find((a) => a.id === loginAgentOf(vendor, method)) : undefined))

  const all = connections ?? (connection ? [connection] : [])
  const anyReady = all.some((c) => c.state === 'ready')
  const ready = connection?.state === 'ready'
  const needsAuth = connection?.state === 'needs_auth'
  // 地址跑不了对话（endpoint_unusable / builtin_only）：状态胶囊说原因，右侧一个「检查连接」（重新探测，通了就变「已连接」）
  const checkable = !multi && !!connection && !ready && isCheckableError(connection) && canCheckConnection(connection)
  const canManage = !multi && ready && !!manage && connection?.route !== 'cli:codex' && connection?.route !== 'cli:claude' && !connection?.id.startsWith('sub:')
  const unavailable = !multi && !method.available
  const [checking, setChecking] = useState(false)
  // 检查过一次仍不通时，以这次的结论为准（连接视图里的旧原因不再用）；null = 还没检查过
  const [checkedReason, setCheckedReason] = useState<string | null>(null)
  const reason = checkedReason ?? (connection?.error === 'endpoint_unusable' ? (connection.error_reason ?? 'unknown') : 'unknown')
  const connAct: ConnAction = actionForReason(reason, checkedReason !== null)
  const checkNow = async () => {
    if (!connection || checking) return
    setChecking(true)
    try {
      const r = await runConnectionCheck(connection.id)
      if (r.ok) {
        setCheckedReason(null)
        onConnected(connection)
      } else setCheckedReason(r.reason ?? 'unknown')
    } finally {
      setChecking(false)
    }
  }
  const runAct = (a: ConnAction) => {
    if (!connection) return
    if (a === 'check') void checkNow()
    else if (a === 'switch_ai') requestConnectAi()
    else if (a === 'refill_key') {
      // 自填地址的连接：打开它的编辑表单（聚焦密钥）；目录厂商就地展开 Key 表单
      if (vendor.region !== 'custom' || !requestEditConnection(connection.id, 'key')) setOpen(true)
    }
    else if (!requestEditConnection(connection.id, a === 'edit_model' ? 'model' : 'url')) setOpen(true)
  }

  const status =
    !multi && connection && !ready ? (
      needsAuth ? (
        <StatusPill tone="warn" dot data-connect-state={connection.state}>
          {cx('needsAuth')}
        </StatusPill>
      ) : checking ? null : (
        // 出错：小红点 + 普通字，不套胶囊（跟右边的按钮拉开一点）
        <span data-connect-state={connection.state} className="mr-1 inline-flex items-center gap-1.5 text-sm text-ink-2">
          <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-danger" />
          {checkable ? probeReasonText(reason) : connectionErrorText(connection)}
        </span>
      )
    ) : null

  let action: ReactNode
  if (multi) {
    // 多种方式：「已连接」只是说有一种连好了——入口仍在，点开二级卡片可以加另一种 / 管理已连的
    action = (
      <span className="flex items-center gap-2">
        {anyReady && (
          <StatusPill tone="ok" dot data-connect-state="ready">
            {cx('connected')}
          </StatusPill>
        )}
        <Button
          variant={anyReady || (open && !onChooseMethods) ? 'ghost' : 'secondary'}
          size="sm"
          data-connect-action="methods"
          aria-expanded={onChooseMethods ? undefined : open}
          aria-label={anyReady || (open && !onChooseMethods) ? cx('connect') : undefined}
          onClick={() => (onChooseMethods ? onChooseMethods(vendor) : setOpen((v) => !v))}
        >
          {/* 方式列表已在下面展开：入口退成一个朝下的箭头（再点收起），不再与下面每种方式的按钮抢 */}
          {anyReady || (open && !onChooseMethods) ? (
            <ChevronRight size={ICON_SIZE.xs} aria-hidden className={cn('transition-transform', open && !onChooseMethods && 'rotate-90')} />
          ) : (
            cx('connect')
          )}
        </Button>
      </span>
    )
  } else if (ready) {
    action = (
      <StatusPill tone="ok" dot data-connect-state="ready">
        {cx('connected')}
      </StatusPill>
    )
  } else if (checkable) {
    // 换个 AI：弹窗里其它厂商就在下面，不再叠一层；设置页里才给这个按钮
    action =
      connAct === 'switch_ai' && onChooseMethods ? null : (
        <Button
          variant="secondary"
          size="sm"
          data-connect-action={connAct === 'check' ? 'check' : connAct}
          loading={checking}
          loadingLabel={connAct === 'check' ? cx('checking') : undefined}
          onClick={() => runAct(connAct)}
        >
          {connActionLabel(connAct)}
        </Button>
      )
  } else if (unavailable) {
    // 命令行 AI 没装 / 打不开 / 关了：一句话 + 一个按钮；找不到这个 agent（老后端）才退回「需先安装」
    action = loginAgent ? (
      <AgentStepAction agent={loginAgent} owner={`row:${vendor.id}`} />
    ) : (
      <span className="type-caption">{cx('needsInstall')}</span>
    )
  } else if (method.kind === 'key') {
    action = (
      <Button variant="secondary" size="sm" data-connect-action="key" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {cx('connect')}
      </Button>
    )
  } else if (method.kind === 'oauth') {
    action = (
      <Button variant="secondary" size="sm" data-connect-action="oauth" onClick={() => setOauth(true)}>
        {cx('signIn')}
      </Button>
    )
  } else {
    action = flow.button
  }

  const rowBody = (
    <>
      <BrandLogo logo={vendor.logo} size={24} />
      <span className="min-w-0 flex-1 truncate text-base font-medium text-ink">{vendorName(vendor)}</span>
    </>
  )

  return (
    <li
      data-connect-vendor={vendor.id}
      data-connect-kind={multi ? 'multi' : method.kind}
      className={cn('flex flex-col px-4 py-2', className)}
    >
      <div className="flex min-h-9 items-center gap-3">
        {canManage ? (
          <button
            type="button"
            data-connect-expand={vendor.id}
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="focus-visible:focus-ring -mx-1 flex min-w-0 flex-1 items-center gap-3 rounded-md px-1 text-left outline-none"
          >
            {rowBody}
          </button>
        ) : (
          <div className="flex min-w-0 flex-1 items-center gap-3">{rowBody}</div>
        )}
        <span className="flex shrink-0 items-center gap-2">
          {status}
          {action}
          {canManage && (
            <ChevronRight size={ICON_SIZE.xs} aria-hidden className={cn('text-ink-3 transition-transform', open && 'rotate-90')} />
          )}
        </span>
      </div>
      {!multi && method.kind === 'login' && flow.hint && <div className="pl-9 pt-1">{flow.hint}</div>}
      {open && !ready && (!checkable || connAct === 'refill_key') && !multi && method.kind === 'key' && (
        <div className="pl-9 pt-2">
          <VendorKeyForm vendor={vendor} method={method} connectionId={connection?.id} onConnected={onConnected} />
        </div>
      )}
      {open && multi && !onChooseMethods && (
        // 不缩进：方式的图标与上面厂商 logo 同一列、方式名与厂商名同一列
        <div className="pt-1">
          <MethodList vendor={vendor} connections={all} onConnected={onConnected} />
        </div>
      )}
      {open && canManage && connection && <div className="pl-9 pt-2">{manage!(connection)}</div>}
      {oauth && (
        <OAuthLoginDialog
          provider={vendor.logo}
          name={vendorName(vendor)}
          onClose={() => setOauth(false)}
          onSignedIn={() => {
            setOauth(false)
            void refreshAndFind(vendor, method).then((r) => r.conn && onConnected(r.conn))
          }}
        />
      )}
    </li>
  )
}
