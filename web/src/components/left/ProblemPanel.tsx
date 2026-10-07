import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  CircleCheck,
  ClipboardList,
  Ellipsis,
  OctagonAlert,
  RefreshCw,
  X,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { formatRelativeTime, listJoin } from '@/i18n/format'
import { t as translate } from '@/i18n'
import { focusFailureMessage, focusIssue } from '@/lib/issueFocus'
import { problemContextNow } from '@/lib/problemContext'
import { profileName } from '@/lib/profileText'
import {
  bucketsByCategory,
  bucketsByFigure,
  cursorFor,
  cursorView,
  drillIssues,
  drillKey,
  drillOf,
  flattenGroups,
  groupIssues,
  isSplit,
  isUnverifiable,
  issuesInScope,
  singleDrill,
  type FigureBucket,
  type ProblemDrill,
  type ProblemScope,
  type ProblemView,
} from '@/lib/problemList'
import { resolveDocumentSpec } from '@/lib/specBinding'
import { cn } from '@/lib/utils'
import { SEVERITIES, type Severity } from '@/lib/profile'
import { severityLabel, subjectName } from '@/lib/validationText'
import type { ValidationIssue } from '@/lib/validation'
import { useDocumentStore } from '@/store/documentStore'
import { yieldsCanvasShortcuts } from '@/store/gestureCoordinator'
import { toCatalog, useProfileStore } from '@/store/profileStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useUiStore } from '@/store/uiStore'
import { schedule, useValidationStore } from '@/store/validationStore'
import { Button, IconButton } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import {
  Menu,
  MenuCheckItem,
  MenuItem,
  MenuLabel,
  MenuRadioGroup,
  MenuRadioItem,
  MenuSeparator,
} from '../ui/Menu'
import { Notice } from '../ui/Notice'
import { Tip } from '../ui/Tooltip'
import { DrawerHeaderActions, DrawerTitleMeta } from './DrawerHeader'
import { runBatchFix, useBatchable } from './IssueFixButton'
import { CategoryCards, FigureCards, SeverityGlyph, UnverifiableEntry } from './ProblemCards'
import { ProblemTreeContext, type ProblemTreeCtx } from './problemTree'
import { GroupNode } from './ProblemTreeRows'
import { useScopedProblems } from './useProblemScope'


/** 本组文案在 errors:problems.* 下（问题的措辞与检查项同一个命名空间） */
const pr = (key: string, values?: Record<string, unknown>) =>
  translate(`problems.${key}`, { ns: 'errors', ...(values ?? {}) })

/**
 * 左侧「问题」抽屉。**打开导出对话框才知道图有没有问题的日子到此为止。**
 *
 * 这一屏只做三件事：说清有什么问题、点一下跳到那个真实对象、能安全修的给
 * 一颗按钮。它**不自己跑检查**（`store/validationStore.ts` 唯一驱动）、
 * **不自己挑规范**（`lib/specBinding.ts` 唯一判据）、**不显示 gid**
 * （精确名词只在每行的技术详情里）。
 *
 * ### 呈现（2026-10-07 设计审计 §9.4：四层头 → 两层，卡片 → 披露树）
 *
 * * **标题行**（`LeftPanel` 的 36px 头）：「问题」+ 范围胶囊「当前图 13 ▾」（两档各带自己的数，
 *   判据 `lib/problemList`，与清单同一份）+ 正在重新检查的 shimmer；「⋯」里是分组方式（按图 / 按类别）、
 *   画布标记开关、重新检查。轨道上不挂数字：有阻断项时一颗红点，数字在可达名里。
 * * **摘要条**（32px）：等级开关（只有阻断着色）+ 唯一一颗填色主动作「全部修复 N」（`batchable()`，不含建议档）。
 *   总数与等级比例条删掉了——数字在开关上各说一遍。
 * * **树**：图（32）→ 子图（28）→ 规则（28）→ 对象（28），全部建在 `listRowClass` 上、**就地展开**；
 *   「按类别」是 类别 → 规则 → 对象。只有一张拆不出子图的图时跳过分桶层，规则直接在顶层。
 *   每行一个 88px 尾随格：静止时是值，指到 / 聚焦 / 当前时同一格换成「修复」（一个动词：修复 / 修复… / 修复 N）。
 *   「无法核验」不进分桶，在树底只占一行。
 * * **定位后清单留在原地**：点一行 = `focusIssue`，正在处理的那一条是选中底（600 + selected，
 *   不只靠颜色）；底部「上一项 / 下一项」，F8 / ⇧F8 同一个动作，走到一支的尽头接着走下一支。
 *   修好一条它会消失，「下一项」指向顶上来的那一条。指着一行时画布上那个对象描一道轮廓（`issueHover`）。
 * * **点开哪一支**：用户的开合在面板里（随现场作废）；直达（`openProblemAt`）与定位写的
 *   `uiStore.problemDrill` 让那一支**强制开着**，游标在它里面走——与卡片层时代同一份状态、同一个现场章。
 *
 * 接入状态（哪张图连没连上脚本）刻意**不混进来**：那是另一类事实，有自己的
 * 中心与自己的下一步；底部只放一条链接把用户送过去。
 */
