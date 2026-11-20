import { memo, useContext, useEffect, useMemo, useState } from 'react'
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
import { canvasScope, figureScope, figureStyleDiff, followCanvasStyle } from '@/store/styleBinding'
import { useDocumentStore } from '@/store/documentStore'
import { useProfileStore } from '@/store/profileStore'
import { queueFontSize } from './queuedFontSize'
import {
  exactPanelManifest,
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
import { EmptyState } from '../ui/EmptyState'
import { Section } from '../ui/Field'
import { Segmented } from '../ui/Segmented'
import { Select } from '../ui/Select'
import { CanvasStyleLayer } from './CanvasStyleLayer'
import { FigureStrip } from './FigureStrip'
import { StyleCanvasActions } from './StyleCanvasActions'
import { useStyleScopeStore, type StyleScope } from './styleScope'
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
 * 左栏「样式」，顶上一个「画布 | 单图」开关（2026-11-20 维护者二次拍板，ADR 0081 §十四）：
 *
 * * **画布**：这张画布跟随哪套样式、那套样式本身的值——改一格 = 改样式、整张画布跟着变（`CanvasStyleLayer`）。
 *   「跟随样式」的选择器只在这一档顶上，全面板一个。
 * * **单图**：顶上一排小方块（每张图一个，`FigureStrip`），下面是选中那张图自己的值。**改一格只改这张图**，
 *   不论画布有没有跟随样式：写的是这张图自己的 override，与属性页手改同一条路（登记里注销，从此归用户，
 *   样式再改也不覆盖）。与画布样式不一样的行末有一颗「跟回画布」。
 *
 * 开关是**粘性**的（`styleScope`）：画布上换选中的图不翻它；面板第一次打开、从没选过时，有选中图 = 单图，否则 = 画布。
 *
 * 与「问题」并列、各管一半：这里管「长什么样」，问题面板管「哪里不合规」。**这里不显示任何
 * 问题记号**（用户 2026-09-26：行尾的八角 / 三角去掉，问题只在左侧图标栏的「问题」面板里看，
 * 那颗图标带计数角标）——字号被阻断这类情况这里也不提示，不判规范、也不认回问题清单。
 *
 * * **当前图**与问题面板同一个判据（`useCurrentFigure`），不写第二份。
 * * **数字是页面上读者量到的 pt**：换算在写入器里（`useTextStyleAdapter`），属性页用的是同一个。
 * * **改动走 Inspector 那条路**：图内元素经 `useTextStyleAdapter`（一次点击 = 一次
 *   `setOverrides` commit），画布标注经 `useCanvasTypography`。
 * * **值四档不压扁**：几个元素不一致时是「多个值」，不拿第一个冒充全部。
 * * 底部是画布级动作（`StyleCanvasActions`：对齐 / 按期刊调整 / 恢复原样 / 管理样式），两档共用。
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
  // 粘性开关：只由用户点；从没选过时按面板打开那一刻有没有选中的图定起点
  const chosen = useStyleScopeStore((s) => s.scope)
  const [initial] = useState<StyleScope>(() => (panel ? 'figure' : 'canvas'))
  const scope = chosen ?? initial
  useEffect(() => {
    if (useStyleScopeStore.getState().scope === null) useStyleScopeStore.getState().setScope(initial)
  }, [initial])

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-style-mode={scope}>
      <div className="shrink-0 px-3 pb-1.5 pt-2" data-style-scope-switch>
        <Segmented<StyleScope>
          ariaLabel={sp('modeLabel')}
          value={scope}
          onChange={(v) => useStyleScopeStore.getState().setScope(v)}
          items={[
            { value: 'canvas', label: sp('modeCanvas'), title: sp('modeCanvasTip') },
            { value: 'figure', label: sp('modeFigure'), title: sp('modeFigureTip') },
          ]}
        />
      </div>
      {scope === 'canvas' ? (
        <div className="flex min-h-0 flex-1 flex-col" data-style-panel-canvas>
          <div data-style-scroll className={cn('min-h-0 flex-1 overflow-y-auto', STYLE_CONTAINER)}>
            <CanvasStyleLayer />
            <StyleCanvasActions />
          </div>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col" data-style-panel>
          <FigureStrip currentId={panel?.id ?? null} />
          {manifest && !exact && (
            <p data-style-sync-status={render?.status === 'error' ? 'error' : 'pending'} role="status"
              className="shrink-0 px-3 py-1 text-xs text-ink-2">
              {sp(render?.status === 'error' ? 'previewFailed' : 'previewPending')}
            </p>
          )}
          <div data-style-scroll className={cn('min-h-0 flex-1 overflow-y-auto', STYLE_CONTAINER)}>
            {!panel ? (
              <p data-style-pick className="px-3 py-3 text-xs text-ink-2">{sp('pickFigure')}</p>
            ) : manifest ? (
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
      )}
    </div>
  )
}

function FigureStyle({ panel, manifest, exact, canQueueSize }: { panel: PanelObject; manifest: Manifest; exact: boolean; canQueueSize: boolean }) {
  // 单图档：改的永远只是这张图自己的 override，与有没有绑样式无关；这一版清单没回来之前普通控件置灰
  const locked = !exact
  const binding = useDocumentStore((s) => s.doc.style)
  const unbound = !binding || !!binding.detached
  const records = useProfileStore((s) => s.styles)
  const overrides = panel.overrides
  // 与画布样式不一样的 (gid, prop)：每一行据此决定要不要「跟回画布」。没绑 / 脱离 / 还没精确清单 = 空
  const diff = useMemo(
    () => (exact ? figureStyleDiff(panel) : []),
    [exact, panel, manifest, overrides, binding, records], // eslint-disable-line react-hooks/exhaustive-deps
  )
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
            diff={diff}
            locked={locked}
            canQueueSize={canQueueSize}
          />
        ))}
        {/* 画布标注属于整张画布：绑着样式时它在「画布」档里改；没绑才在这里直接改 */}
        {unbound && annotations.length > 0 && <AnnotationRow texts={annotations} />}
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
            diff={diff}
            locked={locked}
          />
        ))}
      </Section>
    </>
  )
}

