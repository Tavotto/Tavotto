import { useCallback, useRef, useState } from 'react'
import {
  AlignCenterHorizontal,
  AlignCenterVertical,
  AlignEndHorizontal,
  AlignEndVertical,
  AlignHorizontalDistributeCenter,
  AlignStartHorizontal,
  AlignStartVertical,
  AlignVerticalDistributeCenter,
  CaseSensitive,
  ChevronRight,
  CircleQuestionMark,
  CornerDownLeft,
  CornerUpLeft,
  MoveDown,
  MoveHorizontal,
  MoveUp,
  MoveVertical,
  RotateCcw,
  Subscript,
  Superscript,
  TriangleAlert,
} from 'lucide-react'
import type { AlignMode } from '@/lib/geometry'
import { ENVIRONMENT_CODES } from '@/lib/api'
import type { EditableField, Manifest, ManifestElement } from '@/lib/api'
import { requestRender } from '@/hooks/useEngineSync'
import { useQuickEdit } from '@/canvas/quickEditStore'
import { ALT, cn, combo, modKey } from '@/lib/utils'
import {
  centerInFigure,
  fracToMm,
  layoutBoxes,
  mmToFrac,
  round4,
  scaleGroupAbout,
  type Rect4,
} from '@/lib/axesLayout'
import {
  alignEntries,
  annotationAlignEntries,
  geomTarget,
  groupOf,
  groupPatches,
  type Group,
  isAnnotationEntry,
  type MixedEntry,
  panelFullRect,
  positionOf,
  type AlignEntry,
} from '@/lib/elementGeom'
import {
  applyMixedAlign,
  clearOverride,
  clearOverrides,
  resetOverrides,
  setOverride,
  setOverrides,
  unhideElement,
} from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import {
  EngineEnvironmentCard,
  MissingDependencyCard,
} from '@/components/EngineEnvironmentCard'
import type { PanelObject } from '@/types/document'
import { groupHasContent, groupRank, optionLabel, propLabel, roleName, UNSUPPORTED } from './roles/registry'
import { Button } from '../ui/Button'
import { Grid2, Row, Section } from '../ui/Field'
import { ColorField, NumberField, TextArea } from '../ui/Input'
import { Menu, MenuItem } from '../ui/Menu'
import {
  toggleMathScript,
  transformCase,
  type CaseMode,
} from '@/lib/richText'
import { Popover } from '../ui/Popover'
import { Select } from '../ui/Select'
import { Toggle } from '../ui/Toggle'
import { Tip } from '../ui/Tooltip'
import { SourceSection } from './PanelSection'
import { SyncOverridesButton } from './SyncOverridesButton'

/** 图内元素编辑器：表单结构完全由 manifest.editable 决定 */
export function ElementInspector({ panel }: { panel: PanelObject }) {
  const render = useRenderStore((s) => s.byFile[panel.fileId])
  const selectedGids = useUiStore((s) => s.selectedGids)
  // 折叠状态挂在面板级组件上：同一面板内换元素不重置，换面板才归零
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({})
  const manifest = render?.manifest
  const selected = manifest
    ? selectedGids
        .map((g) => manifest.elements.find((e) => e.gid === g))
        .filter((e): e is ManifestElement => !!e)
    : []
  const picked = selected.at(-1) ?? manifest?.elements.find((e) => e.gid === 'figure') ?? null
  // 色条轴本身没什么可调的，用户想改的是色条：直接换成它的色条元素
  const element =
    picked?.is_colorbar && picked.colorbar_gid
      ? (manifest?.elements.find((e) => e.gid === picked.colorbar_gid) ?? picked)
      : picked
  // shift 加选进来的画布标注（文字/箭头/形状）：与图内元素混排对齐
  const selIds = useSelectionStore((s) => s.ids)
  const docObjects = useDocumentStore((s) => s.doc.objects)
  const annotations = docObjects.filter(
    (o) => selIds.includes(o.id) && (o.type === 'text' || o.type === 'arrow' || o.type === 'shape'),
  )
  const annEntries = annotationAlignEntries(panel, annotations)
  // 多选 → 出对齐工具条，替代单元素表单。位图会归并到宿主子图，
  // 归并后只剩一个几何目标时就没什么可对齐的，仍走单元素表单。
  // 画布标注加进来后与元素同框排版（元素写 override，标注改画布位置）。
  const entries =
    manifest && selected.length ? alignEntries(panel, manifest, selectedGids) : []
  const mixed: MixedEntry[] = [...entries, ...annEntries]
  const alignGroup = mixed.length > 1 ? mixed : null
  // 多选同一种角色 → 批量改公共属性（文字全部调字号、曲线全部换色…）
  const batch =
    selected.length > 1 && selected.every((e) => e.role === selected[0].role) ? selected : null

  return (
    <>
      {/* 缺渲染环境不是「出错」而是缺件，给能点的出口；脚本真报错才显示 traceback */}
      {render?.code === 'missing_dependency' ? (
        <Section>
          <MissingDependencyCard module={render.module} />
        </Section>
      ) : ENVIRONMENT_CODES.includes(render?.code as (typeof ENVIRONMENT_CODES)[number]) ? (
        <Section>
          <EngineEnvironmentCard compact />
        </Section>
      ) : (
        render?.error && (
          <ErrorBlock
            error={render.error}
            traceback={render.traceback}
            onRetry={() => requestRender(panel.fileId, panel.overrides, true)}
          />
        )
      )}
      {!!render?.warnings.length && (
        <Section>
          <ul className="flex flex-col gap-1">
            {render.warnings.map((w, i) => (
              <li key={i} className="flex items-start gap-1.5 text-xs leading-relaxed text-ink-2">
                <TriangleAlert size={12} className="mt-px shrink-0 text-danger" />
                <span>{w}</span>
              </li>
            ))}
          </ul>
          <OrphanOverrides panel={panel} manifest={manifest} />
        </Section>
      )}

      {/* 四层顺序：先公共属性（高频），再对齐与排列 */}
      {batch && <BatchSection panel={panel} elements={batch} />}
      {alignGroup && <AlignSection panel={panel} items={alignGroup} />}
      {alignGroup || batch ? null : (
      <Section>
        {manifest && element && <RelatedRow manifest={manifest} element={element} />}
        {!manifest ? (
          <p className="text-xs text-ink-3">
            {render?.status === 'rendering' ? '正在构建图表…' : '等待引擎渲染…'}
          </p>
        ) : !element?.editable.length ? (
          <p className="text-xs text-ink-3">点击图内元素开始编辑</p>
        ) : (
          <FieldList
            panel={panel}
            element={element}
            warnings={render?.warnings ?? []}
            openGroups={openGroups}
            onToggleGroup={(name) =>
              setOpenGroups((s) => ({ ...s, [name]: !s[name] }))
            }
          />
        )}
        {element && <UnsupportedNote role={element.role} />}
        {element?.role === 'image' && (
          <p className="mt-2 text-xs leading-relaxed text-ink-3">
            位置和大小属于宿主子图；颜色与显示属于当前图像。
          </p>
        )}
        {element?.resizable && manifest && (
          <AxesSizeMm
            panel={panel}
            element={geomTarget(manifest, element)}
            sizeMm={manifest.size_mm}
            proxied={!!element.geom_gid}
            group={groupOf(alignEntries(panel, manifest, [element.gid]), 1)}
          />
        )}
      </Section>
      )}

      <HiddenElements panel={panel} manifest={manifest} />

      <SourceSection panel={panel} />

      <AdvancedSection panel={panel} gid={element?.gid} />
    </>
  )
}