export function ProblemPanel() {
  useTranslation(['errors', 'workspace'])
  const all = useValidationStore((s) => s.issues)
  const ready = useValidationStore((s) => s.ready)
  const failed = useValidationStore((s) => s.failed)
  const queued = useValidationStore((s) => s.queued)
  const filter = useUiStore((s) => s.problemFilter)
  const view = useUiStore((s) => s.problemView)
  const activeCanvasId = useDocumentStore((s) => s.activeCanvasId)
  // 点开的那一支与游标是**派生**的：写下它们的现场（排版 / 范围与当前图 / 切法）不是此刻就是
  // null——换项目、换当前图时没有人需要记得来清（2026-09-29 #690 评审）
  const { figureId, figureName, scope, issues, context, drill, cursor } = useScopedProblems()
  const listRef = useRef<HTMLUListElement>(null)
  /** 用户的开合：键 = drillKey，随现场作废（换范围 / 切法 / 项目都回到默认） */
  const [opened, setOpened] = useState<{ context: string; map: ReadonlyMap<string, boolean> }>(() => ({
    context,
    map: new Map(),
  }))
  const openMap = opened.context === context ? opened.map : EMPTY_MAP
  /** 用户折起的规则组（键 = 支 | 规则） */
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set())
  /** 用户点过「显示其余 N 项」的组 */
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set())
  const rechecking = useDelayed(ready && queued, 300)

  /**
   * 这一轮检查失败了，**但上一轮的结果被留着**（`validationStore` 刻意保留，
   * 见 web/AGENTS.md）。这时候清单要照常列——它们仍然算在计数与导出摘要里，
   * 藏起来等于让用户看得见数字却找不到东西。
   */
  const retained = failed && ready && all.length > 0

  const counts = useMemo(() => {
    const out: Record<Severity, number> = { error: 0, warn: 0, not_verifiable: 0, suggestion: 0 }
    for (const i of issues) out[i.severity] += 1
    return out
  }, [issues])

  const shown = useMemo(() => {
    const keep = filter?.length ? new Set(filter) : null
    return issues.filter((i) => !keep || keep.has(i.severity))
  }, [issues, filter])

  // 分桶：同一份 `shown` 的两种切法；展开一支列的、尾随格「修复 N」修的都是 `drillIssues` 那一份
  const figures = useMemo(() => bucketsByFigure(shown), [shown])
  const categories = useMemo(() => bucketsByCategory(shown), [shown])
  const unverifiable = useMemo(() => shown.filter(isUnverifiable), [shown])
  // 按图看、清单里只有一张拆不出子图的图（单子图的普通图）：分桶层只会有一支，
  // 多点一下什么也没多看到——直接列它的规则组
  const single = useMemo<ProblemDrill | null>(
    () => singleDrill(view, figures, unverifiable.length),
    [view, figures, unverifiable.length],
  )
  /** 游标在哪一支里走：直达 / 定位写下的那一支，或唯一的那一支 */
  const open = drill ?? single
  const listed = useMemo(() => (open ? drillIssues(shown, open) : []), [shown, open])
  const groups = useMemo(() => groupIssues(listed), [listed])
  const cursorAt = useMemo(() => cursorView(groups, cursor), [groups, cursor])

  /** F8 的走法：树里各支从上到下（无法核验不在里面——它们要的是人眼确认，不是逐条处理） */
  const walk = useMemo<ProblemDrill[]>(() => {
    if (single) return [single]
    if (view === 'category') return categories.map((c) => ({ kind: 'category', key: c.key }))
    return figures.flatMap<ProblemDrill>((f) =>
      isSplit(f)
        ? f.parts.map((p) => ({ kind: 'part', figure: f.key, key: p.key }))
        : [{ kind: 'figure', key: f.key }],
    )
  }, [single, view, categories, figures])
  /**
   * 最近一次「点开的那一支还在树上」时的走法（各支的 drillKey）。那一支最后一条修好、整支从树上
   * 消失之后，F8 靠它认出相邻的那一支——否则「下一项」会回到树头 / 树尾（Codex #832）
   */
  const lastWalk = useRef<readonly string[]>([])
  useEffect(() => {
    const keys = walk.map(drillKey)
    if (open && keys.includes(drillKey(open))) lastWalk.current = keys
  }, [open, walk])

  // 与「全部修复」真正执行的是**同一个集合**（`batchable`：本画布、能自动修、
  // 不含建议档）——计数说 5 项、点下去修了 7 项，是这颗按钮最不该有的样子
  const fixableHere = useBatchable(shown, activeCanvasId)
  const fixing = useUiStore((s) => s.fixing)

  // 清单空了，「正在处理第几条」就没有主语了（全修好 / 换了文档）
  useEffect(() => {
    if (cursor && groups.length === 0) useUiStore.getState().setProblemCursor(null)
  }, [cursor, groups.length])

  // 派生已经保证过期的那一支不显示；面板挂着时看到现场换了，再把记着的那份也丢掉——
  // 否则换回原来那张图（A → B → A）时，用户已经离开的那一支会复活
  useEffect(() => {
    const ui = useUiStore.getState()
    if (ui.problemContext !== context && (ui.problemDrill || ui.problemCursor)) ui.setProblemDrill(null)
  }, [context])

  // 每落下一次游标（直达 / 画布标记 / 定位 / F8），它所在的那一组若被用户折着就打开：否则游标条说「第 N 项」，
  // aria-current 那一行却没挂出来，跳了等于没跳（Codex #832）。只在游标换的那一刻打开，之后用户照样能再折起它
  useEffect(() => {
    if (!cursor || !open) return
    const key = `${drillKey(open)}|${cursor.ruleCode}`
    setCollapsed((prev) => (prev.has(key) ? toggled(prev, key) : prev))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cursor])

  // 游标换了之后把那一行滚进清单的可视区（F8 走进视口外的一支、画布标记指到长树深处）。它可能要等上面那次
  // 「打开被折起的组」再渲染一轮才挂出来，所以记成待办、每轮渲染后看一眼：挂出来就滚一次、销账；只看不抢焦点
  // （点画布标记时焦点留在画布）。普通的重渲染没有待办，不滚（Codex #832）
  const scrollFor = useRef<typeof cursor>(null)
  useEffect(() => {
    scrollFor.current = cursor
  }, [cursor])
  useEffect(() => {
    if (!scrollFor.current || scrollFor.current !== cursor) return
    const row = listRef.current?.querySelector<HTMLElement>('[data-issue-row][aria-current="true"]')
    if (!row) return
    scrollFor.current = null
    row.scrollIntoView?.({ block: 'nearest' })
  })

  // 面板卸载（换抽屉 / 收起）时画布上的悬停轮廓一起撤
  useEffect(() => () => useUiStore.getState().setIssueHover(null), [])

  /** 定位 + 记下「正在处理这一条」与它所在的那一支。失败照旧说原因，游标不动。 */
  const locate = (issue: ValidationIssue) => {
    const outcome = focusIssue(issue)
    if (!outcome.ok) {
      useUiStore.getState().setStatus(focusFailureMessage(outcome.reason), 'error')
      return
    }
    // 现场在定位**之后**现取：定位可能把当前图换成了这一行所在的那张（整份排版下点一行
    // 会进它的快速编辑）。这一支一起盖到新现场上——人还在这一支里
    const ui = useUiStore.getState()
    const now = problemContextNow()
    const inSingle = single && drillIssues(shown, single).some((i) => i.issueId === issue.issueId)
    const target = inSingle ? null : drillOf(issue, view, figures)
    if (target) ui.setProblemDrill(target, now)
    // 多支修到只剩直接列出的一支：之前点开的那一支已经不在了，放下它，否则 `open` 仍指着它、
    // 清单是空的、刚落下的游标下一帧就被撤掉（Codex #832）。放下点开的那一支会连游标一起清，所以在落游标之前
    else if (ui.problemDrill) ui.setProblemDrill(null)
    const next = cursorFor(groupIssues(drillIssues(shown, target ?? single!)), issue.issueId)
    if (next) ui.setProblemCursor(next, now)
    else ui.setProblemCursor(null)
  }

  /** 上一项 / 下一项（F8 / ⇧F8 与游标条同一个动作）：一支走完接着走下一支，不跳回开头 */
  const step = (dir: 1 | -1) => {
    const near = dir > 0 ? cursorAt.next : cursorAt.prev
    if (cursor && near) return locate(near)
    const at = open ? walk.findIndex((d) => drillKey(d) === drillKey(open)) : -1
    // 还没开始逐项：从此刻点开的那一支起步（没有就是树的头 / 尾）；已经在走：这一支走完换下一支；
    // 点开的那一支已经修完、从树上消失了：沿它原来的位置往前 / 往后找第一支还在的
    const target =
      !cursor && at >= 0
        ? walk[at]
        : at >= 0
          ? walk[at + dir]
          : open && lastWalk.current.includes(drillKey(open))
            ? adjacentBranch(walk, lastWalk.current, drillKey(open), dir)
            : dir > 0
              ? walk[0]
              : walk.at(-1)
    if (!target) return
    const flat = flattenGroups(groupIssues(drillIssues(shown, target)))
    const issue = dir > 0 ? flat[0] : flat.at(-1)
    if (issue) locate(issue)
  }
  const stepRef = useRef(step)
  stepRef.current = step
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'F8' || e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return
      // 面板挂在模态框底下时，对话框（含其中的输入框）里按 F8 不许走后面的清单（Codex #832）
      if (yieldsCanvasShortcuts(e.target)) return
      e.preventDefault()
      stepRef.current(e.shiftKey ? -1 : 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  /** 方向键：↑↓ 在行间漫游，→ 展开 / ← 收起（收着的、或叶子行按 ← 回到上一层的行） */
  const roam = (e: React.KeyboardEvent) => {
    const rows = [...(listRef.current?.querySelectorAll<HTMLElement>(ROAM_TARGETS) ?? [])]
    if (!rows.length) return
    // 只认从行主按钮上发出的：尾随格里的「修复」/ ⓘ 冒上来的 ↑↓ 不归这里管——此前 at = -1，
    // 焦点被甩回第一行（Codex #832）
    const at = rows.findIndex((r) => r === e.target)
    if (at < 0) return
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      const next = at + (e.key === 'ArrowDown' ? 1 : -1)
      if (next < 0 || next >= rows.length) return
      e.preventDefault()
      rows[next].focus()
      return
    }
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
    const row = rows[at]
    const state = row.getAttribute('aria-expanded')
    if (e.key === 'ArrowRight' && state === 'false') {
      e.preventDefault()
      row.click()
    } else if (e.key === 'ArrowLeft' && state === 'true') {
      e.preventDefault()
      row.click()
    } else if (e.key === 'ArrowLeft') {
      // 回到上一层：最近的、自己就是一支 / 一组的祖先 li 的那颗展开钮
      const parent = row.closest('li')?.parentElement?.closest('li')
      const head = parent?.querySelector<HTMLElement>(':scope > button, :scope > [data-issue-group-head] > button')
      if (head) {
        e.preventDefault()
        head.focus()
      }
    }
  }

  const tree: ProblemTreeCtx = {
    activeCanvasId,
    isOpen: (d, dflt) => forcedOpen(d, drill) || (openMap.get(drillKey(d)) ?? dflt),
    toggle: (d, dflt) => {
      const now = forcedOpen(d, drill) || (openMap.get(drillKey(d)) ?? dflt)
      const map = new Map(openMap)
      map.set(drillKey(d), !now)
      setOpened({ context, map })
      const ui = useUiStore.getState()
      if (now) {
        // 收起直达 / 定位撑开的那一支：放下它（连同游标），否则它下一帧又被撑开
        if (forcedOpen(d, drill)) ui.setProblemDrill(null)
      } else if (!isHeader(d, figures)) {
        // 展开一支 = 点名它（与卡片层「点进一张卡片」同一份状态）：F8 从这里走、教程认得出人在哪一支。
        // 拆成子图的图头只是分组头，不点名
        ui.setProblemDrill(d, problemContextNow())
      }
    },
    body: (d, depth) =>
      groupIssues(drillIssues(shown, d)).map((g) => {
        const key = `${drillKey(d)}|${g.ruleCode}`
        return (
          <GroupNode
            key={key}
            group={g}
            depth={depth}
            open={!collapsed.has(key)}
            expanded={expanded.has(key)}
            onToggle={() => setCollapsed((prev) => toggled(prev, key))}
            onExpand={() => setExpanded((prev) => new Set(prev).add(key))}
            currentId={cursorAt.current?.issueId ?? null}
            activeCanvasId={activeCanvasId}
            onLocate={locate}
          />
        )
      }),
  }

  // 范围胶囊的两个数（审计 B55：两个数得同时看得见，用户才知道切过去会多出几条）
  const figureCount = figureId ? issuesInScope(all, 'figure', figureId).length : 0
  const documentCount = all.length

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <DrawerTitleMeta>
        <ScopePill
          figureId={figureId}
          figureName={figureName}
          scope={scope}
          counts={ready ? { figure: figureCount, document: documentCount } : null}
        />
        {rechecking && (
          <span data-problem-rechecking role="status" className="type-meta shrink-0 text-shimmer">
            {pr('rechecking')}
          </span>
        )}
      </DrawerTitleMeta>
      <DrawerHeaderActions>
        <PanelMenu view={view} />
      </DrawerHeaderActions>

      {/* 摘要条只在这一轮结果就绪后出现：还在检查时挂着一条计数，与下面的骨架是两句打架的话 */}
      {ready && issues.length > 0 && (
        <section
          data-problem-summary
          aria-label={pr('severityLabel')}
          className="flex min-h-10 shrink-0 flex-wrap items-center gap-1 px-2 py-1"
        >
          <div role="group" aria-label={pr('severityLabel')} className="flex flex-wrap items-center gap-1">
            {SEVERITIES.filter((s) => counts[s] > 0).map((s) => (
              <SeverityToggle key={s} severity={s} count={counts[s]} active={!!filter?.includes(s)} />
            ))}
          </div>
          <span className="flex-1" />
          {fixableHere.length > 0 && (
            <Button
              size="lg"
              variant="primary"
              data-problem-autofix
              className="shrink-0 tabular-nums"
              disabled={fixing}
              title={pr('fixAllTip', { count: fixableHere.length })}
              onClick={() => void runBatchFix(fixableHere)}
            >
              {fixing ? pr('fixing') : pr('fixAll', { count: fixableHere.length })}
            </Button>
          )}
        </section>
      )}

      {/*
        这一轮查砸了、但上一轮的结果**留着**（`ready && issues.length`）：
        那就把失败说出来，**同时把留下来的问题继续列出来**。整屏换成一张错误
        空态的话，那些问题仍然被计进计数、也仍然进导出摘要，却在**唯一
        一份完整问题清单**里翻不到、点不到、跳不过去（PR #214 第七轮评审）。
      */}
      {retained && (
        <div className="shrink-0 px-2 pb-2">
          <Notice
            tone="warn"
            role="status"
            action={
              <Button size="sm" variant="ghost" onClick={() => schedule()}>
                {pr('retry')}
              </Button>
            }
          >
            {pr('failedKeptHint')}
          </Notice>
        </div>
      )}

      {failed && !retained ? (
        <EmptyState
          icon={OctagonAlert}
          title={pr('failedTitle')}
          /* 「查不了」与「没问题」是两个答案：压成一个的话用户会带着一屏静悄悄的绿去投稿 */
          hint={pr(ready ? 'failedKeptHint' : 'failedHint')}
          action={{ label: pr('retry'), onClick: () => schedule() }}
        />
      ) : !ready ? (
        /* **判据是 `!ready`，不是 `!ready && running`。** 换文档之后
           `resetValidation()` 与那一轮真正开跑之间有 250ms 防抖窗口，
           那段时间里 `ready=false, running=false, issues=[]` —— 挂着
           `running` 的话会**掉进下面那个绿色的"没有问题"**，而这一刻
           根本还没查过（T-54，PR #214 第六轮评审）。首检是静态骨架（加载四种写法之一）。 */
        <TreeSkeleton />
      ) : shown.length === 0 ? (
        all.length === 0 ? (
          /* 空态给证据：按哪套规范、查了几张图、什么时候——「没问题」得说得出凭什么 */
          <NoneEvidence />
        ) : issues.length === 0 ? (
          /* 范围裁掉了：整份排版里有问题、这张图上没有——是两句不同的话 */
          <EmptyState
            icon={CircleCheck}
            title={pr('noneInScope')}
            hint={pr('noneInScopeHint', { count: all.length })}
            action={{
              label: pr('scopeDocument'),
              onClick: () => useUiStore.getState().setProblemScope('document'),
            }}
          />
        ) : (
          /* 被等级筛选筛空了：不是「修完了」（#690 评审） */
          <EmptyState
            icon={CircleCheck}
            title={pr('noneInFilter')}
            action={{ label: pr('clearFilter'), onClick: () => useUiStore.getState().setProblemFilter(null) }}
          />
        )
      ) : (
        <ProblemTreeContext.Provider value={tree}>
          <ul
            ref={listRef}
            onKeyDown={roam}
            data-problem-tree={view}
            data-problem-cards={view}
            aria-label={pr('listLabel')}
            className="min-h-0 flex-1 overflow-y-auto pb-2"
          >
            {single ? (
              <li data-problem-tier="actionable">
                <ul>{tree.body(single, 0)}</ul>
              </li>
            ) : view === 'figure' ? (
              <FigureCards figures={figures} withHeaders={scope === 'document' || figures.length > 1} />
            ) : (
              <CategoryCards categories={categories} />
            )}
            {unverifiable.length > 0 && <UnverifiableEntry issues={unverifiable} />}
          </ul>
        </ProblemTreeContext.Provider>
      )}

      {open && cursor && groups.length > 0 && <CursorBar view={cursorAt} onStep={step} />}
      <ReadinessLink />
    </div>
  )
}

