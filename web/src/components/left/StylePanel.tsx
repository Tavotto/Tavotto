import { memo, useCallback, useEffect, useMemo, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Paintbrush, RotateCcw } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import type { EditableField, Manifest, ManifestElement } from '@/lib/api'
import { focusFailureMessage, openProblemAt } from '@/lib/issueFocus'
import { profileName } from '@/lib/profileText'
import {
  CANVAS_FAMILY_PATH,
  CANVAS_SIZE_PATH,
  cellIssues,
  elementsWith,
  FIGURE_LINE_ROWS,
  FIGURE_TEXT_ROWS,
  type CellSubject,
} from '@/lib/stylePanelModel'
import { isSubLabel, styleOverrideTargets } from '@/lib/stylePresets'
import { CANVAS_TEXT_FAMILIES, type TypographyValue } from '@/lib/typography'
import { cn } from '@/lib/utils'
import type { ValidationIssue } from '@/lib/validation'
import { issueTitle, SEVERITY_ICON } from '@/lib/validationText'
import { enterElementEdit } from '@/store/actions'
import {
  alignCanvasToStyle,
  bindCanvasStyle,
  bindingName,
  editBoundStyle,
  restoreCanvasStyle,
  restoreReady,
  styleMismatchCount,
} from '@/store/styleBinding'
import { useDocumentStore } from '@/store/documentStore'
import { useProfileStore } from '@/store/profileStore'
import {
  panelRender,
  useExactPanelManifest,
  usePanelDisplayManifest,
  usePanelRender,
  useRenderStore,
} from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import type { PanelObject, TextObject } from '@/types/document'
import { useCanvasTypography } from '../inspector/typographyAdapter'
import { optionLabel } from '../inspector/roles/registry'
import { useTextStyleAdapter } from '../inspector/textStyleAdapter'
import type { ControlValue } from '../inspector/textStyleModel'
import { Button, IconButton } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Section } from '../ui/Field'
import { NumberField } from '../ui/Input'
import { Select } from '../ui/Select'
import { useCurrentFigure } from './useProblemScope'

/** 本面板的文案在 workspace:stylePanel.* 下 */
const sp = (key: string, values?: Record<string, unknown>) =>
  translate(`stylePanel.${key}`, { ns: 'workspace', ...(values ?? {}) })

/** 行名写成字面量表：模板拼出来的键死键门禁看不见 */
const ROW_LABEL: Record<string, () => string> = {
  title: () => sp('row.title'),
  axis_label: () => sp('row.axisLabel'),
  ticks: () => sp('row.ticks'),
  legend: () => sp('row.legend'),
  annotation: () => sp('row.annotation'),
  dataLine: () => sp('row.dataLine'),
  frame: () => sp('row.frame'),
  tickDirection: () => sp('row.tickDirection'),
}

/** 没有任何问题时给格子的稳定空数组（每次新建会让 memo 的行白白重画） */
const NO_ISSUES: ValidationIssue[] = []

/**
 * 左栏「样式」：**当前图长什么样**，随时看、随时改。
 *
 * 与「问题」并列、各管一半：这里管「长什么样」，问题面板管「哪里不合规」。一格与规范
 * 不符时它只**认回**问题清单里已有的那一条（`stylePanelModel.cellIssues`），点那颗
 * 记号跳过去（`issueFocus.openProblemAt`）——不在这里再判一遍规范。
 *
 * * **当前图**与问题面板同一个判据（`useCurrentFigure`），不写第二份。
 * * **数字是页面上读者量到的 pt**：换算在写入器里（`useTextStyleAdapter` 过
 *   `stylePresets.pagePtLens`，属性页用的是同一个），这里拿到的读数、字段上下界、交出去的
 *   值都已经是页面上的。面板缩到 60% 时这里写 9，读者量到的就是 9，而不是 5.4。
 *   画布标注的字号本来就是页面 pt，不换算。
 * * **改动走 Inspector 那条路**：图内元素经 `useTextStyleAdapter`（一次点击 = 一次
 *   `setOverrides` commit），画布标注经 `useCanvasTypography`。
 * * **值四档不压扁**：几个元素不一致时是「多个值」，不拿第一个冒充全部。
 * * 底部「应用样式」= `planStyle` + `applyStylePlan`（一次 commit），先说会改几处；
 *   「恢复原样」清的正是样式管得到的那批 override（`styleOverrideTargets`），走与
 *   属性页恢复菜单同一个 `clearOverrides`。样式库的管理仍在设置里（「管理样式…」）。
 */
