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
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Copy, Download, FileSliders, Plus, RotateCcw, Trash2, Upload, X } from 'lucide-react'
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
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { NumberField, TextInput } from '../ui/Input'
import { Toggle } from '../ui/Toggle'
import { DiagnosticDisclosure, DiagnosticItem, SettingRow, SettingSection } from './SettingRow'

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
}

const SPEC_FIELDS: NumField[] = [
  { path: 'min_effective_font_size_pt', labelKey: 'minFont', min: 1, max: 72, step: 0.5, unit: 'pt' },
  { path: 'absolute_min_font_size_pt', labelKey: 'floorFont', min: 0, max: 72, step: 0.5, unit: 'pt' },
  { path: 'default_font_size_pt', labelKey: 'defaultFont', min: 1, max: 72, step: 0.5, unit: 'pt' },
  { path: 'max_font_size_pt', labelKey: 'maxFont', min: 1, max: 200, step: 1, unit: 'pt' },
  { path: 'widths_mm.single', labelKey: 'singleWidth', min: 10, max: 1000, step: 1, unit: 'mm' },
  { path: 'widths_mm.double', labelKey: 'doubleWidth', min: 10, max: 1000, step: 1, unit: 'mm' },
  { path: 'widths_mm.tolerance_mm', labelKey: 'widthTolerance', min: 0, max: 50, step: 0.1, unit: 'mm' },
  { path: 'min_raster_dpi', labelKey: 'minDpi', min: 1, max: 4800, step: 50 },
  {
    path: 'preferred_formats.export_dpi_default',
    labelKey: 'exportDpi',
    min: 1,
    max: 4800,
    step: 50,
  },
]

/** 样式里最常改的那几项。角色 → prop 的含义见 `lib/stylePresets.STYLE_ROLE_PROPS`。 */
const STYLE_FIELDS: NumField[] = [
  { path: 'element.text.fontsize', labelKey: 'baseFont', min: 3, max: 72, step: 0.5, unit: 'pt' },
  { path: 'element.title.fontsize', labelKey: 'titleFont', min: 3, max: 72, step: 0.5, unit: 'pt' },
  {
    path: 'element.axis_label.fontsize',
    labelKey: 'axisFont',
    min: 3,
    max: 72,
    step: 0.5,
    unit: 'pt',
  },
  { path: 'element.ticks.fontsize', labelKey: 'tickFont', min: 3, max: 72, step: 0.5, unit: 'pt' },
  { path: 'element.legend.fontsize', labelKey: 'legendFont', min: 3, max: 72, step: 0.5, unit: 'pt' },
  { path: 'element.line.linewidth', labelKey: 'lineWidth', min: 0.1, max: 10, step: 0.05, unit: 'pt' },
  {
    path: 'element.axes.spine_linewidth',
    labelKey: 'spineWidth',
    min: 0.1,
    max: 10,
    step: 0.05,
    unit: 'pt',
  },
  { path: 'annotation.sizePt', labelKey: 'annotationFont', min: 3, max: 72, step: 0.5, unit: 'pt' },
]

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

  /** 「应用样式到当前图」：交给样式对话框——那里才看得见影响范围与冲突。 */
  const applyToFigure = () => {
    if (kind !== 'style') return
    useUiStore.getState().setSettingsOpen(false)
    useUiStore.getState().setStylesOpen(true)
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
              <Plus size={12} />
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
              <Copy size={12} />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={exportOne}
              disabled={!selected}
              aria-label={st('export')}
              title={st('export')}
            >
              <Download size={12} />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => fileRef.current?.click()}
              aria-label={st('import')}
              title={st('import')}
            >
              <Upload size={12} />
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
            <p className="text-xs text-ink-3">{st('selectOne')}</p>
          ) : (
            <>
              <SettingRow label={st('name')} labelWidth={92}>
                <TextInput
                  value={name}
                  disabled={!editable}
                  onChange={(e) => setName(e.target.value)}
                  aria-label={st('name')}
                  className="h-6 w-48"
                />
              </SettingRow>

              {fields.map((f) => {
                const raw = readPath(draft ?? {}, f.path)
                const set = typeof raw === 'number' && Number.isFinite(raw)
                return (
                  <SettingRow key={f.path} label={st(`field.${f.labelKey}`)} labelWidth={92}>
                    {/* **「这份配置没管这一项」是独立一档**，不是"等于某个数"。
                        `mixed` 让输入框留空而不是谎报一个值；旁边的 × 是回到
                        那一档的唯一出口（否则设过一次就再也撤不回来）。 */}
                    <NumberField
                      value={set ? (raw as number) : f.min}
                      mixed={!set}
                      disabled={!editable}
                      min={f.min}
                      max={f.max}
                      step={f.step}
                      precision={f.step < 1 ? 2 : 0}
                      suffix={f.unit}
                      ariaLabel={st(`field.${f.labelKey}`)}
                      className="w-28"
                      onChange={(v) => setDraft((d) => (d ? writePath(d, f.path, v) : d))}
                    />
                    {set && editable && (
                      <Button
                        size="icon-sm"
                        className="h-5 w-5"
                        aria-label={st('clearField', { field: st(`field.${f.labelKey}`) })}
                        onClick={() => setDraft((d) => (d ? clearPath(d, f.path) : d))}
                      >
                        <X size={11} className="text-ink-3" />
                      </Button>
                    )}
                  </SettingRow>
                )
              })}

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
                <SettingRow label={st('follow')} help={st('followHelp')} labelWidth={92}>
                  <Toggle
                    checked={doc.profile?.follow === true}
                    onChange={setFollow}
                    aria-label={st('follow')}
                  />
                </SettingRow>
              )}

              {!editable && <p className="text-xs text-ink-3">{st('readOnlyHint')}</p>}
              {conflict && (
                <p className="text-xs text-danger">
                  {st('conflict', { name: conflict.display_name })}
                </p>
              )}
              {error && !conflict && <p className="text-xs text-danger">{error.message}</p>}

              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                <Button
                  variant="primary"
                  size="sm"
                  disabled={!editable || !dirty}
                  loading={busy}
                  onClick={save}
                >
                  {st('save')}
                </Button>
                {kind === 'spec' ? (
                  <Button variant="outline" size="sm" onClick={useForProject}>
                    {st('useForProject')}
                  </Button>
                ) : (
                  <Button variant="outline" size="sm" onClick={applyToFigure}>
                    {st('applyToFigure')}
                  </Button>
                )}
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!editable || !selected.derived_from}
                  onClick={restore}
                  title={selected.derived_from ? undefined : st('restoreNeedsOrigin')}
                >
                  <RotateCcw size={12} />
                  {st('restore')}
                </Button>
                <Button variant="outline" size="sm" disabled={!editable} onClick={remove}>
                  <Trash2 size={12} className="text-danger" />
                  {st('delete')}
                </Button>
              </div>
            </>
          )}
        </div>
      </div>
    </SettingSection>
  )
}
