import { memo, useMemo, useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Bold, Italic, X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import type { Manifest } from '@/lib/api'
import { FIGURE_LINE_ROWS, FIGURE_TEXT_ROWS } from '@/lib/stylePanelModel'
import { CANVAS_TEXT_FAMILIES, styleIsItalic, weightIsBold, withMachineFamilies } from '@/lib/typography'
import { cn } from '@/lib/utils'
import { useRenderStore } from '@/store/renderStore'
import { optionLabel } from '../inspector/roles/registry'
import { FontMissingHint, FontMissingTag, StyleToggle } from '../inspector/controls/textRows'
import { IconButton } from '../ui/Button'
import { NumberField } from '../ui/Input'
import { Select } from '../ui/Select'
import { clearPath, readPath, writePath } from './profilePath'
import { SettingRow, settingControlStyle, settingRowGrid } from './SettingRow'

/**
 * 设置 › 样式页的编辑区：**与左栏「样式」面板同一张行表**（`lib/stylePanelModel` 的
 * `FIGURE_TEXT_ROWS` / `FIGURE_LINE_ROWS`）。
 *
 * 面板 2026-09-26 起每行文字能改字体 + 字号 + 粗体 / 斜体、线条能改刻度方向 / 长度 / 线宽，
 * 这里当时只有八个数字框——同一份样式，在画布旁改得了的东西到样式库里反而改不了。行表
 * 从同一处派生：面板加一行，这里跟着多一行，不另抄一份角色 × 属性。
 *
 * 与面板的不同只有一处：这里编辑的是**样式本身**，不是一张图——
 *
 * * 每一格多一档「这份样式没管这一项」（未设置）：字体下拉留空、数字框留空，行尾的 × 把
 *   整行清回这一档。粗 / 斜体是三态：未设置 → 开（`bold`）→ 显式关（`normal`：「一律不加粗」
 *   也是一种规定，与「不管」不是一回事）→ 回到未设置。
 * * 字体的选项没有 manifest 可问：通用三族 + 每一张已渲染的图里引擎报过的首选项与本机字体
 *   （`font_families`，逐张并，`figureFamilyOptions`）+ 样式里已经写着的那个名字（不认识也照样显示，
 *   不替用户换掉）；哪个运行时都画不出来的（`options_unavailable`）标「未安装」。画布标注是画布文字的
 *   闭集 `CANVAS_TEXT_FAMILIES`。
 * * 形状跟着数据走：图内那几行写 `element.<role>.<prop>`（`weight` / `style` 是 `bold` /
 *   `italic` / `normal`），画布标注写 `annotation.{fontFamily,sizePt,bold,italic}`（boolean），
 *   与 `StyleProfileData` / `styleBinding.editBoundStyle` 写出来的同一种形状。
 */

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`profiles.${key}`, { ns: 'dialogs', ...(values ?? {}) })
const sp = (key: string, values?: Record<string, unknown>) =>
  translate(`stylePanel.${key}`, { ns: 'workspace', ...(values ?? {}) })

/** 行名写成字面量表：模板拼出来的键死键门禁看不见。面板有的行用面板那一份字 */
const ROW_LABEL: Record<string, () => string> = {
  title: () => sp('row.title'),
  axis_label: () => sp('row.axisLabel'),
  ticks: () => sp('row.ticks'),
  legend: () => sp('row.legend'),
  text: () => st('row.text'),
  annotation: () => sp('row.annotation'),
  dataLine: () => sp('row.dataLine'),
  frame: () => sp('row.frame'),
  tickDirection: () => sp('row.tickDirection'),
  tickLength: () => sp('row.tickLength'),
  tickWidth: () => sp('row.tickWidth'),
}

/** 一行文字：各格在样式内容里的点分路径；没有的格不摆 */
interface TextRowSpec {
  id: string
  /** `annotation` 的粗 / 斜体是 boolean，图内的是 `weight` / `style` 的取值 */
  kind: 'element' | 'annotation'
  family: string
  size: string
  weight: string | null
  style: string | null
}

