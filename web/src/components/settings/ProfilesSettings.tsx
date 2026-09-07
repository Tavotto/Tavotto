/**
 * 「样式」与「规范」两个设置分区共用的骨架（Session 10，ADR 0029；Session 19
 * 起按 `kind` 分成两页，ADR 0038）。
 *
 * 两件事**绝不放进同一张表单**：
 *
 *     样式 Style  —— 图长什么样；应用到图 = 一次可撤销的文档修改
 *     规范 Spec   —— 图要满足什么；只用于检查，**永远不改图**
 *
 * 两者共用列表与增删改复制的骨架，编辑区各是各的——混在一起改的话，
 * 「我只是想把字号调大」会顺手把验收口径也放宽，而用户不会知道。
 * 规范页顶部多一行「本项目现在按哪套检查、用的是快照还是全局」——项目里存的
 * 是绑定 + 规则快照（ADR 0029），这层关系在这里说清，不在导出面板里猜。
 * 内部 id / 版本号只在「详情」折叠区里出现（`profileText.ts` 的纪律）。
 *
 * 磁盘一律走 `store/profileStore` → `/api/profiles/*` → `engine/profilestore.py`。
 * 这个组件里没有一行 fetch，也没有任何磁盘格式的知识。
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Copy, Download, FileSliders, Plus, RotateCcw, Trash2, Upload, X } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { msg, t as translate } from '@/i18n'
import type { ProfileKind, ProfileRecord } from '@/lib/api'
import {
  profileName,
  profileOriginLabel,
  profileTechnicalDetail,
  profileWarningText,
} from '@/lib/profileText'
import { severityOf, type PublicationProfile } from '@/lib/profile'
import { ruleExpectation, severityLabel } from '@/lib/validationText'
import { bindingFor, resolveDocumentSpec, type SpecCatalogEntry } from '@/lib/specBinding'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { useProfileStore } from '@/store/profileStore'
import { askConfirm, useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { NumberField, TextInput } from '../ui/Input'
import { Toggle } from '../ui/Toggle'
import { DiagnosticDisclosure, DiagnosticItem, SettingRow, SettingSection } from './SettingRow'
import { StyleSamplePreview } from './StyleSamplePreview'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`profiles.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/* -------------------------------------------------------------------------- */
/*  可编辑字段表                                                                */
/*                                                                            */
/*  声明式而不是每个字段手写一行：加一条规则时只在表里加一行，**而漏改的表现是   */
/*  "设置里改了、检查还按老数字"** —— 那种 bug 没有任何界面信号。               */
/* -------------------------------------------------------------------------- */
interface NumField {
  /** 点分路径，如 `widths_mm.single` */
  path: string
  labelKey: string
  min: number
  max: number
  step: number
  unit?: string
  /** 归到哪一组（`profiles.group.*`）。分组只影响排版，写进磁盘的内容一个字不变 */
  group: string
  /**
   * 这个阈值喂给哪条检查规则（规范页专用）。有它才说得出「填 8 的时候检查是
   * ≥ 8 还是 大于 8」——**边界的包含性只问 `validationText.ruleExpectation`**，
   * 设置页不许照着求值器的判据再抄一遍（审计 T41）。
   */
  rule?: string
}

