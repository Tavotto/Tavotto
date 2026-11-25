import { useAiStore } from '@/store/aiStore'
import type { AiAgentId, AiConnection, AiVendor } from '@/lib/api'
import { ChevronRight } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Button } from '../../ui/Button'
import { BrandLogo } from '../BrandLogo'
import { AgentStepAction } from './AgentStepAction'
import { cx } from './copy'
import { loginAgentOf, methodsOf } from './vendors'
import { useLoginAfterInstall } from './useAgentInstall'
import { useLoginFlow } from './useLoginFlow'
import { useSiwcLogin } from './useSiwcLogin'

/** ChatGPT 订阅那一家：有「Continue with ChatGPT」（SIWC）的优先，否则是订阅登录里指向 codex 的；后端没给 = 找不到，首屏退回完整列表 */
export function chatgptVendorOf(vendors: AiVendor[]): AiVendor | undefined {
  return (
    vendors.find((v) => methodsOf(v).some((m) => m.kind === 'siwc')) ??
    vendors.find((v) => methodsOf(v).some((m) => m.kind === 'login' && loginAgentOf(v, m) === 'codex'))
  )
}

/** 「有订阅」一行的 ChatGPT 登录（SIWC）：浏览器里授权，不需要装任何命令行 AI */
function SiwcStarterRow({ vendor, onConnected }: { vendor: AiVendor; onConnected: (c: AiConnection) => void }) {
  const method = methodsOf(vendor).find((m) => m.kind === 'siwc')!
  const flow = useSiwcLogin(vendor, method, onConnected)
  return (
    <li data-connect-starter="subscription" className="flex flex-col px-4 py-2">
      <div className="flex min-h-9 items-center gap-3">
        <BrandLogo logo={vendor.logo} size={24} />
        <span className="min-w-0 flex-1 text-base font-medium text-ink">{cx('starter.subscription')}</span>
        <span className="flex shrink-0 items-center gap-2">{flow.button}</span>
      </div>
      {flow.hint && <div className="pl-9 pt-1">{flow.hint}</div>}
    </li>
  )
}

/** 「有订阅」一行：ChatGPT 账号登录（codex 登录；没装则先应用内安装再登录） */
function ChatgptRow({ vendor, onConnected }: { vendor: AiVendor; onConnected: (c: AiConnection) => void }) {
  const method = methodsOf(vendor).find((m) => m.kind === 'login' && loginAgentOf(vendor, m) === 'codex')!
  const flow = useLoginFlow(vendor, method, onConnected)
  const owner = `starter:${vendor.id}`
  useLoginAfterInstall(loginAgentOf(vendor, method) as AiAgentId, owner, flow.begin)
  const agent = useAiStore((s) => s.caps?.agents.find((a) => a.id === loginAgentOf(vendor, method)))
  return (
    <li data-connect-starter="subscription" className="flex flex-col px-4 py-2">
      <div className="flex min-h-9 items-center gap-3">
        <BrandLogo logo={vendor.logo} size={24} />
        <span className="min-w-0 flex-1 text-base font-medium text-ink">{cx('starter.subscription')}</span>
        <span className="flex shrink-0 items-center gap-2">
          {method.available || !agent ? flow.button : <AgentStepAction agent={agent} owner={owner} />}
        </span>
      </div>
      {flow.hint && <div className="pl-9 pt-1">{flow.hint}</div>}
    </li>
  )
}

/**
 * 新手首屏（还没有任何连接）：只有两条路——有 ChatGPT 订阅就登录；没有订阅就填一个 Key（点开才展开 Key 类厂商）。
 * 其余（Claude、兼容接口、账号登录…）收进「其他方式」。
 */
export function StarterChoices({
  chatgpt,
  keyVendors,
  keysOpen,
  onToggleKeys,
  onOther,
  onConnected,
  renderKeyRow,
}: {
  chatgpt: AiVendor
  keyVendors: AiVendor[]
  keysOpen: boolean
  onToggleKeys: () => void
  onOther: () => void
  onConnected: (c: AiConnection) => void
  renderKeyRow: (v: AiVendor) => React.ReactNode
}) {
  return (
    <>
      <ul className="-mx-4 flex flex-col">
        {methodsOf(chatgpt).some((m) => m.kind === 'siwc') ? (
          <SiwcStarterRow vendor={chatgpt} onConnected={onConnected} />
        ) : (
          <ChatgptRow vendor={chatgpt} onConnected={onConnected} />
        )}
        <li data-connect-starter="key" className="flex flex-col px-4 py-2">
          <div className="flex min-h-9 items-center gap-3">
            <span className="min-w-0 flex-1 text-base font-medium text-ink">{cx('starter.key')}</span>
            <Button
              variant="secondary"
              size="sm"
              data-connect-starter-key
              aria-expanded={keysOpen}
              onClick={onToggleKeys}
            >
              {keysOpen ? <ChevronRight size={ICON_SIZE.xs} aria-hidden className="rotate-90" /> : cx('connect')}
            </Button>
          </div>
        </li>
        {keysOpen && keyVendors.map(renderKeyRow)}
      </ul>
      <div>
        <Button variant="ghost" size="sm" data-connect-starter-other onClick={onOther}>
          {cx('starter.other')}
        </Button>
      </div>
    </>
  )
}