export function StylePanel() {
  useTranslation(['workspace', 'inspector'])
  const figure = useCurrentFigure()
  const panel = useDocumentStore((s) => {
    const o = figure.id ? s.doc.objects.find((x) => x.id === figure.id) : undefined
    return o?.type === 'panel' ? o : null
  })
  const manifest = usePanelDisplayManifest(panel)
  const exact = useExactPanelManifest(panel)
  const rendering = usePanelRender(panel)?.status === 'rendering'

  if (!panel) {
    // 跟随样式是**画布**一级的事：没选中图时底部的绑定照样在（上面只说怎么看一张图的样式）
    return (
      <div className="flex min-h-0 flex-1 flex-col" data-style-panel-empty>
        <EmptyState
          icon={Paintbrush}
          title={sp('emptyTitle')}
          action={{ label: sp('emptyAction'), onClick: () => useUiStore.getState().setLeftTab('layers') }}
        />
        <ApplyStyle />
      </div>
    )
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col" data-style-panel>
      <p className="shrink-0 truncate px-3 text-sm text-ink" data-style-figure title={figure.name ?? ''}>
        {figure.name}
      </p>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {manifest ? (
          <FigureStyle panel={panel} manifest={exact ?? manifest} exact={!!exact} />
        ) : panel.script ? (
          /* 可编辑、但这一会话还没渲染过：值要引擎读出来。**不自动跑脚本**（「只带基线、
             还没动过」的面板不渲染，heavy 脚本要几分钟——`useEngineSync.renderTargets`），
             由用户点一下，与元素树的「加载元素清单」同一个动作；左栏留在这里 */
          <EmptyState
            icon={Paintbrush}
            title={sp('needRender')}
            hint={rendering ? sp('building') : undefined}
            action={
              rendering
                ? undefined
                : { label: sp('load'), onClick: () => enterElementEdit(panel.id, { leftTab: 'keep' }) }
            }
          />
        ) : (
          /* 不是脚本生成的图：没有图内元素可读，不摆一排读不到值的空控件 */
          <p className="px-3 py-4 text-xs leading-relaxed text-ink-2">{sp('noManifest')}</p>
        )}
      </div>
      <ApplyStyle />
    </div>
  )
}

function FigureStyle({ panel, manifest, exact }: { panel: PanelObject; manifest: Manifest; exact: boolean }) {
  const bound = useDocumentStore((s) => !!s.doc.style && !s.doc.style.detached)
  const locked = !bound && !exact
  const all = useValidationStore((s) => s.issues)
  const texts = useDocumentStore((s) => s.doc.objects)
  const annotations = useMemo(
    // 子图序号标签 (a)(b)(c) 在样式里是另一项（`subLabel`），不跟普通标注一起改（Codex #547）
    () => texts.filter((o): o is TextObject => o.type === 'text' && !isSubLabel(o)),
    [texts],
  )
  const jump = useCallback(
    (issue: ValidationIssue) => {
      const outcome = openProblemAt(issue, useValidationStore.getState().issues, panel.id)
      if (!outcome.ok) useUiStore.getState().setStatus(focusFailureMessage(outcome.reason), 'error')
    },
    [panel.id],
  )

  return (
    <>
      <Section title={sp('groupText')}>
        {FIGURE_TEXT_ROWS.map((row) => (
          <FigureTextRow
            key={row.id}
            id={row.id}
            panel={panel}
            manifest={manifest}
            sizeRole={row.sizeRole}
            familyRole={row.familyRole}
            issues={all}
            onJump={jump}
            bound={bound}
            locked={locked}
          />
        ))}
        {annotations.length > 0 && (
          <AnnotationRow texts={annotations} issues={all} onJump={jump} bound={bound} />
        )}
      </Section>
      <Section title={sp('groupLines')}>
        {FIGURE_LINE_ROWS.map((row) => (
          <FigureLineRow
            key={row.id}
            id={row.id}
            panel={panel}
            manifest={manifest}
            role={row.role}
            prop={row.prop}
            issues={all}
            onJump={jump}
            bound={bound}
            locked={locked}
          />
        ))}
      </Section>
    </>
  )
}

/* ---------------------------------- 行 ----------------------------------- */

