import { useCallback, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import {
  AlignCenterHorizontal,
  AlignCenterVertical,
  AlignEndHorizontal,
  AlignEndVertical,
  AlignHorizontalDistributeCenter,
  AlignStartHorizontal,
  AlignStartVertical,
  AlignVerticalDistributeCenter,
  ChevronRight,
  CircleQuestionMark,
  CornerUpLeft,
  MoveDown,
  MoveHorizontal,
  MoveUp,
  MoveVertical,
  RotateCcw,
  TriangleAlert,
} from 'lucide-react'
import type { AlignMode } from '@/lib/geometry'
import { formatMessage, msg, t as translate, type UiMessage } from '@/i18n'
import { ENVIRONMENT_CODES } from '@/lib/api'
import type { EditableField, Manifest, ManifestElement } from '@/lib/api'
import { requestRender } from '@/hooks/useEngineSync'
import { useQuickEdit } from '@/canvas/quickEditStore'
import { formatNumberList, parseNumberList } from '@/lib/numberList'
import { cn } from '@/lib/utils'
import {
  centerInFigure,
  fracToMm,
  mmToFrac,
  round4,
  scaleGroupAbout,
  type Rect4,
} from '@/lib/axesLayout'
import {
  alignEntries,
  annotationAlignEntries,
  GEOMETRY_WRITE_PROPS,
  geomTarget,
  groupOf,
  groupPatches,
  type Group,
  isAnnotationEntry,
  type MixedEntry,
  positionOf,
  type AlignEntry,
} from '@/lib/elementGeom'
import {
  clearOverride,
  clearOverrides,
  resetOverrides,
  setOverride,
  setOverrides,
  unhideElement,
} from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { previewStyle } from '@/store/svgPreviewStore'
import { canPreviewStyle } from '@/lib/svgStyle'
import { useSelectionStore } from '@/store/selectionStore'
import { useExactPanelManifest, usePanelRender } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import {
  EngineEnvironmentCard,
  MissingDependencyCard,
} from '@/components/EngineEnvironmentCard'
import type { PanelObject } from '@/types/document'
import {
  engineLabel,
  groupLabel,
  groupRank,
  optionLabel,
  propLabel,
  roleName,
  unsupportedOf,
} from './roles/registry'
import { Button } from '../ui/Button'
import { Disclosure, Grid2, Row, Section } from '../ui/Field'
import { ColorField, NumberField, TextArea, TextInput } from '../ui/Input'
import { Popover } from '../ui/Popover'
import { Select } from '../ui/Select'
import { Toggle } from '../ui/Toggle'
import { Tip } from '../ui/Tooltip'
import { useFieldGesture } from './elementWrite'
import { controlKindOf, presentFields } from './presentation/registry'
import type { PresentedField } from './presentation/types'
import { ArrowStylePicker } from './controls/ArrowPickers'
import { ColormapPicker } from './controls/ColormapPicker'
import { HatchPicker } from './controls/HatchPicker'
import { LegendPositionPicker } from './controls/LegendPositionPicker'
import { LineStylePicker } from './controls/LineStylePicker'
import { MarkerPicker } from './controls/MarkerPicker'
import {
  TICK_SPINE_PROPS,
  TickAndSpineDiagram,
  type TickSpineAdapter,
} from './controls/TickAndSpineDiagram'
import { TICK_CARD_PROPS, TickTaskCard } from './controls/TickTaskCard'
import { axisTickState, tickElementOf, tickHostOf, useTickAxisAdapter } from './tickAdapter'
import { useElementWriter } from './elementWrite'
import { TextStyleControls } from './controls/TextStyleControls'
import { useTextStyleAdapter } from './textStyleAdapter'
import { isTextLikeSelection, TEXT_STYLE_PROPS } from './textStyleModel'
import { fontStackOf } from './controls/fontStack'
import { alignSelectedPanelElements } from '@/store/alignAction'
import { useInspectorPrefs } from '@/store/inspectorPrefs'
import { TextActionRow } from './TextActions'
import { hasTextStyleBar, TextStyleBar, TEXT_BAR_PROPS } from './TextStyleBar'
import { HistoryPanel } from './HistoryPanel'
import { UnsupportedProps } from './UnsupportedProps'
import { UpdateSourceButton } from './UpdateSourceButton'
import { SyncOverridesButton } from './SyncOverridesButton'

/** 本文件的文案都在 inspector:element.* 下 */
const el = (key: string, values?: Record<string, unknown>) =>
  translate(`element.${key}`, { ns: 'inspector', ...(values ?? {}) })
const elMsg = (key: string, values?: Record<string, unknown>) =>
  msg(`element.${key}`, values, 'inspector')

/**
 * 图内元素编辑器。**能力**完全由 manifest.editable 决定；**版面**由展示注册表
 * 决定（presentation/registry）：primary 永远展开，「更多」是唯一的中频折叠区
 * （展开状态按角色持久化），「源文件与高级」收纳写回/历史/诊断与低频字段。
 */
export function ElementInspector({ panel }: { panel: PanelObject }) {
  useTranslation('inspector')
  const render = usePanelRender(panel)
  const selectedGids = useUiStore((s) => s.selectedGids)
  // 显示用：列元素、认 role、画角标。可能来自上一版（画布也正显示那一版）
  const manifest = render?.manifest
  /**
   * 几何权威：只有它能喂给对齐、成组缩放、孤儿判定这些**写几何**的地方。
   * null = 这一版还没画出来（改完字号的那 600ms、脚本刚变过），此时那些
   * 入口一律置灰并说明原因，绝不拿上一版的墨迹框硬算（issue #131）。
   */
  const exactManifest = useExactPanelManifest(panel)
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
  /**
   * 权威没就位时选区还在（selectedGids 不清空），但算不出条目。工具条仍要
   * 出现——凭空消失会让用户以为「多选对齐这个功能没了」——只是整排置灰并
   * 说明正在同步。条数按选中的可对齐元素数估，只用于标题文案。
   */
  const syncing =
    !exactManifest && (selected.length > 1 || (selected.length >= 1 && annEntries.length >= 1))
  /**
   * 单选一个**有几何可改**的元素（子图 / 位图代理）时，权威缺席同样不能放行：
   * `AxesSizeMm` 与 editable 里的 `position` 都会拿 manifest 的初值起算，
   * 那份要是上一版的，改尺寸/居中/填数就是把旧几何写成新的。
   * 这条与多选那条分开写：多选走对齐工具条，单选走普通表单，两边的收法不同。
   */
  const singleGeomSyncing = !exactManifest && !!picked?.resizable
  // 多选 → 出对齐工具条，替代单元素表单。位图会归并到宿主子图，
  // 归并后只剩一个几何目标时就没什么可对齐的，仍走单元素表单。
  // 画布标注加进来后与元素同框排版（元素写 override，标注改画布位置）。
  const entries =
    exactManifest && selected.length
      ? alignEntries(panel, exactManifest, selectedGids)
      : []
  const mixed: MixedEntry[] = [...entries, ...annEntries]
  const alignGroup = mixed.length > 1 ? mixed : null
  /**
   * 混排选区里有画布标注时，**两种批量样式都不给**。
   *
   * 两个批量写入器都只写 manifest override（`setOverrides`），标注是文档对象、
   * 走 `updateObjects`——混排时点一次加粗只会改到选中的一部分，而对齐区
   * 明明写着「已选 3 个元素」。这种「改了一半、还不说」正是 web/AGENTS.md
   * 混排对齐那条要求「同一次 commit」的理由（#142 评审 P2）。
   *
   * 跨 writer 的原子写入是延后项（见 docs/ux/UX_CONSISTENCY_PASS.md §8），
   * 在它做出来之前，**宁可不给这个入口**：对齐照旧可用，样式回到单选去改。
   *
   * 判据放在两处的共同上游：`batch`（同角色公共字段）与 `styleBatch`
   * （跨角色文字样式）是同一个形状的两个消费点，只修一个等于没修。
   */
  const mixedWithAnnotations = annotations.length > 0
  // 多选同一种角色 → 批量改公共属性（文字全部调字号、曲线全部换色…）
  const batch =
    !mixedWithAnnotations &&
    selected.length > 1 &&
    selected.every((e) => e.role === selected[0].role)
      ? selected
      : null
  /**
   * 跨角色的**文字样式**批量。与 `batch`（同角色公共字段）和 `alignGroup`
   * （几何对齐）是三个独立概念，可以同时成立：
   *
   *   * 图标题 + X/Y 轴标题 → styleBatch 有、batch 没有（角色不同）；
   *   * 两个轴标题          → 两个都有（样式行在上，其余公共字段在下）；
   *   * 两条曲线            → 只有 batch。
   *
   * 「已经进入对齐模式」**不再是**「不能改公共样式」的理由——旧代码里
   * alignGroup 一出现就把整个属性区换掉，多选三条文字后连字号都改不了。
   */
  const styleBatch =
    !mixedWithAnnotations && isTextLikeSelection(selected) ? selected : null

  // 展示分桶：文字工具条覆盖的属性、刻度任务卡吃掉的字段都让出来
  // （同一属性不出两套控件）。刻度组页上被卡承接的是方向 / 次刻度 / 长宽——
  // 主刻度模式、间距、格式、次刻度定位仍留在通用列表与「更多」里，
  // 逐字段「恢复到脚本」一条都没少（卡里的每一行自己带 ResetChip）。
  // 刻度组页只有在卡**真的接管了这个元素**时才让出字段。Z 刻度（3D）没有
  // 对应的卡，字段必须原样留在通用列表里——否则能力凭空消失（#142 评审 P1）
  const tickAxisOfSelf =
    element?.role === 'ticks' ? (tickHostOf(element.gid)?.axis ?? null) : null
  const tickCardCoversSelf = tickAxisOfSelf === 'x' || tickAxisOfSelf === 'y'
  const consumedBySideDiagram = new Set<string>(
    element?.role === 'axes'
      ? TICK_SPINE_PROPS
      : tickCardCoversSelf
        ? TICK_CARD_PROPS
        : [],
  )
  const buckets =
    element && element.editable.length
      ? presentFields(
          element.role,
          element.editable.filter(
            (f) =>
              (!hasTextStyleBar(element) || !TEXT_BAR_PROPS.has(f.prop)) &&
              !consumedBySideDiagram.has(f.prop) &&
              // 几何字段的初值来自 manifest：权威缺席时连控件都不给，否则
              // 用户看到的是上一版的数字，填一下就把旧几何写成这一版的
              (!!exactManifest || !GEOMETRY_WRITE_PROPS.has(f.prop)),
          ),
          {
            isOverridden: (prop) =>
              panel.overrides.some((o) => o.gid === element.gid && o.prop === prop),
            read: (prop) => {
              const f = element.editable.find((x) => x.prop === prop)
              return f ? currentValue(panel, element.gid, f) : undefined
            },
          },
        )
      : null

  // 四边状态图的宿主：axes 是自己；ticks 挂在宿主子图上（字段在那边）
  const sideHost =
    manifest && element
      ? element.role === 'axes'
        ? element
        : element.role === 'ticks'
          ? (() => {
              const m = element.gid.match(/^(.*)\.[xyz]ticks$/)
              return m ? manifest.elements.find((e) => e.gid === m[1]) : undefined
            })()
          : undefined
      : undefined

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
            onRetry={() => requestRender(panel, true)}
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
          <OrphanOverrides panel={panel} manifest={exactManifest} />
        </Section>
      )}

      {/* 三层顺序：公共文字样式 → 其余公共属性 → 对齐与排列。
          三者互相独立，谁在谁不在只看选择本身，不再互斥。
          `syncing`（#137：几何同步在途）与 `alignGroup` 同档——它只决定对齐区
          在不在、以及单元素表单让不让位，**不影响样式批量**：等一次几何写回
          落地的时候，用户照样该能改字号。 */}
      {styleBatch && <TextStyleBatchSection panel={panel} elements={styleBatch} />}
      {batch && <BatchSection panel={panel} elements={batch} skip={styleBatch ? TEXT_BAR_PROPS : undefined} />}
      {(alignGroup || syncing) && (
        <AlignSection panel={panel} items={alignGroup ?? []} syncing={syncing} />
      )}
      {alignGroup || syncing || batch || styleBatch ? null : (
      <Section>
        {manifest && element && <RelatedRow manifest={manifest} element={element} />}
        {!manifest ? (
          <p className="text-xs text-ink-3">
            {el(render?.status === 'rendering' ? 'building' : 'waiting')}
          </p>
        ) : !element?.editable.length || !buckets ? (
          <>
            <p className="text-xs text-ink-3">{el('clickToEdit')}</p>
            {/* 一条能改的都没有、但有说得出原因的不可改项时，原因仍然要出现
                ——否则这个元素在界面上就只剩一句「点一下开始编辑」 */}
            {element && <UnsupportedProps element={element} />}
          </>
        ) : (
          <FieldList
            panel={panel}
            element={element}
            warnings={render?.warnings ?? []}
            buckets={buckets}
            primaryExtra={
              sideHost && element ? (
                <TickControl
                  panel={panel}
                  manifest={manifest}
                  host={sideHost}
                  element={element}
                />
              ) : null
            }
          />
        )}
        {element && <UnsupportedNote role={element.role} />}
        {element?.role === 'image' && (
          <p className="mt-2 text-xs leading-relaxed text-ink-3">{el('imageHint')}</p>
        )}
        {/* 改尺寸 / 居中是几何写操作：只认权威那一份（issue #131） */}
        {element?.resizable && exactManifest && (
          <AxesSizeMm
            panel={panel}
            element={geomTarget(exactManifest, element)}
            sizeMm={exactManifest.size_mm}
            proxied={!!element.geom_gid}
            group={groupOf(alignEntries(panel, exactManifest, [element.gid]), 1)}
          />
        )}
        {singleGeomSyncing && (
          <p className="mt-2 text-xs leading-relaxed text-ink-3">{el('alignSyncing')}</p>
        )}
      </Section>
      )}

      <HiddenElements panel={panel} manifest={manifest} />

      <SourceAdvancedSection
        panel={panel}
        element={alignGroup || syncing || batch || styleBatch ? null : element}
        advanced={
          alignGroup || syncing || batch || styleBatch ? [] : (buckets?.advanced ?? [])
        }
      />
    </>
  )
}