/**
 * 脚本被改过后，旧基线里指向已消失元素的 override 会一直报「元素不存在」。
 * 它们既改不到东西也删不掉，只能整条清掉——只认 gid 失效这一种，
 * 「属性不支持」类警告是另一回事，不在这里处理。
 */
function OrphanOverrides({ panel, manifest }: { panel: PanelObject; manifest?: Manifest | null }) {
  if (!manifest) return null
  const orphans = panel.overrides.filter((o) => !manifest.elements.some((e) => e.gid === o.gid))
  if (!orphans.length) return null
  const gids = new Set(orphans.map((o) => o.gid))
  return (
    <div className="mt-1.5 flex items-center gap-2">
      <Button
        size="sm"
        variant="outline"
        onClick={() =>
          clearOverrides(
            panel.id,
            '清除失效修改',
            orphans.map((o) => ({ gid: o.gid, prop: o.prop })),
          )
        }
      >
        清除失效修改
      </Button>
      <span className="text-xs text-ink-3">
        {orphans.length} 条 · {gids.size} 个元素
      </span>
    </div>
  )
}

/**
 * 有些角色在 SVG 里没有自己的命中区：柱形系列只画出一根根柱子、刻度组根本
 * 不是图元，点画布永远选不到它们。属性页之间互相跳转是它们唯一的入口。
 */
function relatedGids(
  manifest: Manifest,
  el: ManifestElement,
): { gid: string; label: string; hint?: string }[] {
  const find = (gid: string) =>
    manifest.elements.find((e) => e.gid === gid && e.editable.length > 0)
  const up = (re: RegExp) => {
    const m = el.gid.match(re)
    return m ? find(m[1]) : undefined
  }
  const out: { gid: string; label: string; hint?: string }[] = []
  const push = (e: ManifestElement | undefined, hint?: string) => {
    if (e) out.push({ gid: e.gid, label: e.label, hint })
  }

  if (el.role === 'bar') push(up(/^(.*)\.bar_\d+$/), '所属系列')
  if (el.role === 'legend_text') push(up(/^(.*)\.texts_\d+$/), '所属图例')
  if (el.role === 'ticks') push(up(/^(.*)\.[xyz]ticks$/), '所属子图')
  if (el.role === 'axes' || el.role === 'axes3d') {
    push(find(`${el.gid}.xticks`))
    push(find(`${el.gid}.yticks`))
    push(find(`${el.gid}.zticks`))
  }
  return out
}

function RelatedRow({ manifest, element }: { manifest: Manifest; element: ManifestElement }) {
  const items = relatedGids(manifest, element)
  if (!items.length) return null
  return (
    <div className="mb-1.5 flex flex-wrap items-center gap-1">
      {items.map((it) => (
        <Button
          key={it.gid}
          size="sm"
          className="max-w-full px-1.5 text-ink-2"
          onClick={() => useUiStore.getState().setSelectedGid(it.gid)}
        >
          <CornerUpLeft size={11} className="shrink-0" />
          <span className="truncate">{it.hint ? `${it.hint}：${it.label}` : it.label}</span>
        </Button>
      ))}
    </div>
  )
}