/**
 * 一行的骨架：行名 + 控件 + 行尾的问题记号。记号那一格**总是占着**（28px），
 * 有没有问题各行的控件都从同一条竖线起排、在同一条竖线收。
 */
function StyleRow({
  id,
  children,
  issue,
  onJump,
}: {
  id: string
  children: ReactNode
  issue: ValidationIssue | null
  onJump: (issue: ValidationIssue) => void
}) {
  const label = ROW_LABEL[id]()
  const Icon = issue ? SEVERITY_ICON[issue.severity] : null
  return (
    <div data-style-row={id} className="flex h-7 items-center gap-1.5">
      <span className="w-14 shrink-0 truncate text-xs text-ink-2">{label}</span>
      <div className="flex min-w-0 flex-1 items-center gap-1.5">{children}</div>
      <span className="flex w-7 shrink-0 justify-center">
        {issue && Icon && (
          <IconButton
            data-style-issue={id}
            label={sp('issueJump', { title: issueTitle(issue) })}
            className="text-warn"
            onClick={() => onJump(issue)}
          >
            <Icon size={ICON_SIZE.sm} aria-hidden />
          </IconButton>
        )}
      </span>
    </div>
  )
}

/**
 * 一格的读数：图内那侧是 `ControlValue`（uniform / mixed / unavailable），画布标注那侧是
 * 属性能力层的 `TypographyValue`（多一档 inherit——没设过、显示继承来的值）。两边都不压扁。
 */
type CellValue = ControlValue | TypographyValue

/** 不合规的那一格：警告色的一圈描边（记号在行尾，这一圈说「是这一格」） */
const cellClass = (bad: boolean) => cn('min-w-0 rounded-sm', bad && 'ring-1 ring-warn')

/** 字体下拉：mixed 时空值走占位（「多个值」），不谎报其中某一个 */
function FamilySelect({
  label,
  value,
  options,
  onChange,
  locked,
}: {
  label: string
  value: CellValue
  options: readonly string[]
  onChange: (v: string) => void
  /** 此刻不许写（见 `FigureRowProps.locked`）：置灰并说原因 */
  locked?: boolean
}) {
  const current =
    value.kind === 'uniform' || value.kind === 'inherit' ? String(value.value ?? '') : ''
  // 当前值不在选项里（脚本写了一个引擎首选表之外的族）时照样显示它：把它换掉再
  // 改文档是最坏的处置（typography 能力层「装不上的字体保留名字」同一条）
  const opts = current && !options.includes(current) ? [current, ...options] : [...options]
  return (
    <Select
      className="min-w-0 flex-1"
      ariaLabel={label}
      value={value.kind === 'mixed' ? '' : current}
      placeholder={value.kind === 'mixed' ? translate('element.mixedValues', { ns: 'inspector' }) : undefined}
      onChange={onChange}
      disabled={locked}
      title={locked ? sp('waitingRender') : undefined}
      options={opts.map((o) => ({ value: o, label: optionLabel('fontfamily', o) }))}
    />
  )
}

/** 页面 pt 的数字框：mixed 留空 + 占位；两位小数（0.75 不显示成 0.8） */
function PtField({
  label,
  value,
  field,
  step,
  onChange,
  locked,
}: {
  label: string
  value: CellValue
  /** 值域——写入器给的，已经换到页面上（`pagePtLens.field`） */
  field?: Pick<EditableField, 'min' | 'max'>
  step: number
  onChange: (v: number) => void
  locked?: boolean
}) {
  const shown = value.kind === 'uniform' || value.kind === 'inherit' ? value.value : undefined
  return (
    <NumberField
      ariaLabel={label}
      value={value.kind === 'mixed' ? NaN : Number(shown ?? NaN)}
      mixed={value.kind === 'mixed'}
      min={field?.min}
      max={field?.max}
      step={step}
      precision={2}
      disabled={locked}
      title={locked ? sp('waitingRender') : undefined}
      unit="pt"
      className="shrink-0"
      onChange={onChange}
    />
  )
}

interface FigureRowProps {
  /** 当前画布绑了样式：改一格 = 改这套样式本身（`editBoundStyle`），画布上所有图跟着对齐 */
  bound: boolean
  /**
   * 没绑样式时直接往这张图上写 override，gid 取自显示用的 manifest——那可能是**上一版**（脚本 /
   * override 刚变、这一版还在渲染）。拿过期的 gid 写 override 会写到已经不存在的元素上
   * （Codex #547 P1，与 styleBinding 只认 `exactPanelManifest` 同一条纪律），所以这一版没回来之前
   * 控件置灰、说原因。绑着样式时写的是样式本身，不经这里的 gid，不锁。
   */
  locked: boolean
  panel: PanelObject
  manifest: Manifest
  issues: ValidationIssue[]
  onJump: (issue: ValidationIssue) => void
}