export const STYLE_TEXT_ROWS: readonly TextRowSpec[] = [
  ...FIGURE_TEXT_ROWS.map((r) => ({
    id: r.id,
    kind: 'element' as const,
    family: `element.${r.familyRole}.fontfamily`,
    size: `element.${r.sizeRole}.fontsize`,
    weight: r.faceRole ? `element.${r.faceRole}.weight` : null,
    style: r.faceRole ? `element.${r.faceRole}.style` : null,
  })),
  // 其余图内文字（`text` 角色；应用时只落在 `text` 角色上，示例图里没有它的一笔）：面板上没有这一行——
  // 它不是一张图上的某一类字——但样式页一直管着它的字号
  {
    id: 'text',
    kind: 'element',
    family: 'element.text.fontfamily',
    size: 'element.text.fontsize',
    weight: 'element.text.weight',
    style: 'element.text.style',
  },
  {
    id: 'annotation',
    kind: 'annotation',
    family: 'annotation.fontFamily',
    size: 'annotation.sizePt',
    weight: 'annotation.bold',
    style: 'annotation.italic',
  },
]

/** 数字格的值域与步长（按属性；与面板的步长同一档：字号 0.5、线宽类 0.05） */
const NUMBER_SPEC: Record<string, { min: number; max: number; step: number }> = {
  fontsize: { min: 3, max: 72, step: 0.5 },
  sizePt: { min: 3, max: 72, step: 0.5 },
  linewidth: { min: 0.1, max: 10, step: 0.05 },
  spine_linewidth: { min: 0.1, max: 10, step: 0.05 },
  length: { min: 0, max: 20, step: 0.5 },
  width: { min: 0.1, max: 10, step: 0.05 },
}

/** 刻度方向的取值（引擎 `ticks.direction` 的选项） */
const TICK_DIRECTIONS = ['out', 'in', 'inout'] as const

export const STYLE_LINE_ROWS = FIGURE_LINE_ROWS.map((r) => ({
  id: r.id,
  path: `element.${r.role}.${r.prop}`,
  prop: r.prop,
}))

const GENERIC_FAMILIES = ['serif', 'sans-serif', 'monospace']

const leaf = (path: string) => path.slice(path.lastIndexOf('.') + 1)

/**
 * 粗 / 斜体格读出来的状态。`on`：`null` = 这份样式没管；`raw`：样式里写着的是**非规范值**时的原值
 * （从图里提取的样式可能是 `oblique`、`semibold`、`600`——引擎与后端都原样保留），规范值时为 null。
 * 非规范值按与引擎同一口径归一来显示（`weightIsBold` ≥ 600、`styleIsItalic` 非 normal），但不改写它。
 */
interface FaceRead {
  on: boolean | null
  raw: string | null
}

function readFace(kind: TextRowSpec['kind'], which: 'weight' | 'style', value: unknown): FaceRead {
  if (value === undefined || value === null) return { on: null, raw: null }
  if (kind === 'annotation') return { on: value === true, raw: null }
  const canonOn = which === 'weight' ? 'bold' : 'italic'
  if (value === canonOn) return { on: true, raw: null }
  if (value === 'normal') return { on: false, raw: null }
  return { on: which === 'weight' ? weightIsBold(value) : styleIsItalic(value), raw: String(value) }
}

const faceValue = (kind: TextRowSpec['kind'], which: 'weight' | 'style', on: boolean): unknown =>
  kind === 'annotation' ? on : on ? (which === 'weight' ? 'bold' : 'italic') : 'normal'

/** 图内字体下拉的选项，与其中**哪一个运行时都画不出来**的那几个（`options_unavailable`） */
export interface FigureFamilies {
  options: string[]
  unavailable: string[]
}

