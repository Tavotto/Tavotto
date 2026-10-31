import { Fragment } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import { Button, IconButton } from '@/components/ui/Button'
import { Kbd } from '@/components/ui/Kbd'
import { cn } from '@/lib/utils'
import { readingOrder } from '@/lib/geometry'
import { formatMm } from '@/lib/units'
import { untruncatedLabel } from '@/components/inspector/identityCrumbs'
import { usePagePresets } from '@/components/inspector/pagePreset'
import { engineLabel, roleName } from '@/components/inspector/roles/registry'
import { useOriginalSpec } from '@/hooks/useOriginalSpec'
import type { OriginalOutputSpec } from '@/lib/originalSpec'
import { reasonText, statusLabel } from '@/lib/readinessText'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { usePanelDisplayManifest } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { figuresOf } from '@/lib/figuresOf'
import { useWorkspaceStore } from '@/store/workspaceStore'
import type { PanelObject } from '@/types/document'

/**
 * 画布状态条（UI 审计 B2 / B3 / A1）：画布页签下面一条 28px 的常驻灰条，**说当前状态**，一条顶替此前三处：
 *
 * ```text
 * Figure 1                                      空画布 · 页面 150 × 100 mm · 双栏
 * Figure 1 / Fig1_kinetics                      单图编辑 · 按原图尺寸 73.5 × 57.8 mm 导出
 * Figure 2 / (b) Fig2_yield                     多图排版 · 页面 150 × 100 mm · 双栏 · 3 张图
 * Figure 2 / 已选 2 张：(a) (c)                 多图排版 · …
 * ← Figure 2 / (b) Fig2_yield / 整张图          Esc 退出图内编辑      （图内编辑，子状态）
 * ```
 *
 * * **模式由画布上图数推出**（`workspaceStore.settleMode`，ADR 0028）：0 张空画布 / 1 张单图编辑 / ≥2 张多图排版；
 *   图内编辑是后两者的子状态。这里只翻译，不判断。
 * * **面包屑只跟当前焦点走**（A1）：没选中停在画布；选中一张延伸到这张图（序号 + 图名，序号按阅读顺序、与「添加序号标签」
 *   同一口径）；进图内编辑再延伸到元素；多选写「已选 N 张」。每一段能点回那一层。「子图」这个词不在这里出现。
 * * **出口（B3）**：图内编辑时左端的箭头钮 = 此前浮在图上方的「退出图内编辑」胶囊，Esc 同一个动作；它不再盖住图标题与控制点。
 *   稳定锚点（`data-context-back` / `data-exit-element-edit` / `data-workspace-context-bar` / `data-workspace-mode` /
 *   `data-context-object`）原样保留——教程与 e2e 认它们，不认文案。
 * * 它是固定高度的常驻元素，不是弹出的横条：画布不会因为它出现 / 消失而被推动。
 */
