import { useTranslation } from 'react-i18next'
import { requestConnectAi } from '@/store/connectAiStore'
import { DATA_DECLINED, canUndoApplied, declinedGroups, isCanvasPlan, pendingConfirm, reviewScriptIds, useChangePlanStore, versionNumber, type PlanEntry } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { cn } from '@/lib/utils'
import { Card } from '../ui/Card'
import { Button } from '../ui/Button'
import { Check, Lock, Pencil, RefreshCw } from '../ui/icons'
import { PremiumLock } from '../license/PremiumLock'
import { ICON_SIZE } from '../ui/Icon'
import { StatusPill } from '../ui/StatusPill'
import { TaskChecklist } from '../ui/TaskChecklist'
import { ThinkingOrb } from '../ui/ThinkingOrb'
import { formatAxisLimit } from '@/lib/logPower'
import { CHANGE_PLAN_FEATURE, canRevise, cp, effectiveStatus, useChangePlanAccess, vectorCannotApply, placesPhrase, failureAction, failureText, isScriptPlan, overreachFiles, planSteps, planTally, summaryLine, unhandledViews } from './changePlanModel'
import { FigureThumb } from './FigureThumb'
import { GraphPaper, PlotSketch, type PlotStage } from './PlotSketch'
import { useImageAspect, usePanelThumbSrc, usePlanPanel, usePlanSource, useSelectionPreview } from './usePlanFigure'

/**
 * 助手里的「修改方案」卡（助手一侧，占栏宽）：修改后的缩略图是主体，标题行「修改方案」+ 右侧版本，一句话说改了什么，
 * 主按钮「查看」+ 次级「直接应用」。状态：生成中（思考小球，不自造动画）· 待确认 · 已应用 · 已放弃 · 图已改过 · 失败（一句灰字 + 一个下一步）。
 * `latest = false`（后面还有更新的待确认卡）时「查看」降成次级——除「发送」外一屏只有最新一张待确认卡的这一颗主按钮（ADR 0206 §三）。
 * 悬停缩略图换成修改前（`title` 说明，不堆文字）。
 */