/**
 * 图内字体下拉的选项：通用三族 + **每一张**已渲染的图里引擎报过的首选项与本机族。
 *
 * 逐张并、不挑「第一张带本机表的」：连着不报 `font_families` 的老引擎时，后面几张图的首选项
 * 不能被第一张挡掉；几个 runtime 各报各的本机表时，选项也不能随渲染先后变（Codex #703）。
 * 每张图的首选项与本机表怎么并，走唯一的并表出处 `withMachineFamilies`；图按键排序再并，
 * 先后次序与渲染顺序无关。
 *
 * 不可用标记跟着并（Codex #703）：引擎把脚本写死、本机没装的字体也放进 `options`（下拉得显示
 * 当前值），同时列进 `options_unavailable`——属性页据此标「未安装」。样式是给**所有**图用的，
 * 所以口径是「**任一**运行时画得出就不算不可用」：一张图的运行时画得出（本机表里有、或在它的
 * 选项里且没被标不可用），这个名字就是一个真实可选的字体；只有报过它的运行时全都画不出，才标。
 */
export function figureFamilyOptions(manifests: Record<string, Manifest | null | undefined>): FigureFamilies {
  const out = new Set<string>(GENERIC_FAMILIES)
  const missing = new Set<string>()
  const renderable = new Set<string>(GENERIC_FAMILIES)
  for (const key of Object.keys(manifests).sort()) {
    const manifest = manifests[key]
    if (!manifest) continue
    const preferred = new Set<string>()
    const unavailable = new Set<string>()
    for (const e of manifest.elements) {
      for (const f of e.editable) {
        if (f.prop !== 'fontfamily') continue
        for (const o of f.options ?? []) preferred.add(o)
        for (const o of f.options_unavailable ?? []) unavailable.add(o)
      }
    }
    const field = withMachineFamilies(
      { prop: 'fontfamily', options: [...preferred], options_unavailable: [...unavailable] },
      manifest.font_families,
    )
    for (const o of field?.options ?? []) {
      out.add(o)
      if (unavailable.has(o)) missing.add(o)
      else renderable.add(o)
    }
  }
  return { options: [...out], unavailable: [...missing].filter((o) => !renderable.has(o)).sort() }
}

const sameList = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && a.every((v, i) => v === b[i])

/**
 * 图内字体选项，**内容没变就交回同一个对象**：渲染态每次更新（进度、状态、别的图换了 SVG）都换一个
 * 新的 `byKey`，manifest 与本机表却没变——按 `byKey` 的引用 memo 会每次交出新数组，`FamilySelect`
 * 按引用比较选项，于是五个字体下拉各把几百个 Radix 项重建一遍（Codex #703）。并表照算（逐张并
 * 几百个名字，比重建下拉便宜得多），交出去之前按内容与上一份比——**选项与不可用标记两样都比**，
 * 只改了标记也得换新的——一样就沿用上一份。
 */
export function useFigureFamilies(): FigureFamilies {
  const byKey = useRenderStore((s) => s.byKey)
  const last = useRef<FigureFamilies | null>(null)
  return useMemo(() => {
    const next = figureFamilyOptions(Object.fromEntries(Object.entries(byKey).map(([k, r]) => [k, r.manifest])))
    const prev = last.current
    if (prev && sameList(prev.options, next.options) && sameList(prev.unavailable, next.unavailable)) return prev
    last.current = next
    return next
  }, [byKey])
}

const CANVAS_FAMILIES: FigureFamilies = { options: [...CANVAS_TEXT_FAMILIES], unavailable: [] }