function ErrorBlock({
  error,
  traceback,
  onRetry,
}: {
  error: string
  traceback: string
  onRetry?: () => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <Section>
      <div className="rounded-sm bg-danger-subtle px-2 py-1.5">
        <p className="text-xs text-danger">{error}</p>
        <div className="mt-0.5 flex items-center gap-2">
          <p className="text-xs text-danger/70">已保留上一版图像</p>
          {onRetry && (
            <button
              onClick={onRetry}
              className="flex items-center gap-1 text-xs text-danger underline-offset-2 hover:underline"
            >
              <RotateCcw size={11} />
              重试渲染
            </button>
          )}
        </div>
        {traceback && (
          <>
            <button
              onClick={() => setOpen((v) => !v)}
              className="mt-1 flex items-center gap-0.5 text-xs text-danger/80 hover:text-danger"
            >
              <ChevronRight size={11} className={cn('transition-transform', open && 'rotate-90')} />
              traceback
            </button>
            {open && (
              <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-sm bg-surface p-1.5 font-mono text-xs leading-relaxed text-ink-2">
                {traceback}
              </pre>
            )}
          </>
        )}
      </div>
    </Section>
  )
}

/**
 * 动态表单：无 group 的字段平铺在前，其余按 group 收进可折叠小节。
 * 分组和顺序都由 manifest 决定，前端不排字段清单。
 */
/** 标签列宽：容得下「网格透明度」这类 5 字标签，再长的自身截断 */
const LABEL_W = 72

/** 「移除 override」在不同字段上的自然说法 */
const RESET_HINT: Record<string, string> = {
  vmin: '自动范围',
  vmax: '自动范围',
  size_mm: '恢复脚本原始尺寸',
}

function FieldList({
  panel,
  element,
  warnings,
  openGroups,
  onToggleGroup,
}: {
  panel: PanelObject
  element: ManifestElement
  warnings: string[]
  openGroups: Record<string, boolean>
  onToggleGroup: (name: string) => void
}) {
  const flat = element.editable.filter((f) => !f.group)
  const groups = new Map<string, EditableField[]>()
  for (const f of element.editable) {
    if (!f.group) continue
    groups.set(f.group, [...(groups.get(f.group) ?? []), f])
  }
  // manifest 里 group 的出现次序取决于引擎实现，按注册表排才能跨角色版面一致
  const ordered = [...groups].sort((a, b) => groupRank(a[0]) - groupRank(b[0]))

  const rows = (fields: EditableField[]) => (
    <div className="flex flex-col gap-1.5">
      {fields.map((field) => {
        // 单条 patch 失败会进 warnings（如 log 轴遇非正数据），贴到出问题的字段下面。
        // 用词边界匹配：gid 里的 "texts_0" 不该被认成 text 字段的报错
        const propRe = new RegExp(`(^|[^A-Za-z_])${field.prop}([^A-Za-z_0-9]|$)`)
        const warning = warnings.find((w) => propRe.test(w))
        return (
          <div key={field.prop}>
            <FieldRow panel={panel} element={element} field={field} />
            {panel.overrides.some((o) => o.gid === element.gid && o.prop === field.prop) && (
              <button
                onClick={() => clearOverride(panel.id, element.gid, field.prop)}
                className="mt-0.5 pl-20 text-xs text-ink-3 hover:text-accent"
              >
                {RESET_HINT[field.prop] ?? '回到脚本值'}
              </button>
            )}
            {warning && (
              <p className="mt-0.5 pl-20 text-xs leading-relaxed text-danger">{warning}</p>
            )}
          </div>
        )
      })}
    </div>
  )

  return (
    <>
      {rows(flat)}
      {ordered.map(([name, fields]) => {
        // 组里已经有非默认值（如 Ra 标签自带的黑底）就默认展开——
        // 折叠着会让用户以为这些属性根本不能改
        const auto = groupHasContent(fields, (prop) =>
          panel.overrides.some((o) => o.gid === element.gid && o.prop === prop),
        )
        const open = openGroups[name] ?? auto
        return (
          <div key={name} className="mt-1.5 border-t border-border pt-1.5">
            <button
              onClick={() => onToggleGroup(name)}
              aria-expanded={open}
              className="flex w-full items-center gap-1 text-left text-xs text-ink-2 hover:text-ink"
            >
              <ChevronRight
                size={11}
                className={cn('shrink-0 transition-transform', open && 'rotate-90')}
              />
              {name}
            </button>
            {open && <div className="mt-1.5">{rows(fields)}</div>}
          </div>
        )
      })}
    </>
  )
}

/* -------------------------------------------------------------------------- */
/*  多选同类元素：批量改公共属性                                                */
/* -------------------------------------------------------------------------- */

/**
 * 逐个填才有意义的属性不进批量表单：文字内容、系列名称，以及位置尺寸
 * （几何走上面的对齐工具条，批量写同一个 bbox 会把元素叠在一起）。
 */
const BATCH_SKIP = new Set(['text', 'label', 'position', 'pos_frac', 'size_mm'])
const BATCH_TYPES = new Set(['number', 'color', 'bool', 'enum'])

