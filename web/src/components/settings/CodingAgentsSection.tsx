import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ExternalLink, RefreshCw } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import {
  agentById,
  backendErrorText,
  effectiveAgent,
  patchAiAgent,
  type AiAgentId,
} from '@/lib/api'
import { CODEX_GUIDE_URL, PRODUCT_NAME } from '@/lib/brand'
import { formatDateTime } from '@/i18n/format'
import { useAiStore } from '@/store/aiStore'
import { Button } from '../ui/Button'
import { AgentDetailView } from './AgentDetailView'
import { AgentList } from './AgentList'
import { CodexIntegrationPanel } from './CodexIntegrationPanel'
import { ag } from './agentState'

/** 最近的可滚动祖先（设置对话框的内容区）；返回详情时要把它归位 */
function scrollParent(el: HTMLElement | null): HTMLElement | null {
  for (let p = el?.parentElement ?? null; p; p = p.parentElement) {
    const overflow = getComputedStyle(p).overflowY
    if (overflow === 'auto' || overflow === 'scroll') return p
  }
  return null
}

/**
 * 设置 → 编码 Agent。
 *
 * 一级页面只回答一个问题：**这台机器上有哪些编码 Agent、现在能不能用**。
 * 每行只有名称、版本号、状态；路径、命令、检测来源、第三方接口、Base URL、
 * 密钥、wire api 一个都不在这儿——它们全在各自 Agent 的详情里
 * （`AgentDetailView`，可复制）。这一页上也没有解释段：普通用户装好 CLI
 * 之后什么都不用配，页面本身就是那句话的兑现（ADR 0015 / 0038）。
 *
 * 页面分成两个方向明确的小节，**它们是两件事**：
 *   ① 配置改图助手 —— 借本机的 CLI 改图脚本；
 *   ② 连接外部工具 —— 把 Tavotto 装进 Codex（插件 / 画布）。
 * 小标题按**用户想完成什么**命名，不再是「在 A 中使用 B / 在 B 中使用 A」那样
 * 互为镜像的一对——两句话只差语序时，读者得逐字比对才分得清（审计 T44）。
 * 方向由小节里的内容承担：② 里那一行就叫「Tavotto for Codex」。
 * 「本机装了 codex CLI」不等于「装了 Tavotto for Codex」，两个状态绝不合并。
 *
 * **e2e 锚点**（清单见 web/AGENTS.md）：`data-agent-section="in-app" | "external"`
 * 标住这两节，`data-agent-codex-integration` 标住 ② 里那一行，
 * `data-agent-rescan` / `data-agent-last-checked` 标住重新检测那对控件。
 * 小标题的**文字**归审计管、随时可以再改一次——用例认的是这几个属性，
 * 不认那句话（T44 改名时它们就是靠认文案红的）。
 */