/** 「跟回画布」：这一行里与画布样式不一样的那几项换回样式的值（一次 commit） */
function useFollowBack(panel: PanelObject, diff: Diff, picks: { gids: string[]; prop: string }[]) {
  const targets = diff.filter((d) => picks.some((p) => p.prop === d.prop && p.gids.includes(d.gid)))
  return targets.length ? () => void followCanvasStyle(panel.id, targets) : undefined
}

type Diff = { gid: string; prop: string }[]

interface FigureRowProps {
  /** 与画布样式不一样的那几项（`figureStyleDiff`）：行里据此摆「跟回画布」 */
  diff: Diff
  /**
   * 直接往这张图上写 override，gid 取自显示用的 manifest——那可能是**上一版**（脚本 /
   * override 刚变、这一版还在渲染）。拿过期的 gid 写 override 会写到已经不存在的元素上
   * （Codex #547 P1，与 styleBinding 只认 `exactPanelManifest` 同一条纪律），所以这一版没回来之前
   * 普通控件置灰；字号可排队，等精确清单到达后重新按角色取目标。
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
  diff,
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
  const follow = useFollowBack(panel, diff, [
    { gids: familyEls.map((e) => e.gid), prop: 'fontfamily' },
    { gids: sizeEls.map((e) => e.gid), prop: 'fontsize' },
    { gids: weightEls.map((e) => e.gid), prop: 'weight' },
    { gids: styleEls.map((e) => e.gid), prop: 'style' },
  ])
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
    pw.write(prop, v, () => (prop === 'weight' ? weight : style).writeOnce(prop, v))
  const writeSize = (v: number) => {
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
    <StyleRow id={id} follow={follow}>
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
                pw.write('fontfamily', v, () => family.writeOnce('fontfamily', v))
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
  diff,
  locked,
}: FigureRowProps & { id: string; role: string; prop: string }) {
  useTranslation(['workspace', 'inspector'])
  const els = useMemo(() => elementsWith(manifest, role, prop), [manifest, role, prop])
  const props = useMemo(() => [prop], [prop])
  const adapter = useTextStyleAdapter(panel, els, props)
  const pw = usePendingWrites()
  const field = adapter.fieldOf(prop)
  const follow = useFollowBack(panel, diff, [{ gids: els.map((e) => e.gid), prop }])
  if (!els.length || !field) return null

  const label = ROW_LABEL[id]()
  const value = pw.shown(prop, adapter.valueOf(prop))
  const write = (v: unknown) => pw.write(prop, v, () => adapter.writeOnce(prop, v))

  return (
    <StyleRow id={id} follow={follow}>
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
function AnnotationRow({ texts }: { texts: TextObject[] }) {
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
              pw.write('fontFamily', v, () => adapter.writeOnce('fontFamily', v))
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
              pw.write('sizePt', v, () => adapter.writeOnce('sizePt', v))
            }
          />
        </div>
        <FaceToggles
          row="annotation"
          weight={face('weight')}
          style={face('style')}
          onWeight={(v) => pw.write('weight', v, () => adapter.writeOnce('weight', v))}
          onStyle={(v) => pw.write('style', v, () => adapter.writeOnce('style', v))}
        />
      </ControlLine>
    </StyleRow>
  )
}
