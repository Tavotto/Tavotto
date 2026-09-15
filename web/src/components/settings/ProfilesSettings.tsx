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
import { CircleCheck, Copy, Download, Ellipsis, FileSliders, Plus, RotateCcw, Trash2, Upload, X } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Badge } from '../ui/Badge'
import { msg, t as translate } from '@/i18n'
import type { ProfileKind, ProfileRecord } from '@/lib/api'
import {
  profileName,
  profileOriginLabel,
  profileTechnicalDetail,
  profileWarningText,
} from '@/lib/profileText'
import { bindingFor, resolveDocumentSpec, type SpecCatalogEntry } from '@/lib/specBinding'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { useProfileStore } from '@/store/profileStore'
import { askConfirm, useUiStore } from '@/store/uiStore'
import { Button, IconButton } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Menu, MenuItem } from '../ui/Menu'
import { Segmented } from '../ui/Segmented'
import { Select } from '../ui/Select'
import { NumberField, TextInput } from '../ui/Input'
import { Toggle } from '../ui/Toggle'
import {
  DiagnosticDisclosure,
  DiagnosticItem,
  InlineWarning,
  SettingRow,
  settingControlStyle,
  settingRowGrid,
} from './SettingRow'
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
  /** 这个阈值喂给哪条检查规则（规范页专用；只做标注，设置页不复述判据） */
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

/** 库里不超过这个数时是分段选择器，再多换 Select（宪法第五节：互斥取值超过四五档用 Select） */
const LIBRARY_AS_SEGMENTED = 4

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


/** 一个数值字段在**只读摘要**里长什么样。没设过时说「未设置」，不谎报一个数。 */
function formatValue(raw: unknown, unit?: string): string {
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return st('unset')
  return unit ? `${raw} ${unit}` : String(raw)
}

/** 一组字段：一条 type-section 小标题 + 若干 compact 行。分组只影响排版（审计 T41 / T42）。 */
function FieldGroup({ group, children }: { group: string; children: ReactNode }) {
  return (
    <div data-field-group={group} className="flex flex-col">
      <span className="type-section mb-1">{st(`group.${group}`)}</span>
      {children}
    </div>
  )
}

/**
 * 只读摘要里的一行：名字 + 值。
 *
 * **刻意不是一个 disabled 的输入框**：整页禁用输入看起来像"我的表单坏了"，
 * 而它其实是"这份是内置的、想改先复制一份"（审计 T41 / T42）。
 */
function SummaryRow({ label, value }: { label: string; value: string }) {
  // **与 `SettingRow` 同一份网格**（标题列弹性、控件列 `SETTING_CONTROL_WIDTH`）、
  // 同一档行高（compact 32px）：「摘要 ↔ 输入框」两种模式在同一位置来回切换，
  // 值与输入框从同一条竖线起排，差几个像素就是整列左右跳一下（`settingsDisclosure.test` 量它）
  return (
    <div
      data-summary-row
      style={settingControlStyle}
      className={cn('grid min-h-8 items-center gap-x-6 py-0.5 text-sm', settingRowGrid)}
    >
      <span className="min-w-0 truncate text-ink" title={label}>
        {label}
      </span>
      <span className="justify-self-end tabular-nums text-ink">{value}</span>
    </div>
  )
}

/**
 * 规范页顶部的四个关键数（Session 6）：最小字号 / 单栏宽 / 双栏宽 / 最低分辨率。
 * 这是「规范 = 图要满足什么」的第一眼——与样式页（图长什么样、先看示例图）在认知上
 * 分开。数字来自 `resolveDocumentSpec` 解析出的**本项目实际在用**的那份，不是清单里
 * 选中的那条；全部规则仍在下面的折叠区里。
 */
const KEY_RULES = ['minFont', 'singleWidth', 'doubleWidth', 'minDpi'] as const