/**
 * 脚本被改过后，旧基线里指向已消失元素的 override 会一直报「元素不存在」。
 * 它们既改不到东西也删不掉，只能整条清掉——只认 gid 失效这一种，
 * 「属性不支持」类警告是另一回事，不在这里处理。
 */
function OrphanOverrides({ panel, manifest }: { panel: PanelObject; manifest?: Manifest | null }) {
  useTranslation('inspector')
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
            elMsg('clearOrphans'),
            orphans.map((o) => ({ gid: o.gid, prop: o.prop })),
          )
        }
      >
        {el('clearOrphans')}
      </Button>
      <span className="text-xs text-ink-3">
        {el('orphanCount', { overrides: orphans.length, elements: gids.size })}
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
  target: ManifestElement,
): { gid: string; label: string; hint?: string }[] {
  const find = (gid: string) =>
    manifest.elements.find((e) => e.gid === gid && e.editable.length > 0)
  const up = (re: RegExp) => {
    const m = target.gid.match(re)
    return m ? find(m[1]) : undefined
  }
  const out: { gid: string; label: string; hint?: string }[] = []
  const push = (e: ManifestElement | undefined, hint?: string) => {
    // label 是引擎发来的散文（`曲线 “电流”`），过 engineLabel 换成当前语言
    if (e) out.push({ gid: e.gid, label: engineLabel(e.label), hint })
  }

  if (target.role === 'bar') push(up(/^(.*)\.bar_\d+$/), el('relatedSeries'))
  if (target.role === 'legend_text') push(up(/^(.*)\.texts_\d+$/), el('relatedLegend'))
  if (target.role === 'ticks') push(up(/^(.*)\.[xyz]ticks$/), el('relatedAxes'))
  if (target.role === 'axes' || target.role === 'axes3d') {
    push(find(`${target.gid}.xticks`))
    push(find(`${target.gid}.yticks`))
    push(find(`${target.gid}.zticks`))
  }
  return out
}

