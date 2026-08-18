import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import { Check, Pipette, Plus, Save, Trash2, TriangleAlert, X,
  Paintbrush,
} from 'lucide-react'
import { backendErrorText, deleteStyle, fetchStyles, saveStyle } from '@/lib/api'
import {
  extractFromManifest,
  extractPalette,
  planStyle,
  presetEntries,
  styleRoleLabel,
  styleScopeLabel,
  targetPanels,
  type StylePreset,
  type StyleScope,
} from '@/lib/stylePresets'
import { cn, modKey } from '@/lib/utils'
import { applyStylePlan } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { panelRender, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { askConfirm, useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { propLabel } from './inspector/roles/registry'
import { Button } from './ui/Button'
import { EmptyState } from './ui/EmptyState'
import { Dialog } from './ui/Dialog'
import { ColorField, NumberField, TextInput } from './ui/Input'
import { Segmented } from './ui/Segmented'
import { Select } from './ui/Select'
import { Toggle } from './ui/Toggle'

/**
 * 论文样式：命名保存的排版规格，批量应用到面板 / 文档。
 *
 * 应用只写 override 与标注属性（一条历史，⌘Z 整体撤销），不写回源文件；
 * 想把结果烙进 figures 里的原图，仍走各面板自己的「写回原始文件」。
 */
/** 本对话框的文案在 dialogs:style.* 下 */
const sd = (key: string, values?: Record<string, unknown>) =>
  translate(`style.${key}`, { ns: 'dialogs', ...(values ?? {}) })

export function StyleDialog() {
  const { t } = useTranslation(['dialogs', 'common'])
  const open = useUiStore((s) => s.stylesOpen)
  const setOpen = useUiStore((s) => s.setStylesOpen)

  const [saved, setSaved] = useState<StylePreset[]>([])
  const [draft, setDraft] = useState<StylePreset>(EMPTY)
  const [scope, setScope] = useState<StyleScope>('panel')
  const [withAnnotations, setWithAnnotations] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setError(null)
    fetchStyles()
      .then(setSaved)
      .catch((e) => setError(backendErrorText(e)))
  }, [open])

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

  const save = async () => {
    if (!draft.name.trim()) {
      setError(sd('nameRequired'))
      return
    }
    setBusy(true)
    try {
      const stored = await saveStyle({ ...draft, name: draft.name.trim() })
      setDraft(stored)
      setSaved(await fetchStyles())
      setError(null)
      useUiStore.getState().setStatus(msg('style.saved', { name: stored.name }, 'dialogs'))
    } catch (e) {
      setError(backendErrorText(e))
    } finally {
      setBusy(false)
    }
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
        footer={
          <>
            <Button variant="outline" size="md" onClick={() => setOpen(false)}>
              {t('common:actions.close')}
            </Button>
            <Button
              variant="primary"
              size="md"
              disabled={!primaryManifest}
              title={primaryManifest ? undefined : sd('needPanel')}
              onClick={extract}
            >
              <Pipette size={14} />
              {sd('extract')}
            </Button>
          </>
        }
      >
        <p className="text-xs leading-relaxed text-ink-2">{sd('emptyBody')}</p>
        {error && <p className="mt-2 text-xs text-danger">{error}</p>}
      </Dialog>
    )
  }

  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title={sd('title')}
      description={sd('description')}
      width={760}
      busy={busy}
      footer={
        <>
          <Button variant="outline" size="md" onClick={() => setOpen(false)}>
            {t('common:actions.close')}
          </Button>
          <Button variant="primary" size="md" disabled={!applicable} onClick={apply}>
            <Check size={14} />
            {sd('applyTo', { scope: styleScopeLabel(scope) })}
          </Button>
        </>
      }
    >
      <div className="flex gap-3">
        {/* 左：已存样式 */}
        <div className="flex w-44 shrink-0 flex-col gap-1.5">
          <h3 className="text-xs font-medium uppercase tracking-[.06em] text-ink-3">
            {sd('savedStyles')}
          </h3>
          <ul className="min-h-0 flex-1 overflow-y-auto rounded-sm border border-border">
            {saved.length === 0 && (
              <li>
                <EmptyState icon={Paintbrush} title={sd('noSavedStyles')} />
              </li>
            )}
            {saved.map((s, i) => (
              <li
                key={s.id}
                className={cn('group flex items-center', i > 0 && 'border-t border-border')}
              >
                <button
                  onClick={() => setDraft(structuredClone(s))}
                  className={cn(
                    'h-7 min-w-0 flex-1 truncate px-2 text-left text-xs',
                    draft.id === s.id ? 'bg-accent-subtle text-accent' : 'text-ink hover:bg-ink/[.04]',
                  )}
                >
                  {s.name}
                </button>
                <Button
                  size="icon-sm"
                  className="mr-0.5 h-5 w-5 opacity-0 group-hover:opacity-100"
                  aria-label={sd('deleteStyleAria', { name: s.name })}
                  onClick={async () => {
                    if (
                      !(await askConfirm({
                        title: msg('style.deleteTitle', { name: s.name }, 'dialogs'),
                        body: msg('style.deleteBody', undefined, 'dialogs'),
                        confirmLabel: msg('actions.delete', undefined, 'common'),
                        danger: true,
                      }))
                    ) {
                      return
                    }
                    if (s.id) await deleteStyle(s.id)
                    setSaved(await fetchStyles())
                    if (draft.id === s.id) setDraft(EMPTY)
                  }}
                >
                  <Trash2 size={11} className="text-danger" />
                </Button>
              </li>
            ))}
          </ul>
          <Button variant="outline" size="sm" onClick={() => setDraft(EMPTY)}>
            <Plus size={12} />
            {sd('newStyle')}
          </Button>
        </div>

        {/* 中：样式内容 */}
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex items-center gap-1.5">
            <TextInput
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              placeholder={sd('namePlaceholder')}
              className="h-6 min-w-0 flex-1"
            />
            <Button
              variant="outline"
              size="sm"
              disabled={!primaryManifest}
              title={
                primaryManifest
                  ? sd('extractFrom', { name: primaryPanel?.name ?? primaryPanel?.fileId })
                  : sd('extractNeedPanel')
              }
              onClick={extract}
            >
              <Pipette size={12} />
              {sd('extract')}
            </Button>
            <Button variant="outline" size="sm" loading={busy} onClick={save}>
              <Save size={12} />
              {t('common:actions.save')}
            </Button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto rounded-sm border border-border p-2">
            {entries.length === 0 && !draft.palette?.length ? (
              <p className="py-2 text-xs leading-relaxed text-ink-3">{sd('emptyDraft')}</p>
            ) : (
              <div className="flex flex-col gap-1">
                {entries.map((en) => (
                  <div key={`${en.role}.${en.prop}`} className="flex h-6 items-center gap-1.5">
                    <span className="w-16 shrink-0 truncate text-xs text-ink-3">
                      {styleRoleLabel(en.role)}
                    </span>
                    <span className="w-16 shrink-0 truncate text-xs text-ink-2">
                      {propLabel(en.prop, en.role)}
                    </span>
                    <div className="min-w-0 flex-1">
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
                    <Button
                      size="icon-sm"
                      className="h-5 w-5 shrink-0"
                      aria-label={sd('removeEntry')}
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
                      <X size={11} className="text-ink-3" />
                    </Button>
                  </div>
                ))}

                {!!draft.palette?.length && (
                  <div className="mt-1 border-t border-border pt-1.5">
                    <p className="mb-1 text-xs text-ink-3">{sd('paletteTitle')}</p>
                    <div className="flex flex-wrap items-center gap-1">
                      {draft.palette.map((c, i) => (
                        <span key={i} className="flex items-center gap-0.5">
                          <ColorField
                            value={c}
                            onChange={(v) =>
                              setDraft((d) => ({
                                ...d,
                                palette: d.palette!.map((x, j) => (j === i ? v : x)),
                              }))
                            }
                          />
                          <button
                            aria-label={sd('removeColor')}
                            onClick={() =>
                              setDraft((d) => ({
                                ...d,
                                palette: d.palette!.filter((_, j) => j !== i),
                              }))
                            }
                            className="text-ink-3 hover:text-ink"
                          >
                            <X size={10} />
                          </button>
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

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

            <div className="mt-1.5 flex h-6 items-center gap-1.5 border-t border-border pt-1.5">
              <Toggle
                checked={!!draft.page}
                onChange={(v) =>
                  setDraft((d) => ({
                    ...d,
                    page: v ? { ...useDocumentStore.getState().doc.page } : undefined,
                  }))
                }
              />
              <span className="text-xs text-ink-2">
                {sd('includePageSize')}
                {draft.page ? sd('pageSizeSuffix', { w: draft.page.w, h: draft.page.h }) : ''}
              </span>
            </div>
          </div>
        </div>

        {/* 右：应用范围与预览 */}
        <div className="flex w-52 shrink-0 flex-col gap-2">
          <h3 className="text-xs font-medium uppercase tracking-[.06em] text-ink-3">
            {sd('applyScope')}
          </h3>
          <Segmented
            value={scope}
            onChange={setScope}
            className="w-full"
            items={[
              { value: 'panel', label: sd('scopePanel'), tip: styleScopeLabel('panel') },
              { value: 'selection', label: sd('scopeSelection'), tip: styleScopeLabel('selection') },
              {
                value: 'sameScript',
                label: sd('scopeSameScript'),
                tip: styleScopeLabel('sameScript'),
              },
              { value: 'document', label: sd('scopeDocument'), tip: styleScopeLabel('document') },
            ]}
          />
          <label className="flex items-center gap-1.5 text-xs text-ink-2">
            <Toggle checked={withAnnotations} onChange={setWithAnnotations} />
            {sd('withAnnotations')}
          </label>

          <div className="min-h-0 flex-1 overflow-y-auto rounded-sm border border-border p-2">
            <p className="mb-1 text-xs font-medium text-ink">{sd('willAffect')}</p>
            {plan.panels.length === 0 && (
              <p className="text-xs leading-relaxed text-ink-3">
                {sd(scope === 'panel' ? 'noPanelsPanel' : 'noPanelsScope')}
              </p>
            )}
            <ul className="flex flex-col gap-1">
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
                  <TriangleAlert size={11} className="mt-0.5 shrink-0" />
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
            </ul>
            {plan.panels.some((p) => p.unmappable.length > 0) && (
              <details className="mt-1.5">
                <summary className="cursor-pointer text-xs text-ink-3 hover:text-ink">
                  {sd('unmappableDetails')}
                </summary>
                <ul className="mt-1 flex flex-col gap-0.5">
                  {plan.panels.flatMap((p) =>
                    p.unmappable.slice(0, 20).map((u, i) => (
                      <li key={`${p.panel.id}-${i}`} className="text-xs text-ink-3">
                        {u}
                      </li>
                    )),
                  )}
                </ul>
              </details>
            )}
          </div>
        </div>
      </div>
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
    </Dialog>
  )
}

const EMPTY: StylePreset = { name: '', element: {} }

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
  if (typeof value === 'boolean') return <Toggle checked={value} onChange={onChange} />
  if (typeof value === 'number') {
    return <NumberField value={value} step={prop.includes('size') ? 0.5 : 0.1} onChange={onChange} />
  }
  if (typeof value === 'string' && /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(value)) {
    return <ColorField value={value} onChange={onChange} />
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
      className="h-5"
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
    <div className="mt-1.5 border-t border-border pt-1.5">
      <div className="flex h-6 items-center gap-1.5">
        <Toggle
          checked={!!value}
          onChange={(v) =>
            onChange(v ? { sizePt: 9, bold: boldByDefault, color: '#000000' } : undefined)
          }
        />
        <span className="text-xs text-ink-2">{label}</span>
      </div>
      {value && (
        <div className="mt-1 flex items-center gap-1.5 pl-7">
          <NumberField
            value={value.sizePt ?? 9}
            min={4}
            max={24}
            step={0.5}
            suffix="pt"
            onChange={(v) => onChange({ ...value, sizePt: v })}
          />
          <label className="flex items-center gap-1 text-xs text-ink-2">
            <Toggle checked={!!value.bold} onChange={(v) => onChange({ ...value, bold: v })} />
            {sd('bold')}
          </label>
          <ColorField
            value={value.color ?? '#000000'}
            onChange={(v) => onChange({ ...value, color: v })}
          />
        </div>
      )}
    </div>
  )
}
