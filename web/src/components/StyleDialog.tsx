import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import { Check, Ellipsis, Pipette, Plus, Trash2, TriangleAlert, X } from '@/components/ui/icons'
import { Details, Summary } from '@/components/ui/Details'
import { ICON_SIZE } from '@/components/ui/Icon'
import {
  draftToData,
  extractFromManifest,
  extractPalette,
  groupedEntries,
  planStyle,
  presetEntries,
  profileToDraft,
  styleGroupLabel,
  styleRoleLabel,
  styleScopeLabel,
  targetPanels,
  type StylePreset,
  type StyleScope,
} from '@/lib/stylePresets'
import { modKey } from '@/lib/utils'
import { applyStylePlan } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useProfileStore } from '@/store/profileStore'
import { profileName } from '@/lib/profileText'
import { panelRender, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { askConfirm, dialogCovered, useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { propLabel } from './inspector/roles/registry'
import { FormRow } from './FormRow'
import { Button, IconButton } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { Menu, MenuItem, MenuSeparator } from './ui/Menu'
import { ColorField, NumberField, TextInput } from './ui/Input'
import { Segmented } from './ui/Segmented'
import { Select } from './ui/Select'
import { Toggle } from './ui/Toggle'

/**
 * 论文样式：命名保存的排版规格，批量应用到面板 / 文档。
 *
 * 应用只写 override 与标注属性（一条历史，⌘Z 整体撤销），不写回源文件；
 * 想把结果烙进 figures 里的原图，仍走各面板自己的「写回原始文件」。
 *
 * ### 它是设置 / 导出之上的一步，不是叠在它们上面的第三层浮层（审计 T35）
 *
 * 从设置「应用到当前图」进来时带着那一条样式（`uiStore.stylesPresetId`）预选；
 * 设置本身被盖住但没关（`dialogStack`），这里关掉就回到设置、焦点回到那颗按钮。
 * 没带预选而草稿又是空的，就选第一条已存样式——「空样式」与用户刚才点的那条
 * 是什么关系，不该让用户猜。
 */
/** 本对话框的文案在 dialogs:style.* 下 */
const sd = (key: string, values?: Record<string, unknown>) =>
  translate(`style.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * ⋯ 的可达名**刻意读设置页那一份**（`dialogs:profiles.more`）：同一份样式库在两处
 * 有同一颗 ⋯，名字得是同一个字——在这里另写一句同义词就是审计 T43 记的那种分叉
 * （与 `settings/ExportSettings` 读导出对话框的 key 同一条纪律）。
 */
const moreLabel = () => translate('profiles.more', { ns: 'dialogs' })

export function StyleDialog() {
  const { t } = useTranslation(['dialogs', 'common'])
  const open = useUiStore((s) => s.stylesOpen)
  const presetId = useUiStore((s) => s.stylesPresetId)
  const covered = useUiStore((s) => dialogCovered(s.dialogStack, 'styles'))
  const setOpen = useUiStore((s) => s.setStylesOpen)

  // 清单的唯一持有者是 profileStore（磁盘细节全在后端 engine/profilestore.py）。
  // 这里只把它翻译成"编辑草稿"，并且**只在打开时拉一次**。
  const records = useProfileStore((s) => s.styles)
  const storeError = useProfileStore((s) => s.error)
  const saved = useMemo(() => records.map(profileToDraft), [records])
  const readOnlyIds = useMemo(
    () => new Set(records.filter((r) => r.read_only).map((r) => r.id)),
    [records],
  )
  const nameOf = (preset: StylePreset) => {
    const rec = records.find((r) => r.id === preset.id)
    return rec ? profileName(rec) : preset.name
  }

  const [draft, setDraft] = useState<StylePreset>(EMPTY)
  const [scope, setScope] = useState<StyleScope>('panel')
  const [withAnnotations, setWithAnnotations] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  /** 切到库里的另一份：草稿整份换掉（深拷贝，编辑不回写到清单里的那一份） */
  const pick = (id: string) => {
    const next = saved.find((s) => s.id === id)
    if (next) setDraft(structuredClone(next))
  }
  /** 选中的这一份删得掉吗：内置只读那几份不行（`reason` 会把原因说出来） */
  const deletable = !!draft.id && !readOnlyIds.has(draft.id)
  const removeCurrent = async () => {
    const id = draft.id
    if (!id || readOnlyIds.has(id)) return
    const name = nameOf(draft)
    if (
      !(await askConfirm({
        title: msg('style.deleteTitle', { name }, 'dialogs'),
        body: msg('style.deleteBody', undefined, 'dialogs'),
        confirmLabel: msg('actions.delete', undefined, 'common'),
        danger: true,
      }))
    ) {
      return
    }
    await useProfileStore.getState().remove('style', id)
    setDraft(EMPTY)
  }
  /** 本对话框自己的报错优先，其次是清单那一层的（拉取失败 / 并发撞车）。 */
  const shownError = error ?? storeError?.message ?? null

  useEffect(() => {
    if (!open) return
    setError(null)
    useProfileStore.getState().clearError()
    void useProfileStore.getState().load()
  }, [open])

  /**
   * 打开时预选一次：带了 `presetId` 就选它，没带而草稿是空的就选第一条已存样式。
   * 清单可能晚于打开那一刻到达（`load()`），所以盯着 `saved` 重试，选中过一次
   * 就不再动——用户之后点别的、点「新建样式」都是他的事。
   */
  const preselected = useRef(false)
  useEffect(() => {
    if (!open) {
      preselected.current = false
      return
    }
    if (preselected.current) return
    const want =
      (presetId ? saved.find((s) => s.id === presetId) : undefined) ??
      (isEmptyDraft(draft) ? saved[0] : undefined)
    if (!want) return
    preselected.current = true
    setDraft(structuredClone(want))
  }, [open, presetId, saved, draft])

  const doc = useDocumentStore((s) => s.doc)
  const selectedIds = useSelectionStore((s) => s.ids)
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  // 变体分键之后取 manifest 必须带上面板本身（同文件的两个副本各有各的）
  const byKey = useRenderStore((s) => s.byKey)
  const latest = useRenderStore((s) => s.latest)

  // 「当前面板」：图内编辑中的面板优先，否则选区里最后选中的脚本面板
  const primaryPanel = useMemo(() => {
    const pick = (id: string | null | undefined) => {
      const o = id ? doc.objects.find((x) => x.id === id) : undefined
      return o?.type === 'panel' && o.script ? o : null
    }
    return (
      pick(elementPanelId) ??
      pick([...selectedIds].reverse().find((id) => pick(id))) ??
      null
    )
  }, [doc.objects, elementPanelId, selectedIds])

  const primaryManifest = primaryPanel
    ? (panelRender({ byKey, latest }, primaryPanel)?.manifest ?? null)
    : null

  const plan = useMemo(() => {
    const panels = targetPanels(doc, scope, primaryPanel?.id ?? null, selectedIds)
    return planStyle(
      draft,
      panels,
      (p) => panelRender({ byKey, latest }, p)?.manifest,
      doc,
      withAnnotations,
    )
  }, [doc, scope, primaryPanel, selectedIds, draft, byKey, latest, withAnnotations])

  const extract = () => {
    if (!primaryManifest) return
    setDraft((d) => ({
      ...d,
      element: extractFromManifest(primaryManifest),
      palette: extractPalette(primaryManifest),
    }))
  }

  /**
   * 存盘。**内置样式只读**——在它上面按保存时另存为一份用户样式（这正是
   * 「改内置」的正确出口），而不是弹一句"不能改"把用户挡在原地。
   */
  const save = async () => {
    const name = draft.name.trim()
    if (!name) {
      setError(sd('nameRequired'))
      return
    }
    setBusy(true)
    setError(null)
    const api = useProfileStore.getState()
    const editable = !!draft.id && !readOnlyIds.has(draft.id)
    const stored = editable
      ? ((await api.save('style', draft.id!, draftToData({ ...draft, name }))) &&
        (await api.rename('style', draft.id!, name)))
      : await api.create('style', name, draftToData({ ...draft, name }))
    setBusy(false)
    if (!stored) {
      const err = useProfileStore.getState().error
      setError(err ? err.message : sd('saveFailed'))
      return
    }
    setDraft(profileToDraft(stored))
    useUiStore.getState().setStatus(msg('style.saved', { name: stored.display_name }, 'dialogs'))
  }

  const apply = async () => {
    const touched = plan.panels.filter((p) => p.patches.length)
    const overwrites = plan.panels.reduce((t, p) => t + p.overwrites, 0)
    if (
      overwrites > 0 &&
      !(await askConfirm({
        title: msg('style.confirmTitle', { name: draft.name || sd('untitled') }, 'dialogs'),
        body: msg('style.confirmBody', { count: overwrites, undo: modKey('Z') }, 'dialogs'),
        confirmLabel: msg('style.confirmApply', undefined, 'dialogs'),
      }))
    ) {
      return
    }
    applyStylePlan(plan, { ...draft, name: draft.name || sd('untitledStyle') })
    setOpen(false)
    void touched
  }

  const entries = presetEntries(draft)
  const groups = groupedEntries(draft)
  const applicable =
    plan.panels.some((p) => p.patches.length) ||
    plan.annotationIds.length > 0 ||
    plan.subLabelIds.length > 0 ||
    !!plan.page

  // 没有任何已存样式、草稿也是空的 → 单栏空状态，不画空列表和空影响范围框
  const draftHasContent =
    entries.length > 0 || !!draft.palette?.length || !!draft.annotation || !!draft.subLabel || !!draft.page
  const empty = saved.length === 0 && !draftHasContent

  if (empty) {
    return (
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title={sd('title')}
        description={sd('descriptionEmpty')}
        width={520}
        busy={busy}
        covered={covered}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => setOpen(false)}>
              {t('common:actions.close')}
            </Button>
            <Button
              variant="primary"
              size="md"
              disabled={!primaryManifest}
              title={primaryManifest ? undefined : sd('needPanel')}
              onClick={extract}
            >
              <Pipette size={ICON_SIZE.md} />
              {sd('extract')}
            </Button>
          </>
        }
      >
        <p className="text-xs leading-relaxed text-ink-2">{sd('emptyBody')}</p>
        {shownError && <p className="mt-2 text-xs text-danger">{shownError}</p>}
      </Dialog>
    )
  }

  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title={sd('title')}
      width={920}
      /* 固定高：字段清单在中间滚，样式库与底部的应用范围不随内容高低跳动 */
      height="640px"
      busy={busy}
      covered={covered}
      footer={
        <>
          <Button variant="secondary" size="md" onClick={() => setOpen(false)}>
            {t('common:actions.close')}
          </Button>
          <Button variant="primary" size="md" disabled={!applicable} onClick={apply}>
            <Check size={ICON_SIZE.md} />
            {sd('applyTo', { scope: styleScopeLabel(scope) })}
          </Button>
        </>
      }
    >
      {/*
        一栏：样式库收成顶部一行，编辑器铺满 920（全面打磨 D23）。此前左边一列 176px
        只放一行「默认样式」加一颗「+ 新建样式」——两三份配置不值一整列，而设置 › 样式页
        两天前刚把同一份库收成了一行，同一份东西两种形态。这里与那边同形：一份时只写名字、
        二到四份是分段选择器、再多换下拉，新建 / 提取 / 删除收进行尾的 ⋯。
        应用范围与影响仍在**底部**自成一段（2026-09-13 审计 B25）。
      */}
      <div className="flex h-full min-w-0 flex-col gap-2">
          <FormRow label={sd('savedStyles')}>
            {saved.length === 0 ? (
              <span className="type-meta min-w-0 flex-1">{sd('noSavedStyles')}</span>
            ) : saved.length === 1 ? (
              /* 只有一份时没有可选的：写名字就够了，一格的分段选择器读作坏掉的控件 */
              <span className="min-w-0 flex-1 truncate text-xs text-ink">{nameOf(saved[0])}</span>
            ) : saved.length <= LIBRARY_AS_SEGMENTED ? (
              <Segmented<string>
                ariaLabel={sd('savedStyles')}
                className="min-w-0 flex-1"
                value={draft.id ?? null}
                onChange={(id) => pick(id)}
                items={saved.map((s) => ({ value: s.id ?? '', label: nameOf(s) }))}
              />
            ) : (
              <Select
                ariaLabel={sd('savedStyles')}
                className="min-w-0 flex-1"
                value={draft.id ?? ''}
                onChange={(id) => pick(id)}
                options={saved.map((s) => ({ value: s.id ?? '', label: nameOf(s) }))}
              />
            )}
            <Menu
              align="end"
              trigger={
                <IconButton label={moreLabel()} iconSize="sm">
                  <Ellipsis size={ICON_SIZE.sm} aria-hidden />
                </IconButton>
              }
            >
              <MenuItem icon={Plus} onSelect={() => setDraft(EMPTY)}>
                {sd('newStyle')}
              </MenuItem>
              <MenuItem
                icon={Pipette}
                disabled={!primaryManifest}
                reason={primaryManifest ? undefined : sd('extractNeedPanel')}
                onSelect={extract}
              >
                {sd('extract')}
              </MenuItem>
              {/* 内置只读那几份删不掉：给出**不可用的原因**，不是一颗按了没反应的钮 */}
              <MenuSeparator />
              <MenuItem
                icon={Trash2}
                danger
                disabled={!deletable}
                reason={deletable ? undefined : sd('deleteBuiltinReason')}
                onSelect={() => void removeCurrent()}
              >
                {t('common:actions.delete')}
              </MenuItem>
            </Menu>
          </FormRow>

          {/* 名称也是一行表单（全面打磨 D24）：此前是一个 513 宽、没有标签的框，
              占位文案是它唯一的提示，旁边并排两颗带图标的 secondary，与 footer 的
              主钮抢分量。图标去掉，留给 footer 那一颗 */}
          <FormRow label={sd('nameLabel')}>
            <TextInput
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              placeholder={sd('namePlaceholder')}
              className="min-w-0 flex-1"
            />
            <Button variant="secondary" size="sm" loading={busy} onClick={save}>
              {t('common:actions.save')}
            </Button>
          </FormRow>

          {/* 字段清单：靠组头与留白分区，不套边框（宪法第八节「少用容器」） */}
          <div className="min-h-0 flex-1 overflow-y-auto" data-style-entries>
            {groups.length === 0 && !draft.palette?.length ? (
              <p className="py-2 text-xs leading-relaxed text-ink-3">{sd('emptyDraft')}</p>
            ) : (
              <div className="flex flex-col gap-3">
                {groups.map(({ group, entries: list }) => (
                  <div key={group} data-style-group={group}>
                    <p className="mb-1 type-section">{styleGroupLabel(group)}</p>
                    <div className="flex flex-col gap-0.5">
                      {list.map((en, i) => (
                        <div key={`${en.role}.${en.prop}`}>
                          {/* 角色是**组内的小标**，不是行里的第一列（全面打磨 D25）：
                              此前一行是「角色 64 + 属性 80 + 控件 224 + × 28」四列，
                              两个标签两档灰、都 11px，与导出对话框的 80px 单标签列不是
                              一副；同角色的后续行角色列还留空，右缘参差。现在行只剩
                              「属性 ‖ 控件 ×」，走共用的 `FormRow` */}
                          {(i === 0 || list[i - 1].role !== en.role) && (
                            <p className="flex h-7 items-end text-xs text-ink-3">
                              {styleRoleLabel(en.role)}
                            </p>
                          )}
                          <FormRow label={propLabel(en.prop, en.role)}>
                            {/* 值一列 224px：字体名（Times New Roman）完整可读 */}
                            <div className="w-56 shrink-0">
                              <EntryEditor
                                prop={en.prop}
                                value={en.value}
                                onChange={(v) =>
                                  setDraft((d) => ({
                                    ...d,
                                    element: {
                                      ...d.element,
                                      [en.role]: { ...d.element[en.role], [en.prop]: v },
                                    },
                                  }))
                                }
                              />
                            </div>
                            {/* × 的动作说全：从这份样式里移除这一项（样式不再管它），
                                不是删对象、也不是关掉什么 */}
                            <IconButton
                              iconSize="sm"
                              className="shrink-0 text-ink-3"
                              label={sd('removeEntryNamed', {
                                role: styleRoleLabel(en.role),
                                prop: propLabel(en.prop, en.role),
                              })}
                              onClick={() =>
                                setDraft((d) => {
                                  const role = { ...d.element[en.role] }
                                  delete role[en.prop]
                                  const element = { ...d.element, [en.role]: role }
                                  if (!Object.keys(role).length) delete element[en.role]
                                  return { ...d, element }
                                })
                              }
                            >
                              <X size={ICON_SIZE.sm} />
                            </IconButton>
                          </FormRow>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}

                {!!draft.palette?.length && (
                  <div data-style-group="palette">
                    <p className="mb-1 type-section">{sd('paletteTitle')}</p>
                    <div className="flex flex-wrap items-center gap-1">
                      {draft.palette.map((c, i) => (
                        <span key={i} className="flex items-center gap-0.5">
                          <ColorField
                            ariaLabel={sd('paletteSwatchAria', { index: i + 1 })}
                            value={c}
                            onChange={(v) =>
                              setDraft((d) => ({
                                ...d,
                                palette: d.palette!.map((x, j) => (j === i ? v : x)),
                              }))
                            }
                          />
                          <IconButton
                            iconSize="sm"
                            className="text-ink-3"
                            label={sd('removeColor')}
                            onClick={() =>
                              setDraft((d) => ({
                                ...d,
                                palette: d.palette!.filter((_, j) => j !== i),
                              }))
                            }
                          >
                            <X size={ICON_SIZE.xs} />
                          </IconButton>
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            <div className="mt-3" data-style-group="annotation">
              <p className="mb-1 type-section">{sd('annotationGroup')}</p>
              <TextStylePart
                label={sd('annotationText')}
                boldByDefault={false}
                value={draft.annotation}
                onChange={(v) => setDraft((d) => ({ ...d, annotation: v }))}
              />
              <TextStylePart
                label={sd('subLabel')}
                boldByDefault
                value={draft.subLabel}
                onChange={(v) => setDraft((d) => ({ ...d, subLabel: v }))}
              />
              <label className="flex h-7 items-center gap-1.5 text-xs text-ink-2">
                <Toggle
                  aria-label={sd('includePageSize')}
                  checked={!!draft.page}
                  onChange={(v) =>
                    setDraft((d) => ({
                      ...d,
                      page: v ? { ...useDocumentStore.getState().doc.page } : undefined,
                    }))
                  }
                />
                <span>
                  {sd('includePageSize')}
                  {draft.page ? sd('pageSizeSuffix', { w: draft.page.w, h: draft.page.h }) : ''}
                </span>
              </label>
            </div>
          </div>

          {/* 底部：应用范围与影响。范围是一个取值 → 分段选择器；影响先说总账，
              逐张明细折叠。标签是**标签**不是分区头，分段按内容定宽（全面打磨 D26）：
              此前它用 `type-section` 的字重、又 `flex-1` 撑到 636 宽四格各 158，
              是导出对话框同款控件的 4.8 倍 */}
          <div className="flex flex-col gap-2 border-t border-border pt-3" data-style-scope>
            <FormRow label={sd('applyScope')}>
              <Segmented<StyleScope>
                ariaLabel={sd('applyScope')}
                // 按内容定宽：`Segmented` 默认 `w-full`，在这一行里会撑成 780 宽四格各 195，
                // 是导出对话框同款控件的五倍（全面打磨 D26）
                className="w-auto"
                value={scope}
                onChange={setScope}
                items={(
                  [
                    ['panel', sd('scopePanel')],
                    ['selection', sd('scopeSelection')],
                    ['sameScript', sd('scopeSameScript')],
                    ['document', sd('scopeDocument')],
                  ] as const
                ).map(([value, label]) => ({ value, label, title: styleScopeLabel(value) }))}
              />
            </FormRow>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <label className="flex h-7 items-center gap-1.5 text-xs text-ink-2">
                <Toggle
                  aria-label={sd('withAnnotations')}
                  checked={withAnnotations}
                  onChange={setWithAnnotations}
                />
                {sd('withAnnotations')}
              </label>
              {/* 作用对象与变化数先说总账：用户要的第一个答案是「会改到几张、改多少」 */}
              {/* 空集时只说**下一步**（全面打磨 D27）：此前是「此范围内没有图会被改动。」
                  句号之后再接「 · 先在画布上选中一张可编辑的图」——前一句是后一句的前提，
                  说了等于把唯一能做的事推到第二句去 */}
              <p data-style-affect-summary className="text-xs text-ink-2">
                {plan.panels.length === 0
                  ? sd(scope === 'panel' ? 'noPanelsPanel' : 'noPanelsScope')
                  : sd('affectSummary', {
                      count: plan.panels.length,
                      patches: plan.panels.reduce((t, p) => t + p.patches.length, 0),
                    })}
              </p>
            </div>
            {(plan.panels.length > 0 ||
              plan.unrendered.length > 0 ||
              (withAnnotations && plan.annotationIds.length > 0 && draft.annotation) ||
              (withAnnotations && plan.subLabelIds.length > 0 && draft.subLabel) ||
              plan.page) && (
              <Details>
                <Summary className="h-7 text-xs text-ink-3 hover:text-ink">{sd('affectDetails')}</Summary>
                <ul className="mt-1 flex max-h-32 flex-col gap-1 overflow-y-auto">
                  {plan.panels.map((p) => (
                    <li key={p.panel.id} className="text-xs leading-relaxed text-ink-2">
                      <span className="text-ink">{p.panel.name ?? p.panel.fileId}</span>
                      {sd('panelPatches', { count: p.patches.length })}
                      {p.overwrites > 0 && (
                        <span className="text-danger">
                          {sd('panelOverwrites', { count: p.overwrites })}
                        </span>
                      )}
                      {p.unmappable.length > 0 && (
                        <span className="text-ink-3">
                          {sd('panelUnmappable', { count: p.unmappable.length })}
                        </span>
                      )}
                    </li>
                  ))}
                  {plan.unrendered.map((p: PanelObject) => (
                    <li key={p.id} className="flex items-start gap-1 text-xs leading-relaxed text-ink-3">
                      <TriangleAlert size={ICON_SIZE.xs} className="mt-0.5 shrink-0" />
                      <span>{sd('unrendered', { name: p.name ?? p.fileId })}</span>
                    </li>
                  ))}
                  {withAnnotations && plan.annotationIds.length > 0 && draft.annotation && (
                    <li className="text-xs text-ink-2">
                      {sd('annotationCount', { count: plan.annotationIds.length })}
                    </li>
                  )}
                  {withAnnotations && plan.subLabelIds.length > 0 && draft.subLabel && (
                    <li className="text-xs text-ink-2">
                      {sd('subLabelCount', { count: plan.subLabelIds.length })}
                    </li>
                  )}
                  {plan.page && (
                    <li className="text-xs text-ink-2">
                      {sd('pageSizeTo', { w: plan.page.w, h: plan.page.h })}
                    </li>
                  )}
                  {plan.panels.some((p) => p.unmappable.length > 0) && (
                    <li>
                      <Details>
                        <Summary className="text-xs text-ink-3 hover:text-ink">
                          {sd('unmappableDetails')}
                        </Summary>
                        <ul className="mt-1 flex flex-col gap-0.5">
                          {plan.panels.flatMap((p) =>
                            p.unmappable.slice(0, 20).map((u, i) => (
                              <li key={`${p.panel.id}-${i}`} className="text-xs text-ink-3">
                                {u}
                              </li>
                            )),
                          )}
                        </ul>
                      </Details>
                    </li>
                  )}
                </ul>
              </Details>
            )}
          </div>
      </div>
      {shownError && <p className="mt-2 text-xs text-danger">{shownError}</p>}
    </Dialog>
  )
}

/**
 * 库里几份以内还用分段选择器（与设置 › 样式页同一个数，全面打磨 D23）。
 * 再多就换下拉：五格以上的分段在 920 宽里每格只剩一个词。
 */
const LIBRARY_AS_SEGMENTED = 4

const EMPTY: StylePreset = { name: '', element: {} }

/** 草稿是不是一张白纸（没名字、没条目、没配色、没标注 / 序号 / 页面尺寸） */
function isEmptyDraft(d: StylePreset): boolean {
  return (
    !d.id &&
    !d.name &&
    Object.keys(d.element).length === 0 &&
    !d.palette?.length &&
    !d.annotation &&
    !d.subLabel &&
    !d.page
  )
}

/** 已知枚举 prop 的选项；其余按值类型渲染 */
const ENUM_OPTIONS: Record<string, string[]> = {
  direction: ['out', 'in', 'inout'],
  weight: ['normal', 'bold'],
  fontfamily: ['serif', 'sans-serif', 'Times New Roman', 'Arial', 'Helvetica'],
}

function EntryEditor({
  prop,
  value,
  onChange,
}: {
  prop: string
  value: unknown
  onChange: (v: unknown) => void
}) {
  if (typeof value === 'boolean')
    return <Toggle aria-label={propLabel(prop)} checked={value} onChange={onChange} />
  if (typeof value === 'number') {
    // `fill`：数字框铺满 224 的控件列，与同一列的下拉右缘对齐（全面打磨 D25）；
    // 此前它只有 44 宽，一列控件的右缘参差不齐
    return (
      <NumberField
        value={value}
        step={prop.includes('size') ? 0.5 : 0.1}
        fill
        onChange={onChange}
      />
    )
  }
  if (typeof value === 'string' && /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(value)) {
    return <ColorField ariaLabel={propLabel(prop)} value={value} onChange={onChange} />
  }
  const options = ENUM_OPTIONS[prop]
  if (options && typeof value === 'string') {
    const opts = options.includes(value) ? options : [value, ...options]
    return (
      <Select
        value={value}
        onChange={onChange}
        options={opts.map((o) => ({ value: o, label: o }))}
        ariaLabel={prop}
      />
    )
  }
  return (
    <TextInput
      value={String(value ?? '')}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

/** 标注 / 序号标签的字号加粗颜色小节 */
function TextStylePart({
  label,
  boldByDefault,
  value,
  onChange,
}: {
  label: string
  /**
   * 打开时是否默认加粗。以前是拿 `label.includes('序号')` 判的——那是把
   * **界面文案**当逻辑用，换成英文界面之后序号标签就不再默认加粗了。
   */
  boldByDefault: boolean
  value: { sizePt?: number; bold?: boolean; italic?: boolean; color?: string } | undefined
  onChange: (v: { sizePt?: number; bold?: boolean; italic?: boolean; color?: string } | undefined) => void
}) {
  useTranslation('dialogs')
  return (
    <div>
      <label className="flex h-7 items-center gap-1.5 text-xs text-ink-2">
        <Toggle
          aria-label={label}
          checked={!!value}
          onChange={(v) =>
            onChange(v ? { sizePt: 9, bold: boldByDefault, color: '#000000' } : undefined)
          }
        />
        {label}
      </label>
      {value && (
        <div className="mb-1 flex items-center gap-1.5 pl-7">
          <NumberField
            value={value.sizePt ?? 9}
            min={4}
            max={24}
            step={0.5}
            unit="pt"
            onChange={(v) => onChange({ ...value, sizePt: v })}
          />
          <label className="flex items-center gap-1 text-xs text-ink-2">
            <Toggle
              aria-label={sd('bold')}
              checked={!!value.bold}
              onChange={(v) => onChange({ ...value, bold: v })}
            />
            {sd('bold')}
          </label>
          <ColorField
            ariaLabel={sd('textColorAria')}
            value={value.color ?? '#000000'}
            onChange={(v) => onChange({ ...value, color: v })}
          />
        </div>
      )}
    </div>
  )
}
