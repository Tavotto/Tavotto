import { useState, type ReactNode } from 'react'
import { t as translate } from '@/i18n'
import { isDesktop, pickAnyFile, pickDirectory } from '@/lib/desktop'
import { useAiFixStore, type AiFixEntry } from '@/store/aiFixStore'
import { Button } from './ui/Button'
import { TextInput } from './ui/Input'
import { ProgressBar } from './ui/ProgressBar'

const ft = (key: string, values?: Record<string, unknown>) =>
  translate(`prep.aiFix.${key}`, { ns: 'workspace', ...(values ?? {}) })

/** 修复会话在卡片上的样子（ADR 0149「问与报」）：一句话 + 一个主按钮，其余折叠；不是横条、不占整行以外的位置。 */
export interface AiFixFlowUi {
  title: string
  slot?: ReactNode
  /** 折叠的详情（「详情」那颗展开） */
  details?: ReactNode
  primary?: ReactNode
  ghost?: ReactNode
}

/** 卡片详情：AI 读过哪些项目外的位置，以及哪些绝不读 */
function Reads({ reads, notRead }: { reads: string[]; notRead?: string }) {
  return (
    <div data-ai-fix-reads className="flex flex-col gap-1.5">
      <p className="m-0 font-medium text-ink">{ft('readTitle')}</p>
      {reads.length ? (
        <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
          {reads.map((r) => (
            <li key={r} className="break-all font-mono text-sm">
              {r}
            </li>
          ))}
        </ul>
      ) : (
        <p className="m-0">{ft('readNone')}</p>
      )}
      {notRead ? <p className="m-0 text-ink-3">{ft('notRead')}</p> : null}
    </div>
  )
}

/** 选择题：每个选项是一颗按钮，点了就答（少一步确认）；`allow_other` 时多一行自己填 */
function Choices({ fixKey, entry }: { fixKey: string; entry: AiFixEntry }) {
  const [typed, setTyped] = useState('')
  const card = entry.card!
  const answer = useAiFixStore.getState().answer
  return (
    <div data-ai-fix-choices className="flex flex-col gap-1.5">
      {(card.options ?? []).map((o) => (
        <Button
          key={o.id}
          variant="secondary"
          size="md"
          data-ai-fix-option={o.id}
          className="justify-start text-left"
          disabled={entry.answering}
          onClick={() => void answer(fixKey, o.id)}
        >
          {o.label}
        </Button>
      ))}
      {card.allow_other ? (
        <span className="flex items-center gap-1.5">
          <TextInput
            aria-label={ft('other')}
            placeholder={ft('other')}
            value={typed}
            className="min-w-0 flex-1"
            onChange={(e) => setTyped(e.target.value)}
          />
          <Button
            variant="secondary"
            size="md"
            data-ai-fix-other
            disabled={entry.answering || !typed.trim()}
            onClick={() => void answer(fixKey, 'other', typed.trim())}
          >
            {ft('useIt')}
          </Button>
        </span>
      ) : null}
    </div>
  )
}

/** 浏览器模式没有原生选择器：让用户粘贴路径（后端仍会过敏感目录硬拦） */
function PastePath({ fixKey, entry }: { fixKey: string; entry: AiFixEntry }) {
  const [path, setPath] = useState('')
  return (
    <span className="flex items-center gap-1.5">
      <TextInput
        aria-label={ft('pathAria')}
        placeholder={ft('pathPlaceholder')}
        value={path}
        className="min-w-0 flex-1"
        onChange={(e) => setPath(e.target.value)}
      />
      <Button
        variant="primary"
        size="md"
        data-ai-fix-path
        disabled={entry.answering || !path.trim()}
        onClick={() => void useAiFixStore.getState().answer(fixKey, path.trim())}
      >
        {ft('useIt')}
      </Button>
    </span>
  )
}

/** 进度那一句话：按工具名映射；没有映射的工具用通用一句。`install_packages` 带包名。 */
const activityLine = (a: NonNullable<AiFixEntry['activity']>) => {
  if (a.phase === 'denied') return ft('activity.denied')
  const known = ft(`activity.${a.tool}`, { detail: a.detail || ft('activity.thePackages'), defaultValue: '' })
  return known || ft('activity.generic')
}

/** 卡住的原因：一句话 + 折叠详情里多说一点 */
const stalledLine = (st: NonNullable<AiFixEntry['stalled']>) =>
  ft(`stalled.${st.reason}`, { tool: st.detail || '?', defaultValue: ft('stalled.stalled') })

const failureLine = (reason: string | undefined) => {
  switch (reason) {
    case 'cli_waiting_approval':
    case 'cli_tool_not_delivered':
    case 'stalled':
      return ft(`failed.${reason}`)
    case 'budget':
    case 'max_turns':
    case 'tool_error_loop':
      return ft('failed.budget')
    case 'auth':
      return ft('failed.auth')
    case 'quota':
      return ft('failed.quota')
    case 'network':
      return ft('failed.network')
    case 'refused':
      return ft('failed.refused')
    default:
      return ft('failed.other')
  }
}