function KeyRules({ profile }: { profile: Record<string, unknown> }) {
  return (
    <dl data-spec-key-rules className="flex flex-wrap gap-x-8 gap-y-2">
      {KEY_RULES.map((key) => {
        const f = SPEC_FIELDS.find((x) => x.labelKey === key)!
        return (
          <div key={key} className="flex min-w-24 flex-col">
            <dd className="text-lg font-medium tabular-nums text-ink">
              {formatValue(readPath(profile, f.path), f.unit)}
            </dd>
            <dt className="type-meta">{st(`field.${f.labelKey}`)}</dt>
          </div>
        )
      })}
    </dl>
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

  /**
   * 读屏与视觉都只有一个主动作（Design Constitution 第五节）：规范页里「本项目用
   * 这套规范」在选中那条还没被项目采用时是主动作；样式页 / 已采用的规范页里，
   * 只读那份的主动作是「复制一份再修改」，可编辑那份的是「保存」。
   */
  const bound = kind === 'spec' && !!selected && boundId === selected.id

  return (
    <>
      {/* 规范页顶部：本项目按哪套检查 + 四个关键数 + 全部规则（折叠）。
          项目里存的是**绑定 + 规则全文快照**（ADR 0029）。检查用的就是这几个数，
          全局清单里的同名规范改了也不影响它——这层关系在这里摊开，别让用户去
          导出面板里猜（审计 T41）。解析只有 `resolveDocumentSpec` 一份判据。 */}
      {resolved && (
        <section data-spec-binding className="flex flex-col gap-3">
          {/* 先说这是「项目在用的」，再说是哪一套（2026-09-13 审计 B35：只写名字 +
              「按内置默认」时，读不出下面库里正在浏览的那条与它是不是一回事） */}
          <span className="type-section">{st('binding.current')}</span>
          <div className="-mt-2 flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="type-title">
              {boundRecord
                ? profileName(boundRecord)
                : resolved.source === 'builtin'
                  ? st('binding.builtinDefault')
                  : (boundId ?? '')}
            </span>
            <span className="type-meta">{st(`binding.source.${resolved.source}`)}</span>
            {resolved.globalMissing && <span className="type-meta">{st('binding.globalMissing')}</span>}
            {resolved.updateAvailable && (
              <>
                <span className="type-caption">{st('binding.updateAvailable')}</span>
                <Button variant="secondary" size="sm" onClick={syncToGlobal}>
                  {st('binding.sync')}
                </Button>
              </>
            )}
          </div>
          <KeyRules profile={resolved.profile as unknown as Record<string, unknown>} />
          <DiagnosticDisclosure title={st('snapshotTitle')}>
            <p className="type-caption">{st('snapshotHint')}</p>
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
        </section>
      )}

      {/* 库收成一行（2026-09-15 打磨批次 B，L4）：两三份配置不值一整列——此前左边一列
          176px 只有两行，右边才是编辑器。Select 是「看 / 改哪一份」，新建 / 复制 / 导入 /
          导出收进 ⋯；本项目在用的那份在选项名后标出来。 */}
      <SettingRow label={st(`library.${kind}`)} controlId="profile-library">
        {loaded && records.length === 0 ? (
          <span className="type-meta">{st('empty')}</span>
        ) : records.length === 1 ? (
          /* 只有一份时没有可选的：写名字就够了，一格的分段选择器读作坏掉的控件 */
          <span className="type-body min-w-0 flex-1 truncate text-right" title={profileTechnicalDetail(records[0])}>
            {profileName(records[0])}
          </span>
        ) : records.length <= LIBRARY_AS_SEGMENTED ? (
          /* 二到四份是一组互斥的取值：分段选择器（宪法第五节）；本项目在用的那份带勾 */
          <Segmented
            value={selected?.id ?? null}
            onChange={(id) => setSelectedId(id)}
            ariaLabel={st(`library.${kind}`)}
            className="min-w-0 flex-1"
            items={records.map((r) => ({
              value: r.id,
              label: profileName(r),
              title: profileTechnicalDetail(r),
              icon:
                kind === 'spec' && boundId === r.id ? (
                  <CircleCheck size={ICON_SIZE.xs} aria-hidden className="text-ok" />
                ) : undefined,
            }))}
          />
        ) : (
          <Select
            value={selected?.id ?? ''}
            onChange={(id) => setSelectedId(id)}
            ariaLabel={st(`library.${kind}`)}
            title={selected ? profileTechnicalDetail(selected) : undefined}
            className="min-w-0 flex-1"
            options={records.map((r) => ({
              value: r.id,
              label: [
                profileName(r),
                r.built_in ? st('builtin') : null,
                kind === 'spec' && boundId === r.id ? st('inUse') : null,
              ]
                .filter(Boolean)
                .join(' · '),
            }))}
          />
        )}
        <Menu
          align="end"
          trigger={
            <IconButton label={st('more')} iconSize="sm">
              <Ellipsis size={ICON_SIZE.sm} aria-hidden />
            </IconButton>
          }
        >
          <MenuItem icon={Plus} onSelect={() => void create()} disabled={!records.length}>
            {st('new')}
          </MenuItem>
          <MenuItem icon={Copy} onSelect={() => void duplicate()} disabled={!selected}>
            {st('duplicate')}
          </MenuItem>
          <MenuItem icon={Download} onSelect={() => void exportOne()} disabled={!selected}>
            {st('export')}
          </MenuItem>
          <MenuItem icon={Upload} onSelect={() => fileRef.current?.click()}>
            {st('import')}
          </MenuItem>
        </Menu>
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
      </SettingRow>

      {/* 编辑区（Style 与 Spec 各是各的一套字段） */}
      <div className="flex min-w-0 flex-col gap-5">
          {!selected ? (
            /* 一条都没有时不摆一整套禁用的输入框，只给出口（审计 T42）。
               入口是**导入**不是「新建」：新建等于从选中的那条复制一份，清单空着
               的时候它没有可复制的来源，摆上去就是一颗按了没反应的按钮。
               还没加载完时什么都不画——那不是空，是"还不知道"。 */
            loaded && (
              <EmptyState
                icon={FileSliders}
                title={st('empty')}
                action={{ label: st('import'), onClick: () => fileRef.current?.click() }}
              />
            )
          ) : (
            <>
              {/* 样式页先看图（审计 T42）：字号 / 线宽 / 边框 / 字体族按**当前草稿**现算，
                  所以「把刻度字号调到 7」当场看得见。纯几何、不跑引擎。 */}
              {kind === 'style' && (
                <div className="flex flex-col gap-1.5">
                  <span className="type-section">{st('previewTitle')}</span>
                  <StyleSamplePreview data={draft} />
                </div>
              )}

              {/* 身份行：名字 + 来源 + 这一份的主动作。
                  只读那份：「这份改不了、想改按这里」是**状态 + 动作**，不是一段散文
                  ——只读这个事实是徽标，「复制出来的那份可以编辑」是就在旁边的按钮。 */}
              {editable ? (
                <SettingRow label={st('name')} controlId="profile-name">
                  <TextInput
                    id="profile-name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    aria-label={st('name')}
                  />
                </SettingRow>
              ) : (
                <div data-profile-readonly className="flex min-h-7 flex-wrap items-center gap-x-3 gap-y-2">
                  <span className="type-title min-w-0 truncate">{name}</span>
                  <Badge>{selected.built_in ? st('readOnlyBuiltinBadge') : st('readOnlyBadge')}</Badge>
                  {/* 这一份唯一的主动作（2026-09-15 打磨批次 B，L3）：正在用的规范不需要动作，
                      一行 meta 说明状态；没用的才给「本项目用这套规范」；「复制一份再修改」是
                      次级出口，ghost。此前黑钮与白钮并排，两颗都像主动作。 */}
                  <span className="ml-auto flex items-center gap-1.5">
                    {bound ? (
                      <span className="type-meta flex items-center gap-1 text-ok">
                        <CircleCheck size={ICON_SIZE.xs} aria-hidden />
                        {st('inUse')}
                      </span>
                    ) : (
                      kind === 'spec' && (
                        <Button variant="secondary" size="sm" onClick={useForProject}>
                          {st('useForProject')}
                        </Button>
                      )
                    )}
                    <Button variant="ghost" size="sm" onClick={duplicate} loading={busy}>
                      <Copy size={ICON_SIZE.sm} aria-hidden />
                      {st('duplicateToEdit')}
                    </Button>
                  </span>
                </div>
              )}

              <div className="flex flex-col gap-4">
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
                          />
                        )
                      }
                      return (
                        <SettingRow key={f.path} label={st(`field.${f.labelKey}`)} density="compact">
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
                            unit={f.unit}
                            fill
                            ariaLabel={st(`field.${f.labelKey}`)}
                            className="w-28"
                            onChange={(v) => setDraft((d) => (d ? writePath(d, f.path, v) : d))}
                          />
                          {set && (
                            <IconButton
                              iconSize="sm"
                              label={st('clearField', { field: st(`field.${f.labelKey}`) })}
                              onClick={() => setDraft((d) => (d ? clearPath(d, f.path) : d))}
                            >
                              <X size={ICON_SIZE.sm} aria-hidden className="text-ink-3" />
                            </IconButton>
                          )}
                        </SettingRow>
                      )
                    })}
                  </FieldGroup>
                ))}
              </div>

              {!!selected.warnings.length && (
                <ul className="flex flex-col gap-0.5">
                  {selected.warnings.map((w) => (
                    <li key={w} className="type-caption">
                      {profileWarningText(w)}
                    </li>
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

              {bound && (
                <SettingRow label={st('follow')} controlId="profile-follow">
                  <Toggle
                    id="profile-follow"
                    checked={doc.profile?.follow === true}
                    onChange={setFollow}
                    aria-label={st('follow')}
                  />
                </SettingRow>
              )}

              {conflict && (
                <InlineWarning tone="danger">{st('conflict', { name: conflict.display_name })}</InlineWarning>
              )}
              {error && !conflict && <InlineWarning tone="danger">{error.message}</InlineWarning>}

              {/* 「保存」是改这份配置，「应用 / 使用」是对当前图或当前项目做一件事
                  ——两件事分成两排，别挤在一行里（审计 T42）。只读的那份没有可
                  保存的东西，整排编辑动作就不出现，不摆一排禁用按钮；只读规范的
                  「本项目用这套规范」已经在身份行里。 */}
              {(kind === 'style' || editable) && (
                <div className="flex flex-wrap items-center gap-1.5">
                  {kind === 'spec' ? (
                    <Button variant="secondary" size="sm" onClick={useForProject}>
                      {st('useForProject')}
                    </Button>
                  ) : (
                    <Button variant="secondary" size="sm" onClick={applyToFigure}>
                      {st('applyToFigure')}
                    </Button>
                  )}
                </div>
              )}
              {editable && (
                <div className="flex flex-wrap items-center gap-1.5">
                  <Button variant="primary" size="sm" disabled={!dirty} loading={busy} onClick={save}>
                    {st('save')}
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={!selected.derived_from}
                    onClick={restore}
                    title={selected.derived_from ? undefined : st('restoreNeedsOrigin')}
                  >
                    <RotateCcw size={ICON_SIZE.sm} aria-hidden />
                    {st('restore')}
                  </Button>
                  <Button variant="danger" size="sm" onClick={remove}>
                    <Trash2 size={ICON_SIZE.sm} aria-hidden />
                    {st('delete')}
                  </Button>
                </div>
              )}
            </>
          )}
      </div>
    </>
  )
}