const SPEC_FIELDS: NumField[] = [
  { path: 'min_effective_font_size_pt', labelKey: 'minFont', min: 1, max: 72, step: 0.5, unit: 'pt', group: 'fonts', rule: 'font-too-small' },
  { path: 'absolute_min_font_size_pt', labelKey: 'floorFont', min: 0, max: 72, step: 0.5, unit: 'pt', group: 'fonts', rule: 'font-below-absolute-floor' },
  { path: 'default_font_size_pt', labelKey: 'defaultFont', min: 1, max: 72, step: 0.5, unit: 'pt', group: 'fonts' },
  { path: 'max_font_size_pt', labelKey: 'maxFont', min: 1, max: 200, step: 1, unit: 'pt', group: 'fonts', rule: 'font-too-large' },
  { path: 'widths_mm.single', labelKey: 'singleWidth', min: 10, max: 1000, step: 1, unit: 'mm', group: 'page' },
  { path: 'widths_mm.double', labelKey: 'doubleWidth', min: 10, max: 1000, step: 1, unit: 'mm', group: 'page' },
  { path: 'widths_mm.tolerance_mm', labelKey: 'widthTolerance', min: 0, max: 50, step: 0.1, unit: 'mm', group: 'page' },
  { path: 'min_raster_dpi', labelKey: 'minDpi', min: 1, max: 4800, step: 50, unit: 'ppi', group: 'raster', rule: 'raster-dpi' },
  {
    path: 'preferred_formats.export_dpi_default',
    labelKey: 'exportDpi',
    min: 1,
    max: 4800,
    step: 50,
    unit: 'ppi',
    group: 'raster',
  },
]

/** 样式里最常改的那几项。角色 → prop 的含义见 `lib/stylePresets.STYLE_ROLE_PROPS`。 */
const STYLE_FIELDS: NumField[] = [
  { path: 'element.text.fontsize', labelKey: 'baseFont', min: 3, max: 72, step: 0.5, unit: 'pt', group: 'text' },
  { path: 'element.title.fontsize', labelKey: 'titleFont', min: 3, max: 72, step: 0.5, unit: 'pt', group: 'text' },
  {
    path: 'element.axis_label.fontsize',
    labelKey: 'axisFont',
    min: 3,
    max: 72,
    step: 0.5,
    unit: 'pt',
    group: 'text',
  },
  { path: 'element.legend.fontsize', labelKey: 'legendFont', min: 3, max: 72, step: 0.5, unit: 'pt', group: 'text' },
  { path: 'annotation.sizePt', labelKey: 'annotationFont', min: 3, max: 72, step: 0.5, unit: 'pt', group: 'text' },
  { path: 'element.ticks.fontsize', labelKey: 'tickFont', min: 3, max: 72, step: 0.5, unit: 'pt', group: 'ticks' },
  { path: 'element.line.linewidth', labelKey: 'lineWidth', min: 0.1, max: 10, step: 0.05, unit: 'pt', group: 'lines' },
  {
    path: 'element.axes.spine_linewidth',
    labelKey: 'spineWidth',
    min: 0.1,
    max: 10,
    step: 0.05,
    unit: 'pt',
    group: 'lines',
  },
]

/** 分组的显示顺序（表里出现的顺序不算数：加一条字段不该悄悄换掉版面）。 */
const GROUP_ORDER = ['fonts', 'page', 'raster', 'text', 'ticks', 'lines']

/** 按 `group` 归并，顺序取 `GROUP_ORDER`。 */
function groupFields(fields: NumField[]): { group: string; fields: NumField[] }[] {
  return GROUP_ORDER.map((group) => ({ group, fields: fields.filter((f) => f.group === group) }))
    .filter((g) => g.fields.length > 0)
}

function readPath(obj: Record<string, unknown>, path: string): unknown {
  return path.split('.').reduce<unknown>(
    (acc, key) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[key] : undefined),
    obj,
  )
}

/**
 * 写一个点分路径，**返回新对象**（不改入参）。路径上缺的层补成空对象；
 * 撞上非对象（用户导入的怪东西）就整段替换，不静默丢掉这次修改。
 */
function writePath(
  obj: Record<string, unknown>,
  path: string,
  value: unknown,
): Record<string, unknown> {
  const [head, ...rest] = path.split('.')
  const next = { ...obj }
  if (!rest.length) {
    next[head] = value
    return next
  }
  const child = next[head]
  next[head] = writePath(
    child && typeof child === 'object' && !Array.isArray(child)
      ? (child as Record<string, unknown>)
      : {},
    rest.join('.'),
    value,
  )
  return next
}