const EMPTY_MAP: ReadonlyMap<string, boolean> = new Map()

/** `gone` 在旧走法 `before` 里的位置往 `dir` 方向，第一支仍在 `walk` 上的；没有就是 undefined（到头了，不绕回） */
function adjacentBranch(
  walk: readonly ProblemDrill[],
  before: readonly string[],
  gone: string,
  dir: 1 | -1,
): ProblemDrill | undefined {
  for (let i = before.indexOf(gone) + dir; i >= 0 && i < before.length; i += dir) {
    const hit = walk.find((d) => drillKey(d) === before[i])
    if (hit) return hit
  }
  return undefined
}

const toggled = (prev: ReadonlySet<string>, key: string) => {
  const next = new Set(prev)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  return next
}

/** 拆成子图的图头：分组头，不是一支 */
const isHeader = (d: ProblemDrill, figures: readonly FigureBucket[]) =>
  d.kind === 'figure' && figures.some((f) => f.key === d.key && isSplit(f))

/** 直达 / 定位写下的那一支（与它的图头）强制开着 */
function forcedOpen(d: ProblemDrill, drill: ProblemDrill | null): boolean {
  if (!drill) return false
  if (drillKey(d) === drillKey(drill)) return true
  return d.kind === 'figure' && drill.kind === 'part' && d.key === drill.figure
}

