import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { ICON_SIZE } from '@/components/ui/Icon'
import type { ManifestElement } from '@/lib/api'
import { focusIssue, openProblems } from '@/lib/issueFocus'
import { cn } from '@/lib/utils'
import type { ValidationIssue } from '@/lib/validation'
import { issueDetailText, SEVERITY_ICON } from '@/lib/validationText'
import { useValidationStore } from '@/store/validationStore'
import type { PanelObject } from '@/types/document'
import { FixButton } from '../left/IssueFixButton'

/** 落在**这个元素**上的全部问题（任何规则）：按 objectRef 的对象 + gid 取 */
export function issuesForElement(
  issues: readonly ValidationIssue[],
  panelId: string,
  gid: string,
): ValidationIssue[] {
  return issues.filter((i) => i.objectRef.objectId === panelId && i.objectRef.gid === gid)
}

/** 这两条的修法只有「换一个画得出这些字的字体」，入口文案照着说 */
const GLYPH_RULES: ReadonlySet<string> = new Set(['glyph-missing', 'glyph-substituted'])

const el = (key: string) => translate(`element.${key}`, { ns: 'inspector' })

/**
 * 被选元素上的问题，就地出现在它的内容框下面（审计 T14）。
 *
 * **不跑第二遍求值器、不另起一套判据、不另写一套措辞**：读的是
 * `validationStore` 里同一份清单（ADR 0030 的那一条链），句子是同一句成文
 * （`issueDetailText`），能修的用问题面板同一颗「修复」按钮（`FixButton`），
 * 不能自动修的按规则给出口：字形问题 → 「更换字体」（`focusIssue` 把焦点送到
 * 字体那一行）；说得出字段的 → 「定位到字段」；说不出的（超出图幅这类）→
 * 「在问题面板查看」。这里只是把问题面板里那几行**搬到对象旁边**——用户在图上
 * 看到方框 / 被裁掉的标签时，正选着的就是这个元素，而不是在左侧清单里翻它。
 */
export function ElementIssueNote({
  panel,
  element,
}: {
  panel: PanelObject
  element: ManifestElement
}) {
  useTranslation(['inspector', 'errors'])
  const issues = useValidationStore((s) => s.issues)
  const mine = useMemo(
    () => issuesForElement(issues, panel.id, element.gid),
    [issues, panel.id, element.gid],
  )
  if (!mine.length) return null
  const hasFontField = element.editable.some((f) => f.prop === 'fontfamily')
  return (
    <div data-element-issues className="flex flex-col gap-1 pl-[72px]">
      {mine.map((issue) => {
        const Icon = SEVERITY_ICON[issue.severity]
        const glyph = GLYPH_RULES.has(issue.ruleCode)
        // 出口三选一：能修 → 修复按钮；字形 → 更换字体；有字段 → 定位；否则去面板
        const link: { label: string; title?: string; onClick: () => void } | null =
          issue.fixKind !== 'none'
            ? null
            : glyph
              ? hasFontField
                ? { label: el('glyphChangeFont'), title: el('glyphChangeFontTip'), onClick: () => focusIssue(issue) }
                : null
              : issue.propertyPath
                ? { label: el('issueLocateField'), onClick: () => focusIssue(issue) }
                : { label: el('issueOpenPanel'), onClick: () => openProblems() }
        return (
          <div
            key={issue.issueId}
            data-element-issue={issue.ruleCode}
            className={cn(
              'flex items-start gap-1.5 text-xs leading-relaxed',
              issue.severity === 'error' ? 'text-danger' : 'text-ink-3',
            )}
          >
            <Icon size={ICON_SIZE.xs} aria-hidden className="mt-px shrink-0" />
            <span className="min-w-0 flex-1">
              {issueDetailText(issue)}
              {link && (
                <button
                  type="button"
                  onClick={link.onClick}
                  title={link.title}
                  className="ml-1.5 text-accent underline-offset-2 outline-none hover:underline focus-visible:focus-ring"
                >
                  {link.label}
                </button>
              )}
            </span>
            <FixButton issue={issue} />
          </div>
        )
      })}
    </div>
  )
}