/** 把一个点分路径整段删掉（回到「这份配置没管这一项」那一档）。 */
function clearPath(obj: Record<string, unknown>, path: string): Record<string, unknown> {
  const [head, ...rest] = path.split('.')
  const next = { ...obj }
  if (!rest.length) {
    delete next[head]
    return next
  }
  const child = next[head]
  if (!child || typeof child !== 'object' || Array.isArray(child)) return next
  const pruned = clearPath(child as Record<string, unknown>, rest.join('.'))
  if (Object.keys(pruned).length) next[head] = pruned
  else delete next[head]
  return next
}

/**
 * 只读摘要那一列的宽度。**与 `SettingRow` 的默认标签列同值**——两种模式在同一
 * 个位置来回切换，差几个像素就是整列左右跳一下。
 */
const SUMMARY_LABEL_WIDTH = 160

/** 一个数值字段在**只读摘要**里长什么样。没设过时说「未设置」，不谎报一个数。 */
function formatValue(raw: unknown, unit?: string): string {
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return st('unset')
  return unit ? `${raw} ${unit}` : String(raw)
}

/** 一组字段：一条极淡的小标题 + 若干行。分组只影响排版（审计 T41 / T42）。 */
function FieldGroup({ group, children }: { group: string; children: ReactNode }) {
  return (
    <div data-field-group={group} className="flex flex-col gap-1">
      <span className="text-[11px] font-medium uppercase tracking-[.06em] text-ink-3">
        {st(`group.${group}`)}
      </span>
      {children}
    </div>
  )
}

/**
 * 只读摘要里的一行：名字 + 值 +（规范页）这条阈值会怎么判。
 *
 * **刻意不是一个 disabled 的输入框**：整页禁用输入看起来像"我的表单坏了"，
 * 而它其实是"这份是内置的、想改先复制一份"（审计 T41 / T42）。
 */
function SummaryRow({
  label,
  value,
  note,
}: {
  label: string
  value: string
  note?: string | null
}) {
  return (
    <div className="flex min-h-6 items-baseline gap-2 text-xs">
      <span style={{ width: SUMMARY_LABEL_WIDTH }} className="shrink-0 truncate text-ink-2" title={label}>
        {label}
      </span>
      <span className="shrink-0 tabular-nums text-ink">{value}</span>
      {note && (
        <span className="min-w-0 flex-1 truncate text-right text-ink-3" title={note}>
          {note}
        </span>
      )}
    </div>
  )
}