/** 修复会话进行中 / 等用户 / 结束时的卡片内容；条目不在这几个阶段就返回 null（走原来的卡） */
export function aiFixFlow(fixKey: string, entry: AiFixEntry): AiFixFlowUi | null {
  const store = useAiFixStore.getState()
  const busy = entry.answering === true
  const skip = (label: string, answer = 'skip') => (
    <Button variant="ghost" size="md" className="text-ink-2" data-ai-fix-skip disabled={busy} onClick={() => void store.answer(fixKey, answer)}>
      {label}
    </Button>
  )
  if (entry.phase === 'busy') {
    return { title: ft('busy'), primary: dismissButton(fixKey) }
  }
  if (entry.phase === 'running' && entry.stalled) {
    // 卡住：说原因 + 一个主按钮；不静默挂着（后端过一阵会自动取消，这里让用户先知道、能自己处理）
    return {
      title: stalledLine(entry.stalled),
      details: (
        <p className="m-0 whitespace-pre-wrap break-words">
          {ft(`stalledWhy.${entry.stalled.reason}`, { defaultValue: ft('stalledWhy.stalled') })}
        </p>
      ),
      primary: (
        <Button variant="primary" size="md" data-ai-fix-stop onClick={() => void store.cancel(fixKey)}>
          {ft('stop')}
        </Button>
      ),
    }
  }
  if (entry.phase === 'running') {
    return {
      title: entry.activity ? activityLine(entry.activity) : ft('running'),
      slot: <ProgressBar pct={null} label={ft('running')} data-prep-progress />,
      ghost: (
        <Button variant="ghost" size="md" className="text-ink-2" data-ai-fix-cancel onClick={() => void store.cancel(fixKey)}>
          {ft('cancel')}
        </Button>
      ),
    }
  }
  if (entry.phase === 'done') {
    return {
      title: entry.outcome === 'not_fixed' ? entry.summary || ft('notFixed') : failureLine(entry.reason),
      primary: dismissButton(fixKey),
    }
  }
  if (entry.phase !== 'waiting' || !entry.card) return null
  const card = entry.card
  switch (card.kind) {
    case 'approve':
      return {
        title: card.summary,
        details: (
          <>
            {card.packages?.length ? (
              <p data-ai-fix-packages className="m-0 break-words font-medium text-ink">
                {ft('willInstall', { packages: card.packages.join(', ') })}
              </p>
            ) : null}
            {card.details ? <p className="m-0 whitespace-pre-wrap break-words">{card.details}</p> : null}
            <Reads reads={card.reads ?? []} notRead={card.not_read} />
          </>
        ),
        primary: (
          <Button variant="primary" size="md" data-ai-fix-confirm loading={busy} disabled={busy} onClick={() => void store.answer(fixKey, 'yes')}>
            {ft('confirm')}
          </Button>
        ),
        ghost: skip(ft('decline'), 'no'),
      }
    case 'choice':
      return { title: card.summary, slot: <Choices fixKey={fixKey} entry={entry} />, ghost: skip(ft('skip')) }
    case 'pick': {
      const kind = card.pick === 'dir' ? 'dir' : 'file'
      const choose = async () => {
        const picked = kind === 'dir' ? await pickDirectory(card.summary) : await pickAnyFile(card.summary)
        if (picked) void store.answer(fixKey, picked) // 取消选择器不是答案：卡片留着
      }
      return isDesktop()
        ? {
            title: card.summary,
            primary: (
              <Button variant="primary" size="md" data-ai-fix-pick disabled={busy} onClick={() => void choose()}>
                {kind === 'dir' ? ft('pickDir') : ft('pickFile')}
              </Button>
            ),
            ghost: skip(ft('skip')),
          }
        : { title: card.summary, slot: <PastePath fixKey={fixKey} entry={entry} />, ghost: skip(ft('skip')) }
    }
    case 'send':
      return {
        title: ft('sendTitle'),
        details: (
          <>
            <p className="m-0 font-medium text-ink">{ft('sendNote')}</p>
            <p className="m-0 whitespace-pre-wrap break-words">{card.summary}</p>
            {card.wants_source ? <p className="m-0 text-ink-3">{ft('sendSource')}</p> : null}
          </>
        ),
        primary: (
          <Button variant="primary" size="md" data-ai-fix-send disabled={busy} onClick={() => store.openSend(fixKey)}>
            {ft('sendReview')}
          </Button>
        ),
        ghost: skip(ft('sendNo'), 'no'),
      }
  }
}

const dismissButton = (fixKey: string) => (
  <Button variant="primary" size="md" data-ai-fix-dismiss onClick={() => useAiFixStore.getState().clear(fixKey)}>
    {ft('gotIt')}
  </Button>
)