const gidsOf = (els: ManifestElement[]) => els.map((e) => e.gid)

/**
 * 图内文字的一行：字体（在 `familyRole` 上）+ 页面上的字号（在 `sizeRole` 上）。
 * 两格各用一份 Inspector 的写入器（`useTextStyleAdapter`）：一次改动 = 一次 commit。
 */
const FigureTextRow = memo(function FigureTextRow({
  id,
  panel,
  manifest,
  sizeRole,
  familyRole,
  issues,
  onJump,
  bound,
  locked,
}: FigureRowProps & { id: string; sizeRole: string; familyRole: string }) {
  // memo 的行 props 不随语言变：自己订阅，切语言时行名 / 选项名跟着换（Codex #547）
  useTranslation(['workspace', 'inspector'])
  const sizeEls = useMemo(() => elementsWith(manifest, sizeRole, 'fontsize'), [manifest, sizeRole])
  const familyEls = useMemo(
    () => elementsWith(manifest, familyRole, 'fontfamily'),
    [manifest, familyRole],
  )
  const size = useTextStyleAdapter(panel, sizeEls, SIZE_PROPS)
  const family = useTextStyleAdapter(panel, familyEls, FAMILY_PROPS)
  if (!sizeEls.length && !familyEls.length) return null

  const label = ROW_LABEL[id]()
  const rowGids = [...gidsOf(sizeEls), ...gidsOf(familyEls)]
  const at = (props: string[]): CellSubject => ({ objectIds: [panel.id], gids: rowGids, props })
  const sizeIssues = issues.length ? cellIssues(issues, at(['fontsize'])) : NO_ISSUES
  const familyIssues = issues.length ? cellIssues(issues, at(['fontfamily'])) : NO_ISSUES
  const worst = [...familyIssues, ...sizeIssues].sort(bySeverity)[0] ?? null
  const familyField = family.fieldOf('fontfamily')
  const sizeField = size.fieldOf('fontsize')

  return (
    <StyleRow id={id} issue={worst} onJump={onJump}>
      {familyField && (
        <div data-style-cell={`${id}.family`} className={cn(cellClass(familyIssues.length > 0), 'flex-1')}>
          <FamilySelect
            label={sp('familyOf', { row: label })}
            value={family.valueOf('fontfamily')}
            options={familyField.options ?? []}
            locked={locked}
            onChange={(v) =>
              bound
                ? void editBoundStyle({ kind: 'element', role: familyRole, prop: 'fontfamily', value: v })
                : family.writeOnce('fontfamily', v)
            }
          />
        </div>
      )}
      {sizeField && (
        <div data-style-cell={`${id}.size`} className={cellClass(sizeIssues.length > 0)}>
          <PtField
            label={sp('sizeOf', { row: label })}
            value={size.valueOf('fontsize')}
            field={sizeField}
            step={0.5}
            locked={locked}
            onChange={(v) =>
              bound
                ? void editBoundStyle({ kind: 'element', role: sizeRole, prop: 'fontsize', value: v })
                : size.writeOnce('fontsize', v)
            }
          />
        </div>
      )}
    </StyleRow>
  )
})

const SIZE_PROPS = ['fontsize'] as const
const FAMILY_PROPS = ['fontfamily'] as const