export function ProfilesSettings({ kind }: { kind: ProfileKind }) {
  useTranslation('dialogs')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement | null>(null)

  const records = useProfileStore((s) => (kind === 'style' ? s.styles : s.specs))
  const error = useProfileStore((s) => s.error)
  const conflict = useProfileStore((s) => s.conflict)
  const loaded = useProfileStore((s) => s.loaded)

  useEffect(() => {
    void useProfileStore.getState().load()
  }, [])
  // 同一个组件实例在两个分区之间复用时，选中项不能带到另一类清单上
  useEffect(() => {
    setSelectedId(null)
  }, [kind])

  const selected = useMemo(
    () => records.find((r) => r.id === selectedId) ?? records[0] ?? null,
    [records, selectedId],
  )

  // 选中项换了就重置草稿。**不做 merge**：把上一条的编辑内容带到下一条上，
  // 是那种"我明明没改它"的 bug 里最难查的一种。
  useEffect(() => {
    setDraft(selected ? structuredClone(selected.data) : null)
    setName(selected ? profileName(selected) : '')
  }, [selected?.id, selected?.revision]) // eslint-disable-line react-hooks/exhaustive-deps

  const editable = !!selected && !selected.read_only
  // 空名字不算「改好了」：让它可保存的话，保存会静默跳过改名那一步
  // （后端拒绝空名），用户看到的是"点了保存、名字没变、也没报错"。
  const dirty =
    !!selected &&
    !!draft &&
    !!name.trim() &&
    (JSON.stringify(draft) !== JSON.stringify(selected.data) ||
      name.trim() !== profileName(selected))

  const fields = kind === 'spec' ? SPEC_FIELDS : STYLE_FIELDS
  const grouped = useMemo(() => groupFields(fields), [fields])

  /**
   * 「填了这个数之后，检查会怎么判」（审计 T41）。
   *
   * 两件事都来自**这份规范自己**：边界的包含性问 `validationText.ruleExpectation`
   * （它读的是措辞层那张规则表，与问题面板同一份），等级问 profile 自己的
   * `severity` 表。设置页一个阈值、一个符号都不硬写。
   */
  const ruleNote = (f: NumField): string | null => {
    if (kind !== 'spec' || !f.rule || !draft) return null
    const value = readPath(draft, f.path)
    if (typeof value !== 'number' || !Number.isFinite(value)) return null
    const expect = ruleExpectation(f.rule, value)
    if (!expect) return null
    return st('ruleLine', {
      expect,
      severity: severityLabel(severityOf(draft as unknown as PublicationProfile, f.rule)),
    })
  }

  /** 一次会写盘的操作：期间禁用按钮，无论成败都恢复。 */
  const withBusy = async <T,>(op: () => Promise<T>): Promise<T> => {
    setBusy(true)
    try {
      return await op()
    } finally {
      setBusy(false)
    }
  }

  const create = () =>
    withBusy(async () => {
      // 新建 = 从当前选中的那条复制（多半就是内置默认）。**空白模板没有意义**：
      // 一份什么规则都没有的规范会把所有检查静默放行。
      const base = selected ?? records[0]
      if (!base) return
      const rec = await useProfileStore.getState().duplicate(kind, base.id, st('newName'))
      if (rec) setSelectedId(rec.id)
    })

  const duplicate = () =>
    withBusy(async () => {
      if (!selected) return
      // 名字**在前端拼**：后端的 `display_name` 对内置来说是中文兜底
      // （真正的名字是 `name_key` 查出来的），让后端拼就会在英文界面里
      // 造出一条叫「默认样式 副本」的配置。
      const rec = await useProfileStore
        .getState()
        .duplicate(kind, selected.id, st('copyOf', { name: profileName(selected) }))
      if (rec) setSelectedId(rec.id)
    })

  const save = () =>
    withBusy(async () => {
      if (!selected || !draft) return
      const api = useProfileStore.getState()
      const saved = await api.save(kind, selected.id, draft)
      if (!saved) return
      const trimmed = name.trim()
      if (trimmed && trimmed !== profileName(selected)) {
        await api.rename(kind, selected.id, trimmed)
      }
      useUiStore.getState().setStatus(msg('profiles.saved', { name: trimmed }, 'dialogs'))
    })

  const remove = () =>
    withBusy(async () => {
      if (!selected || selected.read_only) return
      const ok = await askConfirm({
        title: msg('profiles.deleteTitle', { name: profileName(selected) }, 'dialogs'),
        body: msg('profiles.deleteBody', undefined, 'dialogs'),
        confirmLabel: msg('actions.delete', undefined, 'common'),
        danger: true,
      })
      if (!ok) return
      if (await useProfileStore.getState().remove(kind, selected.id)) setSelectedId(null)
    })

  const restore = () =>
    withBusy(async () => {
      if (!selected || selected.read_only) return
      const ok = await askConfirm({
        title: msg('profiles.restoreTitle', { name: profileName(selected) }, 'dialogs'),
        body: msg('profiles.restoreBody', undefined, 'dialogs'),
        confirmLabel: msg('profiles.restoreConfirm', undefined, 'dialogs'),
      })
      if (ok) await useProfileStore.getState().restoreDefaults(kind, selected.id)
    })

  const exportOne = () =>
    withBusy(async () => {
      if (!selected) return
      const text = await useProfileStore.getState().exportOne(kind, selected.id)
      if (!text) return
      // 与「导出诊断包」同一条路径（`PrivacyAboutSettings.downloadDiagnostics`）：
      // 浏览器里能给的只有"下载一个文件"，桌面端也走这条。
      const blob = new Blob([text], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      try {
        const a = document.createElement('a')
        a.href = url
        a.download = `${selected.id}.tavotto-profile.json`
        a.click()
      } finally {
        // 不撤销就是一条挂到刷新为止的引用
        URL.revokeObjectURL(url)
      }
    })

  const importOne = (file: File) =>
    withBusy(async () => {
      const text = await file.text()
      const rec = await useProfileStore.getState().importOne(kind, text)
      if (rec) setSelectedId(rec.id)
    })

  /* -------------------- 与当前项目 / 当前图的两个明确出口 ------------------ */
  const doc = useDocumentStore((s) => s.doc)
  const commit = useDocumentStore((s) => s.commit)

  const asCatalogEntry = (r: ProfileRecord): SpecCatalogEntry => ({
    id: r.id,
    display_name: r.display_name,
    name_key: r.name_key,
    version: r.version,
    built_in: r.built_in,
    data: r.data,
  })

  /** 「为当前项目选择规范」：写一条带快照的绑定进文档（可撤销、正确 dirty）。 */
  const useForProject = () => {
    if (!selected || kind !== 'spec') return
    commit(msg('history.setPublicationProfile', undefined, 'workspace'), (d) => {
      d.profile = bindingFor(asCatalogEntry(selected), {
        journal: doc.profile?.journal,
        // 跟随的表态跟着项目走：换一套规范不该把它悄悄关掉
        follow: doc.profile?.follow,
      })
    })
    useUiStore
      .getState()
      .setStatus(msg('profiles.usedForProject', { name: profileName(selected) }, 'dialogs'))
  }

  /**
   * 「跟随这套规范的更新」。默认**不跟随**（项目结果稳定，ADR 0029）；
   * 打开它等于用户明确说"以后别问我，直接按最新的算"——所以它同样是一次
   * 文档修改（可撤销、正确 dirty），而不是一个本机偏好。
   */
  const setFollow = (on: boolean) => {
    if (!selected || kind !== 'spec') return
    commit(msg('history.setPublicationProfile', undefined, 'workspace'), (d) => {
      d.profile = bindingFor(asCatalogEntry(selected), {
        journal: doc.profile?.journal,
        follow: on,
      })
    })
  }

  /**
   * 「应用样式到当前图」：交给样式对话框——那里才看得见影响范围与冲突。
   *
   * 设置**不关**：样式对话框是压在它上面的一步（`uiStore.dialogStack`），关掉
   * 就回到这里、焦点回到这颗按钮；并且带着此刻选中的这一条——「空样式」与
   * 刚才在设置里点的那条是什么关系，不该让用户猜（审计 T35）。
   */
  const applyToFigure = () => {
    if (kind !== 'style') return
    useUiStore.getState().setStylesOpen(true, { presetId: selected?.id ?? null })
  }

  const boundId = doc.profile?.id

  /**
   * 规范页顶部那一行：本项目按哪套检查、用的是快照还是全局。判据只有
   * `lib/specBinding.resolveDocumentSpec` 一份（导出面板用的同一个）。
   */
  const specCatalog = useMemo<SpecCatalogEntry[]>(
    () => (kind === 'spec' ? records.map(asCatalogEntry) : []),
    [kind, records], // eslint-disable-line react-hooks/exhaustive-deps
  )
  const resolved = useMemo(
    () => (kind === 'spec' ? resolveDocumentSpec(doc.profile, specCatalog) : null),
    [kind, doc.profile, specCatalog],
  )
  const boundRecord = boundId ? records.find((r) => r.id === boundId) : undefined
  const syncToGlobal = () => {
    if (!boundRecord) return
    commit(msg('history.setPublicationProfile', undefined, 'workspace'), (d) => {
      d.profile = bindingFor(asCatalogEntry(boundRecord), {
        journal: doc.profile?.journal,
        follow: doc.profile?.follow,
      })
    })
  }

  return (
    <SettingSection>
      <p className="text-xs leading-relaxed text-ink-3">
        {kind === 'style' ? st('kind.styleHint') : st('kind.specHint')}
      </p>
      {resolved && (
        <div data-spec-binding className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
          <span className="text-ink-2">
            {st('binding.current', {
              name: boundRecord
                ? profileName(boundRecord)
                : resolved.source === 'builtin'
                  ? st('binding.builtinDefault')
                  : (boundId ?? ''),
            })}
          </span>
          <span className="text-ink-3">{st(`binding.source.${resolved.source}`)}</span>
          {resolved.globalMissing && <span className="text-ink-3">{st('binding.globalMissing')}</span>}
          {resolved.updateAvailable && (
            <>
              <span className="text-ink-2">{st('binding.updateAvailable')}</span>
              <Button variant="outline" size="sm" onClick={syncToGlobal}>
                {st('binding.sync')}
              </Button>
            </>
          )}
        </div>
      )}
      {/* 项目里存的是**绑定 + 规则全文快照**（ADR 0029）。检查用的就是下面这几个
          数，全局清单里的同名规范改了也不影响它——这层关系在这里摊开，别让用户
          去导出面板里猜（审计 T41）。解析只有 `resolveDocumentSpec` 一份判据。 */}
      {resolved && (
        <DiagnosticDisclosure title={st('snapshotTitle')}>
          <p className="text-xs leading-relaxed text-ink-3">{st('snapshotHint')}</p>
          {SPEC_FIELDS.map((f) => (
            <DiagnosticItem
              key={f.path}
              name={st(`field.${f.labelKey}`)}
              value={formatValue(
                readPath(resolved.profile as unknown as Record<string, unknown>, f.path),
                f.unit,
              )}
            />
          ))}
        </DiagnosticDisclosure>
      )}

      <div className="flex gap-3">
        {/* 左：清单 */}
        <div className="flex w-48 shrink-0 flex-col gap-1.5">
          <ul className="max-h-64 min-h-0 flex-1 overflow-y-auto rounded-sm border border-border">
            {loaded && records.length === 0 && (
              <li>
                <EmptyState icon={FileSliders} title={st('empty')} />
              </li>
            )}
            {records.map((r, i) => (
              <li key={r.id} className={cn(i > 0 && 'border-t border-border')}>
                <button
                  onClick={() => setSelectedId(r.id)}
                  aria-current={selected?.id === r.id || undefined}
                  className={cn(
                    'flex h-7 w-full min-w-0 items-center gap-1.5 px-2 text-left text-xs',
                    selected?.id === r.id
                      ? 'bg-accent-subtle text-accent'
                      : 'text-ink hover:bg-ink/[.04]',
                  )}
                  title={profileTechnicalDetail(r)}
                >
                  <span className="min-w-0 flex-1 truncate">{profileName(r)}</span>
                  {r.built_in && <span className="shrink-0 text-[10px] text-ink-3">{st('builtin')}</span>}
                  {kind === 'spec' && boundId === r.id && (
                    <span className="shrink-0 text-[10px] text-accent">{st('inUse')}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
          <div className="flex gap-1">
            <Button variant="outline" size="sm" onClick={create} loading={busy}>
              <Plus size={ICON_SIZE.sm} />
              {st('new')}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={duplicate}
              disabled={!selected}
              aria-label={st('duplicate')}
              title={st('duplicate')}
            >
              <Copy size={ICON_SIZE.sm} />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={exportOne}
              disabled={!selected}
              aria-label={st('export')}
              title={st('export')}
            >
              <Download size={ICON_SIZE.sm} />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => fileRef.current?.click()}
              aria-label={st('import')}
              title={st('import')}
            >
              <Upload size={ICON_SIZE.sm} />
            </Button>
            <input
              ref={fileRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              aria-hidden="true"
              tabIndex={-1}
              onChange={(e) => {
                const f = e.target.files?.[0]
                e.target.value = ''
                if (f) void importOne(f)
              }}
            />
          </div>
        </div>

        {/* 右：编辑区（Style 与 Spec 各是各的一套字段） */}
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          {!selected ? (
            /* 一条都没有时不摆一整套禁用的输入框，只给出口（审计 T42）。
               入口是**导入**不是「新建」：新建等于从选中的那条复制一份，清单空着
               的时候它没有可复制的来源，摆上去就是一颗按了没反应的按钮。
               还没加载完时什么都不画——那不是空，是"还不知道"。 */
            loaded && (
              <EmptyState
                icon={FileSliders}
                title={st('empty')}
                hint={st('emptyHint')}
                action={{ label: st('import'), onClick: () => fileRef.current?.click() }}
              />
            )
          ) : (
            <>
              {/* 样式页的示例图（审计 T42）：字号 / 线宽 / 边框 / 字体族按**当前
                  草稿**现算，所以「把刻度字号调到 7」当场看得见。纯几何、不跑引擎。 */}
              {kind === 'style' && (
                <div className="flex flex-col gap-1">
                  <span className="text-[11px] font-medium uppercase tracking-[.06em] text-ink-3">
                    {st('previewTitle')}
                  </span>
                  <StyleSamplePreview data={draft} />
                </div>
              )}

              {editable ? (
                <SettingRow label={st('name')} controlId="profile-name">
                  <TextInput
                    id="profile-name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    aria-label={st('name')}
                    className="h-6 w-48"
                  />
                </SettingRow>
              ) : (
                <>
                  <SummaryRow label={st('name')} value={name} />
                  {/*
                    「这份改不了、想改按这里」是**状态 + 动作**，不是一段散文
                    （审计统一规则第一条：先改善控件，仍有必要才补文字）。原先
                    这里是一句 37 字的解释，和分区顶上那句 43 字的说明叠成两段
                    文字墙——用户得读完整句才知道下一步按哪儿，而流程 D 的
                    「一个分区最多一段长解释」就是这么被顶破的。
                    信息一个字没丢：只读这个事实变成常驻徽标，「复制出来的那份
                    可以编辑」变成一颗就在旁边的按钮（同一个 `duplicate` 动作，
                    原先摆在所有字段下面，要滚很远才看得见）。
                  */}
                  <div
                    data-profile-readonly
                    className="flex min-h-6 flex-wrap items-center gap-2 text-xs"
                  >
                    <span className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-[11px] text-ink-2">
                      {selected.built_in ? st('readOnlyBuiltinBadge') : st('readOnlyBadge')}
                    </span>
                    <Button variant="outline" size="sm" onClick={duplicate} loading={busy}>
                      <Copy size={ICON_SIZE.xs} />
                      {st('duplicateToEdit')}
                    </Button>
                  </div>
                </>
              )}

              {grouped.map(({ group, fields: groupFields }) => (
                <FieldGroup key={group} group={group}>
                  {groupFields.map((f) => {
                    const raw = readPath(draft ?? {}, f.path)
                    const set = typeof raw === 'number' && Number.isFinite(raw)
                    if (!editable) {
                      return (
                        <SummaryRow
                          key={f.path}
                          label={st(`field.${f.labelKey}`)}
                          value={formatValue(raw, f.unit)}
                          note={ruleNote(f)}
                        />
                      )
                    }
                    return (
                      <SettingRow
                        key={f.path}
                        label={st(`field.${f.labelKey}`)}
                        status={ruleNote(f)}
                      >
                        {/* **「这份配置没管这一项」是独立一档**，不是"等于某个数"。
                            `mixed` 让输入框留空而不是谎报一个值；旁边的 × 是回到
                            那一档的唯一出口（否则设过一次就再也撤不回来）。 */}
                        <NumberField
                          value={set ? (raw as number) : f.min}
                          mixed={!set}
                          min={f.min}
                          max={f.max}
                          step={f.step}
                          precision={f.step < 1 ? 2 : 0}
                          suffix={f.unit}
                          ariaLabel={st(`field.${f.labelKey}`)}
                          className="w-28"
                          onChange={(v) => setDraft((d) => (d ? writePath(d, f.path, v) : d))}
                        />
                        {set && (
                          <Button
                            size="icon-sm"
                            className="h-5 w-5"
                            aria-label={st('clearField', { field: st(`field.${f.labelKey}`) })}
                            onClick={() => setDraft((d) => (d ? clearPath(d, f.path) : d))}
                          >
                            <X size={ICON_SIZE.xs} className="text-ink-3" />
                          </Button>
                        )}
                      </SettingRow>
                    )
                  })}
                </FieldGroup>
              ))}

              {!!selected.warnings.length && (
                <ul className="flex flex-col gap-0.5 text-xs text-ink-3">
                  {selected.warnings.map((w) => (
                    <li key={w}>{profileWarningText(w)}</li>
                  ))}
                </ul>
              )}

              {/* 内部 id / 版本 / 修订号只在这里出现（profileText.ts 的纪律） */}
              <DiagnosticDisclosure title={st('details')}>
                <DiagnosticItem name={st('detail.id')} value={selected.id} />
                <DiagnosticItem name={st('detail.version')} value={selected.version || '—'} />
                <DiagnosticItem name={st('detail.revision')} value={String(selected.revision)} />
                <DiagnosticItem name={st('detail.origin')} value={profileOriginLabel(selected)} />
              </DiagnosticDisclosure>

              {kind === 'spec' && boundId === selected.id && (
                <SettingRow
                  label={st('follow')}
                  description={st('followDesc')}
                  controlId="profile-follow"
                >
                  <Toggle
                    id="profile-follow"
                    checked={doc.profile?.follow === true}
                    onChange={setFollow}
                    aria-label={st('follow')}
                  />
                </SettingRow>
              )}

              {conflict && (
                <p className="text-xs text-danger">
                  {st('conflict', { name: conflict.display_name })}
                </p>
              )}
              {error && !conflict && <p className="text-xs text-danger">{error.message}</p>}

              {/* 「保存」是改这份配置，「应用 / 使用」是对当前图或当前项目做一件事
                  ——两件事分成两排，别挤在一行里（审计 T42）。只读的那份没有可
                  保存的东西，整排编辑动作就不出现，不摆一排禁用按钮。 */}
              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                {kind === 'spec' ? (
                  <Button variant="outline" size="sm" onClick={useForProject}>
                    {st('useForProject')}
                  </Button>
                ) : (
                  <Button variant="outline" size="sm" onClick={applyToFigure}>
                    {st('applyToFigure')}
                  </Button>
                )}
              </div>
              {editable && (
                <div className="flex flex-wrap items-center gap-1.5">
                  <Button
                    variant="primary"
                    size="sm"
                    disabled={!dirty}
                    loading={busy}
                    onClick={save}
                  >
                    {st('save')}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!selected.derived_from}
                    onClick={restore}
                    title={selected.derived_from ? undefined : st('restoreNeedsOrigin')}
                  >
                    <RotateCcw size={ICON_SIZE.sm} />
                    {st('restore')}
                  </Button>
                  <Button variant="outline" size="sm" onClick={remove}>
                    <Trash2 size={ICON_SIZE.sm} className="text-danger" />
                    {st('delete')}
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </SettingSection>
  )
}