/** 方向键漫游的落点：每一行的主按钮（分桶的展开钮、组头、对象行、「显示其余」） */
const ROAM_TARGETS =
  '[data-problem-card] > button:first-child, [data-issue-group-toggle], [data-issue-row], [data-issue-show-rest]'

/** 指示物晚一点再出：防抖 250ms 的一轮常常一闪就过，那一闪不该变成一道闪烁的 shimmer */
function useDelayed(on: boolean, ms: number): boolean {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    if (!on) {
      setShown(false)
      return
    }
    const id = setTimeout(() => setShown(true), ms)
    return () => clearTimeout(id)
  }, [on, ms])
  return on && shown
}

/* ------------------------------- 标题行 ----------------------------------- */

/**
 * 范围胶囊：「当前图 13 ▾」。两档各带自己的数；没有当前图时那一档留在原位灰掉、
 * 说明为什么——消失的选项解释不了自己。
 */
function ScopePill({
  figureId,
  figureName,
  scope,
  counts,
}: {
  figureId: string | null
  figureName: string | null
  scope: ProblemScope
  /** 两档各自的问题数；这一轮还没查完时是 null（挂着旧数字与骨架是两句打架的话） */
  counts: { figure: number; document: number } | null
}) {
  const label = scope === 'figure' ? pr('scopeFigure') : pr('scopeDocument')
  const n = counts ? (scope === 'figure' ? counts.figure : counts.document) : null
  return (
    <Menu
      width={220}
      trigger={
        <button
          type="button"
          data-problem-scope-trigger={scope}
          aria-label={
            n != null ? pr('scopeCountAria', { label, count: n }) : label
          }
          title={scope === 'figure' && figureName ? pr('scopeFigureTip', { name: figureName }) : undefined}
          className={cn(
            'flex h-6 min-w-0 items-center gap-1 rounded-full bg-surface-hover pl-2 pr-1.5 text-xs text-ink-2 outline-none',
            'transition-colors duration-fast hover:bg-surface-active hover:text-ink focus-visible:focus-ring data-[state=open]:bg-surface-active',
          )}
        >
          <span className="truncate">{label}</span>
          {n != null && n > 0 && (
            // 计数坐在 hover 5% 的胶囊底上（桌面上合成 ≈ #e4e4e2）：ink-3 在那里只有 4.14:1，跟胶囊的字走 ink-2
            // （e2e/a11y 的自算对比度尺子量到的；数字是要读的字）
            <span aria-hidden className="text-xs tabular-nums">
              {n}
            </span>
          )}
          <ChevronDown size={ICON_SIZE.xs} aria-hidden className="shrink-0 text-ink-3" />
        </button>
      }
    >
      <MenuLabel>{pr('scopeLabel')}</MenuLabel>
      <MenuRadioGroup value={scope} onValueChange={(v) => useUiStore.getState().setProblemScope(v as ProblemScope)}>
        <MenuRadioItem
          value="figure"
          data-problem-scope="figure"
          disabled={!figureId}
          reason={figureId ? (figureName ?? undefined) : pr('scopeFigureUnavailable')}
          shortcut={figureId && counts ? String(counts.figure) : undefined}
        >
          {pr('scopeFigure')}
        </MenuRadioItem>
        <MenuRadioItem
          value="document"
          data-problem-scope="document"
          shortcut={counts ? String(counts.document) : undefined}
        >
          {pr('scopeDocument')}
        </MenuRadioItem>
      </MenuRadioGroup>
    </Menu>
  )
}