/** 选中元素都有、且类型与选项一致的字段——以第一个元素的顺序和分组为准 */
function commonFields(els: ManifestElement[]): EditableField[] {
  const [first, ...rest] = els
  if (!first) return []
  return first.editable.filter((f) => {
    if (BATCH_SKIP.has(f.prop) || !BATCH_TYPES.has(f.type)) return false
    return rest.every((e) => {
      const g = e.editable.find((x) => x.prop === f.prop)
      return (
        !!g &&
        g.type === f.type &&
        JSON.stringify(g.options ?? null) === JSON.stringify(f.options ?? null)
      )
    })
  })
}

function BatchSection({ panel, elements }: { panel: PanelObject; elements: ManifestElement[] }) {
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({})
  const fields = commonFields(elements)
  const flat = fields.filter((f) => !f.group)
  const groups = new Map<string, EditableField[]>()
  for (const f of fields) {
    if (!f.group) continue
    groups.set(f.group, [...(groups.get(f.group) ?? []), f])
  }
  const ordered = [...groups].sort((a, b) => groupRank(a[0]) - groupRank(b[0]))

  const rows = (list: EditableField[]) => (
    <div className="flex flex-col gap-1.5">
      {list.map((f) => (
        <BatchFieldRow key={f.prop} panel={panel} elements={elements} field={f} />
      ))}
    </div>
  )

  return (
    <Section plainTitle title={`已选 ${elements.length} 个${roleName(elements[0].role)}`}>
      {!fields.length ? (
        <p className="text-xs text-ink-3">这些元素没有可一起改的属性</p>
      ) : (
        <>
          <p className="mb-1.5 text-xs text-ink-3">改动会同时写到选中的 {elements.length} 个元素</p>
          {rows(flat)}
          {ordered.map(([name, list]) => {
            const open = openGroups[name] ?? false
            return (
              <div key={name} className="mt-1.5 border-t border-border pt-1.5">
                <button
                  onClick={() => setOpenGroups((s) => ({ ...s, [name]: !s[name] }))}
                  aria-expanded={open}
                  className="flex w-full items-center gap-1 text-left text-xs text-ink-2 hover:text-ink"
                >
                  <ChevronRight
                    size={11}
                    className={cn('shrink-0 transition-transform', open && 'rotate-90')}
                  />
                  {name}
                </button>
                {open && <div className="mt-1.5">{rows(list)}</div>}
              </div>
            )
          })}
        </>
      )}
    </Section>
  )
}

function BatchFieldRow({
  panel,
  elements,
  field,
}: {
  panel: PanelObject
  elements: ManifestElement[]
  field: EditableField
}) {
  const values = elements.map((el) => {
    const own = el.editable.find((f) => f.prop === field.prop) ?? field
    return currentValue(panel, el.gid, own)
  })
  const first = values[0]
  const mixed = values.some((v) => JSON.stringify(v) !== JSON.stringify(first))
  const label = propLabel(field.prop, elements[0].role)
  const labelNode = (
    <span className="block truncate" title={label}>
      {label}
    </span>
  )
  const write = (v: unknown) =>
    setOverrides(
      panel.id,
      `批量修改${label}`,
      elements.map((el) => ({ gid: el.gid, prop: field.prop, value: v })),
    )
  const overridden = elements.filter((el) =>
    panel.overrides.some((o) => o.gid === el.gid && o.prop === field.prop),
  )

  const control = () => {
    switch (field.type) {
      case 'number':
        return (
          <NumberField
            value={mixed ? 0 : Number(first ?? 0)}
            mixed={mixed}
            min={field.min}
            max={field.max}
            step={field.step ?? 1}
            precision={2}
            suffix={field.unit}
            onChange={write}
          />
        )
      case 'color':
        return (
          <>
            <ColorField value={mixed ? '#000000' : String(first ?? '#000000')} onChange={write} />
            {mixed && <span className="shrink-0 text-xs text-ink-3">多个值</span>}
          </>
        )
      case 'bool':
        return (
          <>
            <Toggle checked={!mixed && !!first} onChange={write} />
            {mixed && <span className="shrink-0 text-xs text-ink-3">多个值</span>}
          </>
        )
      case 'enum':
        return (
          <Select
            value={mixed ? '' : String(first ?? '')}
            placeholder="多个值"
            onChange={write}
            options={(field.options ?? []).map((o) => ({
              value: o,
              label: optionLabel(field.prop, o),
            }))}
            ariaLabel={label}
          />
        )
      default:
        return null
    }
  }

  return (
    <div>
      <Row label={labelNode} labelWidth={LABEL_W}>
        {control()}
      </Row>
      {overridden.length > 0 && (
        <button
          onClick={() =>
            clearOverrides(
              panel.id,
              `恢复${label}`,
              overridden.map((el) => ({ gid: el.gid, prop: field.prop })),
            )
          }
          className="mt-0.5 pl-20 text-xs text-ink-3 hover:text-accent"
        >
          {overridden.length === elements.length
            ? '回到脚本值'
            : `回到脚本值（${overridden.length} 个改过）`}
        </button>
      )}
    </div>
  )
}

/** 当前值：优先取尚未渲染回来的 override，保证输入即时反馈 */
function currentValue(panel: PanelObject, gid: string, field: EditableField): unknown {
  const ov = panel.overrides.find((p) => p.gid === gid && p.prop === field.prop)
  return ov ? ov.value : field.value
}

