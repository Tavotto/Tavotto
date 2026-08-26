import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ExternalLink, RefreshCw } from 'lucide-react'
import {
  agentById,
  backendErrorText,
  effectiveAgent,
  patchAiAgent,
  usableAgents,
  type AiAgentId,
} from '@/lib/api'
import { CODEX_GUIDE_URL, PRODUCT_NAME } from '@/lib/brand'
import { formatDateTime } from '@/i18n/format'
import { useAiStore } from '@/store/aiStore'
import { Button } from '../ui/Button'
import { AgentDetailView } from './AgentDetailView'
import { AgentList } from './AgentList'
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
 * 路径输入框、第三方接口、Base URL、密钥、wire api 一个都不在这儿——它们
 * 全在各自 Agent 的详情里（`AgentDetailView`）。普通用户装好 CLI 之后
 * 什么都不用配，这一页就是那句话的兑现。
 *
 * 页面分成两个方向明确的小节，**它们是两件事**：
 *   ① 在 Tavotto 中使用编码 Agent —— 借本机的 CLI 改图脚本；
 *   ② 在编码 Agent 中使用 Tavotto —— 把 Tavotto 装进 Codex（插件 / 画布）。
 * 「本机装了 codex CLI」不等于「装了 Tavotto for Codex」，两个状态绝不合并。
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
      {/* ---------------- 标题区 ---------------- */}
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-medium text-ink">{ag('title')}</h3>
          <p className="mt-0.5 text-xs leading-relaxed text-ink-3">
            {ag('intro', { product: PRODUCT_NAME })}
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Button variant="outline" size="sm" loading={busy} onClick={() => void reload(true)}>
            <RefreshCw size={12} aria-hidden />
            {ag('rescan')}
          </Button>
          {/* 最近检测时间跟着「重新检测」走：它说明的是那个动作上次什么时候
              发生过，摆在列表底下会被读成列表的脚注 */}
          {caps && caps.checked_at_ms > 0 && (
            <span className="text-xs text-ink-3">
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
                className={`flex min-h-16 items-center gap-3 px-3 ${i > 0 ? 'border-t border-border' : ''}`}
              >
                <span className="h-9 w-9 shrink-0 rounded-md bg-surface-2" />
                <span className="flex min-w-0 flex-1 flex-col gap-1.5">
                  <span className="h-3 w-24 rounded-sm bg-surface-2" />
                  <span className="h-3 w-40 rounded-sm bg-surface-2" />
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <>
          <DefaultAgentPicker />

          <section className="flex flex-col gap-1.5">
            <h4 className="text-xs font-medium text-ink-2">{ag('useInProduct', { product: PRODUCT_NAME })}</h4>
            <AgentList
              agents={caps.agents}
              onOpen={openDetail}
              onToggle={(id, v) => void toggle(id, v)}
              busyAgent={busyAgent}
            />
            {effective === null && (
              <p className="text-xs text-ink-3">{ag('noUsableAgent')}</p>
            )}
          </section>

          {/* ---------------- 反方向：在编码 Agent 里用 Tavotto ---------------- */}
          <section className="flex flex-col gap-1.5">
            <h4 className="text-xs font-medium text-ink-2">{ag('useFromAgents', { product: PRODUCT_NAME })}</h4>
            <div className="rounded-md border border-border bg-surface p-3">
              <p className="text-sm font-medium text-ink">
                {ag('codexIntegrationName', { product: PRODUCT_NAME })}
              </p>
              <p className="mt-0.5 text-xs leading-relaxed text-ink-3">
                {ag('codexIntegrationDesc', { product: PRODUCT_NAME })}
              </p>
              <a
                href={CODEX_GUIDE_URL}
                target="_blank"
                rel="noreferrer"
                className="mt-1.5 inline-flex items-center gap-1 text-xs text-accent outline-none hover:underline focus-visible:focus-ring"
              >
                {ag('viewGuide')}
                <ExternalLink size={11} aria-hidden />
              </a>
            </div>
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

/**
 * 默认 Agent。只列 `usable` 的那些；只有一个时显示成只读——为一个选项画一个
 * 下拉框是纯粹的噪音。首选那个暂时不可用时**自动用第一个可用的，但不改
 * 用户存着的首选值**（它恢复以后还该是默认项）。
 */
function DefaultAgentPicker() {
  useTranslation('dialogs')
  const caps = useAiStore((s) => s.caps)
  const preferred = useAiStore((s) => s.agent)
  const list = usableAgents(caps)
  const effective = effectiveAgent(preferred, caps)

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-ink-2">{ag('defaultAgent')}</span>
      {list.length === 0 ? (
        <span className="text-xs text-ink-3">{ag('noUsableAgentShort')}</span>
      ) : list.length === 1 ? (
        <span className="text-xs text-ink">{list[0].display_name}</span>
      ) : (
        <select
          value={effective ?? ''}
          onChange={(e) => useAiStore.getState().setAgent(e.target.value)}
          aria-label={ag('defaultAgentAria')}
          className="h-7 min-w-32 rounded-sm border border-border bg-surface px-1.5 text-xs text-ink outline-none focus-visible:focus-ring"
        >
          {list.map((a) => (
            <option key={a.id} value={a.id}>
              {a.display_name}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}