/** 标题行的「⋯」：分组方式（取值，单选）、画布上的问题标记（开关）、重新检查 */
function PanelMenu({ view }: { view: ProblemView }) {
  const pins = useUiStore((s) => s.problemPins)
  return (
    <Menu
      width={220}
      align="end"
      trigger={
        <IconButton label={pr('menuLabel')} data-problem-menu>
          <Ellipsis size={ICON_SIZE.md} className="text-ink-3" />
        </IconButton>
      }
    >
      <MenuLabel>{pr('view.label')}</MenuLabel>
      <MenuRadioGroup value={view} onValueChange={(v) => useUiStore.getState().setProblemView(v as ProblemView)}>
        <MenuRadioItem value="figure" data-problem-view="figure">
          {pr('view.figure')}
        </MenuRadioItem>
        <MenuRadioItem value="category" data-problem-view="category">
          {pr('view.category')}
        </MenuRadioItem>
      </MenuRadioGroup>
      <MenuSeparator />
      <MenuCheckItem data-problem-pins checked={pins} onSelect={() => useUiStore.getState().setProblemPins(!pins)}>
        {pr('pins')}
      </MenuCheckItem>
      <MenuItem icon={RefreshCw} data-problem-recheck onSelect={() => schedule()}>
        {pr('retry')}
      </MenuItem>
    </Menu>
  )
}