export function CanvasStatusStrip() {
  const { t } = useTranslation('workspace')
  const fastEdit = useWorkspaceStore((s) => s.mode === 'fast_edit')
  const activePanelId = useWorkspaceStore((s) => s.activePanelId)
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  const selectedGids = useUiStore((s) => s.selectedGids)
  const selectedIds = useSelectionStore((s) => s.ids)
  const canvasName = useDocumentStore((s) => s.doc.name)
  const page = useDocumentStore((s) => s.doc.page)
  const objects = useDocumentStore((s) => s.doc.objects)
  const presets = usePagePresets()

  const figures = figuresOf(objects)
  const count = figures.length
  const mode = fastEdit ? 'single' : count === 0 ? 'empty' : 'multi'
  const editPanel = figures.find((p) => p.id === elementPanelId) ?? null
  const editingHere = !!editPanel
  // 单图编辑里即使没选中，当前图也只有这一张：面包屑仍按「选中」走（没选中停在画布），所以只认选区
  const selectedPanels = figures.filter((p) => selectedIds.includes(p.id))
  const focusPanel = editPanel ?? (selectedPanels.length === 1 ? selectedPanels[0] : null)
  const exitPanel = fastEdit ? figures.find((p) => p.id === activePanelId) ?? null : editPanel

  // 序号 (a)(b)…：只在多图时有意义，按阅读顺序，与 `actions.addSubLabels` 同一口径
  const order = readingOrder(figures)
  const seqOf = (p: PanelObject) => (count > 1 ? `(${String.fromCharCode(97 + order.indexOf(p))}) ` : '')
  const figName = (p: PanelObject) => `${seqOf(p)}${p.name ?? p.fileId}`

  const manifest = usePanelDisplayManifest(editingHere ? editPanel : null)
  const gid = editingHere ? selectedGids.at(-1) : undefined
  const element = gid ? manifest?.elements.find((e) => e.gid === gid) : undefined
  // 图内编辑里没选元素时，当前对象就是「整张图」（右栏头部也这么写）
  const objectCrumb = !editingHere
    ? null
    : selectedGids.length > 1
      ? translate('elementsSelected', { ns: 'inspector', count: selectedGids.length })
      : selectedGids.length === 0
        ? roleName('figure')
        : element
          ? engineLabel(untruncatedLabel(element.label, fullTextOf(element)))
          : null

  // 单图编辑的状态句：按原图尺寸导出。规格是 ADR 0028 的唯一一份（`useOriginalSpec`），这里不自己算
  const spec = useOriginalSpec(mode === 'single' ? (exitPanel?.fileId ?? null) : null)
  const capability = useAssetStore((s) =>
    mode === 'single' && exitPanel ? s.byId[exitPanel.fileId]?.capability : undefined,
  )
  const editable = !!exitPanel?.script
  const activePreset = presets.find((p) => p.w === page.w && p.h === page.h)
  const pageText = [
    t('stageStrip.page', { w: formatMm(page.w), h: formatMm(page.h) }),
    activePreset && translate(`canvas.presets.${activePreset.id}.label`, { ns: 'inspector' }),
  ]
    .filter(Boolean)
    .join(' · ')
  const modeLabel =
    mode === 'single' ? t('stageStrip.single') : mode === 'multi' ? t('stageStrip.multi') : t('stageStrip.empty')
  const detail =
    mode === 'single'
      ? !editable && exitPanel
        ? // 进不了图内编辑时诚实说明并给出下一步——不画成错误
          capability
          ? `${statusLabel(capability.status)} · ${reasonText(capability)}`
          : translate('fastEdit.layoutOnly', { ns: 'workspace' })
        : spec && trustedSize(spec)
          ? t('stageStrip.exportAtOriginal', { w: formatMm(spec.widthMm), h: formatMm(spec.heightMm) })
          : null
      : mode === 'multi'
        ? `${pageText} · ${t('stageStrip.count', { n: count })}`
        : pageText

  /** 退出图内编辑并选中该面板——属性页落在面板上，「写回原始文件」就在手边（与 `useKeyboard` 里 Esc 的最后一层同一件事） */
  const back = () => {
    if (!editPanel) return
    useUiStore.getState().setElementPanel(null)
    useSelectionStore.getState().set([editPanel.id])
  }
  /** 回到画布层：退出图内编辑、清选区 */
  const toCanvas = () => {
    useUiStore.getState().setElementPanel(null)
    useSelectionStore.getState().clear()
  }

  // 面包屑：画布 / 图（或「已选 N 张」）/ 元素。最后一段是当前所在，不可点
  const segs: { key: string; text: string; onClick?: () => void; object?: boolean }[] = [
    { key: 'canvas', text: canvasName },
  ]
  if (focusPanel) segs.push({ key: 'figure', text: figName(focusPanel) })
  else if (selectedPanels.length > 1)
    segs.push({
      key: 'multi',
      text: t('stageStrip.selected', {
        n: selectedPanels.length,
        names: readingOrder(selectedPanels)
          .map((p) => (count > 1 ? `(${String.fromCharCode(97 + order.indexOf(p))})` : (p.name ?? p.fileId)))
          .join(' '),
      }),
    })
  if (objectCrumb) segs.push({ key: 'object', text: objectCrumb, object: true })
  segs.forEach((seg, i) => {
    if (i === segs.length - 1) return
    seg.onClick = seg.key === 'canvas' ? toCanvas : back
  })

  return (
    <nav
      aria-label={t('stageStrip.label')}
      data-workspace-context-bar
      data-workspace-mode={fastEdit ? 'fast_edit' : 'layout'}
      className="type-body flex h-7 shrink-0 items-center gap-2 border-b border-border bg-surface-2 px-3 text-ink-2"
    >
      {editingHere && (
        // 图内编辑的唯一出口（Esc 同一个动作）：e2e 拿 `data-exit-element-edit` 判「进了图内编辑」，
        // 属性页的「编辑图内元素」按钮进编辑后把焦点交到这里
        <IconButton
          label={fastEdit ? t('stage.exitElementEdit') : t('stage.backToCanvas')}
          tip={t('stage.exitTitle')}
          side="bottom"
          iconSize="xs"
          className="-ms-1 shrink-0"
          data-context-back
          data-exit-element-edit=""
          onClick={back}
        >
          <ArrowLeft size={ICON_SIZE.xs} />
        </IconButton>
      )}
      <ol aria-label={t('stage.crumbsLabel')} className="flex min-w-0 flex-1 items-center gap-1">
        {segs.map((seg, i) => {
          const last = i === segs.length - 1
          return (
            <Fragment key={seg.key}>
              {i > 0 && (
                <li aria-hidden className="shrink-0 text-ink-faint">
                  /
                </li>
              )}
              <li
                className={cn('min-w-0 truncate', last ? 'font-medium text-ink' : 'text-ink-2')}
                data-context-object={seg.object ? '' : undefined}
                title={seg.text}
                aria-current={last ? 'location' : undefined}
              >
                {seg.onClick ? (
                  <button
                    type="button"
                    data-crumb={seg.key}
                    className="max-w-full truncate rounded-xs hover:text-ink hover:underline focus-visible:focus-ring"
                    onClick={seg.onClick}
                  >
                    {seg.text}
                  </button>
                ) : (
                  seg.text
                )}
              </li>
            </Fragment>
          )
        })}
      </ol>
      {editingHere ? (
        <span className="flex shrink-0 items-center gap-1.5 text-ink-3">
          <Kbd>{translate('keycap.esc')}</Kbd>
          {fastEdit ? t('stage.exitElementEdit') : t('stage.backToCanvas')}
        </span>
      ) : (
        <span className="flex min-w-0 shrink items-center gap-1.5 text-ink-3">
          <b className="shrink-0 font-medium text-ink">{modeLabel}</b>
          {detail && <span className="min-w-0 truncate">· {detail}</span>}
          {mode === 'single' && exitPanel && !editable && (
            <Button
              size="sm"
              variant="ghost"
              className="-me-2 shrink-0 whitespace-nowrap"
              onClick={() =>
                useProjectReadinessStore.getState().focusPanel(exitPanel.fileId, 'quickedit')
              }
            >
              {t('fastEdit.connectSource')}
            </Button>
          )}
        </span>
      )}
    </nav>
  )
}

/**
 * 只说有出处的尺寸：占位值（`fallback`）、源文件不在了的旧规格（`stale`）、按假定密度反算的位图毫米数
 * 都不说——静默报一个编出来的尺寸比不说更糟（`lib/originalSpec` 的「不发明」）。尺寸与来源在导出对话框里说全。
 */
function trustedSize(spec: OriginalOutputSpec): boolean {
  return !spec.fallback && !spec.stale && spec.dpiSource !== 'assumed'
}

/** 名字里被引擎截到 18 字的那段用户文字的全文：文字元素的 `text`、系列的 `label`（与右栏头部同一判据） */
function fullTextOf(el: { editable: { prop: string; value: unknown }[] }): string | undefined {
  const v = el.editable.find((f) => f.prop === 'text' || f.prop === 'label')?.value
  return typeof v === 'string' ? v : undefined
}