/** 「线条」的一行：一条属性（线宽换算到页面 pt；刻度方向是枚举） */
const FigureLineRow = memo(function FigureLineRow({
  id,
  panel,
  manifest,
  role,
  prop,
  issues,
  onJump,
  bound,
  locked,
}: FigureRowProps & { id: string; role: string; prop: string }) {
  useTranslation(['workspace', 'inspector'])
  const els = useMemo(() => elementsWith(manifest, role, prop), [manifest, role, prop])
  const props = useMemo(() => [prop], [prop])
  const adapter = useTextStyleAdapter(panel, els, props)
  const field = adapter.fieldOf(prop)
  if (!els.length || !field) return null

  const label = ROW_LABEL[id]()
  const found = issues.length
    ? cellIssues(issues, { objectIds: [panel.id], gids: gidsOf(els), props: [prop] })
    : NO_ISSUES
  const value = adapter.valueOf(prop)

  return (
    <StyleRow id={id} issue={found[0] ?? null} onJump={onJump}>
      <div data-style-cell={id} className={cn(cellClass(found.length > 0), field.type === 'enum' && 'flex-1')}>
        {field.type === 'enum' ? (
          <Select
            className="min-w-0 flex-1"
            ariaLabel={label}
            value={value.kind === 'uniform' ? String(value.value) : ''}
            placeholder={
              value.kind === 'mixed' ? translate('element.mixedValues', { ns: 'inspector' }) : undefined
            }
            onChange={(v) =>
              bound ? void editBoundStyle({ kind: 'element', role, prop, value: v }) : adapter.writeOnce(prop, v)
            }
            disabled={locked}
            title={locked ? sp('waitingRender') : undefined}
            options={(field.options ?? []).map((o) => ({ value: o, label: optionLabel(prop, o) }))}
          />
        ) : (
          <PtField
            label={label}
            value={value}
            field={field}
            step={0.05}
            locked={locked}
            onChange={(v) =>
              bound
                ? void editBoundStyle({ kind: 'element', role, prop, value: v })
                : adapter.writeOnce(prop, v)
            }
          />
        )}
      </div>
    </StyleRow>
  )
})

/** 画布标注：字号本来就是页面上的 pt（不换算），字体是画布文字的闭集 */
function AnnotationRow({
  texts,
  issues,
  onJump,
  bound,
}: {
  bound: boolean
  texts: TextObject[]
  issues: ValidationIssue[]
  onJump: (issue: ValidationIssue) => void
}) {
  const adapter = useCanvasTypography(texts)
  const label = ROW_LABEL.annotation()
  const ids = texts.map((o) => o.id)
  const sizeIssues = cellIssues(issues, { objectIds: ids, gids: null, props: [CANVAS_SIZE_PATH] })
  const familyIssues = cellIssues(issues, { objectIds: ids, gids: null, props: [CANVAS_FAMILY_PATH] })
  const worst = [...familyIssues, ...sizeIssues].sort(bySeverity)[0] ?? null
  return (
    <StyleRow id="annotation" issue={worst} onJump={onJump}>
      <div data-style-cell="annotation.family" className={cn(cellClass(familyIssues.length > 0), 'flex-1')}>
        <FamilySelect
          label={sp('familyOf', { row: label })}
          value={adapter.valueOf('fontFamily')}
          options={CANVAS_TEXT_FAMILIES}
          onChange={(v) =>
            bound ? void editBoundStyle({ kind: 'annotation', prop: 'fontFamily', value: v }) : adapter.writeOnce('fontFamily', v)
          }
        />
      </div>
      <div data-style-cell="annotation.size" className={cellClass(sizeIssues.length > 0)}>
        <PtField
          label={sp('sizeOf', { row: label })}
          value={adapter.valueOf('sizePt')}
          step={0.5}
          onChange={(v) =>
            bound ? void editBoundStyle({ kind: 'annotation', prop: 'sizePt', value: v }) : adapter.writeOnce('sizePt', v)
          }
        />
      </div>
    </StyleRow>
  )
}

const SEVERITY_ORDER = ['error', 'warn', 'not_verifiable', 'suggestion']
const bySeverity = (a: ValidationIssue, b: ValidationIssue) =>
  SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity)

/* ------------------------------- 绑定 / 恢复 ------------------------------- */

/** 「不跟随样式」那一项的取值（样式 id 不会长这样：库里的 id 是 `builtin-…` / 生成的 uuid） */
const UNBOUND = '__unbound__'
/** 「已脱离」那一项的取值：只在脱离时出现；选回那套样式 = 恢复跟随 */
const DETACHED = '__detached__'