/* ------------------------------- 摘要条 ----------------------------------- */

/**
 * 等级开关：记号 + 数，可切换（aria-pressed）。**只有阻断着色**（danger 浅底 + content 字）——
 * 警告 / 建议不打扰，形状与名字照样说出等级（名字在可达名里）。
 */
function SeverityToggle({ severity, count, active }: { severity: Severity; count: number; active: boolean }) {
  const label = severityLabel(severity)
  const toggle = () => {
    const cur = useUiStore.getState().problemFilter ?? []
    const next = cur.includes(severity) ? cur.filter((s) => s !== severity) : [...cur, severity]
    useUiStore.getState().setProblemFilter(next.length ? next : null)
  }
  const tinted = severity === 'error'
  return (
    <Tip label={pr('filterAria', { label, count })} side="bottom">
      <button
        type="button"
        onClick={toggle}
        data-problem-severity={severity}
        aria-pressed={active}
        aria-label={pr('filterAria', { label, count })}
        className={cn(
          'flex h-6 shrink-0 items-center gap-1 rounded-full px-2 text-xs tabular-nums outline-none',
          'outline-1 -outline-offset-1 transition-colors duration-fast focus-visible:focus-ring',
          tinted
            ? 'bg-danger-surface text-danger-content hover:bg-danger-surface'
            : 'text-ink-2 hover:bg-surface-hover hover:text-ink',
          active ? (tinted ? 'font-medium outline-danger-border' : 'bg-selected font-medium text-ink outline-border-strong') : 'outline-transparent',
        )}
      >
        <SeverityGlyph severity={severity} size="sm" />
        <span>{count}</span>
        <span className="sr-only">{label}</span>
      </button>
    </Tip>
  )
}

