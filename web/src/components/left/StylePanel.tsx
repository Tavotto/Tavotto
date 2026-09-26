import { memo, useEffect, useMemo, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Bold, Italic, Paintbrush, RotateCcw } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import type { EditableField, Manifest, ManifestElement } from '@/lib/api'
import { profileName } from '@/lib/profileText'
import {
  elementsWith,
  FIGURE_LINE_ROWS,
  FIGURE_TEXT_ROWS,
} from '@/lib/stylePanelModel'
import { isSubLabel, styleOverrideTargets } from '@/lib/stylePresets'
import { CANVAS_TEXT_FAMILIES, nextToggle, toggleStateOf, type TypographyValue } from '@/lib/typography'
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
import type { PanelObject, TextObject } from '@/types/document'
import { useCanvasTypography } from '../inspector/typographyAdapter'
import { optionLabel } from '../inspector/roles/registry'
import { useTextStyleAdapter } from '../inspector/textStyleAdapter'
import type { ControlValue } from '../inspector/textStyleModel'
import { StyleToggle } from '../inspector/controls/textRows'
import { Button } from '../ui/Button'
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
  tickLength: () => sp('row.tickLength'),
  tickWidth: () => sp('row.tickWidth'),
}

/**
 * 左栏「样式」：**当前图长什么样**，随时看、随时改。
 *
 * 与「问题」并列、各管一半：这里管「长什么样」，问题面板管「哪里不合规」。**这里不显示任何
 * 问题记号**（用户 2026-09-26：行尾的八角 / 三角去掉，问题只在左侧图标栏的「问题」面板里看，
 * 那颗图标带计数角标）——字号被阻断这类情况这里也不提示，不判规范、也不认回问题清单。
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
  const texts = useDocumentStore((s) => s.doc.objects)
  const annotations = useMemo(
    // 子图序号标签 (a)(b)(c) 在样式里是另一项（`subLabel`），不跟普通标注一起改（Codex #547）
    () => texts.filter((o): o is TextObject => o.type === 'text' && !isSubLabel(o)),
    [texts],
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
            faceRole={row.faceRole}
            bound={bound}
            locked={locked}
          />
        ))}
        {annotations.length > 0 && (
          <AnnotationRow texts={annotations} bound={bound} />
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
 * 一行的骨架：**两列固定网格**（2026-09-26 用户反馈：各行的控件左缘、宽度对不齐）。
 *
 * * 标签列 `4rem`：行名，放不下时截断、悬停看全名；
 * * 控件列弹性：一行或几行控件（`ControlLine`；文字行 = 字体一行 + 「字号 · 粗体 · 斜体」一行）。
 *   每一行里「值」那一格（字号 / 线宽 / 刻度方向）都是同一个宽度 `VALUE_W`、都从控件列的左缘
 *   起排，所以上下各行的数字框在同一条竖线上。
 *
 * 没有状态列：问题只在「问题」面板里看（见 `StylePanel` 的说明）。
 */
function StyleRow({ id, children }: { id: string; children: ReactNode }) {
  const label = ROW_LABEL[id]()
  return (
    <div data-style-row={id} className="grid grid-cols-[4rem_minmax(0,1fr)] items-start gap-x-1.5 py-0.5">
      <span className="h-7 truncate text-xs leading-7 text-ink-2" title={label}>
        {label}
      </span>
      <div className="flex min-w-0 flex-col gap-1">{children}</div>
    </div>
  )
}

/** 控件列里的一行：高 28，控件从左缘起排 */
function ControlLine({ line, children }: { line: string; children: ReactNode }) {
  return (
    <div data-style-line={line} className="flex h-7 min-w-0 items-center gap-1">
      {children}
    </div>
  )
}

/**
 * 「值」那一格的宽度：字号 / 线宽的数字框与刻度方向的下拉**同一个宽**。放得下「多个值 pt」
 * 与英文的「Outward」；280 px 的最窄侧栏里「值 + 粗体 + 斜体」仍排得下一行。
 */
const VALUE_W = 'w-[5.5rem] shrink-0'

/**
 * 一格的读数：图内那侧是 `ControlValue`（uniform / mixed / unavailable），画布标注那侧是
 * 属性能力层的 `TypographyValue`（多一档 inherit——没设过、显示继承来的值）。两边都不压扁。
 */
type CellValue = ControlValue | TypographyValue


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

/**
 * 页面 pt 的数字框：mixed 留空 + 占位「多个值」；两位小数（0.75 不显示成 0.8）。
 *
 * 宽度是 `VALUE_W`、输入框铺满（`fill`）：此前框按 4 个等宽字符定宽，占位「多个值」被裁成
 * 「多个僮」。mixed 时悬停说明这是什么意思、输入一个数会怎样。
 */
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
  const mixed = value.kind === 'mixed'
  return (
    <NumberField
      ariaLabel={label}
      value={mixed ? NaN : Number(shown ?? NaN)}
      mixed={mixed}
      min={field?.min}
      max={field?.max}
      step={step}
      precision={2}
      disabled={locked}
      title={locked ? sp('waitingRender') : mixed ? sp('mixedHint') : undefined}
      unit="pt"
      fill
      className={VALUE_W}
      onChange={onChange}
    />
  )
}

