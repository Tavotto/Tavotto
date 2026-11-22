import { memo, useContext, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { figureFontChoice } from '@/lib/figureFontChoice'
import { FIGURE_LINE_ROWS, FIGURE_TEXT_ROWS } from '@/lib/stylePanelModel'
import { isSubLabel } from '@/lib/stylePresets'
import { styleSummaryText } from '@/lib/styleSummary'
import { profileName } from '@/lib/profileText'
import { CANVAS_TEXT_FAMILIES, styleIsItalic, weightIsBold } from '@/lib/typography'
import { cn } from '@/lib/utils'
import { bindCanvasStyle, bindingName, editBoundStyle, resolvedStyle } from '@/store/styleBinding'
import { useDocumentStore } from '@/store/documentStore'
import { useProfileStore } from '@/store/profileStore'
import { optionLabel } from '../inspector/roles/registry'
import { Section } from '../ui/Field'
import { Select, type SelectOption } from '../ui/Select'
import { NUMBER_SPEC, useFigureFamilies } from '../settings/StyleProfileFields'
import { readPath } from '../settings/profilePath'
import { StyleSamplePreview } from '../settings/StyleSamplePreview'
import {
  CanvasPending,
  ControlLine,
  FaceToggles,
  FamilySelect,
  PtField,
  ROW_LABEL,
  StyleRow,
  VALUE_W,
  WIDE_FAMILY_COL,
  sp,
} from './styleRowParts'

/** 「不跟随样式」那一项的取值（样式 id 不会长这样：库里的 id 是 `builtin-…` / 生成的 uuid） */
const UNBOUND = '__unbound__'
/** 「已脱离」那一项的取值：只在脱离时出现；选回那套样式 = 恢复跟随 */
const DETACHED = '__detached__'

type Data = Record<string, unknown>
const TICK_DIRECTIONS = ['out', 'in', 'inout'] as const

/**
 * 画布层（样式面板里没选中图、或点了「改整张画布」时的那一层，2026-11-20 维护者拍板）：
 *
 * * 顶上一个选择器 = 这张画布**跟随**哪套样式（ADR 0081：应用 = 绑定，**只有这一处**）；
 *   选项带一张小样张和一行摘要，选之前就看得出里面是什么；
 * * 绑定之后，下面是这套样式**本身**的值，行与图层同一套零件（`styleRowParts`）；改一格 =
 *   `editBoundStyle`（先存库、再一次 commit 对齐整张画布，一条历史）。
 *
 * 没有第二套状态：读的是 `resolvedStyle(binding)`（库里的现值，库里没有就是快照），写的是
 * 既有的 `editBoundStyle`；这一层不新增任何样式键。
 */
export function CanvasStyleLayer() {
  useTranslation(['workspace', 'inspector'])
  const records = useProfileStore((s) => s.styles)
  const loaded = useProfileStore((s) => s.loaded)
  const binding = useDocumentStore((s) => s.doc.style ?? null)
  const detached = !!binding?.detached
  const bound = !!binding && !detached
  const data = useMemo(
    () => (binding && !detached ? (resolvedStyle(binding) as unknown as Data) : null),
    [binding, detached, records, loaded], // eslint-disable-line react-hooks/exhaustive-deps
  )
  const summary = data ? styleSummaryText(data) : null

  const options: SelectOption[] = [
    ...(binding && detached ? [{ value: DETACHED, label: sp('detached', { name: bindingName(binding) }) }] : []),
    ...(bound ? [{ value: UNBOUND, label: sp('unbound') }] : []),
    ...records.map((r) => ({
      value: r.id,
      label: <StyleOption name={profileName(r)} data={r.data as unknown as Data} />,
      searchText: profileName(r),
      rowClass: 'h-auto py-1.5',
    })),
    // 绑着的那一条库里找不到了（换了台电脑）：照样列出来，快照仍然说了算
    ...(binding && !records.some((r) => r.id === binding.id)
      ? [{ value: binding.id, label: bindingName(binding) }]
      : []),
  ]

  return (
    <div data-style-canvas>
      <div data-style-apply className="shrink-0 px-3 pb-1 pt-2">
        <Select
          className="w-full min-w-0"
          ariaLabel={sp('bindLabel')}
          value={detached ? DETACHED : (binding?.id ?? '')}
          placeholder={sp('canvasUnbound')}
          prefix={bound ? sp('canvasFollows') : undefined}
          triggerLabel={binding && bound ? bindingName(binding) : undefined}
          title={detached ? sp('detachedHint') : bound ? sp('boundHint') : sp('unboundHint')}
          onChange={(id) => id !== DETACHED && bindCanvasStyle(id === UNBOUND ? null : id)}
          options={options}
        />
        {bound && data && (
          <div data-style-bound-preview className="mt-1.5 flex items-center gap-2">
            <StyleSamplePreview data={data} thumb />
            {summary && (
              <p data-style-summary className="type-caption line-clamp-2 min-w-0 flex-1" title={summary}>
                {summary}
              </p>
            )}
          </div>
        )}
        {detached && (
          <p data-style-bind-hint className="type-caption mt-1.5">
            {sp('detachedHint')}
          </p>
        )}
      </div>
      {bound && data && <CanvasRows data={data} />}
    </div>
  )
}

/** 样式下拉里的一项：小样张 + 名字 + 一行摘要（摘要放不下就截断，全文在悬停里） */
function StyleOption({ name, data }: { name: string; data: Data }) {
  const summary = styleSummaryText(data)
  return (
    <span data-style-option className="flex min-w-0 items-center gap-2" title={summary ?? undefined}>
      <StyleSamplePreview data={data} thumb />
      <span className="min-w-0">
        <span className="block truncate">{name}</span>
        {summary && (
          <span data-style-option-summary className="block truncate text-xs text-ink-3">
            {summary}
          </span>
        )}
      </span>
    </span>
  )
}

function CanvasRows({ data }: { data: Data }) {
  const objects = useDocumentStore((s) => s.doc.objects)
  const hasAnnotations = useMemo(
    () => objects.some((o) => o.type === 'text' && !isSubLabel(o)),
    [objects],
  )
  const showAnnotation = hasAnnotations || readPath(data, 'annotation') !== undefined
  return (
    <>
      <Section title={sp('groupText')}>
        {FIGURE_TEXT_ROWS.map((row) => (
          <CanvasTextRow key={row.id} data={data} {...row} />
        ))}
        {showAnnotation && <CanvasAnnotationRow data={data} />}
      </Section>
      <Section title={sp('groupLines')}>
        {FIGURE_LINE_ROWS.map((row) => (
          <CanvasLineRow key={row.id} data={data} {...row} />
        ))}
      </Section>
    </>
  )
}

/** 样式里写着的字体名（候选列表 / 主备字体取第一个）；没写 = 空串 */
function familyText(raw: unknown): string {
  const primary = figureFontChoice(raw)?.primary ?? raw
  const first = Array.isArray(primary) ? primary[0] : primary
  return typeof first === 'string' ? first : ''
}

const CanvasTextRow = memo(function CanvasTextRow({
  data,
  id,
  sizeRole,
  familyRole,
  faceRole,
}: {
  data: Data
  id: string
  sizeRole: string
  familyRole: string
  faceRole: string | null
}) {
  useTranslation(['workspace', 'inspector'])
  const pw = useContext(CanvasPending)!
  const families = useFigureFamilies()
  const label = ROW_LABEL[id]()
  const size = readPath(data, `element.${sizeRole}.fontsize`)
  const face = (prop: 'weight' | 'style') => {
    if (!faceRole) return null
    const raw = readPath(data, `element.${faceRole}.${prop}`)
    const on = prop === 'weight' ? weightIsBold(raw) : styleIsItalic(raw)
    return pw.shown(`${id}.${prop}`, { kind: 'uniform' as const, value: on ? (prop === 'weight' ? 'bold' : 'italic') : 'normal' })
  }
  const edit = (key: string, value: unknown, role: string, prop: string) =>
    pw.write(key, value, () => editBoundStyle({ kind: 'element', role, prop, value }))
  return (
    <StyleRow id={id}>
      <ControlLine line={`${id}.family`}>
        <div data-style-cell={`${id}.family`} className={cn('flex min-w-0 flex-1', WIDE_FAMILY_COL)}>
          <FamilySelect
            label={sp('familyOf', { row: label })}
            value={pw.shown(`${id}.family`, { kind: 'uniform' as const, value: familyText(readPath(data, `element.${familyRole}.fontfamily`)) })}
            options={families.options}
            labels={families.names}
            onChange={(v) => edit(`${id}.family`, v, familyRole, 'fontfamily')}
          />
        </div>
      </ControlLine>
      <ControlLine line={`${id}.size`}>
        <div data-style-cell={`${id}.size`} className="contents">
          <PtField
            label={sp('sizeOf', { row: label })}
            value={pw.shown(`${id}.size`, { kind: 'uniform' as const, value: typeof size === 'number' ? size : undefined })}
            field={NUMBER_SPEC.fontsize}
            step={0.5}
            onChange={(v) => edit(`${id}.size`, v, sizeRole, 'fontsize')}
          />
        </div>
        <FaceToggles
          row={id}
          weight={face('weight')}
          style={face('style')}
          onWeight={(v) => faceRole && edit(`${id}.weight`, v, faceRole, 'weight')}
          onStyle={(v) => faceRole && edit(`${id}.style`, v, faceRole, 'style')}
        />
      </ControlLine>
    </StyleRow>
  )
})

/** 画布标注：样式里存 `annotation.{fontFamily,sizePt,bold,italic}`（粗 / 斜体是 boolean） */
function CanvasAnnotationRow({ data }: { data: Data }) {
  useTranslation(['workspace', 'inspector'])
  const pw = useContext(CanvasPending)!
  const label = ROW_LABEL.annotation()
  const size = readPath(data, 'annotation.sizePt')
  const face = (prop: 'bold' | 'italic') => {
    const raw = readPath(data, `annotation.${prop}`)
    const on = raw === true || (prop === 'bold' ? weightIsBold(raw) : styleIsItalic(raw))
    return pw.shown(`annotation.${prop}`, { kind: 'uniform' as const, value: on ? prop : 'normal' })
  }
  const write = (key: string, value: unknown, prop: 'sizePt' | 'fontFamily') =>
    pw.write(key, value, () => editBoundStyle({ kind: 'annotation', prop, value }))
  const writeFace = (prop: 'bold' | 'italic', v: string) =>
    pw.write(`annotation.${prop}`, v, () => editBoundStyle({ kind: 'annotation', prop, value: v === prop }))
  return (
    <StyleRow id="annotation">
      <ControlLine line="annotation.family">
        <div data-style-cell="annotation.family" className={cn('flex min-w-0 flex-1', WIDE_FAMILY_COL)}>
          <FamilySelect
            label={sp('familyOf', { row: label })}
            value={pw.shown('annotation.family', { kind: 'uniform' as const, value: familyText(readPath(data, 'annotation.fontFamily')) })}
            options={CANVAS_TEXT_FAMILIES}
            onChange={(v) => write('annotation.family', v, 'fontFamily')}
          />
        </div>
      </ControlLine>
      <ControlLine line="annotation.size">
        <div data-style-cell="annotation.size" className="contents">
          <PtField
            label={sp('sizeOf', { row: label })}
            value={pw.shown('annotation.size', { kind: 'uniform' as const, value: typeof size === 'number' ? size : undefined })}
            field={NUMBER_SPEC.sizePt}
            step={0.5}
            onChange={(v) => write('annotation.size', v, 'sizePt')}
          />
        </div>
        <FaceToggles
          row="annotation"
          weight={face('bold')}
          style={face('italic')}
          onWeight={(v) => writeFace('bold', v)}
          onStyle={(v) => writeFace('italic', v)}
        />
      </ControlLine>
    </StyleRow>
  )
}

const CanvasLineRow = memo(function CanvasLineRow({
  data,
  id,
  role,
  prop,
}: {
  data: Data
  id: string
  role: string
  prop: string
}) {
  useTranslation(['workspace', 'inspector'])
  const pw = useContext(CanvasPending)!
  const label = ROW_LABEL[id]()
  const raw = readPath(data, `element.${role}.${prop}`)
  const write = (v: unknown) => pw.write(id, v, () => editBoundStyle({ kind: 'element', role, prop, value: v }))
  const shown = pw.shown(id, { kind: 'uniform' as const, value: raw })
  const spec = NUMBER_SPEC[prop]
  return (
    <StyleRow id={id}>
      <ControlLine line={id}>
        <div data-style-cell={id} className="contents">
          {prop === 'direction' ? (
            <Select
              className={VALUE_W}
              ariaLabel={label}
              value={typeof shown.value === 'string' ? shown.value : ''}
              onChange={write}
              options={TICK_DIRECTIONS.map((o) => ({ value: o, label: optionLabel(prop, o) }))}
            />
          ) : (
            <PtField label={label} value={shown} field={spec} step={spec?.step ?? 0.05} onChange={write} />
          )}
        </div>
      </ControlLine>
    </StyleRow>
  )
})
