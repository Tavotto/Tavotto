import { memo, useContext, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import type { Manifest, ManifestElement } from '@/lib/api'
import {
  elementsWith,
  FIGURE_LINE_ROWS,
  FIGURE_TEXT_ROWS,
} from '@/lib/stylePanelModel'
import { isSubLabel } from '@/lib/stylePresets'
import { CANVAS_TEXT_FAMILIES, type TypographyValue } from '@/lib/typography'
import { enterElementEdit } from '@/store/actions'
import { canvasScope, editBoundStyle, figureScope } from '@/store/styleBinding'
import { useDocumentStore } from '@/store/documentStore'
import { queueFontSize } from './queuedFontSize'
import {
  exactPanelManifest,
  useExactPanelManifest,
  usePanelDisplayManifest,
  usePanelRender,
  useRenderStore,
} from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject, TextObject } from '@/types/document'
import { useCanvasTypography } from '../inspector/typographyAdapter'
import { optionLabel } from '../inspector/roles/registry'
import { useTextStyleAdapter } from '../inspector/textStyleAdapter'
import { EmptyState } from '../ui/EmptyState'
import { Section } from '../ui/Field'
import { Select } from '../ui/Select'
import { CanvasStyleLayer } from './CanvasStyleLayer'
import { StyleCanvasActions } from './StyleCanvasActions'
import { useCurrentFigure } from './useProblemScope'
import {
  CanvasPending,
  CanvasPendingScope,
  ControlLine,
  FaceToggles,
  FamilySelect,
  PtField,
  ROW_LABEL,
  STYLE_CONTAINER,
  StyleRow,
  VALUE_W,
  WIDE_FAMILY_COL,
  faceValue,
  sp,
  usePendingWrites,
} from './styleRowParts'

/**
 * 左栏「样式」，**两层**（2026-11-20 维护者拍板，ADR 0081 §十四）：
 *
 * * **画布层**（没选中图 / 点了「改整张画布」）：这张画布跟随哪套样式、那套样式本身的值——改一格 =
 *   改样式、整张画布跟着变（`CanvasStyleLayer`）。「跟随样式」的选择器只在这一层顶上，全面板一个。
 * * **图层**（选中一张图）：这张图长什么样，随时看、随时改；顶上一行说清「只改这张图」，旁边「改整张画布」回画布层。
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
 * * 底部是画布级动作（`StyleCanvasActions`：对齐 / 按期刊调整 / 恢复原样 / 管理样式），两层共用。
 */
export function StylePanel() {
  // 各行挂着的「还没落定的写入」（`usePendingWrites`）按**这一行真正的归属**取范围，范围一变那组行
  // 重挂、从真实读数起算（#688；维度清单与理由在 `styleBinding.canvasScope` / `figureScope`）：
  // * 画布级（画布标注、画布层的所有行）：挂起值住在这里的 `CanvasPendingScope` 里，只随 `canvasScope`
  //   重挂——同一张画布上切选中的图不清它；
  // * 图级（图内文字、线条）：各行自己的状态，所在的 `FigureStyle` 按 `figureScope` 重挂。
  // 写入本身排在 `styleBinding` 的队列里、不随组件卸载，那一笔照旧按发起时的代次落定。
  const scope = useDocumentStore(canvasScope)
  return (
    <CanvasPendingScope key={scope}>
      <StylePanelBody />
    </CanvasPendingScope>
  )
}