/**
 * 粗体 / 斜体两颗开关（与属性页 `TypographyControls` 同一个 `StyleToggle`、同一套三态：
 * mixed 画成按钮底部一道短线，不拿其中一个冒充全部）。某一条不支持（`null`）就不摆那一颗。
 */
function FaceToggles({
  row,
  weight,
  style,
  onWeight,
  onStyle,
  locked,
}: {
  row: string
  weight: TypographyValue | null
  style: TypographyValue | null
  onWeight: (next: string) => void
  onStyle: (next: string) => void
  locked?: boolean
}) {
  const mixedText = translate('element.mixedValues', { ns: 'inspector' })
  const tb = (key: string, values?: Record<string, unknown>) =>
    translate(`textBar.${key}`, { ns: 'inspector', ...(values ?? {}) })
  const hint = (state: 'on' | 'off' | 'mixed', key: string, on: string, off: string) =>
    locked ? sp('waitingRender') : tb(key, { value: state === 'mixed' ? mixedText : tb(state === 'on' ? on : off) })
  const boldState = weight ? toggleStateOf(weight, 'bold') : 'off'
  const italicState = style ? toggleStateOf(style, 'italic') : 'off'
  return (
    <>
      {weight && (
        <span className="contents" data-style-face={`${row}.weight`}>
          <StyleToggle
            state={boldState}
            label={sp('boldOf', { row: ROW_LABEL[row]() })}
            hint={hint(boldState, 'boldWeight', 'weightBold', 'weightNormal')}
            disabled={locked}
            onClick={() => onWeight(nextToggle(weight, 'bold', 'normal'))}
          >
            <Bold size={ICON_SIZE.sm} />
          </StyleToggle>
        </span>
      )}
      {style && (
        <span className="contents" data-style-face={`${row}.style`}>
          <StyleToggle
            state={italicState}
            label={sp('italicOf', { row: ROW_LABEL[row]() })}
            hint={hint(italicState, 'italicStyle', 'styleItalic', 'styleNormal')}
            disabled={locked}
            onClick={() => onStyle(nextToggle(style, 'italic', 'normal'))}
          >
            <Italic size={ICON_SIZE.sm} />
          </StyleToggle>
        </span>
      )}
    </>
  )
}

/** 图内写入器的读数 → 开关用的三态读数；`unavailable`（有元素不暴露这条）= 不摆开关 */
const faceValue = (v: ControlValue): TypographyValue | null => (v.kind === 'unavailable' ? null : v)

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
}

/**
 * 图内文字的一行：字体（在 `familyRole` 上）；页面上的字号（在 `sizeRole` 上）+ 粗体 / 斜体
 * （在 `faceRole` 上）。每一格各用一份 Inspector 的写入器（`useTextStyleAdapter`）：一次改动 =
 * 一次 commit。
 */
