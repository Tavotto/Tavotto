import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { MAX_NOTE_CHARS, notesOf, REVISE_UNDO_BLOCKED, useChangePlanStore, versionNumber, versionsOf, type PlanNote } from '@/store/changePlanStore'
import { PremiumLock } from '../license/PremiumLock'
import { useDocumentStore } from '@/store/documentStore'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { Lock, Pencil } from '../ui/icons'
import { ICON_SIZE } from '../ui/Icon'
import { Segmented } from '../ui/Segmented'
import { Select } from '../ui/Select'
import { DiffView } from '../ai/DiffView'
import { ChangeList, NoteList, RevisingNoteList } from './ChangeList'
import { CHANGE_PLAN_FEATURE, canRevise, cp, effectiveStatus, failureText, groupViews, isScriptPlan, markNoteHintSeen, noteHintSeen, noteResults, planSteps, scriptInfo, unhandledViews, useChangePlanAccess, type PlanBox } from './changePlanModel'
import { CompareStage, type CompareMode, type NoteEditing, type NoteTarget } from './CompareStage'
import { failedChecks } from '@/lib/vectorEdit'
import { useImageAspect, usePlanManifest, usePlanPanel, usePlanSource, useSelectionPreview } from './usePlanFigure'
import { noteLabelOf } from './noteTarget'
import { usePanelDisplayManifest } from '@/store/renderStore'
import { DURATION, prefersReducedMotion } from '@/lib/motion'

type View = 'compare' | 'annotate'

/** 新版刚出来时改动框全亮多久（之后照常按 `duration-slow` 淡出）；减少动效时整段不放 */
const LANDING_FLASH_MS = 1000

const newNoteId = () => `n${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`

/**
 * 修改方案弹窗（`ui/Dialog` 的 shell 外壳，按窗口取大：宽 min(1280, 92vw)、高 min(860, 90vh)）：左侧对比舞台、右侧改动清单。
 * 顶栏左「修改方案」+ 版本切换（出过第 2 版才有：两版紧凑分段，更多版收成下拉），右「对比 / 批注」切换（位置固定）；Esc / × = 关闭，方案留着、卡片仍待确认。
 * - 对比：滑动 / 并排 / 切换，右栏是改动清单，页脚「放弃」｜「写批注」（次级）+「应用」（**主按钮**）。
 * - 批注（高级版 `change-plan-rerun`，ADR 0206 §五）：舞台只有「修改后」的大图，点元素 / 框选 / 整体写批注（`Popover` 锚在那一处）；右栏是批注列表；
 *   页脚「放弃」+「按批注重改 · N」（N = 0 禁用、次级样式；N ≥ 1 主），批注视图里没有「应用」（维护者 2026-11-28）。出好后自动回到「对比」并选中新版本；
 *   重改进行中：舞台是上一版「修改后」压暗降饱和、批注编号钉在锚点上，三步清单（跟后端阶段）在图下面排一行；右栏是这次发出的批注，
 *   清单头只诚实地说「正在按这 N 条批注改」（后端没有逐条进度，不逐条打勾）。新版出来：旧图淡出露出对比、改动框全亮约 1 秒再淡出，
 *   右栏顶部逐条标结果（`noteResults`：改成了 = 它那一组，没改成 = 一句原因；脚本方案对不上，只说一句）；减少动效时直接切换。
 *   没解锁的用户：切换项与「写批注」带锁（`PremiumLock`），点了只弹一句话 +「了解高级版 ›」，不进入；授权入口没出现（上线开关关着）时这些入口整个不画。
 * 第 2 版起「修改前」默认是上一版的「修改后」（标「第 N−1 版 / 第 N 版」），要看原图切回第 1 版；「应用」仍是这一版对原图的全量补丁。
 * 「应用」= 一次 commit 进排版时间线，⌘Z 一步撤回（`changePlanStore.apply`）；全部取消勾选时禁用。
 */