function StylePanelBody() {
  useTranslation(['workspace', 'inspector'])
  const figure = useCurrentFigure()
  const panel = useDocumentStore((s) => {
    const o = figure.id ? s.doc.objects.find((x) => x.id === figure.id) : undefined
    return o?.type === 'panel' ? o : null
  })
  const manifest = usePanelDisplayManifest(panel)
  const exact = useExactPanelManifest(panel)
  const render = usePanelRender(panel)
  const rendering = render?.status === 'rendering'
  const figureKey = useDocumentStore((s) => (panel ? figureScope(s, panel) : ''))
  const bound = useDocumentStore((s) => !!s.doc.style && !s.doc.style.detached)
  // 「改整张画布」：回到画布层。记的是**那一刻的现场**（哪张图 + 选区），选区或图一变就回到图层，
  // 不另设「画布层开着」的持久状态
  const selection = useSelectionStore((s) => s.ids.join(','))
  const [dismissed, setDismissed] = useState<string | null>(null)
  const fieldKey = `${figure.id ?? ''}|${selection}`
  const canvasLayer = !panel || dismissed === fieldKey
  const toCanvas = () => {
    useSelectionStore.getState().clear()
    setDismissed(`${figure.id ?? ''}|`)
  }

  if (canvasLayer) {
    return (
      <div className="flex min-h-0 flex-1 flex-col" data-style-panel-canvas>
        <div data-style-scroll className={cn('min-h-0 flex-1 overflow-y-auto', STYLE_CONTAINER)}>
          <CanvasStyleLayer />
          <StyleCanvasActions />
        </div>
      </div>
    )
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col" data-style-panel>
      <p className="shrink-0 truncate px-3 text-sm text-ink" data-style-figure title={figure.name ?? ''}>
        {figure.name}
      </p>
      <p data-style-scope className="type-meta shrink-0 px-3 pb-1">
        {bound ? sp('scopeFigureBound') : sp('scopeFigure')}
        {' · '}
        <button
          type="button"
          data-style-to-canvas
          onClick={toCanvas}
          className="rounded-sm underline-offset-2 outline-none hover:text-ink-2 hover:underline focus-visible:focus-ring"
        >
          {sp('toCanvas')}
        </button>
      </p>
      {manifest && !exact && (
        <p data-style-sync-status={render?.status === 'error' ? 'error' : 'pending'} role="status"
          className="shrink-0 px-3 py-1 text-xs text-ink-2">
          {sp(render?.status === 'error' ? 'previewFailed' : 'previewPending')}
        </p>
      )}
      <div data-style-scroll className={cn('min-h-0 flex-1 overflow-y-auto', STYLE_CONTAINER)}>
        {manifest ? (
          <FigureStyle key={figureKey} panel={panel} manifest={exact ?? manifest} exact={!!exact} canQueueSize={render?.status !== 'error'} />
        ) : panel.script ? (
          /* 可编辑、但这一会话还没渲染过：值要引擎读出来。**不自动跑脚本**（「只带基线、
             还没动过」的面板不渲染，heavy 脚本要几分钟——`useEngineSync.renderTargets`），
             由用户点一下，与元素树的「加载元素清单」同一个动作；左栏留在这里 */
          <EmptyState
            art="pencil"
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
        <StyleCanvasActions />
      </div>
    </div>
  )
}

function FigureStyle({ panel, manifest, exact, canQueueSize }: { panel: PanelObject; manifest: Manifest; exact: boolean; canQueueSize: boolean }) {
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
            canQueueSize={canQueueSize}
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


interface FigureRowProps {
  /** 当前画布绑了样式：改一格 = 改这套样式本身（`editBoundStyle`），画布上所有图跟着对齐 */
  bound: boolean
  /**
   * 没绑样式时直接往这张图上写 override，gid 取自显示用的 manifest——那可能是**上一版**（脚本 /
   * override 刚变、这一版还在渲染）。拿过期的 gid 写 override 会写到已经不存在的元素上
   * （Codex #547 P1，与 styleBinding 只认 `exactPanelManifest` 同一条纪律），所以这一版没回来之前
   * 普通控件置灰；字号可排队，等精确清单到达后重新按角色取目标。绑着样式时写的是样式本身，不经这里的 gid。
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
  canQueueSize,
}: FigureRowProps & { id: string; sizeRole: string; familyRole: string; faceRole: string | null; canQueueSize: boolean }) {
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
  const pw = usePendingWrites()
  const ownerScope = figureScope(useDocumentStore.getState(), panel)
  if (!sizeEls.length && !familyEls.length) return null

  const label = ROW_LABEL[id]()
  const familyField = family.fieldOf('fontfamily')
  const sizeField = size.fieldOf('fontsize')
  const faceOf = (prop: 'weight' | 'style', a: typeof weight) => {
    const v = faceRole && a.fieldOf(prop) ? faceValue(a.valueOf(prop)) : null
    return v && pw.shown(prop, v)
  }
  const weightVal = faceOf('weight', weight)
  const styleVal = faceOf('style', style)
  const writeFace = (prop: 'weight' | 'style', v: string) =>
    pw.write(prop, v, () =>
      bound && faceRole
        ? editBoundStyle({ kind: 'element', role: faceRole, prop, value: v })
        : (prop === 'weight' ? weight : style).writeOnce(prop, v),
    )
  const writeSize = (v: number) => {
    if (bound) return editBoundStyle({ kind: 'element', role: sizeRole, prop: 'fontsize', value: v })
    const ds = useDocumentStore.getState()
    const live = ds.doc.objects.find((o) => o.id === panel.id)
    if (live?.type !== 'panel' || figureScope(ds, live) !== ownerScope) {
      useUiStore.getState().setStatus(msg('stylePanel.pendingSizeCancelled', undefined, 'workspace'), 'error')
      return
    }
    // 捕获阶段的别的动作可能刚改了文档，React 还没来得及把这一行重画成同步态。
    // 此时不能用闭包里的旧元素；连已经缓存好新清单的情形也重新按角色取目标。
    if (live.overrides !== panel.overrides || !exactPanelManifest(useRenderStore.getState(), live)) {
      return queueFontSize(live, sizeRole, v)
    }
    size.writeOnce('fontsize', v)
  }

  return (
    <StyleRow id={id}>
      {familyField && (
        <ControlLine line={`${id}.family`}>
          <div data-style-cell={`${id}.family`} className={cn("flex min-w-0 flex-1", WIDE_FAMILY_COL)}>
            <FamilySelect
              label={sp('familyOf', { row: label })}
              value={pw.shown('fontfamily', family.valueOf('fontfamily'))}
              options={familyField.options ?? []}
              labels={manifest.font_family_names}
              locked={locked}
              onChange={(v) =>
                pw.write('fontfamily', v, () =>
                  bound
                    ? editBoundStyle({ kind: 'element', role: familyRole, prop: 'fontfamily', value: v })
                    : family.writeOnce('fontfamily', v),
                )
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
                value={pw.shown('fontsize', size.valueOf('fontsize'))}
                field={sizeField}
                step={0.5}
                locked={locked && !canQueueSize}
                onChange={(v) => pw.write('fontsize', v, () => writeSize(v))}
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
  const pw = usePendingWrites()
  const field = adapter.fieldOf(prop)
  if (!els.length || !field) return null

  const label = ROW_LABEL[id]()
  const value = pw.shown(prop, adapter.valueOf(prop))
  const write = (v: unknown) =>
    pw.write(prop, v, () =>
      bound ? editBoundStyle({ kind: 'element', role, prop, value: v }) : adapter.writeOnce(prop, v),
    )

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
  // 画布级：挂起值在 `CanvasPendingScope` 里，切选中的图不清（#688）
  const pw = useContext(CanvasPending)!
  const label = ROW_LABEL.annotation()
  const face = (prop: 'weight' | 'style'): TypographyValue | null =>
    adapter.fieldOf(prop) ? pw.shown(prop, adapter.valueOf(prop)) : null
  return (
    <StyleRow id="annotation">
      <ControlLine line="annotation.family">
        <div data-style-cell="annotation.family" className={cn("flex min-w-0 flex-1", WIDE_FAMILY_COL)}>
          <FamilySelect
            label={sp('familyOf', { row: label })}
            value={pw.shown('fontFamily', adapter.valueOf('fontFamily'))}
            options={CANVAS_TEXT_FAMILIES}
            onChange={(v) =>
              pw.write('fontFamily', v, () =>
                bound
                  ? editBoundStyle({ kind: 'annotation', prop: 'fontFamily', value: v })
                  : adapter.writeOnce('fontFamily', v),
              )
            }
          />
        </div>
      </ControlLine>
      <ControlLine line="annotation.size">
        <div data-style-cell="annotation.size" className="contents">
          <PtField
            label={sp('sizeOf', { row: label })}
            value={pw.shown('sizePt', adapter.valueOf('sizePt'))}
            step={0.5}
            onChange={(v) =>
              pw.write('sizePt', v, () =>
                bound
                  ? editBoundStyle({ kind: 'annotation', prop: 'sizePt', value: v })
                  : adapter.writeOnce('sizePt', v),
              )
            }
          />
        </div>
        <FaceToggles
          row="annotation"
          weight={face('weight')}
          style={face('style')}
          onWeight={(v) =>
            pw.write('weight', v, () =>
              bound
                ? editBoundStyle({ kind: 'annotation', prop: 'bold', value: v === 'bold' })
                : adapter.writeOnce('weight', v),
            )
          }
          onStyle={(v) =>
            pw.write('style', v, () =>
              bound
                ? editBoundStyle({ kind: 'annotation', prop: 'italic', value: v === 'italic' })
                : adapter.writeOnce('style', v),
            )
          }
        />
      </ControlLine>
    </StyleRow>
  )
}