/** 画框最宽的比例：再宽的图在框里上下留纸，框不再变矮——生成中三步清单要叠在上面放得下（各状态同一个框，卡片不跳） */
const MAX_THUMB_ASPECT = 2

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
  // 批注入口：与弹窗「写批注」同一套（铅笔 + 两个字的细描边胶囊），比「直接应用」轻一级，不抢主按钮
  const reviseBtn = 'rounded-full inset-ring inset-ring-border'
  // 对数轴下限：还没回答就先问，不给「查看 / 应用」；答了「不改」的组不进方案，卡面按剩下的组出图
  const ask = status === 'ready' ? pendingConfirm(entry) : undefined
  const declined = declinedGroups(entry).length > 0
  const dataAsk = ask?.confirm?.kind === 'script_data_changed'
  const askHook = dataAsk ? 'data-change-plan-confirm-data' : 'data-change-plan-confirm-log'
  const selection = useSelectionPreview(entry, status === 'ready' && declined)
  // 批注重改的入口（高级版，ADR 0206 §五）：只在审阅 / 助手的图内修改卡上；授权入口没上线整个不画，免费用户带锁点了不进入
  const access = useChangePlanAccess()
  const reviseEntry = access.visible && canRevise(entry)

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
  const shownAfter = entry.status === 'applied' && entry.appliedPartial ? (entry.appliedSrc ?? thumb) : status === 'ready' && declined ? (selection.url ?? beforeSrc) : entry.afterSrc
  const dim = status === 'discarded' || status === 'stale'
  const handled = plan ? planTally(entry).done : 0
  // 「撤销」只在这张图还是我们写的那一份时给：用户之后又改过它，就不替他回到改前
  // 脚本修改方案：不在文档历史里（⌘Z 不撤脚本），卡片上的「撤销」按备份还原；后端核「脚本还是应用之后那一版」
  const script = isScriptPlan(entry)
  const canUndo = status === 'applied' && (script ? !!plan?.applied && !entry.scriptBusy : !!panel && canUndoApplied(entry, panel.overrides))
  // 分量：待处理（生成中 / 待确认 / 图已改过）是完整卡面 + 微投影；已应用沉到次级卡面；已放弃再降一档、预览区收矮
  const pending = generating || status === 'ready' || status === 'stale'
  const busyText = entry.revising !== undefined ? cp('card.revising') : entry.kind === 'vector' ? cp('card.generatingVector') : entry.from === 'assistant' ? cp('card.generatingAssistant') : entry.commentCount > 0 ? cp('card.generating', { count: entry.commentCount }) : cp('card.generatingAny')
  // 生成中的三步清单（一键排版的整版卡没有阶段，照旧一句话）
  const steps = generating ? planSteps(entry) : null
  // 有三步清单的卡（生成中 / 刚出好待确认）正文区同高
  const activeStep = steps?.find((x) => x.state === 'active')?.id
  const plotStage: PlotStage = activeStep === 'before' ? 'read' : activeStep === 'after' ? 'draw' : 'think'
  const freshFlow = !!steps || (status === 'ready' && !isCanvasPlan(entry))

  return (
    <Card
      appearance={pending ? 'raised' : 'subtle'}
      padding="sm"
      className={cn(pending && 'bg-field', status === 'discarded' && 'opacity-80')}
      data-change-plan-card={generating ? 'generating' : status}
    >
      {/* 标题在图上面、三步清单在图下面（用户 2026-11-26） */}
      <div className="flex items-baseline justify-between gap-2 px-1.5 pb-2 pt-0.5">
        <span className="text-base font-medium text-ink">{cp('card.title')}</span>
        {!generating && plan && <span className="type-meta">{cp('card.version', { n: versionNumber(entry) })}</span>}
      </div>
      <div className="group/thumb" title={showAfter ? cp('card.holdBefore') : undefined}>
        <FigureThumb
          src={showAfter ? shownAfter : status === 'discarded' ? thumb : beforeSrc}
          alt={cp('card.thumbAlt')}
          aspect={status === 'discarded' ? 2.8 : Math.min(aspect, MAX_THUMB_ASPECT)}
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
          {/* 生成中：坐标纸上一幅正在作图的小画，跟着清单走到的那一步（用户 2026-11-26：传达品牌、不重复上面的思考小球） */}
          {generating && (
            <span aria-hidden data-change-plan-frost className="absolute -inset-px overflow-hidden rounded-md bg-paper">
              <GraphPaper />
              <PlotSketch stage={plotStage} />
            </span>
          )}
        </FigureThumb>
      </div>
      {/* 正文区留够三步清单的高度：从生成中到出好卡片不变高（用户 2026-11-26「完成时卡片不要缩短」） */}
      <div className={cn('flex flex-col gap-2 px-1.5 pb-1 pt-2.5', freshFlow && 'min-h-21.5')} data-change-plan-body>
        {steps ? (
          <>
            <p className="sr-only" role="status" data-change-plan-generating>
              {busyText}
            </p>
            <TaskChecklist bare steps={steps} className="-mx-1.5 bg-transparent px-1.5 py-0" data-change-plan-steps />
          </>
        ) : generating ? (
          <p className="flex items-center gap-2 text-sm text-ink-2" data-change-plan-generating>
            <ThinkingOrb form="solving" />
            <span>{busyText}</span>
          </p>
        ) : (
          <p className={cn('flex items-center gap-1.5 text-sm', status === 'discarded' ? 'text-ink-3' : 'text-ink-2')}>
            {status === 'applied' && <Check size={ICON_SIZE.sm} className="shrink-0 text-ok" />}
            {status === 'applied'
              ? script
                ? cp('card.appliedScript')
                : entry.kind === 'layout'
                ? cp('card.appliedLayout', { count: entry.layout?.count ?? 0 })
                : entry.kind === 'vector'
                  ? cp('card.appliedVector', { count: entry.vector?.count ?? 0 })
                : entry.from === 'assistant'
                  ? cp('card.appliedAssistant', { places: placesPhrase(planTally(entry).places) })
                  : cp('card.applied', { count: handled })
              : status === 'discarded'
                ? cp('card.discarded')
                : status === 'stale'
                  ? cp('card.stale')
                  : ask?.confirm?.kind === 'script_data_changed'
                    ? cp('card.dataChangedAsk')
                    : ask?.confirm?.kind === 'log_lower_limit'
                      ? cp(`card.logLimitAsk`, { axis: cp(ask.confirm.axis === 'x' ? 'card.axisX' : 'card.axisY'), to: formatAxisLimit(ask.confirm.to, true) })
                      : summaryLine(entry)}
          </p>
        )}
        {entry.notice && <p className="text-xs text-ink-3" data-change-plan-notice={entry.notice}>{cp(entry.notice === 'apply_failed' ? (script ? 'card.applyFailedScript' : 'card.applyFailed') : script ? 'card.undoFailedScript' : 'card.undoFailed')}</p>}
        {/* 生成中先把两颗按钮摆好（灰着）：出好时只是亮起来，卡片不挪 */}
        {generating && !steps && (
          <div className="flex items-center gap-2 pt-0.5" data-change-plan-pending-actions>
            <Button variant="secondary" size="md" className={btn} disabled>
              {cp('card.view')}
            </Button>
            <Button variant="secondary" size="md" className={btn} disabled>
              {cp('card.applyNow')}
            </Button>
          </div>
        )}
        {!generating && (
          <div className="flex items-center gap-2 pt-0.5">
            {ask && (
              <>
                <Button variant={primary} size="md" className={btn} onClick={() => store().answerConfirm(entry.key, ask.key, 'yes')} {...{ [askHook]: 'yes' }}>
                  {cp(dataAsk ? 'card.dataChangedYes' : 'card.logLimitYes')}
                </Button>
                <Button variant="secondary" size="md" className={btn} onClick={() => store().answerConfirm(entry.key, ask.key, 'no')} {...{ [askHook]: 'no' }}>
                  {cp(dataAsk ? 'card.dataChangedNo' : 'card.logLimitNo')}
                </Button>
              </>
            )}
            {status === 'ready' && !ask && (
              <>
                {/* 对比图没出来（整版预览失败）时没有可看的：只留「直接应用」 */}
                {!!entry.afterSrc && (
                  <Button variant={primary} size="md" className={btn} onClick={() => store().openDialog(entry.key)} data-change-plan-view>
                    {cp('card.view')}
                  </Button>
                )}
                <Button variant="secondary" size="md" className={btn} onClick={() => void store().apply(entry.key)} disabled={entry.scriptBusy || vectorCannotApply(entry)} data-change-plan-apply>
                  {cp('card.applyNow')}
                </Button>
                {reviseEntry && !!entry.afterSrc && (
                  <ReviseLock>
                    <Button variant="ghost" size="md" className={reviseBtn} title={cp('card.markRevise')} onClick={() => store().openDialog(entry.key, { view: 'annotate' })} data-change-plan-mark>
                      <ReviseIcon pro={access.pro} />
                      {cp('card.markReviseLabel')}
                    </Button>
                  </ReviseLock>
                )}
              </>
            )}
            {status === 'ready' && entry.vector?.failed && (
              <Button variant="ghost" className="-ml-1" onClick={() => entry.vector?.recompute?.(true)} data-change-plan-vector-retry>
                {cp('card.retry')}
              </Button>
            )}
            {canUndo && (
              <Button variant="ghost" className="-ml-3" onClick={() => void store().undo(entry.key)} data-change-plan-undo>
                {cp('card.undo')}
              </Button>
            )}
            {canUndo && reviseEntry && (
              <ReviseLock>
                <Button
                  variant="ghost"
                  size="md"
                  className={reviseBtn}
                  title={cp('card.reviseAgainTip')}
                  // 只打开批注，不撤回：画布保持已应用的样子；点「按批注重改」时才（在能撤回的前提下）撤回这一次
                  onClick={() => store().openDialog(entry.key, { view: 'annotate' })}
                  data-change-plan-revise-again
                >
                  <ReviseIcon pro={access.pro} />
                  {cp('card.reviseAgain')}
                </Button>
              </ReviseLock>
            )}
            {!entry.scriptStarted && !ask && (status === 'ready' || status === 'applied') && (entry.from === 'assistant' ? (plan?.unhandled ?? []).some((u) => u.reason === 'needs_script') : reviewScriptIds(entry).length > 0) && (
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

/** 批注入口的图标：高级版是铅笔；免费用户换成锁（与弹窗「对比 / 批注」切换上的锁一致），一眼看出是高级功能 */
function ReviseIcon({ pro }: { pro: boolean }) {
  return pro ? <Pencil size={ICON_SIZE.sm} aria-hidden /> : <Lock size={ICON_SIZE.sm} aria-hidden className="text-ink-2" />
}

/** 批注入口的锁：高级版原样；免费用户置灰、悬停一句话、点一下去授权行（不进入、不撤回）。授权入口没上线时调用方不画 */
function ReviseLock({ children }: { children: React.ReactElement }) {
  const { pro } = useChangePlanAccess()
  if (pro) return children
  const tip = (
    <>
      {cp('dialog.lockTip')}
      <span className="mt-2 block text-ink">{cp('dialog.lockLearn')}</span>
    </>
  )
  return (
    <PremiumLock feature={CHANGE_PLAN_FEATURE} side="top" arrow soft tip={tip}>
      {children}
    </PremiumLock>
  )
}

/**
 * 失败的卡：一句灰字（稳定码 → `errors:backend.*`）+ 一个可走的下一步——连接 AI / 重试 / 重新生成 / 收起，绝不报错卡住、图一个字没动。
 * 「没有一条意见能落成图内修改」（`change_plan_empty`）把没处理的几条并列在下面。
 */
function FailedCard({ entry }: { entry: PlanEntry }) {
  const code = entry.failure?.code ?? 'change_plan_failed'
  const action = failureAction(code, entry.failure?.params)
  const store = useChangePlanStore.getState
  const un = code === 'change_plan_empty' ? unhandledViews(entry) : []
  const assistant = entry.from === 'assistant'
  const label = { connect: cp('card.connect'), retry: cp('card.retry'), regenerate: cp('card.regenerate'), dismiss: cp('card.dismiss'), script: cp('card.retry'), restore: cp('card.restoreScript') }[action]
  const run = () => {
    // 连上 AI 之后接着生成（同一份请求），不用再回来点一次
    if (action === 'connect') requestConnectAi({ onConnected: () => void store().regenerate(entry.key) })
    else if (action === 'dismiss') store().dismiss(entry.key)
    else if (action === 'restore') void store().restoreScript(entry.key)
    else if (action === 'script') void store().runScript(entry.key)
    else void store().regenerate(entry.key)
  }
  // 助手来源：用户的指令不能掉在地上——方案没出来时，另给一条「改脚本做」的路（现有改脚本路径）
  // 审阅来源：没有一条能落成图内修改、但有「要改脚本」的意见时，同样给这条路（只由用户点，不自动续走）
  const scriptWay = code !== DATA_DECLINED && entry.kind !== 'vector' && (assistant ? true : code === 'change_plan_empty' && !entry.scriptStarted && reviewScriptIds(entry).length > 0) && action !== 'script' && action !== 'restore'
  return (
    <Card appearance="subtle" padding="sm" data-change-plan-card="failed" data-change-plan-code={code}>
      <div className="flex flex-col gap-2 px-1.5 py-1">
        <span className="text-base font-medium text-ink">{cp('card.title')}</span>
        <p className="text-sm text-ink-3">{failureText(code, entry.failure?.params, assistant)}</p>
        {entry.notice === 'restore_failed' && (
          <p className="text-xs text-ink-3" data-change-plan-notice="restore_failed">
            {cp('card.restoreFailed')}
          </p>
        )}
        {code === 'change_plan_script_overreach' && overreachFiles(entry.failure?.params) && (
          <p className="type-meta break-all" data-change-plan-overreach-files>
            {overreachFiles(entry.failure?.params)}
          </p>
        )}
        {un.length > 0 && (
          <ul className="flex flex-col gap-1">
            {un.map((u) => (
              <li key={u.id} className="flex flex-col" data-change-plan-unhandled={u.id}>
                <span className="truncate text-sm text-ink-3">{u.text}</span>
                {(u.reviewer || u.reason) && <span className="type-meta">{[u.reviewer, u.reason].filter(Boolean).join(' · ')}</span>}
              </li>
            ))}
          </ul>
        )}
        <div className="flex items-center gap-1 pt-0.5">
          <Button variant="ghost" className="-ml-3" onClick={run} disabled={entry.scriptBusy} data-change-plan-failed-action={action}>
            {label}
          </Button>
          {scriptWay && (
            <Button variant="ghost" onClick={() => void store().runScript(entry.key)} data-change-plan-run-script>
              {cp('card.runScriptAll')}
            </Button>
          )}
          {action !== 'dismiss' && (
            <Button variant="ghost" onClick={() => store().dismiss(entry.key)} data-change-plan-dismiss>
              {action === 'restore' ? cp('card.keepCurrent') : cp('card.dismiss')}
            </Button>
          )}
        </div>
      </div>
    </Card>
  )
}