const FigureTextRow = memo(function FigureTextRow({
  id,
  panel,
  manifest,
  sizeRole,
  familyRole,
  faceRole,
  bound,
  locked,
}: FigureRowProps & { id: string; sizeRole: string; familyRole: string; faceRole: string | null }) {
  // memo 的行 props 不随语言变：自己订阅，切语言时行名 / 选项名跟着换（Codex #547）
  useTranslation(['workspace', 'inspector'])
  const sizeEls = useMemo(() => elementsWith(manifest, sizeRole, 'fontsize'), [manifest, sizeRole])
  const familyEls = useMemo(
    () => elementsWith(manifest, familyRole, 'fontfamily'),
    [manifest, familyRole],
  )
  const weightEls = useMemo(
    () => (faceRole ? elementsWith(manifest, faceRole, 'weight') : NO_ELEMENTS),
    [manifest, faceRole],
  )
  const styleEls = useMemo(
    () => (faceRole ? elementsWith(manifest, faceRole, 'style') : NO_ELEMENTS),
    [manifest, faceRole],
  )
  const size = useTextStyleAdapter(panel, sizeEls, SIZE_PROPS)
  const family = useTextStyleAdapter(panel, familyEls, FAMILY_PROPS)
  const weight = useTextStyleAdapter(panel, weightEls, WEIGHT_PROPS)
  const style = useTextStyleAdapter(panel, styleEls, STYLE_PROPS)
  if (!sizeEls.length && !familyEls.length) return null

  const label = ROW_LABEL[id]()
  const familyField = family.fieldOf('fontfamily')
  const sizeField = size.fieldOf('fontsize')
  const weightVal = faceRole && weight.fieldOf('weight') ? faceValue(weight.valueOf('weight')) : null
  const styleVal = faceRole && style.fieldOf('style') ? faceValue(style.valueOf('style')) : null
  const writeFace = (prop: 'weight' | 'style', v: string) =>
    bound && faceRole
      ? void editBoundStyle({ kind: 'element', role: faceRole, prop, value: v })
      : (prop === 'weight' ? weight : style).writeOnce(prop, v)

  return (
    <StyleRow id={id}>
      {familyField && (
        <ControlLine line={`${id}.family`}>
          <div data-style-cell={`${id}.family`} className="flex min-w-0 flex-1">
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
        </ControlLine>
      )}
      {(sizeField || weightVal || styleVal) && (
        <ControlLine line={`${id}.size`}>
          {sizeField && (
            <div data-style-cell={`${id}.size`} className="contents">
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
          <FaceToggles
            row={id}
            weight={weightVal}
            style={styleVal}
            locked={locked}
            onWeight={(v) => writeFace('weight', v)}
            onStyle={(v) => writeFace('style', v)}
          />
        </ControlLine>
      )}
    </StyleRow>
  )
})

const NO_ELEMENTS: ManifestElement[] = []
const SIZE_PROPS = ['fontsize'] as const
const FAMILY_PROPS = ['fontfamily'] as const
const WEIGHT_PROPS = ['weight'] as const
const STYLE_PROPS = ['style'] as const

/** 「线条」的一行：一条属性（线宽 / 刻度长度换算到页面 pt；刻度方向是枚举） */
const FigureLineRow = memo(function FigureLineRow({
  id,
  panel,
  manifest,
  role,
  prop,
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
  const value = adapter.valueOf(prop)
  const write = (v: unknown) =>
    bound ? void editBoundStyle({ kind: 'element', role, prop, value: v }) : adapter.writeOnce(prop, v)

  return (
    <StyleRow id={id}>
      <ControlLine line={id}>
        <div data-style-cell={id} className="contents">
          {field.type === 'enum' ? (
            <Select
              className={VALUE_W}
              ariaLabel={label}
              value={value.kind === 'uniform' ? String(value.value) : ''}
              placeholder={
                value.kind === 'mixed' ? translate('element.mixedValues', { ns: 'inspector' }) : undefined
              }
              onChange={write}
              disabled={locked}
              title={locked ? sp('waitingRender') : value.kind === 'mixed' ? sp('mixedHint') : undefined}
              options={(field.options ?? []).map((o) => ({ value: o, label: optionLabel(prop, o) }))}
            />
          ) : (
            <PtField label={label} value={value} field={field} step={0.05} locked={locked} onChange={write} />
          )}
        </div>
      </ControlLine>
    </StyleRow>
  )
})

/** 画布标注：字号本来就是页面上的 pt（不换算），字体是画布文字的闭集；粗体 / 斜体是 `TextObject` 的 `bold` / `italic` */
function AnnotationRow({
  texts,
  bound,
}: {
  bound: boolean
  texts: TextObject[]
}) {
  const adapter = useCanvasTypography(texts)
  const label = ROW_LABEL.annotation()
  const face = (prop: 'weight' | 'style'): TypographyValue | null =>
    adapter.fieldOf(prop) ? adapter.valueOf(prop) : null
  return (
    <StyleRow id="annotation">
      <ControlLine line="annotation.family">
        <div data-style-cell="annotation.family" className="flex min-w-0 flex-1">
          <FamilySelect
            label={sp('familyOf', { row: label })}
            value={adapter.valueOf('fontFamily')}
            options={CANVAS_TEXT_FAMILIES}
            onChange={(v) =>
              bound ? void editBoundStyle({ kind: 'annotation', prop: 'fontFamily', value: v }) : adapter.writeOnce('fontFamily', v)
            }
          />
        </div>
      </ControlLine>
      <ControlLine line="annotation.size">
        <div data-style-cell="annotation.size" className="contents">
          <PtField
            label={sp('sizeOf', { row: label })}
            value={adapter.valueOf('sizePt')}
            step={0.5}
            onChange={(v) =>
              bound ? void editBoundStyle({ kind: 'annotation', prop: 'sizePt', value: v }) : adapter.writeOnce('sizePt', v)
            }
          />
        </div>
        <FaceToggles
          row="annotation"
          weight={face('weight')}
          style={face('style')}
          onWeight={(v) =>
            bound ? void editBoundStyle({ kind: 'annotation', prop: 'bold', value: v === 'bold' }) : adapter.writeOnce('weight', v)
          }
          onStyle={(v) =>
            bound ? void editBoundStyle({ kind: 'annotation', prop: 'italic', value: v === 'italic' }) : adapter.writeOnce('style', v)
          }
        />
      </ControlLine>
    </StyleRow>
  )
}


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
      {/* 与上面各行同一副两列网格：「跟随样式」的下拉与各行控件左右缘都对齐 */}
      <div className="grid h-7 grid-cols-[4rem_minmax(0,1fr)] items-center gap-x-1.5">
        <span className="truncate text-xs text-ink-2" title={sp('bindLabel')}>
          {sp('bindLabel')}
        </span>
        <Select
          className="min-w-0"
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
