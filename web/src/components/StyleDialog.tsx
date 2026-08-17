import { useEffect, useMemo, useState } from 'react'
import { Check, Pipette, Plus, Save, Trash2, TriangleAlert, X,
  Paintbrush,
} from 'lucide-react'
import { deleteStyle, fetchStyles, saveStyle } from '@/lib/api'
import {
  extractFromManifest,
  extractPalette,
  planStyle,
  presetEntries,
  STYLE_ROLE_LABEL,
  STYLE_SCOPE_LABEL,
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
export function StyleDialog() {
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
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
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
      setError('先给样式起个名字')
      return
    }
    setBusy(true)
    try {
      const stored = await saveStyle({ ...draft, name: draft.name.trim() })
      setDraft(stored)
      setSaved(await fetchStyles())
      setError(null)
      useUiStore.getState().setStatus(`已保存样式「${stored.name}」`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
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
        title: `应用样式「${draft.name || '未命名'}」？`,
        body: `将覆盖 ${overwrites} 项已有的图内修改（${modKey('Z')} 可整体撤销）。`,
        confirmLabel: '应用',
      }))
    ) {
      return
    }
    applyStylePlan(plan, { ...draft, name: draft.name || '未命名样式' })
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
        title="论文样式"
        description="把字号、线宽、刻度、配色等排版规格存成命名样式，批量应用；只写图内修改，不改源文件"
        width={520}
        busy={busy}
        footer={
          <>
            <Button variant="outline" size="md" onClick={() => setOpen(false)}>
              关闭
            </Button>
            <Button
              variant="primary"
              size="md"
              disabled={!primaryManifest}
              title={primaryManifest ? undefined : '先在画布上选中一个已渲染的 可参数化面板'}
              onClick={extract}
            >
              <Pipette size={14} />
              从当前面板提取
            </Button>
          </>
        }
      >
        <p className="text-xs leading-relaxed text-ink-2">
          还没有保存的样式。选中一个已渲染的 可参数化面板，提取它的字号 / 线宽 / 刻度 / 配色作为起点。
        </p>
        {error && <p className="mt-2 text-xs text-danger">{error}</p>}
      </Dialog>
    )
  }

  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title="论文样式"
      description="把字号、线宽、刻度、配色等排版规格存成命名样式，批量应用到面板；只写图内修改，不改源文件"
      width={760}
      busy={busy}
      footer={
        <>
          <Button variant="outline" size="md" onClick={() => setOpen(false)}>
            关闭
          </Button>
          <Button variant="primary" size="md" disabled={!applicable} onClick={apply}>
            <Check size={14} />
            应用到{STYLE_SCOPE_LABEL[scope]}
          </Button>
        </>
      }
    >
      <div className="flex gap-3">
        {/* 左：已存样式 */}
        <div className="flex w-44 shrink-0 flex-col gap-1.5">
          <h3 className="text-xs font-medium uppercase tracking-[.06em] text-ink-3">
            已存样式
          </h3>
          <ul className="min-h-0 flex-1 overflow-y-auto rounded-sm border border-border">
            {saved.length === 0 && (
              <li>
                <EmptyState icon={Paintbrush} title="还没有保存的样式" />
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
                  aria-label={`删除样式 ${s.name}`}
                  onClick={async () => {
                    if (
                      !(await askConfirm({
                        title: `删除样式「${s.name}」？`,
                        body: '删除后无法找回（已应用到文档的修改不受影响）。',
                        confirmLabel: '删除',
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
            新建样式
          </Button>
        </div>

        {/* 中：样式内容 */}
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex items-center gap-1.5">
            <TextInput
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              placeholder="样式名称（如「AMFE 正文图」）"
              className="h-6 min-w-0 flex-1"
            />
            <Button
              variant="outline"
              size="sm"
              disabled={!primaryManifest}
              title={
                primaryManifest
                  ? `从「${primaryPanel?.name ?? primaryPanel?.fileId}」读取当前值`
                  : '选中一个已渲染的 可参数化面板后可提取'
              }
              onClick={extract}
            >
              <Pipette size={12} />
              从当前面板提取
            </Button>
            <Button variant="outline" size="sm" loading={busy} onClick={save}>
              <Save size={12} />
              保存
            </Button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto rounded-sm border border-border p-2">
            {entries.length === 0 && !draft.palette?.length ? (
              <p className="py-2 text-xs leading-relaxed text-ink-3">
                空样式。点「从当前面板提取」读取选中面板的字号 / 线宽 / 刻度 / 配色，
                删掉不想统一的项后保存。
              </p>
            ) : (
              <div className="flex flex-col gap-1">
                {entries.map((en) => (
                  <div key={`${en.role}.${en.prop}`} className="flex h-6 items-center gap-1.5">
                    <span className="w-16 shrink-0 truncate text-xs text-ink-3">
                      {STYLE_ROLE_LABEL[en.role] ?? en.role}
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
                      aria-label="移除此项"
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
                    <p className="mb-1 text-xs text-ink-3">
                      系列配色（按曲线 / 散点 / 柱形出现顺序循环）
                    </p>
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
                            aria-label="移除颜色"
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
              label="标注文字"
              value={draft.annotation}
              onChange={(v) => setDraft((d) => ({ ...d, annotation: v }))}
            />
            <TextStylePart
              label="序号标签 (a)(b)(c)"
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
                包含页面尺寸
                {draft.page ? `（${draft.page.w}×${draft.page.h} mm）` : ''}
              </span>
            </div>
          </div>
        </div>

        {/* 右：应用范围与预览 */}
        <div className="flex w-52 shrink-0 flex-col gap-2">
          <h3 className="text-xs font-medium uppercase tracking-[.06em] text-ink-3">
            应用范围
          </h3>
          <Segmented
            value={scope}
            onChange={setScope}
            className="w-full"
            items={[
              { value: 'panel', label: '面板', tip: STYLE_SCOPE_LABEL.panel },
              { value: 'selection', label: '选区', tip: STYLE_SCOPE_LABEL.selection },
              { value: 'sameScript', label: '同脚本', tip: STYLE_SCOPE_LABEL.sameScript },
              { value: 'document', label: '全文档', tip: STYLE_SCOPE_LABEL.document },
            ]}
          />
          <label className="flex items-center gap-1.5 text-xs text-ink-2">
            <Toggle checked={withAnnotations} onChange={setWithAnnotations} />
            含画布标注与序号标签
          </label>

          <div className="min-h-0 flex-1 overflow-y-auto rounded-sm border border-border p-2">
            <p className="mb-1 text-xs font-medium text-ink">将影响</p>
            {plan.panels.length === 0 && (
              <p className="text-xs leading-relaxed text-ink-3">
                {scope === 'panel' ? '先在画布上选中一个 可参数化面板' : '范围内没有 可参数化面板'}
              </p>
            )}
            <ul className="flex flex-col gap-1">
              {plan.panels.map((p) => (
                <li key={p.panel.id} className="text-xs leading-relaxed text-ink-2">
                  <span className="text-ink">{p.panel.name ?? p.panel.fileId}</span>
                  ：{p.patches.length} 项
                  {p.overwrites > 0 && (
                    <span className="text-danger">（覆盖 {p.overwrites} 项已有修改）</span>
                  )}
                  {p.unmappable.length > 0 && (
                    <span className="text-ink-3">
                      ；{p.unmappable.length} 项无法映射
                    </span>
                  )}
                </li>
              ))}
              {plan.unrendered.map((p: PanelObject) => (
                <li key={p.id} className="flex items-start gap-1 text-xs leading-relaxed text-ink-3">
                  <TriangleAlert size={11} className="mt-0.5 shrink-0" />
                  <span>{p.name ?? p.fileId}：尚未渲染，无法映射（先进入图内编辑渲染一次）</span>
                </li>
              ))}
              {withAnnotations && plan.annotationIds.length > 0 && draft.annotation && (
                <li className="text-xs text-ink-2">标注文字：{plan.annotationIds.length} 条</li>
              )}
              {withAnnotations && plan.subLabelIds.length > 0 && draft.subLabel && (
                <li className="text-xs text-ink-2">序号标签：{plan.subLabelIds.length} 个</li>
              )}
              {plan.page && (
                <li className="text-xs text-ink-2">
                  页面尺寸 → {plan.page.w}×{plan.page.h} mm
                </li>
              )}
            </ul>
            {plan.panels.some((p) => p.unmappable.length > 0) && (
              <details className="mt-1.5">
                <summary className="cursor-pointer text-xs text-ink-3 hover:text-ink">
                  无法映射的明细
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
  value,
  onChange,
}: {
  label: string
  value: { sizePt?: number; bold?: boolean; italic?: boolean; color?: string } | undefined
  onChange: (v: { sizePt?: number; bold?: boolean; italic?: boolean; color?: string } | undefined) => void
}) {
  return (
    <div className="mt-1.5 border-t border-border pt-1.5">
      <div className="flex h-6 items-center gap-1.5">
        <Toggle
          checked={!!value}
          onChange={(v) => onChange(v ? { sizePt: 9, bold: label.includes('序号'), color: '#000000' } : undefined)}
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
            粗体
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