export function ChangePlanDialog() {
  useTranslation('ai')
  const key = useChangePlanStore((s) => s.dialogKey)
  const entry = useChangePlanStore((s) => (s.dialogKey ? s.entries[s.dialogKey] : undefined))
  const [mode, setMode] = useState<CompareMode>('slide')
  const [view, setView] = useState<View>('compare')
  const [editing, setEditing] = useState<NoteEditing | null>(null)
  const [armed, setArmed] = useState(false)
  const [activeId, setActiveId] = useState<string | null>(null)
  // 脚本修改方案：左侧舞台在「对比图 / 脚本改动」之间切（弹窗不新造风格，diff 用现成的 `DiffView`）
  const [stage, setStage] = useState<'figure' | 'script'>('figure')
  const access = useChangePlanAccess()
  // 一键排版 / 矢量编辑的卡不能批注重改：入口（对比 / 批注切换、写批注、悬停写批注）整个不出现；脚本方案可以（ADR 0206 修订 2026-11-28）
  const revisable = !!entry && canRevise(entry)
  const visible = access.visible && revisable
  const pro = access.pro && revisable
  const panel = usePlanPanel(entry ?? { panelId: '', documentId: '', loadSeq: 0, canvasId: '' })
  const aspect = useImageAspect(entry?.afterSrc)
  // 换一份方案 / 换版本 / 重新打开：高亮与批注框不带过去，新版本出来回到「对比」
  const planId = entry?.planId
  const [landing, setLanding] = useState<{ from: string; lit: boolean } | null>(null)
  useEffect(() => {
    setActiveId(null)
    setEditing(null)
    // 卡片上的批注入口带来初始视图（取一次就清）：免费用户 `annotating` 本来就不成立，不会进
    setView(key ? (useChangePlanStore.getState().consumeDialogView() ?? 'compare') : 'compare')
    setStage('figure')
    setLanding(null)
  }, [key, planId])
  // 新版刚出来（上一刻还在重改、这一刻换成了新的一版且没报错）：重改时那张旧图淡出、改动框全亮一下。失败 / 手动切版本不触发
  const last = useRef<{ planId?: string; revising: boolean; after?: string }>({ revising: false })
  const nowRevising = entry?.revising !== undefined
  useEffect(() => {
    const was = last.current
    if (was.revising && !nowRevising && planId && planId !== was.planId && !entry?.reviseError && was.after && !prefersReducedMotion()) setLanding({ from: was.after, lit: true })
    last.current = { planId, revising: nowRevising, after: entry?.afterSrc }
  }, [planId, nowRevising, entry?.afterSrc, entry?.reviseError])
  // 先全亮 LANDING_FLASH_MS，再撤亮让框按 `duration-slow` 淡出，淡完收掉
  const lit = !!landing?.lit
  useEffect(() => {
    if (!landing) return
    const t = lit ? window.setTimeout(() => setLanding((l) => (l ? { ...l, lit: false } : l)), LANDING_FLASH_MS) : window.setTimeout(() => setLanding(null), DURATION.slow)
    return () => window.clearTimeout(t)
  }, [!!landing, lit]) // eslint-disable-line react-hooks/exhaustive-deps
  // 第一次打开（高级版）：「写批注」上方出一次轻提示，点任意处或 5 秒后消失，本机记住
  const [hint, setHint] = useState(false)
  const shown = !!key
  useEffect(() => {
    if (!shown || !pro || view !== 'compare' || noteHintSeen()) return
    setHint(true)
    const done = () => {
      setHint(false)
      markNoteHintSeen()
    }
    const t = window.setTimeout(done, 5000)
    window.addEventListener('pointerdown', done, { once: true, capture: true })
    return () => {
      window.clearTimeout(t)
      window.removeEventListener('pointerdown', done, { capture: true })
      setHint(false)
    }
  }, [shown, pro, view])
  // 弹窗开着时图被改了：基线对不上，不让点「应用」（卡片也已转「图已改过」）
  const source = usePlanSource(panel)
  useDocumentStore((s) => s.past.at(-1))
  useDocumentStore((s) => s.future.at(-1))
  const status = entry ? effectiveStatus(entry, panel?.overrides ?? null, source) : 'stale'
  // 已应用的条目（从卡片「再改一版」进来）只有批注一个视图：图保持已应用，关弹窗什么都不发生
  const appliedView = status === 'applied' && pro
  const annotating = pro && (view === 'annotate' || appliedView)
  // 进过批注视图就算看过「写批注」那句提示（不论从哪个入口进的）：重改完回到对比不再弹
  useEffect(() => {
    if (!shown || !annotating) return
    setHint(false)
    markNoteHintSeen()
  }, [shown, annotating])
  // 重改进行中也要：批注编号按当前版本元素表里的框钉
  const manifest = usePlanManifest(entry, pro && (armed || annotating || nowRevising))
  const store = useChangePlanStore.getState
  const plan = entry?.plan
  const open = !!entry && !!plan && !!entry.beforeSrc && !!entry.afterSrc && (status === 'ready' || appliedView)
  // 「修改后」跟着勾选的组合走：与「应用」写入的是同一份
  const preview = useSelectionPreview(entry, open)
  // 第 2 版起「修改前」默认是上一版的「修改后」（只改显示；应用仍是这一版对原图的全量补丁）。看原图 = 切回第 1 版
  const recs = entry ? versionsOf(entry) : []
  const current = entry ? versionNumber(entry) : 1
  const prevRec = current >= 2 ? recs[current - 2] : undefined
  // 上一版「修改后」的元素表：左侧改动框按它画（取到之前先用上一版自己记的框）
  // 脚本方案没有逐处改动框，不取（左侧只是上一版的图）
  const prevManifest = usePlanManifest(entry && prevRec ? { ...entry, plan: prevRec.plan } : undefined, open && !!prevRec && !annotating && plan?.kind !== 'script')
  // 改动清单里的元素名：这张图的元素表（只用来显示名字）
  const shownManifest = usePanelDisplayManifest(panel)
  if (!entry || !plan || !entry.beforeSrc || !entry.afterSrc) return null
  const script = isScriptPlan(entry)
  const info = scriptInfo(entry)
  // 脚本方案只有一个 `atomic` 的脚本组：整组要或不要，没有逐组勾选（不要就「放弃」）；应用不进文档历史
  const labelMap = new Map((shownManifest?.elements ?? []).map((x) => [x.gid, x.label]))
  const labelOf = (gid: string) => {
    const raw = labelMap.get(gid)
    return raw ? noteLabelOf(raw) : undefined
  }
  const groups = script ? [] : groupViews(entry, labelOf)
  const chosen = groups.filter((g) => !entry.unchecked[g.key])
  // 对比上一版时，左侧的框取这一处在上一版里的位置：上一版元素表 → 上一版方案记的「修改后」框 → 原图里的位置
  const prevBoxOf = (gid: string | undefined): PlanBox | undefined => {
    if (!gid || !prevRec) return undefined
    const el = prevManifest?.elements.find((x) => x.gid === gid)
    if (el) return el.bbox
    for (const g of prevRec.plan.groups) {
      const b = g.boxes.after.find((x) => x.gid === gid)
      if (b) return b.box
    }
    return undefined
  }
  // 一组都没勾 =「修改后」就是原图：两边都按原图说，不拿上一版对原图。脚本方案只有一个整组（没有勾选），第 2 版起照样对比上一版
  const cmpPrev = script || chosen.length > 0 ? prevRec : undefined
  const stageGroups = cmpPrev ? chosen.map((g) => ({ ...g, boxes: g.boxes.map((b) => ({ ...b, before: prevBoxOf(b.gid) ?? b.before })) })) : chosen
  const stageBefore = cmpPrev?.afterSrc ?? entry.beforeSrc
  const stageLabels = cmpPrev ? { before: cp('dialog.versionN', { n: current - 1 }), after: cp('dialog.versionN', { n: current }) } : undefined
  const canApply = status === 'ready' && (script ? !entry.scriptBusy : chosen.length > 0 && !entry.vector?.busy && !entry.vector?.failed)
  // 答了「不改」的组不在清单里，但仍是没勾选的组：按方案的全部组数判「部分」，出图才不带上它
  const partial = !script && chosen.length < (entry.plan?.groups.length ?? groups.length)
  // 一组都没勾 = 不改，「修改后」就是改前；部分勾选 = 按勾选出的那张（还没出来 / 失败时先用全量图，失败另有一句灰字）
  const after = script ? entry.afterSrc : chosen.length === 0 ? entry.beforeSrc : partial && preview.url ? preview.url : entry.afterSrc
  const previewBusy = partial && preview.loading
  // 只有「出好的、且正是此刻勾选组合」的那张才交给卡片当实际应用的图
  const keysSig = chosen.map((g) => g.key).join('\u0000')
  const appliedSrc = partial && !preview.loading && !preview.failed && preview.sig === keysSig ? (preview.url ?? undefined) : undefined

  const notes = notesOf(entry)
  const revising = entry.revising !== undefined
  const sent = entry.revisingNotes ?? notes
  const results = revising ? null : noteResults(entry)
  const reviseError = entry.reviseError
    ? entry.reviseError.code === 'not_licensed'
      ? cp('dialog.reviseNotLicensed')
      : entry.reviseError.code === REVISE_UNDO_BLOCKED
        ? cp('dialog.reviseUndoBlocked')
        : failureText(entry.reviseError.code, entry.reviseError.params)
    : undefined
  const edit = (n: PlanNote) => setEditing({ id: n.id, target: { anchor: n.anchor, label: n.label } })
  const saveNote = (id: string | undefined, target: NoteTarget, text: string) =>
    store().saveNote(entry.key, { id: id ?? newNoteId(), anchor: target.anchor, label: target.label, text: text.slice(0, MAX_NOTE_CHARS) })
  const toVersion = (v: string) => store().setViewVersion(entry.key, v)
  const versionOptions = recs.map((v, i) => ({ value: v.planId, label: cp('dialog.versionN', { n: i + 1 }) }))
  // 版本切换在左上、紧挨标题（两版用紧凑分段，更多版收成下拉）；「对比 / 批注」固定在右上，不随版本出现而挪位
  const versionSwitch =
    recs.length > 2 ? (
      <Select<string> value={entry.planId ?? ''} onChange={toVersion} ariaLabel={cp('dialog.versionLabel')} className="w-24" options={versionOptions} />
    ) : (
      <Segmented<string> compact value={entry.planId ?? ''} onChange={toVersion} ariaLabel={cp('dialog.versionLabel')} className="w-32 font-normal" items={versionOptions} />
    )
  // 免费用户点带锁的入口：只在原地弹这一句 +「了解高级版 ›」，不直接跳设置（维护者 2026-11-28）
  const lockTip = cp('dialog.lockTip')
  const lockLearn = cp('dialog.lockLearn')
  // 「批注」切换项（图内方案的「对比 / 批注」与脚本方案的「对比图 / 脚本改动 / 批注」共用）：免费用户带锁，点了只弹一句
  const annotateItem = {
    value: 'annotate' as const,
    label: (
      <>
        {!pro && <Lock size={ICON_SIZE.xs} aria-hidden className="text-ink-2" />}
        <span className={pro ? undefined : 'text-ink-2'}>{cp('dialog.modeAnnotate')}</span>
      </>
    ),
    wrap: (btn: React.ReactElement) => (
      <PremiumLock feature={CHANGE_PLAN_FEATURE} side="bottom" align="end" alignOffset={-40} arrow soft tip={lockTip} learn={lockLearn}>
        {btn}
      </PremiumLock>
    ),
  }
  const rerunBtn = (
    <Button
      variant={notes.length === 0 ? 'secondary' : 'primary'}
      size="lg"
      data-change-plan-rerun
      loading={revising}
      loadingLabel={cp('dialog.rerunning')}
      disabled={notes.length === 0}
      title={notes.length === 0 ? cp('dialog.rerunNeedNote') : undefined}
      onClick={() => void store().revise(entry.key)}
    >
      {notes.length === 0 ? cp('dialog.rerun') : cp('dialog.annotateRerun', { count: notes.length })}
    </Button>
  )
  const applyBtn = (
    <Button
      variant="primary"
      size="lg"
      onClick={() => void (script ? store().applyScript(entry.key) : store().apply(entry.key, { previewSrc: appliedSrc }))}
      disabled={!canApply || revising}
      data-change-plan-confirm
    >
      {cp('dialog.apply')}
    </Button>
  )

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => !v && store().closeDialog()}
      title={
        <span className="flex items-center gap-3">
          {cp('dialog.title')}
          {recs.length > 1 && versionSwitch}
        </span>
      }
      chrome="shell"
      width="min(1280px, 92vw)"
      height="min(860px, 90vh)"
      anchor="change-plan"
      headerEnd={
        // 脚本方案：「对比图 / 脚本改动」放在右上（与图内方案的「对比 / 批注」同一个位置），不和下面的「滑动 / 并排 / 切换」挤成一排；
        // 能批注重改时「批注」是同一排的第三项（不另起一排切换）；已应用（「再改一版」进来）只有批注一个视图，不画切换
        script ? (
          appliedView ? undefined : (
            <Segmented<'figure' | 'script' | 'annotate'>
              value={annotating ? 'annotate' : stage}
              onChange={(v) => {
                if (v === 'annotate') return setView('annotate')
                setView('compare')
                setStage(v)
              }}
              ariaLabel={cp('dialog.stageLabel')}
              className={visible ? 'w-60' : 'w-44'}
              items={[
                { value: 'figure', label: cp('dialog.stageFigure') },
                { value: 'script', label: cp('dialog.stageScript') },
                ...(visible ? [annotateItem] : []),
              ]}
            />
          )
        ) : visible && !appliedView ? (
          <Segmented<View>
            value={annotating ? 'annotate' : 'compare'}
            onChange={setView}
            ariaLabel={cp('dialog.modeLabel')}
            className="w-40"
            items={[
              { value: 'compare', label: cp('dialog.modeCompare') },
              annotateItem,
            ]}
          />
        ) : undefined
      }
      footer={{
        // 右侧两个位置在两个模式间连续：左边是「写批注」（对比）/「按批注重改 · N」（批注），右边是「应用」；每屏只有一颗主按钮
        // 「放弃」与「应用」挨在一起（仓库对话框的惯例：取消 / 放弃在主按钮左边，`start` 只给破坏性的另一条路）
        // 已应用的条目（「再改一版」进来）：没有「放弃」与「应用」，右侧只剩「按批注重改」一颗
        secondary: appliedView ? undefined : (
          <>
            <Button variant="ghost" size="lg" onClick={() => store().discard(entry.key)} data-change-plan-discard>
              {cp('dialog.discard')}
            </Button>
            {annotating ? null : visible ? (
          <span className="relative inline-flex">
            {hint && (
              <span
                data-change-plan-first-hint
                role="status"
                className="pointer-events-none absolute bottom-full right-0 mb-2 inline-flex h-7 items-center whitespace-nowrap rounded-full bg-surface px-3 text-xs text-ink shadow-pop inset-ring inset-ring-border"
              >
                {cp('dialog.firstHint')}
              </span>
            )}
            <PremiumLock feature={CHANGE_PLAN_FEATURE} side="top" align="center" arrow soft tip={lockTip} learn={lockLearn}>
              <Button variant="secondary" size="lg" className="rounded-full inset-ring inset-ring-border" data-change-plan-write-note onClick={() => setView('annotate')}>
                <Pencil size={ICON_SIZE.sm} aria-hidden />
                {cp('dialog.writeNote')}
              </Button>
            </PremiumLock>
          </span>
        ) : undefined}
          </>
        ),
        // 批注视图右下只有「按批注重改 · N」（N = 0 时禁用、次级样式）；「应用」只在对比视图
        primary: annotating ? rerunBtn : applyBtn,
      }}
    >
      <div className="flex min-h-0 flex-1" data-change-plan-dialog data-change-plan-dialog-view={annotating ? 'annotate' : 'compare'} data-change-plan-version={current}>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col bg-canvas">
          <div className="flex h-11 shrink-0 items-end justify-center px-5">
            {!annotating && !revising && (!script || stage === 'figure') && (
              <Segmented<CompareMode>
                value={mode}
                onChange={setMode}
                ariaLabel={cp('dialog.compareLabel')}
                compact
                className="w-60"
                items={[
                  { value: 'slide', label: cp('dialog.slide') },
                  { value: 'side', label: cp('dialog.side') },
                  { value: 'toggle', label: cp('dialog.toggle') },
                ]}
              />
            )}
          </div>
          {revising ? (
            // 舞台用他的图：上一版「修改后」压暗、批注编号钉在锚点上，三步清单（跟后端阶段）在图下面一行
            <CompareStage
              before={stageBefore}
              after={entry.afterSrc}
              aspect={aspect}
              mode="revising"
              groups={[]}
              activeId={null}
              revising={{ notes: sent, manifest, steps: planSteps(entry), status: cp('card.revising') }}
            />
          ) : script && stage === 'script' && info && !annotating ? (
            <div className="flex min-h-0 flex-1 flex-col px-5 pb-5" data-change-plan-script-diff>
              <DiffView diff={info.diff} script={info.rel} variant="panel" />
            </div>
          ) : (
          <CompareStage
            intro={annotating ? undefined : landing?.from}
            flash={!annotating && landing ? { keys: new Set(groups.filter((g) => g.fromNote).map((g) => g.key)), lit: landing.lit } : undefined}
            before={stageBefore}
            labels={stageLabels}
            // 批注写在这一版完整的「修改后」上（重改从它出发），不按勾选的组合出图
            after={annotating ? entry.afterSrc : after}
            afterBusy={!annotating && (previewBusy || !!entry.vector?.busy)}
            aspect={aspect}
            mode={annotating ? 'annotate' : mode}
            groups={stageGroups}
            activeId={activeId}
            quick={
              // 悬停点选用的是完整「修改后」的元素表：只有对比里显示的正是它（全部勾选）才开；脚本方案的「修改后」就是整份候选出的图
              pro && !annotating && (script || (!partial && chosen.length === groups.length && groups.length > 0))
                ? {
                    manifest,
                    onArm: () => setArmed(true),
                    onPick: (target) => {
                      setView('annotate')
                      setEditing({ target })
                    },
                  }
                : undefined
            }
            noting={
              annotating
                ? {
                    manifest,
                    notes,
                    editing,
                    onEdit: setEditing,
                    onSave: saveNote,
                    onRemove: (id) => store().removeNote(entry.key, id),
                  }
                : undefined
            }
          />
          )}
        </div>
        <div className="flex w-[19rem] shrink-0 flex-col border-l border-border">
          {revising ? (
            <RevisingNoteList notes={sent} />
          ) : annotating ? (
            <NoteList
              notes={notes}
              onOpenNote={edit}
              onRemove={(id) => store().removeNote(entry.key, id)}
              onOverall={() => setEditing({ target: { anchor: { kind: 'overall' }, label: cp('dialog.noteOverallLabel') } })}
              error={reviseError}
            />
          ) : (
            <>
              {partial && preview.failed && (
                <p className="shrink-0 px-4 pt-3 type-meta" data-change-plan-preview-failed>
                  {cp('dialog.previewStale')}
                </p>
              )}
              {entry.vector?.failed && (
                <p className="flex shrink-0 items-center justify-between gap-2 px-4 pt-3 text-sm text-ink" data-change-plan-vector-failed>
                  <span>{cp('dialog.vectorRecomputeFailed')}</span>
                  <Button variant="ghost" size="sm" className="-mr-2 text-ink underline underline-offset-2" onClick={() => entry.vector?.recompute?.(true)} data-change-plan-vector-retry>
                    {cp('card.retry')}
                  </Button>
                </p>
              )}
              {entry.vector?.result && failedChecks(entry.vector.result) > 0 && (
                <p className="shrink-0 px-4 pt-3 type-meta" data-change-plan-vector-checks>
                  {cp('dialog.vectorChecks', { count: failedChecks(entry.vector.result) })}
                </p>
              )}
              {script && info ? (
                <div className="flex flex-col gap-1 px-4 pt-4" data-change-plan-script-meta>
                  {/* 脚本方案的批注重改：所有批注并成一条指令、只回一份脚本，对不上逐条，只说一句 */}
                  {results && (
                    <p className="mb-1 border-b border-border pb-2 text-sm text-ink-2" data-change-plan-note-tally>
                      {cp('dialog.noteResultScript', { count: results.notes.length })}
                    </p>
                  )}
                  <p className="type-meta">{cp('dialog.scriptMeta', { added: info.added, removed: info.removed })}</p>
                  <p className="break-all text-sm text-ink-2">{info.rel}</p>
                  <p className="type-meta">{cp('dialog.scriptBackupNote')}</p>
                </div>
              ) : (
                <ChangeList
                  muted={!!entry.vector?.failed}
                  heading={entry.kind === 'layout' ? cp('dialog.layoutHeading', { count: entry.layout?.count ?? 0 }) : entry.kind === 'vector' ? cp('dialog.vectorHeading', { count: entry.vector?.count ?? 0 }) : undefined}
                  groups={groups}
                  unhandled={unhandledViews(entry)}
                  onAction={(u) => u.action && entry.vector?.confirm?.(u.action.item, u.action.op)}
                  unchecked={entry.unchecked}
                  onToggle={(g) => store().toggleGroup(entry.key, g)}
                  activeId={activeId}
                  onActive={setActiveId}
                  noteResults={results?.rows ?? undefined}
                />
              )}
            </>
          )}
        </div>
      </div>
    </Dialog>
  )
}