export function CodingAgentsSection() {
  useTranslation('dialogs')
  const caps = useAiStore((s) => s.caps)
  const preferred = useAiStore((s) => s.agent)
  const [detailId, setDetailId] = useState<AiAgentId | null>(null)
  const [busy, setBusy] = useState(false)
  const [busyAgent, setBusyAgent] = useState<AiAgentId | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [announce, setAnnounce] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)
  // 进详情前记下列表的滚动位置，返回时归位（否则每次返回都跳回顶部）
  const savedScroll = useRef(0)

  const reload = useCallback(async (refresh = false) => {
    setError(null)
    setBusy(true)
    try {
      await useAiStore.getState().loadCaps(refresh)
      setAnnounce(ag('announce.done'))
    } catch (e) {
      // **保留上一次成功的结果**：清空的话用户会看到「全部未安装」，那是假的
      setError(backendErrorText(e))
      setAnnounce(ag('announce.failed'))
    } finally {
      setBusy(false)
    }
  }, [])

  // 打开设置页就探一次（用缓存，不强制重跑子进程）
  useEffect(() => {
    void reload(false)
  }, [reload])

  /**
   * 稳定身份的「重新探测」。**不能写成内联箭头**：它进了详情页
   * `InstallPanel` 轮询 effect 的依赖数组，每次渲染换一个函数 = 每次渲染
   * 清掉再重建那个 2s 定时器，安装进度会一直查不出来。
   */
  const refreshNow = useCallback(() => reload(true), [reload])

  useLayoutEffect(() => {
    if (detailId === null) {
      const parent = scrollParent(rootRef.current)
      if (parent) parent.scrollTop = savedScroll.current
    }
  }, [detailId])

  const openDetail = (id: AiAgentId) => {
    savedScroll.current = scrollParent(rootRef.current)?.scrollTop ?? 0
    setDetailId(id)
  }

  const toggle = async (id: AiAgentId, enabled: boolean) => {
    setError(null)
    setBusyAgent(id)
    try {
      await patchAiAgent(id, { enabled })
      await useAiStore.getState().loadCaps(true)
    } catch (e) {
      setError(backendErrorText(e))
    } finally {
      setBusyAgent(null)
    }
  }

  const detail = agentById(caps, detailId)
  if (detail && caps) {
    return (
      <div ref={rootRef}>
        <AgentDetailView
          agent={detail}
          caps={caps}
          onBack={() => setDetailId(null)}
          onRefreshed={refreshNow}
        />
      </div>
    )
  }

  // 「这一刻实际会派给谁」只有一份实现（lib/api 的 effectiveAgent）——
  // 在组件里再写一遍同样的三元表达式，就是这次重构要消灭的那种第二权威
  const effective = effectiveAgent(preferred, caps)

  return (
    <div ref={rootRef} className="flex flex-col gap-3">
      {/* ---------------- 标题区：只有动作，没有解释（ADR 0038） ---------------- */}
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-medium text-ink">{ag('title')}</h3>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Button
            data-agent-rescan
            variant="secondary"
            size="sm"
            loading={busy}
            onClick={() => void reload(true)}
          >
            <RefreshCw size={ICON_SIZE.sm} aria-hidden />
            {ag('rescan')}
          </Button>
          {/* 最近检测时间跟着「重新检测」走：它说明的是那个动作上次什么时候
              发生过，摆在列表底下会被读成列表的脚注 */}
          {caps && caps.checked_at_ms > 0 && (
            <span data-agent-last-checked className="text-xs text-ink-3">
              {ag('lastChecked', { time: formatDateTime(caps.checked_at_ms) })}
            </span>
          )}
        </div>
      </header>

      {/* 检测结果的播报：完成 / 失败都要说一声，不能只有视觉上的变化 */}
      <p aria-live="polite" className="sr-only">
        {announce}
      </p>

      {caps === null ? (
        <>
          <p className="text-xs text-ink-3">{ag('state.detecting')}</p>
          {/* 骨架屏：**绝不先显示红叉或「未安装」**——那两个都是没有依据的断言。
              两行是为了让首屏高度接近最终结果，减少布局跳动。 */}
          <ul aria-hidden className="overflow-hidden rounded-md border border-border bg-surface">
            {[0, 1].map((i) => (
              <li
                key={i}
                className={`flex min-h-12 items-center gap-3 px-3 ${i > 0 ? 'border-t border-border' : ''}`}
              >
                <span className="h-9 w-9 shrink-0 rounded-md bg-surface-2" />
                <span className="h-3 w-24 rounded-sm bg-surface-2" />
                <span className="ml-auto h-3 w-16 rounded-sm bg-surface-2" />
              </li>
            ))}
          </ul>
        </>
      ) : (
        <>
          <section data-agent-section="in-app" className="flex flex-col gap-1.5">
            <h4 className="text-xs font-medium text-ink-2">{ag('useInProduct')}</h4>
            {/* 默认 Agent 不再单独一行（2026-09-11 用户反馈）：每行自带「默认」按钮。
                首选那个暂时不可用时**按下态落在第一个可用的，但不改用户存着的首选值**
                （它恢复以后还该是默认项） */}
            <AgentList
              agents={caps.agents}
              onOpen={openDetail}
              onToggle={(id, v) => void toggle(id, v)}
              busyAgent={busyAgent}
              defaultId={effective}
              onSetDefault={(id) => useAiStore.getState().setAgent(id)}
            />
            {effective === null && (
              <p className="text-xs text-ink-3">{ag('noUsableAgent')}</p>
            )}
          </section>

          {/* ---------------- 反方向：在编码 Agent 里用 Tavotto ----------------
              一行：名字 + 外链。没有卡片外框、没有说明段（ADR 0038）——
              「本机装了 codex CLI」仍然绝不写成「Tavotto for Codex 已安装」。 */}
          <section data-agent-section="external" className="flex flex-col gap-1.5">
            <h4 className="text-xs font-medium text-ink-2">{ag('useFromAgents')}</h4>
            <div data-agent-codex-integration className="flex min-h-7 items-center gap-3 px-1">
              <span className="min-w-0 flex-1 truncate text-sm text-ink">
                {ag('codexIntegrationName', { product: PRODUCT_NAME })}
              </span>
              <a
                href={CODEX_GUIDE_URL}
                target="_blank"
                rel="noreferrer"
                className="inline-flex shrink-0 items-center gap-1 text-xs text-accent outline-none hover:underline focus-visible:focus-ring"
              >
                {ag('viewGuide')}
                <ExternalLink size={ICON_SIZE.xs} aria-hidden />
              </a>
            </div>
            {/* 桌面版才有的安装入口：spawn `tavotto-cli codex install --json`。
                浏览器模式下 CodexIntegrationPanel 自己返回 null——上面那一行
                （名字 + 指南）在两种形态下一模一样。 */}
            <CodexIntegrationPanel />
          </section>
        </>
      )}

      {error && (
        <p role="alert" className="text-xs text-danger">
          {ag('refreshFailed')} {error}
        </p>
      )}
    </div>
  )
}