/* --------------------------------- 状态 ----------------------------------- */

/** 首检：静态骨架（加载四种写法之一：sweep / 静态骨架 / shimmer / 转圈），读屏念一句「正在检查」 */
function TreeSkeleton() {
  return (
    <div data-problem-loading className="flex flex-col gap-1 px-1 pt-1" aria-busy="true">
      <span role="status" className="sr-only">
        {pr('running')}
      </span>
      {[72, 56, 64, 48].map((w, i) => (
        <span key={i} aria-hidden className="mx-1 flex h-8 items-center gap-2 px-2">
          <span className="size-6 shrink-0 rounded-xs bg-surface-hover opacity-65" />
          <span className="h-2.5 rounded-xs bg-surface-hover opacity-65" style={{ width: `${w}%` }} />
        </span>
      ))}
    </div>
  )
}

/**
 * 证据里「什么时候」的那个「现在」：从 `since` 起按整分钟走一格（下一次文字可能变的时刻），
 * 只在证据挂着时走、卸载即停。不靠别的重渲染顺带刷新——抽屉一直开着、清单不变时没人重渲染，
 * 「刚刚」会一直挂到几小时后（Codex #832）。`since` 一换（重新检查）按新的起点重新对齐。
 */
function useMinuteClock(since: number | null): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (since == null) return
    let timer: ReturnType<typeof setTimeout>
    const arm = () => {
      const elapsed = Math.max(0, Date.now() - since)
      // 多等 50ms：落在边界之后，`formatRelativeTime` 的取整已经跨过去
      timer = setTimeout(() => {
        setNow(Date.now())
        arm()
      }, 60_000 - (elapsed % 60_000) + 50)
    }
    arm()
    return () => clearTimeout(timer)
  }, [since])
  return now
}

/**
 * 「未发现问题」+ 证据：按哪套规范、查了几张图、什么时候（2026-10-07 设计审计 §9.4）。
 *
 * 规范按**每张画布各自的绑定**说，与 `collectCanvases()` 给每张画布跑检查时的输入同一份
 * 判据（激活画布读现值 `doc.profile`，别的画布读 `canvases[].profile`）：几张画布绑了不同的
 * 规范时，只报当前画布那一套等于把它盖到别的画布的图上（Codex #832）。只算装着图的画布——
 * 证据说的是「这些图按什么查的」；一张图都没有时退回当前画布那一套。
 */