function FieldRow({
  panel,
  element,
  field,
}: {
  panel: PanelObject
  element: ManifestElement
  field: EditableField
}) {
  const value = currentValue(panel, element.gid, field)
  const gidRef = useRef<string>('')
  const taRef = useRef<HTMLTextAreaElement | null>(null)
  const autoFocus = useCallback(
    (el: HTMLInputElement | HTMLTextAreaElement | null) => {
      // 只在切到新元素的那一次抢焦点，后续重渲染不打断用户
      if (!el || gidRef.current === element.gid) return
      gidRef.current = element.gid
      // 双击弹出的快捷编辑正开着：焦点属于弹层里的内容框，这里不抢
      if (useQuickEdit.getState().target) return
      el.focus()
      el.select()
    },
    [element.gid],
  )
  const label = propLabel(field.prop, element.role)
  // 标签列定宽 + 自身截断：中文标签长短不一，控件列不能被挤或被压
  const labelNode = (
    <span className="block truncate" title={label}>
      {label}
    </span>
  )
  const write = (v: unknown, immediate = false) =>
    setOverride(panel.id, element.gid, field.prop, v, immediate)

  const beginTxn = () => useDocumentStore.getState().beginTxn(`修改${label}`)
  const endTxn = () => useDocumentStore.getState().endTxn()


  switch (field.type) {
    case 'text': {
      const text = String(value ?? '')
      const insertNewline = () => {
        const ta = taRef.current
        if (!ta) return
        const s = ta.selectionStart ?? text.length
        const t = ta.selectionEnd ?? text.length
        write(text.slice(0, s) + '\n' + text.slice(t))
        requestAnimationFrame(() => {
          ta.focus()
          ta.setSelectionRange(s + 1, s + 1)
        })
      }
      /**
       * 上下标走 matplotlib mathtext（`cm$^{-1}$`）——图内文字是交给
       * matplotlib 排的，跟画布标注那套 `^{…}` 标记不是一回事。
       */
      const wrapMath = (kind: 'sup' | 'sub') => {
        const ta = taRef.current
        const s = ta?.selectionStart ?? text.length
        const t = ta?.selectionEnd ?? text.length
        const next = toggleMathScript(text, s, t, kind)
        write(next.text, true)
        requestAnimationFrame(() => {
          ta?.focus()
          ta?.setSelectionRange(next.start, next.end)
        })
      }
      const changeCase = (mode: CaseMode) =>
        write(transformCase(text, mode, true), true) // 保护 $…$ 里的公式
      return (
        <Row label={labelNode} labelWidth={LABEL_W}>
          {/* 输入框占满整行，四个动作横排在下方——竖排会把这一行拉得比输入框还高 */}
          <div className="flex w-full min-w-0 flex-col gap-1">
            <TextArea
              // 选中带文字的元素就直接可以打字，不用再点一次输入框
              ref={(el) => {
                taRef.current = el
                autoFocus(el)
              }}
              rows={Math.min(4, text.split('\n').length)}
              value={text}
              onFocus={beginTxn}
              onBlur={endTxn}
              onChange={(e) => write(e.target.value)}
              onKeyDown={(e) => {
                e.stopPropagation()
                if (e.key === 'Escape') {
                  e.currentTarget.blur()
                } else if (e.key === 'Enter') {
                  // 契约与画布文字一致：Enter 提交，⌥/⌘/Ctrl+Enter 换行
                  e.preventDefault()
                  if (e.altKey || e.metaKey || e.ctrlKey) insertNewline()
                  else e.currentTarget.blur()
                }
              }}
            />
            {/* 按下时一律不抢焦点：textarea 的编辑事务不因点按钮而提交 */}
            <div className="flex shrink-0 justify-end gap-0.5">
              <Button
                size="icon-sm"
                onPointerDown={(e) => e.preventDefault()}
                onClick={insertNewline}
                title={`在光标处插入换行（${combo(ALT, '⏎')} / ${modKey('⏎')}）`}
                aria-label="插入换行"
              >
                <CornerDownLeft size={12} />
              </Button>
              <Button
                size="icon-sm"
                onPointerDown={(e) => e.preventDefault()}
                onClick={() => wrapMath('sup')}
                title="上标：选中一段再点，写成 matplotlib 公式（cm$^{-1}$）"
                aria-label="上标"
              >
                <Superscript size={12} />
              </Button>
              <Button
                size="icon-sm"
                onPointerDown={(e) => e.preventDefault()}
                onClick={() => wrapMath('sub')}
                title="下标：选中一段再点，写成 matplotlib 公式（H$_{2}$O）"
                aria-label="下标"
              >
                <Subscript size={12} />
              </Button>
              <Menu
                align="end"
                width={140}
                trigger={
                  <Button
                    size="icon-sm"
                    onPointerDown={(e) => e.preventDefault()}
                    title="大小写转换"
                    aria-label="大小写转换"
                  >
                    <CaseSensitive size={12} />
                  </Button>
                }
              >
                <MenuItem onSelect={() => changeCase('upper')}>全部大写</MenuItem>
                <MenuItem onSelect={() => changeCase('lower')}>全部小写</MenuItem>
                <MenuItem onSelect={() => changeCase('title')}>每词首字母大写</MenuItem>
                <MenuItem onSelect={() => changeCase('sentence')}>每句首字母大写</MenuItem>
              </Menu>
            </div>
          </div>
        </Row>
      )
    }

    case 'number':
      return (
        <Row label={labelNode} labelWidth={LABEL_W}>
          <NumberField
            value={Number(value ?? 0)}
            min={field.min}
            max={field.max}
            step={field.step ?? 1}
            precision={2}
            suffix={field.unit}
            onChange={(v) => write(v)}
            onScrubStart={beginTxn}
            onScrubEnd={endTxn}
          />
        </Row>
      )

    case 'color':
      return (
        <Row label={labelNode} labelWidth={LABEL_W}>
          <ColorField value={String(value ?? '#000000')} onChange={(v) => write(v, true)} />
        </Row>
      )

    case 'bool':
      return (
        <Row label={labelNode} labelWidth={LABEL_W}>
          <Toggle checked={!!value} onChange={(v) => write(v, true)} />
        </Row>
      )

    case 'enum':
      return (
        <Row label={labelNode} labelWidth={LABEL_W}>
          <Select
            value={String(value ?? '')}
            onChange={(v) => write(v, true)}
            options={(field.options ?? []).map((o) => ({
              value: o,
              label: optionLabel(field.prop, o),
            }))}
            ariaLabel={label}
          />
        </Row>
      )

    case 'order': {
      // 图例条目顺序：value = 按显示顺序排的原始序号，options = 当前显示文字
      const perm = Array.isArray(value)
        ? (value as number[])
        : (field.options ?? []).map((_, i) => i)
      const labels = field.options ?? []
      const move = (i: number, delta: -1 | 1) => {
        const j = i + delta
        if (j < 0 || j >= perm.length) return
        const next = [...perm]
        ;[next[i], next[j]] = [next[j], next[i]]
        write(next, true)
      }
      return (
        <Row label={labelNode} labelWidth={LABEL_W}>
          <ul className="min-w-0 flex-1 rounded-sm border border-border">
            {perm.map((origIdx, i) => (
              <li
                key={`${origIdx}-${i}`}
                className={cn(
                  'flex h-6 items-center gap-1 px-1.5',
                  i > 0 && 'border-t border-border',
                )}
              >
                <span className="min-w-0 flex-1 truncate text-xs text-ink">
                  {labels[i] ?? `条目 ${origIdx + 1}`}
                </span>
                <Button
                  size="icon-sm"
                  className="h-5 w-5"
                  disabled={i === 0}
                  onClick={() => move(i, -1)}
                  aria-label="上移"
                >
                  <MoveUp size={11} />
                </Button>
                <Button
                  size="icon-sm"
                  className="h-5 w-5"
                  disabled={i === perm.length - 1}
                  onClick={() => move(i, 1)}
                  aria-label="下移"
                >
                  <MoveDown size={11} />
                </Button>
              </li>
            ))}
          </ul>
        </Row>
      )
    }

    case 'pair':
    case 'rect': {
      const arr = Array.isArray(value) ? (value as number[]) : []
      const step = field.type === 'rect' ? 0.01 : 1
      return (
        <Row label={labelNode} labelWidth={LABEL_W}>
          <div className={cn('grid flex-1 gap-1', field.type === 'rect' ? 'grid-cols-4' : 'grid-cols-2')}>
            {arr.map((v, i) => (
              <NumberField
                key={i}
                value={Number(v)}
                step={step}
                precision={field.type === 'rect' ? 3 : 2}
                onChange={(nv) => {
                  const next = [...arr]
                  next[i] = nv
                  write(next)
                }}
                onScrubStart={beginTxn}
                onScrubEnd={endTxn}
              />
            ))}
          </div>
          {field.unit && <span className="shrink-0 text-xs text-ink-3">{field.unit}</span>}
        </Row>
      )
    }

    default:
      return null
  }
}

