import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import type { ManifestElement } from '@/lib/api'
import { focusIssue } from '@/lib/issueFocus'
import { cn } from '@/lib/utils'
import type { ValidationIssue } from '@/lib/validation'
import { issueDetailText, SEVERITY_ICON } from '@/lib/validationText'
import { useValidationStore } from '@/store/validationStore'
import type { PanelObject } from '@/types/document'

/** 落在**这个元素**上的字形问题：画不出来（方框）与换了一张脸画，两条各一句 */
const GLYPH_RULES: ReadonlySet<string> = new Set(['glyph-missing', 'glyph-substituted'])

export function glyphIssuesFor(
  issues: readonly ValidationIssue[],
  panelId: string,
  gid: string,
): ValidationIssue[] {
  return issues.filter(
    (i) => GLYPH_RULES.has(i.ruleCode) && i.objectRef.objectId === panelId && i.objectRef.gid === gid,
  )
}

/**
 * 缺字提示就地出现在被选文字的内容框下面（审计 T14）。
 *
 * **不跑第二遍求值器、不另起一套判据**：读的是 `validationStore` 里同一份清单
 * （ADR 0030 的那一条链），措辞是同一句成文（`issueDetailText`），「更换字体」
 * 走同一个定位服务（`focusIssue` → 焦点落到字体那一行的 `data-prop`）。
 * 这里只是把问题面板里那一行**搬到对象旁边**——用户在图上看到方框时，正
 * 选着的就是这段文字，而不是在左侧清单里翻它。
 *
 * 能修的动作只有「换一个画得出这些字的字体」，而换哪一个只有用户说得出
 * （`RULES` 里两条都是 `fix: 'none'`），所以入口是把焦点送到字体控件上，
 * 不是替他挑。元素没有字体字段（引擎这次没发）时只提示、不给按钮。
 */
export function GlyphIssueNote({
  panel,
  element,
}: {
  panel: PanelObject
  element: ManifestElement
}) {
  useTranslation(['inspector', 'errors'])
  const issues = useValidationStore((s) => s.issues)
  const mine = useMemo(() => glyphIssuesFor(issues, panel.id, element.gid), [issues, panel.id, element.gid])
  if (!mine.length) return null
  const canFix = element.editable.some((f) => f.prop === 'fontfamily')
  return (
    <div data-glyph-note className="flex flex-col gap-1 pl-[72px]">
      {mine.map((issue) => {
        const Icon = SEVERITY_ICON[issue.severity]
        const missing = issue.ruleCode === 'glyph-missing'
        return (
          <p
            key={issue.issueId}
            data-glyph-rule={issue.ruleCode}
            className={cn(
              'flex items-start gap-1.5 text-xs leading-relaxed',
              missing ? 'text-danger' : 'text-ink-3',
            )}
          >
            <Icon size={12} aria-hidden className="mt-px shrink-0" />
            <span className="min-w-0 flex-1">
              {issueDetailText(issue)}
              {canFix && (
                <button
                  type="button"
                  onClick={() => focusIssue(issue)}
                  title={translate('element.glyphChangeFontTip', { ns: 'inspector' })}
                  className="ml-1.5 text-accent underline-offset-2 outline-none hover:underline focus-visible:focus-ring"
                >
                  {translate('element.glyphChangeFont', { ns: 'inspector' })}
                </button>
              )}
            </span>
          </p>
        )
      })}
    </div>
  )
}