export function StyleProfileFields({
  draft,
  editable,
  onChange,
}: {
  draft: Record<string, unknown>
  editable: boolean
  /**
   * 交出一次修改（这里不持有状态）。**给的是函数不是结果**：字体下拉按数据 memo，跳过重画时
   * 它手里的回调是上一次渲染的——回调里要是捏着当时的草稿，改字体会把之后改过的字号冲掉
   */
  onChange: (update: (draft: Record<string, unknown>) => Record<string, unknown>) => void
}) {
  useTranslation(['dialogs', 'workspace', 'inspector'])
  const figureFamilies = useFigureFamilies()
  const set = (path: string, value: unknown) => onChange((d) => writePath(d, path, value))
  const clear = (paths: (string | null)[]) =>
    onChange((d) => paths.reduce<Record<string, unknown>>((acc, p) => (p ? clearPath(acc, p) : acc), d))

  return (
    <div className="flex flex-col gap-4">
      <FieldGroup group="text">
        {STYLE_TEXT_ROWS.map((row) =>
          editable ? (
            <TextRowEditor
              key={row.id}
              row={row}
              draft={draft}
              families={row.kind === 'annotation' ? CANVAS_FAMILIES : figureFamilies}
              onSet={set}
              onClear={clear}
            />
          ) : (
            <SummaryRow key={row.id} id={row.id} value={textSummary(row, draft)} />
          ),
        )}
      </FieldGroup>
      <FieldGroup group="lines">
        {STYLE_LINE_ROWS.map((row) =>
          editable ? (
            <LineRowEditor key={row.id} row={row} draft={draft} onSet={set} onClear={clear} />
          ) : (
            <SummaryRow key={row.id} id={row.id} value={lineSummary(row, draft)} />
          ),
        )}
      </FieldGroup>
    </div>
  )
}

/** 一组：一条 type-section 小标题 + 若干行（与规范页同一种分组，`data-field-group` 同名） */
function FieldGroup({ group, children }: { group: 'text' | 'lines'; children: ReactNode }) {
  return (
    <div data-field-group={group} className="flex flex-col">
      <span className="type-section mb-1">{st(`group.${group}`)}</span>
      {children}
    </div>
  )
}

function TextRowEditor({
  row,
  draft,
  families,
  onSet,
  onClear,
}: {
  row: TextRowSpec
  draft: Record<string, unknown>
  families: FigureFamilies
  onSet: (path: string, value: unknown) => void
  onClear: (paths: (string | null)[]) => void
}) {
  const label = ROW_LABEL[row.id]()
  const family = readPath(draft, row.family)
  const size = readPath(draft, row.size)
  const weight = row.weight ? readFace(row.kind, 'weight', readPath(draft, row.weight)) : null
  const style = row.style ? readFace(row.kind, 'style', readPath(draft, row.style)) : null
  const paths = [row.family, row.size, row.weight, row.style]
  const anySet = paths.some((p) => p && readPath(draft, p) !== undefined)
  const range = NUMBER_SPEC[leaf(row.size)]
  const sizeSet = typeof size === 'number' && Number.isFinite(size)
  // 粗 / 斜体三态：未设置（应用时保留图里原样）→ 开 → 显式关（应用时一律去掉：图内写 `normal`、
  // 画布标注写 false）→ 回到未设置。「关」与「未设置」应用起来不是一回事，所以看得出来：未设置用
  // 与面板「多个值」同一副第三态视觉（按钮下一道短横、`aria-pressed="mixed"`），名字换成「未设置」。
  // 非规范值（`oblique` / `semibold` / `600`…）按归一后的开 / 关显示，悬停说出原值；不点就原样留着，
  // 点一下按它**显示的**那一态往下走（显示为开 → 显式关；显示为关 → 开），按下去看得见变化
  const face = (which: 'weight' | 'style', path: string | null, f: FaceRead | null) => {
    if (!path || !f) return null
    const { on, raw } = f
    const hintKey = raw !== null ? (on ? 'otherOn' : 'otherOff') : on === null ? 'unset' : on ? 'on' : 'off'
    return (
      <span className="contents" data-style-face={`${row.id}.${which}`}>
        <StyleToggle
          state={on === null ? 'mixed' : on ? 'on' : 'off'}
          mixedText={st('unset')}
          label={sp(which === 'weight' ? 'boldOf' : 'italicOf', { row: label })}
          hint={st(`faceHint.${which}.${hintKey}`, { value: raw ?? '' })}
          onClick={() =>
            on === false && raw === null ? onClear([path]) : onSet(path, faceValue(row.kind, which, !on))
          }
        >
          {which === 'weight' ? <Bold size={ICON_SIZE.sm} /> : <Italic size={ICON_SIZE.sm} />}
        </StyleToggle>
      </span>
    )
  }
  return (
    <SettingRow label={label} density="compact" data-style-row={row.id}>
      {/* 两行控件（字体一行、「字号 · 粗体 · 斜体 · ×」一行）铺满控件列：列是贴右缩到内容宽的，
            不给定宽的话字体下拉会缩成它最短的那个选项 */}
        <div className="flex w-[var(--setting-control)] min-w-0 flex-col gap-1 py-0.5">
        <div data-style-cell={`${row.id}.family`} className="flex min-w-0">
          <FamilySelect
            label={sp('familyOf', { row: label })}
            value={typeof family === 'string' ? family : ''}
            families={families}
            onChange={(v) => onSet(row.family, v)}
          />
        </div>
        <div className="flex h-7 min-w-0 items-center gap-1">
          <div data-style-cell={`${row.id}.size`} className="contents">
            <NumberField
              value={sizeSet ? (size as number) : range.min}
              mixed={!sizeSet}
              mixedPlaceholder={st('unset')}
              min={range.min}
              max={range.max}
              step={range.step}
              precision={2}
              unit="pt"
              fill
              ariaLabel={sp('sizeOf', { row: label })}
              className="w-28"
              onChange={(v) => onSet(row.size, v)}
            />
          </div>
          {face('weight', row.weight, weight)}
          {face('style', row.style, style)}
          {anySet && <ClearButton label={label} onClick={() => onClear(paths)} />}
        </div>
      </div>
    </SettingRow>
  )
}