/** 「修改逻辑」说明：讲清 override 与改脚本这两层的区别 */
function HowItWorks() {
  return (
    <Popover
      width={268}
      align="end"
      trigger={
        <Button variant="outline" size="sm" className="w-full text-ink-2" aria-label="修改逻辑说明">
          <CircleQuestionMark size={13} />
          修改保存在哪里？
        </Button>
      }
    >
      <div className="flex flex-col gap-2 text-xs leading-relaxed text-ink-2">
        <div>
          <p className="font-medium text-ink">图内修改（override）</p>
          <p className="mt-0.5">
            不动脚本文件。每次渲染时把你的改动叠加到脚本输出之上，存在布局文档里，
            进撤销栈，「重置图内修改」可一次清空。导出时会随面板一起发给引擎，
            按全质量重出矢量。
          </p>
        </div>
        <div className="h-px bg-border" />
        <div>
          <p className="font-medium text-ink">AI 改脚本</p>
          <p className="mt-0.5">
            AI 标签页里的修改会直接改写 .py 源文件，属于破坏性改动，
            但每次都有快照，可以「回滚此次修改」。
          </p>
        </div>
        <div className="h-px bg-border" />
        <div>
          <p className="font-medium text-ink">两者相遇时</p>
          <p className="mt-0.5">
            脚本变了之后，指向已消失元素的 override 会成为孤儿，
            渲染时以警告列出并自动忽略，其余修改照常生效。
          </p>
        </div>
      </div>
    </Popover>
  )
}

/* -------------------------------------------------------------------------- */
/*  子图布局                                                                   */
/* -------------------------------------------------------------------------- */

