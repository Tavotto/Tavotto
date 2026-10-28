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

/** 订阅登录方式里指向 codex 的那家厂商（ChatGPT 订阅）；后端没给 = 找不到，首屏退回完整列表 */
export function chatgptVendorOf(vendors: AiVendor[]): AiVendor | undefined {
  return vendors.find((v) => methodsOf(v).some((m) => m.kind === 'login' && loginAgentOf(v, m) === 'codex'))
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
        <ChatgptRow vendor={chatgpt} onConnected={onConnected} />
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