function LineRowEditor({
  row,
  draft,
  onSet,
  onClear,
}: {
  row: (typeof STYLE_LINE_ROWS)[number]
  draft: Record<string, unknown>
  onSet: (path: string, value: unknown) => void
  onClear: (paths: (string | null)[]) => void
}) {
  const label = ROW_LABEL[row.id]()
  const raw = readPath(draft, row.path)
  const isSet = raw !== undefined
  return (
    <SettingRow label={label} density="compact" data-style-row={row.id}>
      {/* 与文字行同一副格子：控件从控件列左缘起排（与上面的字号框同一条竖线），× 在右缘；
          × 出现 / 消失时控件不左右跳 */}
      <div className="flex w-[var(--setting-control)] min-w-0 items-center gap-1">
        <div data-style-cell={row.id} className="contents">
          {row.prop === 'direction' ? (
            <Select
              className="w-28"
              ariaLabel={label}
              value={typeof raw === 'string' ? raw : ''}
              placeholder={st('unset')}
              onChange={(v) => onSet(row.path, v)}
              options={withCurrent(TICK_DIRECTIONS, typeof raw === 'string' ? raw : '').map((o) => ({
                value: o,
                label: optionLabel('direction', o),
              }))}
            />
          ) : (
            <NumberField
              value={typeof raw === 'number' ? raw : NUMBER_SPEC[row.prop].min}
              mixed={typeof raw !== 'number'}
              mixedPlaceholder={st('unset')}
              min={NUMBER_SPEC[row.prop].min}
              max={NUMBER_SPEC[row.prop].max}
              step={NUMBER_SPEC[row.prop].step}
              precision={2}
              unit="pt"
              fill
              ariaLabel={label}
              className="w-28"
              onChange={(v) => onSet(row.path, v)}
            />
          )}
        </div>
        {isSet && <ClearButton label={label} onClick={() => onClear([row.path])} />}
      </div>
    </SettingRow>
  )
}

function ClearButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <IconButton iconSize="sm" className="ml-auto shrink-0" label={st('clearField', { field: label })} onClick={onClick}>
      <X size={ICON_SIZE.sm} aria-hidden className="text-ink-3" />
    </IconButton>
  )
}

/** 当前值不在选项里（样式里写着一个本机没有的族）时照样列出来：替用户换掉是最坏的处置 */
const withCurrent = (options: readonly string[], current: string): string[] =>
  current && !options.includes(current) ? [current, ...options] : [...options]