const ALIGN_BUTTONS: { mode: AlignMode; icon: typeof AlignStartVertical; tip: string; min: number }[] = [
  { mode: 'left', icon: AlignStartVertical, tip: '左对齐', min: 2 },
  { mode: 'hcenter', icon: AlignCenterVertical, tip: '水平居中', min: 2 },
  { mode: 'right', icon: AlignEndVertical, tip: '右对齐', min: 2 },
  { mode: 'top', icon: AlignStartHorizontal, tip: '顶对齐', min: 2 },
  { mode: 'vcenter', icon: AlignCenterHorizontal, tip: '垂直居中', min: 2 },
  { mode: 'bottom', icon: AlignEndHorizontal, tip: '底对齐', min: 2 },
  { mode: 'hdist', icon: AlignHorizontalDistributeCenter, tip: '水平等距（≥3）', min: 3 },
  { mode: 'vdist', icon: AlignVerticalDistributeCenter, tip: '垂直等距（≥3）', min: 3 },
  { mode: 'samew', icon: MoveHorizontal, tip: '等宽（以最后选中的为基准）', min: 2 },
  { mode: 'sameh', icon: MoveVertical, tip: '等高（以最后选中的为基准）', min: 2 },
]

/**
 * 缩放控件：组与单个子图共用。它是**相对**操作——应用完就回到 100%，
 * 所以旁边必须写清楚，否则「再次选中怎么又是 100%」会让人以为没生效。
 */
function ScaleField({ panel, group }: { panel: PanelObject; group: Group }) {
  const [pct, setPct] = useState(100)
  const label = group.entries.length > 1 ? `缩放 ${group.entries.length} 个子图` : '缩放子图'

  const apply = (v: number) => {
    if (v === 100) return
    setOverrides(panel.id, label, groupPatches(group, scaleGroupAbout(group.box, v / 100)))
    setPct(100)
  }

  return (
    <div className="mt-1.5">
      <Row label="缩放">
        <NumberField
          value={pct}
          min={10}
          max={400}
          step={5}
          suffix="%"
          title="绕中心缩放，组内相对布局不变"
          onChange={apply}
        />
      </Row>
      <p className="mt-1 text-xs leading-relaxed text-ink-3">
        相对当前大小；应用后回到 100%
      </p>
    </div>
  )
}

/**
 * 多选时的对齐工具条：几何全部在面板内容的 top-origin 分数框里算，
 * 子图落成 position、文字/图例落成新锚点，画布标注（shift 加选进来的）
 * 改画布位置——override 与位移进同一次 commit，一条撤销、一次渲染。
 */
function AlignSection({ panel, items }: { panel: PanelObject; items: MixedEntry[] }) {
  // 只有子图（含位图代理）能改尺寸，文字/图例/标注进选区就得禁掉等宽等高与成组缩放
  const allResizable = items.every((i) => i.resizable)
  const elementItems = items.filter((i): i is AlignEntry => !isAnnotationEntry(i))
  const hasAnnotations = elementItems.length !== items.length
  const group = hasAnnotations ? null : groupOf(elementItems)

  const apply = (mode: AlignMode, label: string) => {
    const boxes = layoutBoxes(items, mode)
    const full = panelFullRect(panel)
    const patches: { gid: string; prop: string; value: unknown }[] = []
    const moves: { id: string; x: number; y: number }[] = []
    for (const it of items) {
      const next = boxes.get(it.key)
      if (!next) continue
      if (isAnnotationEntry(it)) {
        moves.push({
          id: it.objectId,
          x: full.x + next[0] * full.w,
          y: full.y + next[1] * full.h,
        })
      } else {
        patches.push(it.write(next))
      }
    }
    applyMixedAlign(panel.id, label, patches, moves)
  }

  return (
    <Section plainTitle title={`已选 ${items.length} 个${hasAnnotations ? '对象' : '元素'}`}>
      <div className="grid grid-cols-6 gap-0.5">
        {ALIGN_BUTTONS.map(({ mode, icon: Icon, tip, min }) => {
          const sizeOnly = mode === 'samew' || mode === 'sameh'
          const disabled = items.length < min || (sizeOnly && !allResizable)
          return (
            <Tip key={mode} label={sizeOnly && !allResizable ? `${tip} · 仅限子图` : tip} side="left">
              <Button
                size="icon"
                className="w-full"
                disabled={disabled}
                onClick={() => apply(mode, tip.replace(/（.*/, ''))}
                aria-label={tip}
              >
                <Icon size={14} />
              </Button>
            </Tip>
          )
        })}
      </div>
      {group && <ScaleField panel={panel} group={group} />}
      <p className="mt-2 text-xs leading-relaxed text-ink-3">
        子图改的是它在整张图里的占比（matplotlib position），文字与图例改的是锚点位置，
        都不影响画布上的面板尺寸。
        {hasAnnotations && '画布标注跟着同一条基线排，位置改在画布对象上。'}
        {group && '拖画布上的组包围框手柄也能成组缩放。'}
      </p>
      <ul className="mt-2 flex flex-col gap-0.5">
        {items.map((it, i) => (
          <li key={it.key} className="flex items-center gap-1.5 text-xs text-ink-2">
            <span className="truncate">{it.label}</span>
            {i === items.length - 1 && (
              <span className="ml-auto shrink-0 font-mono text-xs text-accent/70">基准</span>
            )}
          </li>
        ))}
      </ul>
    </Section>
  )
}

/**
 * 单选子图：用 mm 直接给宽高，并可在整张图里居中。
 * element 是几何落点——点位图时这里给的已经是它的宿主子图。
 */
