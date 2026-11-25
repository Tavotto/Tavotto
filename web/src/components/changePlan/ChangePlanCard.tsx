import { useTranslation } from 'react-i18next'
import { requestConnectAi } from '@/store/connectAiStore'
import { canUndoApplied, useChangePlanStore, versionNumber, type PlanEntry } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { cn } from '@/lib/utils'
import { Card } from '../ui/Card'
import { Button } from '../ui/Button'
import { Check, RefreshCw } from '../ui/icons'
import { ICON_SIZE } from '../ui/Icon'
import { StatusPill } from '../ui/StatusPill'
import { ThinkingOrb } from '../ui/ThinkingOrb'
import { cp, effectiveStatus, failureAction, failureText, planTally, summaryLine, unhandledViews } from './changePlanModel'
import { FigureThumb } from './FigureThumb'
import { useImageAspect, usePanelThumbSrc, usePlanPanel, usePlanSource } from './usePlanFigure'

/**
 * 助手里的「修改方案」卡（助手一侧，占栏宽）：修改后的缩略图是主体，标题行「修改方案」+ 右侧版本，一句话说改了什么，
 * 主按钮「查看」+ 次级「直接应用」。状态：生成中（思考小球，不自造动画）· 待确认 · 已应用 · 已放弃 · 图已改过 · 失败（一句灰字 + 一个下一步）。
 * `latest = false`（后面还有更新的待确认卡）时「查看」降成次级——除「发送」外一屏只有最新一张待确认卡的这一颗主按钮（ADR 0206 §三）。
 * 悬停缩略图换成修改前（`title` 说明，不堆文字）。
 */