/**
 * 字体下拉。选项常有几百项（本机字体），Radix Select 收起时也把全部 item 渲一遍——按数据
 * 比较，值与选项没变就不重画（与属性页 `FontFamilyRow` 同一个理由），改字号时不拖着字体下拉重建。
 * 哪一个运行时都画不出来的字体（`unavailable`）照样列出、名字不换，标记与当前值下的 warning
 * 用属性页同一副（`FontMissingTag` / `FontMissingHint`）。
 */
const FamilySelect = memo(
  function FamilySelect({
    label,
    value,
    families,
    onChange,
  }: {
    label: string
    value: string
    /** 选项与不可用标记是一个对象（`useFigureFamilies` 内容没变时引用不变）：按它一次比完 */
    families: FigureFamilies
    onChange: (v: string) => void
  }) {
    const missing = new Set(families.unavailable)
    return (
      <div className="flex min-w-0 flex-1 flex-col">
        <Select
          className="min-w-0 flex-1"
          ariaLabel={label}
          value={value}
          placeholder={st('unset')}
          onChange={onChange}
          options={withCurrent(families.options, value).map((o) => ({
            value: o,
            label: (
              <span>
                {optionLabel('fontfamily', o)}
                {missing.has(o) && <FontMissingTag />}
              </span>
            ),
          }))}
        />
        {missing.has(value) && <FontMissingHint />}
      </div>
    )
  },
  (a, b) =>
    a.value === b.value && a.label === b.label && a.families === b.families,
)

/* --------------------------------- 只读摘要 -------------------------------- */

const ptText = (v: unknown): string | null =>
  typeof v === 'number' && Number.isFinite(v) ? `${v} pt` : null

/** 一行文字在只读摘要里的读法：「Arial · 9 pt · 粗体」；一项都没管就是「未设置」 */
export function textSummary(row: TextRowSpec, draft: Record<string, unknown>): string {
  const family = readPath(draft, row.family)
  const parts = [
    typeof family === 'string' && family ? optionLabel('fontfamily', family) : null,
    ptText(readPath(draft, row.size)),
  ]
  const weight = row.weight ? readFace(row.kind, 'weight', readPath(draft, row.weight)) : null
  const style = row.style ? readFace(row.kind, 'style', readPath(draft, row.style)) : null
  // 非规范值照原值说（「字重 半粗」「字形 倾斜」「字重 600」），不归一成「粗体」/「不加粗」
  if (weight?.raw != null) parts.push(st('face.weightValue', { value: optionLabel('weight', weight.raw) }))
  else if (weight && weight.on !== null) parts.push(st(weight.on ? 'face.bold' : 'face.notBold'))
  if (style?.raw != null) parts.push(st('face.styleValue', { value: optionLabel('style', style.raw) }))
  else if (style && style.on !== null) parts.push(st(style.on ? 'face.italic' : 'face.notItalic'))
  const shown = parts.filter((p): p is string => !!p)
  return shown.length ? shown.join(' · ') : st('unset')
}

function lineSummary(row: (typeof STYLE_LINE_ROWS)[number], draft: Record<string, unknown>): string {
  const raw = readPath(draft, row.path)
  if (row.prop === 'direction') return typeof raw === 'string' ? optionLabel('direction', raw) : st('unset')
  return ptText(raw) ?? st('unset')
}

/**
 * 只读摘要里的一行：名字 + 值。**刻意不是一排 disabled 的控件**（审计 T41 / T42）；
 * 与 `SettingRow` 同一份网格、同一档行高，「摘要 ↔ 输入框」来回切换时整列不跳。
 */
function SummaryRow({ id, value }: { id: string; value: string }) {
  const label = ROW_LABEL[id]()
  return (
    <div
      data-summary-row
      data-style-row={id}
      style={settingControlStyle}
      className={cn('grid min-h-8 items-center gap-x-6 py-0.5 text-sm', settingRowGrid)}
    >
      <span className="min-w-0 truncate text-ink" title={label}>
        {label}
      </span>
      <span className="min-w-0 justify-self-end truncate tabular-nums text-ink" title={value}>
        {value}
      </span>
    </div>
  )
}