function AxesSizeMm({
  panel,
  element,
  sizeMm,
  proxied,
  group,
}: {
  panel: PanelObject
  element: ManifestElement
  sizeMm: [number, number]
  proxied: boolean
  group: Group | null
}) {
  const rect = positionOf(panel, element)
  if (!rect) return null
  const [figW, figH] = sizeMm

  const write = (next: Rect4, label: string) =>
    setOverrides(panel.id, label, [
      { gid: element.gid, prop: 'position', value: next.map(round4) },
    ])

  return (
    <div className="mt-2 border-t border-border pt-2">
      {proxied && (
        <p className="mb-1.5 text-xs leading-relaxed text-ink-3">
          位置与大小作用于宿主子图「{element.label}」。
        </p>
      )}
      <Grid2>
        <NumberField
          prefix="W"
          suffix="mm"
          value={fracToMm(rect[2], figW)}
          step={0.5}
          min={1}
          max={figW}
          onChange={(v) => write([rect[0], rect[1], mmToFrac(v, figW), rect[3]], '修改子图宽度')}
        />
        <NumberField
          prefix="H"
          suffix="mm"
          value={fracToMm(rect[3], figH)}
          step={0.5}
          min={1}
          max={figH}
          onChange={(v) =>
            // 高度以顶边为锚点变化，和等高对齐的行为保持一致
            write(
              [rect[0], rect[1] + rect[3] - mmToFrac(v, figH), rect[2], mmToFrac(v, figH)],
              '修改子图高度',
            )
          }
        />
      </Grid2>
      {group && <ScaleField panel={panel} group={group} />}
      <div className="mt-1.5 flex gap-1.5">
        <Button
          variant="outline"
          size="sm"
          className="flex-1"
          onClick={() => write(centerInFigure(rect, 'x'), '子图水平居中')}
        >
          <AlignCenterVertical size={13} />
          水平居中
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="flex-1"
          onClick={() => write(centerInFigure(rect, 'y'), '子图垂直居中')}
        >
          <AlignCenterHorizontal size={13} />
          垂直居中
        </Button>
      </div>
      <p className="mt-1.5 font-mono text-xs text-ink-3">
        整图 {figW}×{figH} mm
      </p>
    </div>
  )
}

/**
 * 高级层：改的是磁盘上的文件或跨图搬运，都是低频且不可轻率的动作，
 * 默认折叠，别和日常调样式的字段挤在一起。
 */
function AdvancedSection({ panel, gid }: { panel: PanelObject; gid?: string }) {
  const [open, setOpen] = useState(false)
  return (
    <Section>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1 text-left text-xs text-ink-2 hover:text-ink"
      >
        <ChevronRight size={11} className={cn('shrink-0 transition-transform', open && 'rotate-90')} />
        高级
      </button>
      {open && (
        <div className="mt-1.5 flex flex-col gap-1.5">
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            disabled={!panel.overrides.length}
            title="清空全部图内修改（含写回原始文件时继承来的基线），回到脚本的原始输出"
            onClick={() => resetOverrides(panel.id)}
          >
            <RotateCcw size={13} />
            重置到脚本原始{panel.overrides.length ? `（${panel.overrides.length}）` : ''}
          </Button>
          <SyncOverridesButton panel={panel} />
          <HowItWorks />
          {gid && (
            <p className="truncate font-mono text-xs text-ink-3" title={gid}>
              {gid}
            </p>
          )}
        </div>
      )}
    </Section>
  )
}

/**
 * 引擎明确做不到的能力：说清原因，并给一条真能做到的路（改图助手改脚本）。
 * 不画空白页，也不摆假的 disabled 控件。
 */
function UnsupportedNote({ role }: { role: string }) {
  const info = UNSUPPORTED[role]
  if (!info) return null
  return (
    <div className="mt-2 rounded-sm border border-border bg-surface-2 p-2">
      <p className="text-xs leading-relaxed text-ink-2">
        <b className="font-medium text-ink">{info.title}</b>：{info.reason}
      </p>
      <Button
        variant="outline"
        size="sm"
        className="mt-1.5 w-full"
        onClick={() => useUiStore.getState().setRightTab('assistant')}
      >
        用改图助手完成
      </Button>
    </div>
  )
}

/** 已隐藏元素的恢复入口；一个都没有时整块不显示 */
function HiddenElements({ panel, manifest }: { panel: PanelObject; manifest?: Manifest | null }) {
  const [open, setOpen] = useState(false)
  const hidden = (manifest?.elements ?? []).filter((el) =>
    panel.overrides.some((p) => p.gid === el.gid && p.prop === 'visible' && p.value === false),
  )
  if (!hidden.length) return null

  return (
    <Section>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1 text-left text-xs text-ink-2 hover:text-ink"
      >
        <ChevronRight size={11} className={cn('shrink-0 transition-transform', open && 'rotate-90')} />
        已隐藏元素（{hidden.length}）
      </button>
      {open && (
        <ul className="mt-1 flex flex-col gap-0.5">
          {hidden.map((el) => (
            <li key={el.gid} className="flex items-center gap-1.5">
              <span className="min-w-0 flex-1 truncate text-xs text-ink-3" title={el.gid}>
                {el.label}
              </span>
              <Button
                size="sm"
                className="shrink-0 text-ink-2"
                onClick={() => unhideElement(panel.id, el.gid)}
              >
                恢复
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}