/**
 * 底部：当前画布**跟随**哪套样式（ADR 0081：应用 = 绑定，只有这一处）。选中一套 = 绑定
 * 并立刻对齐这张画布上的所有图（一次 commit）；「不跟随样式」= 解绑，图保持此刻的样子。
 * 绑定之后，上面各格的改动改的是这套样式本身，画布上的图跟着变。
 *
 * 撤销过一次样式修改的画布「已脱离」（ADR 0081 §十二）：选择器停在「已脱离「X」」，
 * 重新选中 X（或别的）才恢复跟随。
 *
 * 「恢复原样」清掉这张画布上所有图里样式管得到的 override，并解绑（理由见
 * `styleBinding.restoreCanvasStyle`）；没有可恢复的、也没有绑定时不摆这颗钮。
 *
 * 脚本重跑之后样式不自动对齐（用户 2026-09-25 裁决：脚本赢）：与样式不一致的处数与「对齐」只在
 * 有不一致时出现（`styleMismatchCount` / `alignCanvasToStyle`，一次 commit）。
 */
function ApplyStyle() {
  const records = useProfileStore((s) => s.styles)
  const loaded = useProfileStore((s) => s.loaded)
  const binding = useDocumentStore((s) => s.doc.style ?? null)
  const objects = useDocumentStore((s) => s.doc.objects)
  const byKey = useRenderStore((s) => s.byKey)
  const latest = useRenderStore((s) => s.latest)

  useEffect(() => {
    if (!loaded) void useProfileStore.getState().load()
  }, [loaded])

  const ready = useMemo(() => restoreReady(), [objects, byKey, latest]) // eslint-disable-line react-hooks/exhaustive-deps
  const restoreCount = useMemo(() => {
    let n = 0
    for (const o of objects) {
      if (o.type !== 'panel') continue
      const m = panelRender({ byKey, latest }, o)?.manifest
      if (m) n += styleOverrideTargets(o, m).length
    }
    return n
  }, [objects, byKey, latest])

  const detached = !!binding?.detached
  const mismatches = useMemo(
    () => (binding && !detached ? styleMismatchCount() : 0),
    [binding, detached, objects, byKey, latest, records], // eslint-disable-line react-hooks/exhaustive-deps
  )
  const options = [
    ...(binding && detached ? [{ value: DETACHED, label: sp('detached', { name: bindingName(binding) }) }] : []),
    { value: UNBOUND, label: sp('unbound') },
    ...records.map((r) => ({ value: r.id, label: profileName(r) })),
    // 绑着的那一条库里找不到了（换了台电脑）：照样列出来，快照仍然说了算
    ...(binding && !records.some((r) => r.id === binding.id)
      ? [{ value: binding.id, label: bindingName(binding) }]
      : []),
  ]

  return (
    <section data-style-apply className="shrink-0 border-t border-border px-3 pb-3 pt-2">
      <div className="flex h-7 items-center gap-1.5">
        <span className="w-14 shrink-0 text-xs text-ink-2">{sp('bindLabel')}</span>
        <Select
          className="min-w-0 flex-1"
          ariaLabel={sp('bindLabel')}
          value={detached ? DETACHED : (binding?.id ?? UNBOUND)}
          onChange={(id) => id !== DETACHED && bindCanvasStyle(id === UNBOUND ? null : id)}
          options={options}
        />
      </div>
      <p data-style-bind-hint className="mt-1.5 text-xs leading-relaxed text-ink-2">
        {detached ? sp('detachedHint') : binding ? sp('boundHint') : sp('unboundHint')}
      </p>
      {mismatches > 0 && (
        <div data-style-mismatch className="mt-1.5 flex h-7 items-center gap-1.5">
          <span className="min-w-0 flex-1 truncate text-xs text-ink-2">{sp('mismatch', { count: mismatches })}</span>
          <Button data-style-align variant="ghost" onClick={() => void alignCanvasToStyle()}>
            {sp('align')}
          </Button>
        </div>
      )}
      <div className="mt-2 flex items-center gap-1.5">
        {(restoreCount > 0 || binding) && (
          <Button
            data-style-restore
            variant="ghost"
            // 有图还没渲染出来时认不出它身上哪些 override 是样式写的：先等，并说为什么
            disabled={!ready}
            title={ready ? undefined : sp('restoreWaiting')}
            onClick={() => void restoreCanvasStyle()}
          >
            <RotateCcw size={ICON_SIZE.sm} aria-hidden />
            {sp('restore', { count: restoreCount })}
          </Button>
        )}
        <button
          type="button"
          data-style-manage
          onClick={() => useUiStore.getState().setSettingsOpen(true, 'style')}
          className="ml-auto rounded-sm text-xs text-ink-3 underline-offset-2 outline-none hover:text-ink-2 hover:underline focus-visible:focus-ring"
        >
          {sp('manage')}
        </button>
      </div>
    </section>
  )
}