function NoneEvidence() {
  const checkedAt = useValidationStore((s) => s.checkedAt)
  const specs = useProfileStore((s) => s.specs)
  const canvases = useDocumentStore((s) => s.canvases)
  const activeCanvasId = useDocumentStore((s) => s.activeCanvasId)
  const activeObjects = useDocumentStore((s) => s.doc.objects)
  const activeBinding = useDocumentStore((s) => s.doc.profile)
  const { figures, names } = useMemo(() => {
    const catalog = toCatalog(specs)
    const nameOf = (binding: typeof activeBinding) => {
      const resolved = resolveDocumentSpec(binding, catalog)
      const record = resolved.profileId ? specs.find((r) => r.id === resolved.profileId) : undefined
      return record ? profileName(record) : resolved.profile.label
    }
    let figures = 0
    const used: string[] = []
    for (const c of canvases) {
      const active = c.id === activeCanvasId
      const n = (active ? activeObjects : c.objects).filter((o) => o.type === 'panel').length
      if (n === 0) continue
      figures += n
      const name = nameOf(active ? activeBinding : c.profile)
      if (!used.includes(name)) used.push(name)
    }
    return { figures, names: used.length ? used : [nameOf(activeBinding)] }
  }, [specs, canvases, activeCanvasId, activeObjects, activeBinding])
  const now = useMinuteClock(checkedAt)
  // 挂上之后才检查完的：`now` 还是挂上那一刻（早于 checkedAt），差是负的，照样是「刚刚」
  const when = checkedAt == null ? null : now - checkedAt < 60_000 ? pr('justNow') : formatRelativeTime(checkedAt, now)
  const hint = !when
    ? undefined
    : names.length === 1
      ? pr('noneEvidence', { spec: names[0], count: figures, when })
      : pr('noneEvidenceMulti', { specs: listJoin(names), specCount: names.length, count: figures, when })
  return <EmptyState icon={CircleCheck} title={pr('none')} hint={hint} data-problem-evidence />
}

/* ------------------------------- 游标 ------------------------------------- */

/**
 * 「正在处理第几条」+ 上一项 / 下一项（F8 / ⇧F8）。那条修好消失之后这里说「已处理」，
 * 「下一项」指向顶上来的那条——清单不必重开、位置不必重找。
 */
function CursorBar({ view, onStep }: { view: ReturnType<typeof cursorView>; onStep: (dir: 1 | -1) => void }) {
  return (
    <div
      data-problem-cursor
      aria-label={pr('cursorLabel')}
      // 左栏页脚只有一种行语法：`border-t px-1.5 py-1` + 28px 控件，底色是抽屉底
      className="flex shrink-0 items-center gap-1 border-t border-border bg-[var(--drawer-bg,var(--color-surface))] px-1.5 py-1"
    >
      <span className="min-w-0 flex-1 truncate pl-1.5 text-xs text-ink-2">
        {view.current
          ? `${pr('cursorAt', { pos: view.position, total: view.total })} · ${subjectName(view.current)}`
          : pr('cursorDone', { count: view.total })}
      </span>
      {/* 「上一项 / 下一项」是一对方向相反的同一个动作，两颗同形（左栏审计 L26） */}
      <IconButton iconSize="sm" side="top" label={pr('prev')} shortcut="⇧F8" onClick={() => onStep(-1)}>
        <ChevronUp size={ICON_SIZE.sm} />
      </IconButton>
      <IconButton iconSize="sm" side="top" label={pr('next')} shortcut="F8" onClick={() => onStep(1)}>
        <ChevronDown size={ICON_SIZE.sm} />
      </IconButton>
      <IconButton
        iconSize="sm"
        side="top"
        label={pr('cursorClose')}
        onClick={() => useUiStore.getState().setProblemCursor(null)}
      >
        <X size={ICON_SIZE.sm} />
      </IconButton>
    </div>
  )
}

/**
 * 接入状态的出口。**不把就绪度问题混进上面的清单**——「这张图还没连上脚本」
 * 与「这张图字号偏小」的下一步完全不同，混在一起用户两件事都做不了。
 */
function ReadinessLink(): ReactNode {
  const report = useProjectReadinessStore((s) => s.report)
  if (!report || report.summary.total <= 0 || report.summary.editable >= report.summary.total) {
    return null
  }
  const pending = report.summary.total - report.summary.editable
  return (
    <div className="shrink-0 border-t border-border bg-[var(--drawer-bg,var(--color-surface))] px-1.5 py-1">
      <Tip label={pr('readinessTip')} side="top">
        <button
          type="button"
          onClick={() => useProjectReadinessStore.getState().openCenter({ source: 'panel' })}
          className={cn(
            'flex h-7 w-full items-center gap-1.5 rounded-md px-1.5 text-left text-xs text-ink-2 outline-none',
            'transition-colors duration-fast hover:bg-surface-hover hover:text-ink focus-visible:focus-ring',
          )}
        >
          <ClipboardList size={ICON_SIZE.sm} className="shrink-0 text-ink-3" aria-hidden />
          <span className="min-w-0 flex-1 truncate">{pr('readiness', { count: pending })}</span>
          <ChevronRight size={ICON_SIZE.xs} className="shrink-0 text-ink-3" aria-hidden />
        </button>
      </Tip>
    </div>
  )
}