export function ChangePlanCard({ entry, latest = true }: { entry: PlanEntry; latest?: boolean }) {
  useTranslation('ai')
  const panel = usePlanPanel(entry)
  const status = effectiveStatus(entry, panel?.overrides ?? null, usePlanSource(panel))
  const plan = entry.plan
  // 历史栈顶：「撤销」只在它还是应用那一次 commit 时给（订阅它，之后改了别的这里跟着收起）
  useDocumentStore((s) => s.past.at(-1))
  useDocumentStore((s) => s.future.at(-1)) // 一键排版卡：⌘Z / ⌘⇧Z 之后状态跟着变
  // 方案出来之后卡面用方案自己的两张图；只有还没有（生成中）/ 已放弃时才为缩略图出图
  const thumb = usePanelThumbSrc(panel, !entry.beforeSrc || entry.status === 'discarded' || (entry.status === 'applied' && !!entry.appliedPartial && !entry.appliedSrc))
  const beforeSrc = entry.beforeSrc ?? thumb
  const aspect = useImageAspect(entry.afterSrc ?? beforeSrc)
  const store = useChangePlanStore.getState
  const primary = latest ? 'primary' : 'secondary'
  const btn = 'rounded-full'

  if (status === 'failed') return <FailedCard entry={entry} />
  // 助手来源，整条指令都要改脚本：这里只留一行灰字，接着由改脚本那一轮自己说话
  if (status === 'handoff') {
    return (
      <p className="px-1 text-sm text-ink-3" data-change-plan-handoff>
        {cp('card.handoff')}
      </p>
    )
  }

  // 按批注重改进行中：卡片也回到「生成中」（旧版仍在弹窗里），出好后自动换成新版
  const generating = status === 'generating' || !plan || (status === 'ready' && entry.revising !== undefined)
  // 已放弃：后端那份已经收了，卡面画这张图此刻的样子（不再挂方案的图）
  const showAfter = !generating && !!entry.afterSrc && status !== 'discarded'
  // 已应用且只应用了部分组：挂实际应用的那一张（弹窗里按勾选出的图；没有就挂这张图此刻的样子），不挂全量候选图
  const shownAfter = entry.status === 'applied' && entry.appliedPartial ? (entry.appliedSrc ?? thumb) : entry.afterSrc
  const dim = status === 'discarded' || status === 'stale'
  const handled = plan ? planTally(entry).done : 0
  // 「撤销」只在这张图还是我们写的那一份时给：用户之后又改过它，就不替他回到改前
  const canUndo = status === 'applied' && !!panel && canUndoApplied(entry, panel.overrides)
  // 分量：待处理（生成中 / 待确认 / 图已改过）是完整卡面 + 微投影；已应用沉到次级卡面；已放弃再降一档、预览区收矮
  const pending = generating || status === 'ready' || status === 'stale'

  return (
    <Card
      appearance={pending ? 'raised' : 'subtle'}
      padding="sm"
      className={cn(pending && 'bg-field', status === 'discarded' && 'opacity-80')}
      data-change-plan-card={generating ? 'generating' : status}
    >
      <div className="group/thumb" title={showAfter ? cp('card.holdBefore') : undefined}>
        <FigureThumb
          src={showAfter ? shownAfter : status === 'discarded' ? thumb : beforeSrc}
          alt={cp('card.thumbAlt')}
          aspect={status === 'discarded' ? 2.8 : aspect}
          dim={dim}
          cover={status === 'discarded'}
        >
          {showAfter && beforeSrc && (
            <>
              <img
                src={beforeSrc}
                alt=""
                draggable={false}
                className="absolute inset-0 size-full rounded-md bg-paper object-contain opacity-0 transition-opacity duration-base group-hover/thumb:opacity-100"
              />
              <StatusPill className="absolute left-1.5 top-1.5 bg-surface opacity-0 transition-opacity duration-base group-hover/thumb:opacity-100">
                {cp('dialog.before')}
              </StatusPill>
            </>
          )}
          {generating && <span aria-hidden data-change-plan-frost className="absolute inset-0 rounded-md bg-surface/40 backdrop-blur-sm" />}
        </FigureThumb>
      </div>
      <div className="flex flex-col gap-2 px-1.5 pb-1 pt-2.5">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-base font-medium text-ink">{cp('card.title')}</span>
          {!generating && plan && <span className="type-meta">{cp('card.version', { n: versionNumber(entry) })}</span>}
        </div>
        {generating ? (
          <p className="flex items-center gap-2 text-sm text-ink-2" data-change-plan-generating>
            <ThinkingOrb form="solving" />
            <span>{entry.revising !== undefined ? cp('card.revising') : entry.from === 'assistant' ? cp('card.generatingAssistant') : entry.commentCount > 0 ? cp('card.generating', { count: entry.commentCount }) : cp('card.generatingAny')}</span>
          </p>
        ) : (
          <p className={cn('flex items-center gap-1.5 text-sm', status === 'discarded' ? 'text-ink-3' : 'text-ink-2')}>
            {status === 'applied' && <Check size={ICON_SIZE.sm} className="shrink-0 text-ok" />}
            {status === 'applied'
              ? entry.kind === 'layout'
                ? cp('card.appliedLayout', { count: entry.layout?.count ?? 0 })
                : entry.from === 'assistant'
                  ? cp('card.appliedAssistant', { places: planTally(entry).places })
                  : cp('card.applied', { count: handled })
              : status === 'discarded'
                ? cp('card.discarded')
                : status === 'stale'
                  ? cp('card.stale')
                  : summaryLine(entry)}
          </p>
        )}
        {entry.notice && <p className="text-xs text-ink-3" data-change-plan-notice={entry.notice}>{cp(entry.notice === 'apply_failed' ? 'card.applyFailed' : 'card.undoFailed')}</p>}
        {!generating && (
          <div className="flex items-center gap-2 pt-0.5">
            {status === 'ready' && (
              <>
                <Button variant={primary} size="md" className={btn} onClick={() => store().openDialog(entry.key)} data-change-plan-view>
                  {cp('card.view')}
                </Button>
                <Button variant="secondary" size="md" className={btn} onClick={() => void store().apply(entry.key)} data-change-plan-apply>
                  {cp('card.applyNow')}
                </Button>
              </>
            )}
            {canUndo && (
              <Button variant="ghost" className="-ml-3" onClick={() => void store().undo(entry.key)} data-change-plan-undo>
                {cp('card.undo')}
              </Button>
            )}
            {entry.from === 'assistant' && !entry.scriptStarted && (status === 'ready' || status === 'applied') && (plan?.unhandled ?? []).some((u) => u.reason === 'needs_script') && (
              <Button variant="ghost" size="md" className="rounded-full" title={status === 'ready' ? cp('card.runScriptTip') : undefined} onClick={() => void store().runScript(entry.key)} data-change-plan-run-script>
                {cp('card.runScript')}
              </Button>
            )}
            {status === 'stale' && (
              <Button variant={primary} size="md" className={btn} onClick={() => void store().regenerate(entry.key)} data-change-plan-regenerate>
                <RefreshCw size={ICON_SIZE.sm} />
                {cp('card.regenerate')}
              </Button>
            )}
          </div>
        )}
      </div>
    </Card>
  )
}