function RelatedRow({ manifest, element }: { manifest: Manifest; element: ManifestElement }) {
  useTranslation('inspector')
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
          <span className="truncate">
            {it.hint ? el('relatedWithHint', { hint: it.hint, label: it.label }) : it.label}
          </span>
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
  error: UiMessage
  traceback: string
  onRetry?: () => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <Section>
      <div className="rounded-sm bg-danger-subtle px-2 py-1.5">
        {/* 描述符在**显示这一刻**才翻，切语言后这条跟着换 */}
        <p className="text-xs text-danger">{formatMessage(error)}</p>
        <div className="mt-0.5 flex items-center gap-2">
          <p className="text-xs text-danger/70">{el('keptPrevious')}</p>
          {onRetry && (
            <button
              onClick={onRetry}
              className="flex items-center gap-1 text-xs text-danger underline-offset-2 hover:underline"
            >
              <RotateCcw size={11} />
              {el('retryRender')}
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
              {el('traceback')}
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

/**
 * 「移除 override」在不同字段上的自然说法；没有专属说法的用通用那条。
 * 注意查的是**固定的几个 prop**，不是开放集合，所以这里可以放心用 key。
 */
const RESET_HINT_PROPS = new Set(['vmin', 'vmax', 'size_mm'])
const resetHint = (prop: string) =>
  el(`resetHint.${RESET_HINT_PROPS.has(prop) ? prop : 'default'}`)

/** 字段行 + 贴在它下面的引擎警告（单条 patch 失败时） */
function FieldBlock({
  panel,
  element,
  field,
  warnings,
}: {
  panel: PanelObject
  element: ManifestElement
  field: EditableField
  warnings: string[]
}) {
  // 用词边界匹配：gid 里的 "texts_0" 不该被认成 text 字段的报错
  const propRe = new RegExp(`(^|[^A-Za-z_])${field.prop}([^A-Za-z_0-9]|$)`)
  const warning = warnings.find((w) => propRe.test(w))
  return (
    <div>
      <FieldRow panel={panel} element={element} field={field} />
      {warning && (
        <p className="mt-0.5 pl-20 text-xs leading-relaxed text-danger">{warning}</p>
      )}
    </div>
  )
}

/**
 * 单元素表单：primary 永远展开；「更多」是唯一的中频折叠区，展开状态按角色
 * 持久化（换面板不重置），折叠时标题右侧显示里面有几项被改过——
 * override 不因折叠而不可发现。
 */
function FieldList({
  panel,
  element,
  warnings,
  buckets,
  primaryExtra,
}: {
  panel: PanelObject
  element: ManifestElement
  warnings: string[]
  buckets: { primary: PresentedField[]; more: PresentedField[] }
  /** 首屏里的复合控件（四边状态图等），排在 primary 行之后、「更多」之前 */
  primaryExtra?: ReactNode
}) {
  // 文字元素的字号/加粗/字形/颜色/背景/描边/排版全部收进工具条，
  // 平铺列表要把它们让出来——同一个属性出两套控件是最坏的那种冗余
  const bar = hasTextStyleBar(element)
  const role = element.role
  const moreOpen = useInspectorPrefs((s) => s.moreOpen[role] ?? false)
  const setMoreOpen = useInspectorPrefs((s) => s.setMoreOpen)

  const rows = (fields: PresentedField[]) => (
    <div className="flex flex-col gap-1.5">
      {fields.map(({ field }) => (
        <FieldBlock
          key={field.prop}
          panel={panel}
          element={element}
          field={field}
          warnings={warnings}
        />
      ))}
    </div>
  )

  // 「更多」内部不再有第二层折叠；兜底进来的字段按引擎分组给一行小标题
  const named = buckets.more.filter((pf) => pf.order < 1000)
  const rest = buckets.more.filter((pf) => pf.order >= 1000)
  const restGroups: [string | undefined, PresentedField[]][] = []
  for (const pf of rest) {
    const last = restGroups.at(-1)
    if (last && last[0] === pf.field.group) last[1].push(pf)
    else restGroups.push([pf.field.group, [pf]])
  }

  const modifiedInMore = buckets.more.filter((pf) =>
    panel.overrides.some((o) => o.gid === element.gid && o.prop === pf.field.prop),
  ).length

  return (
    <>
      {rows(buckets.primary)}
      {bar && (
        <div className={cn(buckets.primary.length > 0 && 'mt-1.5')}>
          <TextStyleBar panel={panel} element={element} />
        </div>
      )}
      {primaryExtra && <div className="mt-2">{primaryExtra}</div>}
      {/* guard 挡掉的能力要说得出为什么——否则开关就是「消失了」（#76） */}
      <UnsupportedProps element={element} />
      {buckets.more.length > 0 && (
        <div className="mt-1.5 border-t border-border pt-1.5">
          <button
            onClick={() => setMoreOpen(role, !moreOpen)}
            aria-expanded={moreOpen}
            className="flex h-6 w-full items-center gap-1 rounded-sm text-left text-xs text-ink-2 outline-none hover:text-ink focus-visible:focus-ring"
          >
            <ChevronRight
              size={11}
              aria-hidden
              className={cn('shrink-0 transition-transform', moreOpen && 'rotate-90')}
            />
            <span className="font-medium">{el('more')}</span>
            {!moreOpen && modifiedInMore > 0 && (
              <span className="ml-auto shrink-0 text-xs text-ink-3">
                {el('modifiedCount', { count: modifiedInMore })}
              </span>
            )}
          </button>
          {moreOpen && (
            <div className="mt-1.5 flex flex-col gap-1.5">
              {rows(named)}
              {restGroups.map(([group, fields], i) => (
                <div key={group ?? `flat-${i}`}>
                  {group && (
                    <p className="mb-1 mt-1 text-xs uppercase tracking-[.06em] text-ink-3">
                      {groupLabel(group)}
                    </p>
                  )}
                  {rows(fields)}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  )
}

/**
 * 四边刻度/边框状态图的写入接线：axes 页与刻度组页共用。
 * 字段全部真实存在于宿主 axes 的 manifest 上；写入走 useElementWriter
 * （一次点击 = 一条历史 + 一次渲染），单项恢复走 clearOverride。
 */
/**
 * 刻度与边框的完整任务入口：**状态图 + X/Y 刻度配置在同一处**。
 *
 * 四边点按（刻度线 / 边框）与网格开关照旧写宿主子图的字段；方向 / 次刻度 /
 * 长度 / 宽度写对应轴的刻度元素（`axes_0.xticks` / `.yticks`）。两组字段
 * 分属两个 manifest 元素，界面把它们并到一张卡上——用户不需要理解
 * axes / xticks / yticks 的对象关系才能改一件事。
 *
 * 从子图页进来给两个轴（顶部出 X/Y 切换）；从刻度组页进来只给它自己那个轴
 * （切过去会写到另一个元素，而用户选的是这一个）——**同一个组件、同一套
 * 视觉语言，不是两套控件**。
 */
function TickControl({
  panel,
  manifest,
  host,
  element,
}: {
  panel: PanelObject
  manifest: Manifest | null | undefined
  /** 四边开关与网格的宿主（永远是子图） */
  host: ManifestElement
  /** 当前选中的元素：子图或某一个刻度组 */
  element: ManifestElement
}) {
  useTranslation('inspector')
  const w = useElementWriter(panel, host)
  const xEl = tickElementOf(manifest, host.gid, 'x')
  const yEl = tickElementOf(manifest, host.gid, 'y')
  // hook 数量固定：两个轴各调一次，元素不在时 adapter 回 null
  const xAdapter = useTickAxisAdapter(panel, xEl, 'x')
  const yAdapter = useTickAxisAdapter(panel, yEl, 'y')

  const selfAxis = element.role === 'ticks' ? (tickHostOf(element.gid)?.axis ?? null) : null
  const all = [xAdapter, yAdapter].filter((a): a is NonNullable<typeof a> => !!a)
  /**
   * 刻度组页只给**它自己那个轴**。
   *
   * 3D 图会发 `axes_i.zticks`（`tick_axes` 里 is3d 那一支），而这张卡只有
   * X / Y 两个适配器——选中 Z 刻度时**一个都不给**，绝不退回 `all`：
   * 那会摆出一组写到 `xticks` / `yticks` 的控件，用户改的是 Z、动的是 X，
   * 而 Z 自己的长度 / 宽度 / 次刻度还被 consumed 规则从通用列表里拿掉了
   * ——改错对象 + 真控件消失，两头都错（#142 评审 P1）。
   */
  const axes = selfAxis ? all.filter((a) => a.axis === selfAxis) : all

  const adapter: TickSpineAdapter = {
    has: (p) => w.has(p),
    read: (p) => w.read(p),
    toggle: (p, next) => w.writeOnce(p, next),
    labelOf: (p) => propLabel(p, host.role),
    isOverridden: (p) => panel.overrides.some((o) => o.gid === host.gid && o.prop === p),
    reset: (p) => clearOverride(panel.id, host.gid, p),
    axisState: (a) => axisTickState(a === 'x' ? xAdapter : yAdapter),
  }
  return (
    <div className="flex flex-col gap-2">
      <TickAndSpineDiagram adapter={adapter} />
      {axes.length > 0 && <TickTaskCard axes={axes} labelWidth={LABEL_W} />}
    </div>
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

/**
 * 跨角色的公共文字样式。控件与单选**完全相同**（`TextStyleControls`）：
 * 字体是带 Aa 预览的下拉、字号是数字框、B/I 是三态图标按钮、颜色是色块，
 * 不会因为选中了第二个对象就退化成 `常规 / 加粗` 的通用枚举列表。
 *
 * 只显示 manifest 交集里真有的属性；内容（`text`）刻意不给——批量改内容
 * 等于把三个标题写成同一句话。
 */
function TextStyleBatchSection({
  panel,
  elements,
}: {
  panel: PanelObject
  elements: ManifestElement[]
}) {
  const adapter = useTextStyleAdapter(panel, elements)
  const roles = [...new Set(elements.map((e) => e.role))]
  const hasAny = TEXT_STYLE_PROPS.some((p) => adapter.fieldOf(p))
  return (
    <Section plainTitle title={el('textBatchTitle', { count: elements.length })}>
      {!hasAny ? (
        <p className="text-xs text-ink-3">{el('batchNoCommon')}</p>
      ) : (
        <>
          <p className="mb-1.5 text-xs text-ink-3">
            {roles.length > 1
              ? el('textBatchHintMixed', { count: elements.length })
              : el('batchHint', { count: elements.length })}
          </p>
          <TextStyleControls adapter={adapter} labelWidth={LABEL_W} />
        </>
      )}
    </Section>
  )
}

function BatchSection({
  panel,
  elements,
  skip,
}: {
  panel: PanelObject
  elements: ManifestElement[]
  /** 已被上面的共享控件承接的属性——同一属性不出两套控件 */
  skip?: ReadonlySet<string>
}) {
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({})
  const fields = commonFields(elements).filter((f) => !skip?.has(f.prop))
  const flat = fields.filter((f) => !f.group)
  const groups = new Map<string, EditableField[]>()
  for (const f of fields) {
    if (!f.group) continue
    groups.set(f.group, [...(groups.get(f.group) ?? []), f])
  }
  const ordered = [...groups].sort((a, b) => groupRank(a[0]) - groupRank(b[0]))

  // 公共样式已由上面的 TextStyleBatchSection 承接、这里一条不剩时整段不画：
  // 紧挨着可用的样式控件再来一句「没有公共属性」是自相矛盾的
  if (skip && !fields.length) return null

  const rows = (list: EditableField[]) => (
    <div className="flex flex-col gap-1.5">
      {list.map((f) => (
        <BatchFieldRow key={f.prop} panel={panel} elements={elements} field={f} />
      ))}
    </div>
  )

  return (
    <Section
      plainTitle
      title={el('batchTitle', { count: elements.length, role: roleName(elements[0].role) })}
    >
      {!fields.length ? (
        <p className="text-xs text-ink-3">{el('batchNoCommon')}</p>
      ) : (
        <>
          <p className="mb-1.5 text-xs text-ink-3">
            {el('batchHint', { count: elements.length })}
          </p>
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
                  {groupLabel(name)}
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
  const gesture = useFieldGesture(panel, el('batchEdit', { label }))
  // 多选里每个成员各自判断能不能预览：同时选中曲线和刻度组时，曲线照样
  // 抢先显示，刻度组安静地等后端——**不能因为有一个不支持就整批放弃**
  const previewables = elements.filter((el) => canPreviewStyle(el.role, field.prop))

  const write = (v: unknown, immediate = false) => {
    if (previewables.length && !gesture.isOpen()) gesture.start()
    let previewed = previewables.length === elements.length
    for (const el of previewables) {
      if (!previewStyle(el.gid, el.role, field.prop, v)) previewed = false
    }
    // 只要有一个成员没预览成功就照旧走后端：宁可多渲染一次，
    // 也不能让画布上一部分元素显示新值、另一部分停在旧值
    setOverrides(
      panel.id,
      elMsg('batchEdit', { label }),
      elements.map((item) => ({ gid: item.gid, prop: field.prop, value: v })),
      previewed ? 'none' : immediate,
    )
    gesture.touch()
  }

  const writeOnce = (v: unknown) => {
    write(v, true)
    gesture.end()
  }
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
            onChange={(v) => write(v)}
            onScrubStart={gesture.start}
            onScrubEnd={gesture.end}
          />
        )
      case 'color':
        return (
          <>
            <ColorField
              value={mixed ? '#000000' : String(first ?? '#000000')}
              onChange={(v) => write(v, true)}
              onGestureEnd={gesture.end}
            />
            {mixed && <span className="shrink-0 text-xs text-ink-3">{el('mixedValues')}</span>}
          </>
        )
      case 'bool':
        return (
          <>
            <Toggle checked={!mixed && !!first} onChange={writeOnce} />
            {mixed && <span className="shrink-0 text-xs text-ink-3">{el('mixedValues')}</span>}
          </>
        )
      case 'enum': {
        // **视觉选择器不因为多选而退化**：线型仍是真实线段预览、marker 仍是
        // 图形网格、图例位置仍是 3×3 网格。同一个属性在单选与多选下是同一种
        // 视觉语言——这是本轮的核心纪律（docs/ux/UX_CONSISTENCY_PASS.md）。
        // 取值不一致时传 null：一个格子都不标选中，也不把「空」当自定义值。
        const v = mixed ? null : String(first ?? '')
        const opts = field.options ?? []
        const kind = controlKindOf(elements[0].role, field)
        const picker = () => {
          switch (kind) {
            case 'line-style':
              return <LineStylePicker value={v} options={opts} onChange={writeOnce} ariaLabel={label} />
            case 'marker':
              return <MarkerPicker value={v} options={opts} onChange={writeOnce} ariaLabel={label} />
            case 'hatch':
              return <HatchPicker value={v} options={opts} onChange={writeOnce} ariaLabel={label} />
            case 'colormap':
              return <ColormapPicker value={v} options={opts} onChange={writeOnce} ariaLabel={label} />
            case 'legend-position':
              return <LegendPositionPicker value={v} options={opts} onChange={writeOnce} ariaLabel={label} />
            case 'arrow-style':
              return <ArrowStylePicker value={v} options={opts} onChange={writeOnce} ariaLabel={label} />
            case 'font':
              return (
                <Select
                  className="min-w-0 flex-1"
                  value={mixed ? '' : String(first ?? '')}
                  placeholder={el('mixedValues')}
                  onChange={(x) => writeOnce(x)}
                  options={opts.map((o) => ({
                    value: o,
                    label: (
                      <span style={{ fontFamily: fontStackOf(o) }}>
                        {optionLabel('fontfamily', o)}
                      </span>
                    ),
                  }))}
                  ariaLabel={label}
                />
              )
            default:
              return (
                <Select
                  value={mixed ? '' : String(first ?? '')}
                  placeholder={el('mixedValues')}
                  onChange={(x) => writeOnce(x)}
                  options={opts.map((o) => ({ value: o, label: optionLabel(field.prop, o) }))}
                  ariaLabel={label}
                />
              )
          }
        }
        return (
          <>
            {picker()}
            {mixed && kind !== 'marker' && kind !== 'hatch' && kind !== 'colormap' && (
              <span className="shrink-0 text-xs text-ink-3">{el('mixedValues')}</span>
            )}
          </>
        )
      }
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
              elMsg('resetProp', { label }),
              overridden.map((item) => ({ gid: item.gid, prop: field.prop })),
            )
          }
          className="mt-0.5 pl-20 text-xs text-ink-3 hover:text-accent"
        >
          {overridden.length === elements.length
            ? el('backToScript')
            : el('backToScriptPartial', { count: overridden.length })}
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
      // 键盘用户正在元素树里漫游（焦点在树行上，漫游即选中）：抢过来会把
      // 方向键导航当场掐断——走到任何文字元素树就再也走不下去（issue #37）。
      // 鼠标从画布点选时焦点不在树里，仍保留「选中即可打字」的便利。
      const ae = document.activeElement
      if (ae instanceof HTMLElement && ae.closest('[role="tree"]')) return
      el.focus()
      el.select()
    },
    [element.gid],
  )
  const label = propLabel(field.prop, element.role)
  const overridden = panel.overrides.some(
    (o) => o.gid === element.gid && o.prop === field.prop,
  )
  // 标签列定宽 + 自身截断：中文标签长短不一，控件列不能被挤或被压。
  // 已修改的属性带一个状态点（形状而非仅颜色）+ sr-only 文案 + 行尾的恢复按钮，
  // 三重表达「这个值来自你的修改，不是脚本」。
  const labelNode = (
    <span
      className="flex min-w-0 items-center gap-1"
      title={overridden ? `${label} · ${el('modified')}` : label}
    >
      {overridden && (
        <span aria-hidden className="h-1 w-1 shrink-0 rounded-full bg-accent" />
      )}
      <span className="min-w-0 truncate">{label}</span>
      {overridden && <span className="sr-only">{el('modified')}</span>}
    </span>
  )
  const gesture = useFieldGesture(panel, el('editProp', { label }))
  const previewable = canPreviewStyle(element.role, field.prop)

  /**
   * 写一个值。
   *
   * 能局部预览的字段：先把新样子贴到 SVG 上（rAF 合并），**这一轮完全不发
   * 后端**（render:'none'）——文档改动照旧经过 documentStore.commit，历史
   * 一条不少，只是 matplotlib 等到这一轮结束才跑一次。scrub 没起手（直接
   * 敲数字回车）时就地开一轮，由安静计时收尾。
   *
   * 预览没生效（不在能力表里 / gid 在 SVG 里查不到 / 值类型不对）就原路走
   * 后端——immediate 参数照旧生效，行为与改动前一字不差。
   */
  const write = (v: unknown, immediate = false) => {
    if (previewable && !gesture.isOpen()) gesture.start()
    const previewed = previewable && previewStyle(element.gid, element.role, field.prop, v)
    setOverride(panel.id, element.gid, field.prop, v, previewed ? 'none' : immediate)
    gesture.touch()
  }

  /** 一次性的离散动作（开关）：写一次当场收尾，一条历史 + 一次权威渲染 */
  const writeOnce = (v: unknown) => {
    write(v, true)
    gesture.end()
  }

  const beginTxn = gesture.start
  // 结束事务 = 这一轮连续调整定稿：把挂起的那次立刻发出去，
  // 并保证最终那张不是拖动期的降质预览（见 flushRender）
  const endTxn = gesture.end

  /** 每种控件都套同一个壳：标签列 + 控件 + （已修改时）恢复到脚本 */
  const wrap = (children: ReactNode) => (
    <Row label={labelNode} labelWidth={LABEL_W}>
      {children}
      {overridden && (
        <Tip label={resetHint(field.prop)} side="left">
          <Button
            size="icon-sm"
            className="shrink-0 self-start"
            aria-label={el('resetProp', { label })}
            onClick={() => clearOverride(panel.id, element.gid, field.prop)}
          >
            <RotateCcw size={11} className="text-ink-3" />
          </Button>
        </Tip>
      )}
    </Row>
  )


  // enum 的视觉控件按展示注册表分派；剩下的按字段类型走
  const kind = controlKindOf(element.role, field)
  const enumValue = String(value ?? '')
  const enumOptions = field.options ?? []
  switch (kind) {
    case 'line-style':
      return wrap(
        <LineStylePicker
          value={enumValue}
          options={enumOptions}
          onChange={writeOnce}
          ariaLabel={label}
        />,
      )
    case 'marker':
      return wrap(
        <MarkerPicker
          value={enumValue}
          options={enumOptions}
          onChange={writeOnce}
          ariaLabel={label}
        />,
      )
    case 'hatch':
      return wrap(
        <HatchPicker
          value={enumValue}
          options={enumOptions}
          onChange={writeOnce}
          ariaLabel={label}
        />,
      )
    case 'colormap':
      return wrap(
        <ColormapPicker
          value={enumValue}
          options={enumOptions}
          onChange={writeOnce}
          ariaLabel={label}
        />,
      )
    case 'legend-position':
      return wrap(
        <LegendPositionPicker
          value={enumValue}
          options={enumOptions}
          onChange={writeOnce}
          ariaLabel={label}
        />,
      )
    case 'arrow-style':
      return wrap(
        <ArrowStylePicker
          value={enumValue}
          options={enumOptions}
          onChange={writeOnce}
          ariaLabel={label}
        />,
      )
    case 'font':
      return wrap(
        <Select
          value={enumValue}
          onChange={(v) => writeOnce(v)}
          options={enumOptions.map((o) => ({
            value: o,
            label: (
              <span style={{ fontFamily: fontStackOf(o) }}>{optionLabel('fontfamily', o)}</span>
            ),
          }))}
          ariaLabel={label}
        />,
      )
    default:
      break
  }

  switch (field.type) {
    case 'text': {
      const text = String(value ?? '')
      return wrap(
        <>
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
                  if (e.altKey || e.metaKey || e.ctrlKey) {
                    const ta = e.currentTarget
                    const a = ta.selectionStart ?? text.length
                    const b = ta.selectionEnd ?? text.length
                    write(text.slice(0, a) + '\n' + text.slice(b))
                    requestAnimationFrame(() => {
                      ta.focus()
                      ta.setSelectionRange(a + 1, a + 1)
                    })
                  } else e.currentTarget.blur()
                }
              }}
            />
            <TextActionRow
              text={text}
              taRef={taRef}
              onChange={(next, immediate) => write(next, immediate)}
            />
          </div>
        </>
      )
    }

    case 'number':
      return wrap(
        <>
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
        </>
      )

    case 'color':
      return wrap(
        <>
          {/* 取色是**连续**动作：系统取色盘拖着走会发一串 change。开一轮事务，
              每次变化只贴 SVG，blur 或安静一会儿才定稿——否则拖一次颜色就是
              十几条撤销 + 十几次 matplotlib 渲染 */}
          <ColorField
            value={String(value ?? '#000000')}
            onChange={(v) => write(v, true)}
            onGestureEnd={gesture.end}
          />
        </>
      )

    case 'bool':
      return wrap(
        <>
          <Toggle checked={!!value} onChange={writeOnce} />
        </>
      )

    case 'enum':
      return wrap(
        <>
          <Select
            value={String(value ?? '')}
            onChange={(v) => write(v, true)}
            options={(field.options ?? []).map((o) => ({
              value: o,
              label: optionLabel(field.prop, o),
            }))}
            ariaLabel={label}
          />
        </>
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
      return wrap(
        <>
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
                  {labels[i] ?? el('orderEntry', { index: origIdx + 1 })}
                </span>
                <Button
                  size="icon-sm"
                  className="h-5 w-5"
                  disabled={i === 0}
                  onClick={() => move(i, -1)}
                  aria-label={el('moveUp')}
                >
                  <MoveUp size={11} />
                </Button>
                <Button
                  size="icon-sm"
                  className="h-5 w-5"
                  disabled={i === perm.length - 1}
                  onClick={() => move(i, 1)}
                  aria-label={el('moveDown')}
                >
                  <MoveDown size={11} />
                </Button>
              </li>
            ))}
          </ul>
        </>
      )
    }

    case 'number_list': {
      // 一串数（固定刻度位置）。用一个文本框而不是 N 个数字框：刻度个数本来
      // 就是用户要改的东西，固定成 N 个格子等于不让他增删。分隔符逗号、空格、
      // 中文逗号都收——用户多半是从别处粘一串数进来的。
      const arr = Array.isArray(value) ? (value as number[]) : []
      return wrap(
        <>
          <div className="flex w-full min-w-0 flex-col gap-1">
            <TextInput
              defaultValue={formatNumberList(arr)}
              // 受控会在每敲一个字符时把 "1, " 重写成 "1"，逗号根本打不出来。
              // 因此按「失焦 / 回车提交」处理，key 跟着权威值走以便外部更新时重置
              key={arr.join(',')}
              placeholder={el('numberList.placeholder')}
              aria-label={label}
              onKeyDown={(e) => {
                e.stopPropagation()
                if (e.key === 'Enter') e.currentTarget.blur()
                else if (e.key === 'Escape') {
                  e.currentTarget.value = formatNumberList(arr)
                  e.currentTarget.blur()
                }
              }}
              onBlur={(e) => {
                const typed = parseNumberList(e.target.value)
                // **清空 = 把此刻这组刻度定格下来**，而不是提交一个「空」。
                //
                // 空列表的含义要到应用那一刻才由引擎解析成具体位置（脚本原样
                // 的那组），所以清空会让画面当场跳一下——而用户按下删除键时
                // 想的是「就保持现在这个样子」。定格成真数字则所见即所得，
                // 而且这组值实打实进了文档，重开、写回、换台机器都一样。
                // 想让刻度重新跟着脚本走是「自动」档的事，不是清空的事。
                const next = typed.length ? typed : arr
                if (next.length !== arr.length || next.some((v, i) => v !== arr[i])) {
                  write(next, true)
                  gesture.end()
                } else if (!typed.length) {
                  // 没写盘（值没变），得自己把定格下来的那组显示回去——
                  // 输入框的 key 跟着权威值走，值没变就不会重挂载
                  e.target.value = formatNumberList(next)
                }
              }}
            />
            <span className="text-xs text-ink-3">
              {arr.length
                ? el('numberList.count', { count: arr.length })
                : el('numberList.empty')}
            </span>
          </div>
        </>
      )
    }

    case 'pair':
    case 'rect': {
      const arr = Array.isArray(value) ? (value as number[]) : []
      const step = field.type === 'rect' ? 0.01 : 1
      return wrap(
        <>
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
        </>
      )
    }

    default:
      return null
  }
}

/** 「修改逻辑」说明：讲清 override 与改脚本这两层的区别 */
function HowItWorks() {
  useTranslation('inspector')
  return (
    <Popover
      width={268}
      align="end"
      trigger={
        <Button
          variant="outline"
          size="sm"
          className="w-full text-ink-2"
          aria-label={el('howItWorksAria')}
        >
          <CircleQuestionMark size={13} />
          {el('howItWorksTrigger')}
        </Button>
      }
    >
      <div className="flex flex-col gap-2 text-xs leading-relaxed text-ink-2">
        <div>
          <p className="font-medium text-ink">{el('howOverrideTitle')}</p>
          <p className="mt-0.5">{el('howOverrideBody')}</p>
        </div>
        <div className="h-px bg-border" />
        <div>
          <p className="font-medium text-ink">{el('howAiTitle')}</p>
          <p className="mt-0.5">{el('howAiBody')}</p>
        </div>
        <div className="h-px bg-border" />
        <div>
          <p className="font-medium text-ink">{el('howBothTitle')}</p>
          <p className="mt-0.5">{el('howBothBody')}</p>
        </div>
      </div>
    </Popover>
  )
}

/* -------------------------------------------------------------------------- */
/*  子图布局                                                                   */
/* -------------------------------------------------------------------------- */

/**
 * 对齐按钮。`tip` 是带条件说明的长提示，`history` 是落进撤销栈的短标签——
 * 以前是拿 tip 正则掐掉括号来当历史标签的，换了语言那条正则立刻失效。
 */
const ALIGN_BUTTONS: {
  mode: AlignMode
  icon: typeof AlignStartVertical
  /** 长提示的 key（在 inspector:element 下）；没有就用 alignMode 的短名 */
  tipKey?: string
  min: number
}[] = [
  { mode: 'left', icon: AlignStartVertical, min: 2 },
  { mode: 'hcenter', icon: AlignCenterVertical, min: 2 },
  { mode: 'right', icon: AlignEndVertical, min: 2 },
  { mode: 'top', icon: AlignStartHorizontal, min: 2 },
  { mode: 'vcenter', icon: AlignCenterHorizontal, min: 2 },
  { mode: 'bottom', icon: AlignEndHorizontal, min: 2 },
  { mode: 'hdist', icon: AlignHorizontalDistributeCenter, tipKey: 'alignHdist', min: 3 },
  { mode: 'vdist', icon: AlignVerticalDistributeCenter, tipKey: 'alignVdist', min: 3 },
  { mode: 'samew', icon: MoveHorizontal, tipKey: 'alignSameW', min: 2 },
  { mode: 'sameh', icon: MoveVertical, tipKey: 'alignSameH', min: 2 },
]

/**
 * 缩放控件：组与单个子图共用。它是**相对**操作——应用完就回到 100%，
 * 所以旁边必须写清楚，否则「再次选中怎么又是 100%」会让人以为没生效。
 */
function ScaleField({ panel, group }: { panel: PanelObject; group: Group }) {
  const [pct, setPct] = useState(100)
  const apply = (v: number) => {
    if (v === 100) return
    setOverrides(
      panel.id,
      group.entries.length === 1
        ? elMsg('scaleAxes')
        : elMsg('scaleAxesMulti', { count: group.entries.length }),
      groupPatches(group, scaleGroupAbout(group.box, v / 100)),
    )
    setPct(100)
  }

  return (
    <div className="mt-1.5">
      <Row label={el('scaleLabel')}>
        <NumberField
          value={pct}
          min={10}
          max={400}
          step={5}
          suffix="%"
          title={el('scaleTitle')}
          onChange={apply}
        />
      </Row>
      <p className="mt-1 text-xs leading-relaxed text-ink-3">{el('scaleHint')}</p>
    </div>
  )
}

/**
 * 位置类对齐的基准是**选区边界**（left 取 minL、right 取 maxR……），
 * 只有等宽 / 等高拿末位元素当参照。UI 上的「基准」角标必须跟着这条走
 * ——issue #131 之前它无条件挂在最后一项上，读起来就是「左对齐到最后选中
 * 的那个」，而算法根本不是那么做的。
 */
const REF_IS_LAST_SELECTED = (mode: AlignMode) => mode === 'samew' || mode === 'sameh'

/**
 * 多选时的对齐工具条：几何全部在面板内容的 top-origin 分数框里算，
 * 子图落成 position、文字/图例落成新锚点，画布标注（shift 加选进来的）
 * 改画布位置——override 与位移进同一次 commit，一条撤销、一次渲染。
 *
 * 按钮**只发意图**：真正的几何在 `alignSelectedPanelElements` 里、于点击那一刻
 * 从 store 现取（issue #131）。这里的 `items` 只用来决定禁用态与列表文案，
 * 绝不参与写入——React 上一轮 render 捕获的 bbox/anchor 闭包到点击时可能
 * 已经过期几百毫秒。
 */
function AlignSection({
  panel,
  items,
  syncing = false,
}: {
  panel: PanelObject
  items: MixedEntry[]
  /** 几何权威还没就位：整排置灰并说明原因，选区照旧留着 */
  syncing?: boolean
}) {
  // 只有子图（含位图代理）能改尺寸，文字/图例/标注进选区就得禁掉等宽等高与成组缩放
  const allResizable = items.length > 0 && items.every((i) => i.resizable)
  const elementItems = items.filter((i): i is AlignEntry => !isAnnotationEntry(i))
  const hasAnnotations = elementItems.length !== items.length
  const group = hasAnnotations || syncing ? null : groupOf(elementItems)
  const setStatus = useUiStore((s) => s.setStatus)

  const apply = (mode: AlignMode) => {
    const res = alignSelectedPanelElements(panel.id, mode)
    if (res.ok) return
    // 拒绝必须说得出原因：什么都不发生而界面一声不吭，用户只会再点几下
    if (res.reason === 'syncing') setStatus(elMsg('alignSyncing'))
    else if (res.reason === 'noop') setStatus(elMsg('alignNoop'))
    else if (res.reason === 'invalid') setStatus(elMsg('alignInvalid'), 'error')
  }

  return (
    <Section
      plainTitle
      title={el(hasAnnotations ? 'alignTitleObjects' : 'alignTitleElements', {
        count: items.length,
      })}
    >
      <div className="grid grid-cols-6 gap-0.5">
        {ALIGN_BUTTONS.map(({ mode, icon: Icon, tipKey, min }) => {
          const sizeOnly = mode === 'samew' || mode === 'sameh'
          const disabled = syncing || items.length < min || (sizeOnly && !allResizable)
          const base = tipKey
            ? el(tipKey)
            : translate(`alignMode.${mode}`, { ns: 'inspector' })
          // 提示里说清基准是「选区边界」还是「最后选中」——两者的结果差得很远
          const tip = syncing
            ? el('alignSyncingTip', { tip: base })
            : el(REF_IS_LAST_SELECTED(mode) ? 'alignRefLastTip' : 'alignRefBoundsTip', {
                tip: base,
              })
          return (
            <Tip
              key={mode}
              label={sizeOnly && !allResizable ? el('axesOnlySuffix', { tip }) : tip}
              side="left"
            >
              <Button
                size="icon"
                className="w-full"
                disabled={disabled}
                onClick={() => apply(mode)}
                aria-label={base}
              >
                <Icon size={14} />
              </Button>
            </Tip>
          )
        })}
      </div>
      {group && <ScaleField panel={panel} group={group} />}
      <p className="mt-2 text-xs leading-relaxed text-ink-3">
        {syncing ? (
          el('alignSyncing')
        ) : (
          <>
            {el('alignHint')}
            {hasAnnotations && el('alignHintAnnotations')}
            {group && el('alignHintGroup')}
          </>
        )}
      </p>
      <ul className="mt-2 flex flex-col gap-0.5">
        {items.map((it, i) => (
          <li key={it.key} className="flex items-center gap-1.5 text-xs text-ink-2">
            <span className="truncate">{engineLabel(it.label)}</span>
            {/*
              「基准」只在它**真的是基准**时出现：等宽/等高拿末位当参照，
              位置类对齐用的是选区边界，末位元素并不特殊。
            */}
            {i === items.length - 1 && allResizable && (
              <span className="ml-auto shrink-0 font-mono text-xs text-accent/70">
                {el('alignBaselineSize')}
              </span>
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

  const write = (next: Rect4, key: string) =>
    setOverrides(panel.id, elMsg(key), [
      { gid: element.gid, prop: 'position', value: next.map(round4) },
    ])

  return (
    <div className="mt-2 border-t border-border pt-2">
      {proxied && (
        <p className="mb-1.5 text-xs leading-relaxed text-ink-3">
          {el('proxiedGeometry', { label: engineLabel(element.label) })}
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
          onChange={(v) => write([rect[0], rect[1], mmToFrac(v, figW), rect[3]], 'setAxesWidth')}
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
              'setAxesHeight',
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
          onClick={() => write(centerInFigure(rect, 'x'), 'centerAxesH')}
        >
          <AlignCenterVertical size={13} />
          {el('centerH')}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="flex-1"
          onClick={() => write(centerInFigure(rect, 'y'), 'centerAxesV')}
        >
          <AlignCenterHorizontal size={13} />
          {el('centerV')}
        </Button>
      </div>
      <p className="mt-1.5 font-mono text-xs text-ink-3">
        {el('figureSize', { w: figW, h: figH })}
      </p>
    </div>
  )
}

/**
 * 第三层「源文件与高级」：一切触碰磁盘原始文件的动作（写回 / 历史）、
 * 恢复入口（当前元素 / 整个面板）、低频字段（层级、裸坐标）与 gid 诊断。
 * 默认折叠、会话内按角色记忆——高风险低频动作不和日常调样式挤在一起。
 */
function SourceAdvancedSection({
  panel,
  element,
  advanced,
}: {
  panel: PanelObject
  element?: ManifestElement | null
  advanced: PresentedField[]
}) {
  useTranslation('inspector')
  const role = element?.role ?? 'panel'
  const open = useInspectorPrefs((s) => s.advancedOpen[role] ?? false)
  const setOpen = useInspectorPrefs((s) => s.setAdvancedOpen)
  const gid = element?.gid
  const elementCount = gid
    ? panel.overrides.filter((o) => o.gid === gid).length
    : 0

  return (
    <Disclosure
      title={el('sourceAdvanced')}
      open={open}
      onToggle={() => setOpen(role, !open)}
      summary={panel.script?.split('/').pop()}
    >
      <div className="flex flex-col gap-1.5">
        {advanced.length > 0 && (
          <div className="flex flex-col gap-1.5 border-b border-border pb-2">
            {element &&
              advanced.map(({ field }) => (
                <FieldRow key={field.prop} panel={panel} element={element} field={field} />
              ))}
          </div>
        )}
        {gid && elementCount > 0 && (
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() =>
              clearOverrides(
                panel.id,
                elMsg('resetElement'),
                panel.overrides
                  .filter((o) => o.gid === gid)
                  .map((o) => ({ gid: o.gid, prop: o.prop })),
              )
            }
          >
            <RotateCcw size={13} />
            {el('resetElementCount', { count: elementCount })}
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          className="w-full"
          disabled={!panel.overrides.length}
          title={el('resetTitle')}
          onClick={() => resetOverrides(panel.id)}
        >
          <RotateCcw size={13} />
          {panel.overrides.length
            ? el('resetToScriptCount', { count: panel.overrides.length })
            : el('resetToScript')}
        </Button>
        {panel.script && (
          <div className="flex gap-1.5">
            <UpdateSourceButton panel={panel} />
            <HistoryPanel panel={panel} />
          </div>
        )}
        <SyncOverridesButton panel={panel} />
        <HowItWorks />
        {gid && (
          <p className="truncate font-mono text-xs text-ink-3" title={gid}>
            {gid}
          </p>
        )}
      </div>
    </Disclosure>
  )
}

/**
 * 引擎明确做不到的能力：说清原因，并给一条真能做到的路（改图助手改脚本）。
 * 不画空白页，也不摆假的 disabled 控件。
 */
function UnsupportedNote({ role }: { role: string }) {
  const info = unsupportedOf(role)
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
        {el('useAssistant')}
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
        {el('hiddenElements', { count: hidden.length })}
      </button>
      {open && (
        <ul className="mt-1 flex flex-col gap-0.5">
          {hidden.map((item) => (
            <li key={item.gid} className="flex items-center gap-1.5">
              <span className="min-w-0 flex-1 truncate text-xs text-ink-3" title={item.gid}>
                {engineLabel(item.label)}
              </span>
              <Button
                size="sm"
                className="shrink-0 text-ink-2"
                onClick={() => unhideElement(panel.id, item.gid)}
              >
                {el('restore')}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}
