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
  type ProblemScope,
} from '@/lib/problemList'
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
}

/**
 * 「当前图」= 快速编辑中的那张 → 图内编辑中的那张 → 选中的面板。
 * 三档合成一个 id：快速编辑的 `activePanelId` 与图内编辑的 `elementPanelId`
 * 正常情况下指同一个对象，第三档才是「排版模式下点了一张图」。
 */
export function useCurrentFigure(): { id: string | null; name: string | null } {
  const activePanelId = useWorkspaceStore((s) => s.activePanelId)
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  const primary = useSelectionStore((s) => s.ids.at(-1) ?? null)
  const objects = useDocumentStore((s) => s.doc.objects)
  return useMemo(() => {
    for (const id of [activePanelId, elementPanelId, primary]) {
      if (!id) continue
      const o = objects.find((x) => x.id === id)
      if (o?.type === 'panel') return { id: o.id, name: o.name ?? o.fileId }
    }
    return { id: null, name: null }
  }, [activePanelId, elementPanelId, primary, objects])
}

export function useScopedProblems(): ScopedProblems {
  const all = useValidationStore((s) => s.issues)
  const choice = useUiStore((s) => s.problemScope)
  const figure = useCurrentFigure()
  const scope = effectiveScope(choice, figure.id)
  const issues = useMemo(() => issuesInScope(all, scope, figure.id), [all, scope, figure.id])
  return { figureId: figure.id, figureName: figure.name, choice, scope, issues }
}