/**
 * 失败的卡：一句灰字（稳定码 → `errors:backend.*`）+ 一个可走的下一步——连接 AI / 重试 / 重新生成 / 收起，绝不报错卡住、图一个字没动。
 * 「没有一条意见能落成图内修改」（`change_plan_empty`）把没处理的几条并列在下面。
 */
function FailedCard({ entry }: { entry: PlanEntry }) {
  const code = entry.failure?.code ?? 'change_plan_failed'
  const action = failureAction(code)
  const store = useChangePlanStore.getState
  const un = code === 'change_plan_empty' ? unhandledViews(entry) : []
  const assistant = entry.from === 'assistant'
  const label = { connect: cp('card.connect'), retry: cp('card.retry'), regenerate: cp('card.regenerate'), dismiss: cp('card.dismiss'), script: cp('card.retry') }[action]
  const run = () => {
    // 连上 AI 之后接着生成（同一份请求），不用再回来点一次
    if (action === 'connect') requestConnectAi({ onConnected: () => void store().regenerate(entry.key) })
    else if (action === 'dismiss') store().dismiss(entry.key)
    else if (action === 'script') void store().runScript(entry.key)
    else void store().regenerate(entry.key)
  }
  // 助手来源：用户的指令不能掉在地上——方案没出来时，另给一条「改脚本做」的路（现有改脚本路径）
  const scriptWay = assistant && action !== 'script'
  return (
    <Card appearance="subtle" padding="sm" data-change-plan-card="failed" data-change-plan-code={code}>
      <div className="flex flex-col gap-2 px-1.5 py-1">
        <span className="text-base font-medium text-ink">{cp('card.title')}</span>
        <p className="text-sm text-ink-3">{failureText(code, entry.failure?.params, assistant)}</p>
        {un.length > 0 && (
          <ul className="flex flex-col gap-1">
            {un.map((u) => (
              <li key={u.id} className="flex flex-col" data-change-plan-unhandled={u.id}>
                <span className="truncate text-sm text-ink-3">{u.text}</span>
                <span className="type-meta">{[u.reviewer, u.reason].filter(Boolean).join(' · ')}</span>
              </li>
            ))}
          </ul>
        )}
        <div className="flex items-center gap-1 pt-0.5">
          <Button variant="ghost" className="-ml-3" onClick={run} data-change-plan-failed-action={action}>
            {label}
          </Button>
          {scriptWay && (
            <Button variant="ghost" onClick={() => void store().runScript(entry.key)} data-change-plan-run-script>
              {cp('card.runScriptAll')}
            </Button>
          )}
          {action !== 'dismiss' && (
            <Button variant="ghost" onClick={() => store().dismiss(entry.key)} data-change-plan-dismiss>
              {cp('card.dismiss')}
            </Button>
          )}
        </div>
      </div>
    </Card>
  )
}
