import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import type { Manifest, ManifestElement } from '@/lib/api'
import { ALIGN_BUTTONS } from '@/components/inspector/arrangeButtons'
import { isTextLikeSelection } from '@/components/inspector/textStyleModel'
import { FIGURE_TEXT_BATCH_PROPS } from '@/components/inspector/typographyAdapter'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Button } from '@/components/ui/Button'
import { Tip } from '@/components/ui/Tooltip'
import { alignEntries, annotationAlignEntries } from '@/lib/elementGeom'
import type { AlignMode } from '@/lib/geometry'
import { alignSelectedPanelElements } from '@/store/alignAction'
import { useDocumentStore } from '@/store/documentStore'
import { useExactPanelManifest, usePanelDisplayManifest } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import type {
  ArrowObject,
  CanvasObject,
  PanelObject,
  ShapeObject,
  TextObject,
} from '@/types/document'
import { FigureTextQuick } from './ElementBar'
import { OpenInspectorButton, Sep } from './shared'
import { qb } from './text'

const el = (key: string, values?: Record<string, unknown>) =>
  translate(`element.${key}`, { ns: 'inspector', ...(values ?? {}) })

/**
 * 图内多选（两个及以上图内元素）的浮动工具条（ADR 0089）。
 *
 * 它**不是第二套对齐或样式系统**，两件事各走属性页那一条路：
 *
 *   * 对齐按钮只发意图，落地是 `alignSelectedPanelElements`——与 ElementInspector 的
 *     对齐区同一个函数、于点击那一刻从 store 现取几何权威（issue #131）；权威没就位时
 *     整排置灰、不猜；
 *   * 字号 / 加粗 / 斜体 / 颜色（完整档还有字体）只在选区是同一文字家族时给
 *     （`isTextLikeSelection`，与属性页的 `styleBatch` 同一条判据），数据与写入是
 *     `useFigureTypography` 的批量适配器——一次改动 = 一次 `setOverrides` = 一条历史。
 *
 * 选区里有 Shift 加选进来的画布标注时样式整组不给（标注走 `updateObjects`、元素走
 * override，跨 writer 的原子写入是延后项，理由与 ElementInspector 的
 * `mixedWithAnnotations` 同一条），对齐照旧可用。
 */
export function ElementMultiBar({
  panel,
  gids,
  compact,
}: {
  panel: PanelObject
  gids: string[]
  /** 停靠的属性页正铺着同一批文字控件：字体下拉与取色器让给右栏（与单选同一条判据） */
  compact: boolean
}) {
  useTranslation('workspace')
  useTranslation('inspector')
  const manifest = usePanelDisplayManifest(panel)
  const exact = useExactPanelManifest(panel)
  const selIds = useSelectionStore((s) => s.ids)
  const objects = useDocumentStore((s) => s.doc.objects)
  const setStatus = useUiStore((s) => s.setStatus)
  const annotations = annotationsIn(objects, selIds)

  const gidsKey = gids.join('\n')
  // 适配器要稳定的数组引用：gids 按内容比（选区 store 每次 set 都是新数组）
  const selected = useMemo(
    () => selectedElements(manifest, gids),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [manifest, gidsKey],
  )
  const plan = elementMultiPlan(panel, selected, exact, gids, annotations)

  const apply = (mode: AlignMode) => {
    const res = alignSelectedPanelElements(panel.id, mode)
    if (res.ok) return
    // 拒绝必须说得出原因（与属性页对齐区同一套文案）
    if (res.reason === 'syncing') setStatus(msg('element.alignSyncing', undefined, 'inspector'))
    else if (res.reason === 'noop') setStatus(msg('element.alignNoop', undefined, 'inspector'))
    else if (res.reason === 'invalid')
      setStatus(msg('element.alignInvalid', undefined, 'inspector'), 'error')
  }

  const count = gids.length + annotations.length
  return (
    <span className="flex items-center gap-1" data-element-multi-bar>
      <span data-selection-count={count} className="whitespace-nowrap px-1 text-ink-2">
        {qb('selectedCount', { count })}
      </span>
      <Sep />
      {plan.align && (
        <>
          <span className="flex items-center gap-0.5">
            {ALIGN_BUTTONS.map(({ mode, icon: Icon }) => {
              const base = translate(`alignMode.${mode}`, { ns: 'inspector' })
              const tip = plan.syncing
                ? el('alignSyncingTip', { tip: base })
                : el('alignRefBoundsTip', { tip: base })
              return (
                <Tip key={mode} label={tip} side="bottom">
                  <Button
                    size="icon-sm"
                    data-align-mode={mode}
                    aria-label={base}
                    disabled={plan.syncing}
                    onClick={() => apply(mode)}
                  >
                    <Icon size={ICON_SIZE.sm} />
                  </Button>
                </Tip>
              )
            })}
          </span>
          <Sep />
        </>
      )}
      {plan.style && (
        <FigureTextQuick
          panel={panel}
          elements={selected}
          props={FIGURE_TEXT_BATCH_PROPS}
          compact={compact}
        />
      )}
      <OpenInspectorButton />
    </span>
  )
}

type Annotation = ReturnType<typeof annotationsIn>[number]

/** 图内编辑态里 Shift 加选进来的画布标注（与 ElementInspector 同一个筛法） */
export function annotationsIn(objects: readonly CanvasObject[], selIds: readonly string[]) {
  return objects.filter(
    (o): o is TextObject | ArrowObject | ShapeObject =>
      selIds.includes(o.id) && (o.type === 'text' || o.type === 'arrow' || o.type === 'shape'),
  )
}

export function selectedElements(manifest: Manifest | null | undefined, gids: readonly string[]) {
  if (!manifest) return []
  return gids
    .map((g) => manifest.elements.find((e) => e.gid === g))
    .filter((e): e is ManifestElement => !!e)
}

/**
 * 图内多选栏给什么——`ContextBar`（出不出现）与本组件（画什么）读同一份，
 * 不许两边各判一遍：
 *
 *   * `align`：选区里能参与对齐的目标（`alignEntries` + 标注）≥ 2；权威没就位时
 *     （`syncing`）照旧出现、整排置灰——凭空消失会让人以为功能没了（与属性页同一条）。
 *     图例项、刻度这类位置由容器决定的元素不可对齐，只选它们时对齐行不出；
 *   * `style`：同一文字家族且没有混进画布标注（`isTextLikeSelection`，属性页 `styleBatch`
 *     的同一条判据）。
 *
 * 两样都没有 → 浮动栏不出现（一个孤零零的「全部属性」不值得盖住画布）。
 */
export function elementMultiPlan(
  panel: PanelObject,
  selected: ManifestElement[],
  exact: Manifest | null | undefined,
  gids: string[],
  annotations: Annotation[],
): { align: boolean; syncing: boolean; style: boolean } {
  const syncing = !exact && selected.length + annotations.length > 1
  const entries = exact
    ? alignEntries(panel, exact, gids).length + annotationAlignEntries(panel, annotations).length
    : 0
  return {
    align: syncing || entries >= 2,
    syncing,
    style: annotations.length === 0 && isTextLikeSelection(selected),
  }
}
