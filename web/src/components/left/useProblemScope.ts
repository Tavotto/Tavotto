/**
 * 问题面板的范围 hook（审计 T09）。「当前图」是谁、范围实际是哪一档、
 * 裁完之后剩哪些问题——面板与抽屉标题的计数共用这一份，两处不会说出两个数。
 *
 * 判据全在 `lib/problemList.ts`（纯函数）；这里只负责从 store 里把现场取出来。
 */
import { useMemo } from 'react'
import {
  effectiveScope,
  issuesInScope,
  problemContextKey,
  type ProblemCursor,
  type ProblemDrill,
  type ProblemScope,
} from '@/lib/problemList'
import { currentFigureOf } from '@/lib/problemContext'
import type { ValidationIssue } from '@/lib/validation'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { useWorkspaceStore } from '@/store/workspace'

export interface ScopedProblems {
  /** 「当前图」的面板对象 id；没有就 null（那一档不可选） */
  figureId: string | null
  /** 当前图的名字（面板名 / 文件名）；没有当前图就 null */
  figureName: string | null
  /** 用户的选择（null = 没选过） */
  choice: ProblemScope | null
  /** 实际生效的范围 */
  scope: ProblemScope
  /** 范围内的问题（未按等级筛） */
  issues: ValidationIssue[]
  /** 此刻的现场键（`problemContextKey`）：点卡片、落游标时带上它 */
  context: string
  /** 在这个现场里仍然有效的那张卡片；写下它的现场不是此刻 = null（回到总览） */
  drill: ProblemDrill | null
  /** 同上，「正在处理」的游标 */
  cursor: ProblemCursor | null
}

/** 「当前图」（判据 `lib/problemContext.currentFigureOf`） */
export function useCurrentFigure(): { id: string | null; name: string | null } {
  const activePanelId = useWorkspaceStore((s) => s.activePanelId)
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  const primary = useSelectionStore((s) => s.ids.at(-1) ?? null)
  const objects = useDocumentStore((s) => s.doc.objects)
  return useMemo(
    () => currentFigureOf([activePanelId, elementPanelId, primary], objects),
    [activePanelId, elementPanelId, primary, objects],
  )
}

export function useScopedProblems(): ScopedProblems {
  const all = useValidationStore((s) => s.issues)
  const choice = useUiStore((s) => s.problemScope)
  const figure = useCurrentFigure()
  const scope = effectiveScope(choice, figure.id)
  const issues = useMemo(() => issuesInScope(all, scope, figure.id), [all, scope, figure.id])
  const loadSeq = useDocumentStore((s) => s.loadSeq)
  const view = useUiStore((s) => s.problemView)
  const context = problemContextKey({ loadSeq, scope, figureId: figure.id, view })
  const stamped = useUiStore((s) => s.problemContext)
  const rawDrill = useUiStore((s) => s.problemDrill)
  const rawCursor = useUiStore((s) => s.problemCursor)
  const live = stamped === context
  return {
    figureId: figure.id,
    figureName: figure.name,
    choice,
    scope,
    issues,
    context,
    drill: live ? rawDrill : null,
    cursor: live ? rawCursor : null,
  }
}
