import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Bookmark, Copy, Layers2, Pencil, RotateCcw, Trash2, X,
  History,
} from 'lucide-react'
import {
  backendErrorText,
  createVersion,
  deleteVersion,
  duplicateVersion,
  fetchVersionDoc,
  fetchVersions,
  panelSrc,
  updateVersion,
  type LayoutVersionMeta,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { msg, t as translate } from '@/i18n'
import { formatTime } from '@/i18n/format'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { finishActiveGesture } from '@/store/gestureCoordinator'
import { useVariantPng } from '@/hooks/useVariantPng'
import { askConfirm, useUiStore } from '@/store/uiStore'
import type { FigureDocument, PanelObject } from '@/types/document'
import { objectLabel } from '@/types/document'
import { Button } from './ui/Button'
import { EmptyState } from './ui/EmptyState'
import { Dialog } from './ui/Dialog'
import { TextInput } from './ui/Input'
import { Segmented } from './ui/Segmented'
import { Tip } from './ui/Tooltip'

/**
 * 布局版本时间线 —— 右侧抽屉形态，画布保持可见，恢复前后可直接对照。
 *
 * 与两套已有机制的边界：
 * - 本机自动保存（localStorage）：浏览器里的工作副本，无版本概念 —— 保留不动。
 * - 「写回原始文件」历史（baked_overrides）：作用于单张图的源文件 —— 完全无关。
 * 这里的版本是**整份布局文档**的服务器快照；恢复只改文档内容（可撤销），
 * 不触碰 figures 里的任何文件。
 */
/** 本抽屉的文案在 dialogs:versions.* 下 */
const vd = (key: string, values?: Record<string, unknown>) =>
  translate(`versions.${key}`, { ns: 'dialogs', ...(values ?? {}) })

export function VersionDrawer() {
  useTranslation(['dialogs', 'common'])
  const open = useUiStore((s) => s.versionsOpen)
  const setOpen = useUiStore((s) => s.setVersionsOpen)
  const docId = useDocumentStore((s) => s.documentId)

  const [versions, setVersions] = useState<LayoutVersionMeta[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [selectedDoc, setSelectedDoc] = useState<FigureDocument | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saveName, setSaveName] = useState('')
  const [busy, setBusy] = useState(false)
  const asideRef = useRef<HTMLElement>(null)
  const restoreFocus = useRef<HTMLElement | null>(null)

  const reload = useCallback(async () => {
    try {
      const list = await fetchVersions(docId)
      setVersions(list.slice().reverse()) // 最新在上
      setError(null)
    } catch (e) {
      setError(backendErrorText(e))
    }
  }, [docId])

  useEffect(() => {
    if (!open) return
    setSelected(null)
    setSelectedDoc(null)
    setSaveName('')
    void reload()
    // 打开时记住触发点，关闭后把焦点还回去
    restoreFocus.current = document.activeElement as HTMLElement | null
    const id = requestAnimationFrame(() =>
      asideRef.current?.querySelector<HTMLElement>('input, button')?.focus(),
    )
    return () => {
      cancelAnimationFrame(id)
      restoreFocus.current?.focus?.()
    }
  }, [open, reload])

  // 选中版本后取完整文档快照（列表只有元信息）
  useEffect(() => {
    if (!selected) {
      setSelectedDoc(null)
      return
    }
    let alive = true
    fetchVersionDoc(docId, selected)
      .then((v) => alive && setSelectedDoc(v.doc))
      .catch((e) => alive && setError(backendErrorText(e)))
    return () => {
      alive = false
    }
  }, [docId, selected])

  const saveNow = async () => {
    setBusy(true)
    try {
      await createVersion(docId, {
        name: saveName.trim() || undefined,
        doc: useDocumentStore.getState().doc,
      })
      setSaveName('')
      await reload()
      useUiStore.getState().setStatus(msg('versions.saved', undefined, 'dialogs'))
    } catch (e) {
      setError(backendErrorText(e))
    } finally {
      setBusy(false)
    }
  }

  if (!open) return null

  const meta = versions.find((v) => v.id === selected) ?? null

  return (
    <aside
      ref={asideRef}
      role="dialog"
      aria-label={vd('drawerLabel')}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && !busy) {
          e.stopPropagation()
          setOpen(false)
        }
      }}
      className="absolute inset-y-0 right-0 z-40 flex w-[400px] max-w-[92vw] flex-col border-l border-border bg-surface shadow-pop"
    >
      <div className="flex h-11 shrink-0 items-center gap-2 px-3">
        <h2 className="text-sm font-medium text-ink">{vd('title')}</h2>
        {versions.length > 0 && (
          <span className="font-mono text-xs text-ink-3">{versions.length}</span>
        )}
        <span className="flex-1" />
        <Button
          size="icon-sm"
          className="-mr-1"
          disabled={busy}
          onClick={() => setOpen(false)}
          aria-label={vd('close')}
        >
          <X size={14} className="text-ink-3" />
        </Button>
      </div>
      <p className="shrink-0 px-3 pb-2 text-xs leading-relaxed text-ink-3">
        {vd('intro')}
      </p>

      <div className="flex shrink-0 gap-1.5 px-3 pb-2">
        <TextInput
          value={saveName}
          onChange={(e) => setSaveName(e.target.value)}
          onKeyDown={(e) => {
            e.stopPropagation()
            if (e.key === 'Enter') void saveNow()
          }}
          placeholder={vd('namePlaceholder')}
          className="min-w-0 flex-1"
        />
        <Button variant="outline" size="sm" loading={busy} onClick={saveNow}>
          <Bookmark size={12} />
          {vd('save')}
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {versions.length === 0 ? (
          <EmptyState
            icon={History}
            title={vd('emptyTitle')}
            hint={vd('emptyHint')}
          />
        ) : (
          <ul aria-label={vd('listLabel')}>
            {versions.map((v) => (
              <li key={v.id}>
                <button
                  onClick={() => setSelected(v.id === selected ? null : v.id)}
                  aria-expanded={v.id === selected}
                  className={cn(
                    'flex w-full flex-col gap-0.5 px-3 py-1.5 text-left outline-none focus-visible:focus-ring',
                    v.id === selected
                      ? 'border-l-2 border-accent bg-accent-subtle'
                      : 'border-l-2 border-transparent hover:bg-ink/[.04]',
                  )}
                >
                  <span className="flex items-center gap-1.5">
                    <span
                      className={cn(
                        'min-w-0 flex-1 truncate text-xs',
                        v.id === selected ? 'font-medium text-accent' : 'text-ink',
                      )}
                    >
                      {v.name}
                    </span>
                    {v.auto && (
                      <span className="shrink-0 rounded-[3px] border border-border px-1 text-xs text-ink-3">
                        {vd('autoBadge')}
                      </span>
                    )}
                  </span>
                  <span className="text-xs text-ink-3">
                    {vd('metaObjects', { time: formatTime(v.ts), count: v.objects })}
                    {v.page ? vd('metaPage', { w: v.page.w, h: v.page.h }) : ''}
                  </span>
                </button>
                {v.id === selected && meta && (
                  <VersionDetail
                    docId={docId}
                    meta={meta}
                    versionDoc={selectedDoc}
                    onChanged={reload}
                    onClose={() => setOpen(false)}
                    setBusy={setBusy}
                  />
                )}
              </li>
            ))}
          </ul>
        )}
        {error && <p className="px-3 py-2 text-xs text-danger">{error}</p>}
      </div>
    </aside>
  )
}

/* ------------------------------- 详情面板 --------------------------------- */

function VersionDetail({
  docId,
  meta,
  versionDoc,
  onChanged,
  onClose,
  setBusy,
}: {
  docId: string
  meta: LayoutVersionMeta
  versionDoc: FigureDocument | null
  onChanged: () => Promise<void>
  onClose: () => void
  setBusy: (v: boolean) => void
}) {
  useTranslation(['dialogs', 'common'])
  const currentDoc = useDocumentStore((s) => s.doc)
  const [view, setView] = useState<'version' | 'current'>('version')
  const [compareOpen, setCompareOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [draft, setDraft] = useState(meta.name)
  /** 有面板的图内修改渲染不出来 → 详情下方明确标「近似预览」 */
  const [approximate, setApproximate] = useState(false)

  useEffect(() => {
    setDraft(meta.name)
    setRenaming(false)
    setApproximate(false)
  }, [meta.id, meta.name])

  const diff = useMemo(
    () => (versionDoc ? diffDocs(versionDoc, currentDoc) : []),
    [versionDoc, currentDoc],
  )

  const restore = async () => {
    if (!versionDoc) return
    // 恢复是离散动作：先收掉还开着的连续编辑（否则这次 commit 会被并进
    // 上一条历史，一次撤销同时吐出「刚才那笔编辑」和「整份恢复」）。
    // 收尾也保证了自动存档存下去的是**真正的当前状态**，而不是一个
    // 事务开着、值还没落定的中间态（issue #131）。
    finishActiveGesture()
    setBusy(true)
    try {
      // 先把当前状态自动存档：恢复默认产生新版本，绝不覆盖当前工作
      await createVersion(docId, {
        name: vd('beforeRestore', { time: formatTime(Date.now()) }),
        auto: true,
        doc: useDocumentStore.getState().doc,
      })
      useDocumentStore
        .getState()
        .commit(msg('versions.restoreHistory', { name: meta.name }, 'dialogs'), (d) => {
          d.name = versionDoc.name
          d.page = structuredClone(versionDoc.page)
          d.objects = structuredClone(versionDoc.objects)
          d.guides = structuredClone(versionDoc.guides)
        })
      await onChanged()
      useUiStore
        .getState()
        .setStatus(msg('versions.restored', { name: meta.name }, 'dialogs'))
      onClose()
    } finally {
      setBusy(false)
    }
  }

  const rename = async () => {
    const name = draft.trim()
    setRenaming(false)
    if (!name || name === meta.name) return
    await updateVersion(docId, meta.id, { name })
    await onChanged()
  }

  const remove = async () => {
    if (
      !(await askConfirm({
        title: msg('versions.deleteTitle', { name: meta.name }, 'dialogs'),
        body: msg('versions.deleteBody', undefined, 'dialogs'),
        confirmLabel: msg('actions.delete', undefined, 'common'),
        danger: true,
      }))
    ) {
      return
    }
    await deleteVersion(docId, meta.id)
    await onChanged()
  }

  return (
    <div className="flex flex-col gap-2 bg-surface-2/60 px-3 py-2">
      <div className="flex items-center gap-0.5">
        {renaming ? (
          <input
            autoFocus
            value={draft}
            aria-label={vd('versionName')}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={rename}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
              if (e.key === 'Escape') {
                setDraft(meta.name)
                setRenaming(false)
              }
            }}
            className="h-7 min-w-0 flex-1 rounded-sm border border-accent bg-surface px-1.5 text-xs text-ink outline-none"
          />
        ) : (
          <span className="min-w-0 flex-1" />
        )}
        <Tip label={vd('rename')}>
          <Button size="icon-sm" onClick={() => setRenaming(true)} aria-label={vd('rename')}>
            <Pencil size={12} className="text-ink-3" />
          </Button>
        </Tip>
        <Tip label={vd('duplicate')}>
          <Button
            size="icon-sm"
            onClick={async () => {
              await duplicateVersion(docId, meta.id)
              await onChanged()
            }}
            aria-label={vd('duplicate')}
          >
            <Copy size={12} className="text-ink-3" />
          </Button>
        </Tip>
        {meta.auto && (
          <Button
            size="sm"
            className="text-ink-2"
            title={vd('keepTitle')}
            onClick={async () => {
              await updateVersion(docId, meta.id, { auto: false })
              await onChanged()
            }}
          >
            {vd('keep')}
          </Button>
        )}
        <Tip label={vd('delete')}>
          <Button size="icon-sm" onClick={remove} aria-label={vd('delete')}>
            <Trash2 size={12} className="text-danger" />
          </Button>
        </Tip>
      </div>

      {!versionDoc ? (
        <p className="py-4 text-center text-xs text-ink-3">{vd('loadingSnapshot')}</p>
      ) : (
        <>
          <div className="flex items-center justify-between gap-1.5">
            <Segmented
              value={view}
              onChange={(v) => setView(v)}
              items={[
                { value: 'version', label: vd('viewVersion') },
                { value: 'current', label: vd('viewCurrent') },
              ]}
            />
            <Tip label={vd('compareTip')}>
              <Button
                size="icon-sm"
                onClick={() => setCompareOpen(true)}
                aria-label={vd('compareAria')}
              >
                <Layers2 size={13} className="text-ink-2" />
              </Button>
            </Tip>
          </div>

          <LayoutSnapshot
            doc={view === 'version' ? versionDoc : currentDoc}
            renderOverrides
            onApproximate={setApproximate}
          />
          {approximate && (
            <p className="text-xs leading-relaxed text-ink-3">{vd('previewApproximate')}</p>
          )}

          <Button variant="primary" size="sm" className="w-full" onClick={restore}>
            <RotateCcw size={12} />
            {vd('restore')}
          </Button>

          {diff.length === 0 ? (
            <p className="text-xs text-ink-3">{vd('noDiff')}</p>
          ) : (
            <ul className="max-h-48 overflow-y-auto">
              {diff.map((d, i) => (
                <li
                  key={i}
                  className="flex items-start gap-1.5 py-0.5 text-xs leading-relaxed text-ink-2"
                >
                  <span
                    className={cn(
                      'mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full',
                      d.kind === 'add' && 'bg-accent',
                      d.kind === 'remove' && 'bg-danger',
                      d.kind !== 'add' && d.kind !== 'remove' && 'bg-ink-faint',
                    )}
                  />
                  <span>{d.text}</span>
                </li>
              ))}
            </ul>
          )}

          <Dialog
            open={compareOpen}
            onOpenChange={setCompareOpen}
            title={vd('compareTitle')}
            description={vd('compareDescription')}
            size="lg"
          >
            {/* 对比叠加：底图按**版本自己的** overrides 出，上层是当前布局的轮廓 */}
            <div className="relative mx-auto" style={{ maxWidth: 480 }}>
              <LayoutSnapshot doc={versionDoc} renderOverrides />
              <div className="absolute inset-0 opacity-55">
                <LayoutSnapshot doc={currentDoc} outline />
              </div>
            </div>
          </Dialog>
        </>
      )}
    </div>
  )
}

/* ------------------------------ 布局缩略图 -------------------------------- */

/**
 * 真素材缩略：面板用现成的 /api/render 小档位图片按布局摆进等比页面，
 * 文字/形状画轮廓块。不是像素级导出预览，但位置、比例、内容一目了然。
 */
export function LayoutSnapshot({
  doc,
  outline = false,
  renderOverrides = false,
  onApproximate,
}: {
  doc: FigureDocument
  outline?: boolean
  /**
   * 按面板自己的 overrides 出图（而不是磁盘素材）。**只给用户当前展开的那一份
   * 版本详情用**：版本列表里每一条都渲染的话，一次展开就是几十次 matplotlib
   * 往返（heavy 脚本上是分钟级）。
   */
  renderOverrides?: boolean
  /** 有面板的图内修改渲染不出来 —— 调用方据此标注「近似预览」 */
  onApproximate?: (approximate: boolean) => void
}) {
  const assets = useAssetStore((s) => s.byId)
  const { w: pw, h: ph } = doc.page
  return (
    <div
      className={cn(
        'relative w-full overflow-hidden rounded-[3px] border border-border',
        outline ? 'bg-transparent' : 'bg-white',
      )}
      style={{ aspectRatio: `${pw} / ${ph}` }}
    >
      {doc.objects
        .filter((o) => !o.hidden)
        .map((o) => {
          const style: React.CSSProperties = {
            left: `${(o.x / pw) * 100}%`,
            top: `${(o.y / ph) * 100}%`,
            width: `${(o.w / pw) * 100}%`,
            height: `${(o.h / ph) * 100}%`,
          }
          if (outline) {
            return (
              <div
                key={o.id}
                className="absolute border border-accent"
                style={style}
                title={objectLabel(o)}
              />
            )
          }
          if (o.type === 'panel') {
            return (
              <SnapshotPanel
                key={o.id}
                panel={o}
                style={style}
                mtime={assets[o.fileId]?.mtime}
                renderOverrides={renderOverrides}
                onApproximate={onApproximate}
              />
            )
          }
          if (o.type === 'text') {
            return (
              <div
                key={o.id}
                className="absolute overflow-hidden whitespace-pre leading-none"
                style={{ ...style, fontSize: 6, color: o.color, fontFamily: 'var(--font-doc)' }}
              >
                {o.text}
              </div>
            )
          }
          return <div key={o.id} className="absolute border border-ink-faint" style={style} />
        })}
    </div>
  )
}

/**
 * 缩略图里的一个面板。
 *
 * `renderOverrides` 打开时按这一版**自己的 overrides** 出图——这正是布局版本
 * 时间线以前缺的东西：只画磁盘素材的话，两个图内布局完全不同的版本长得一模
 * 一样（issue #131）。出不来就退回磁盘图并把 `approximate` 报上去，由外面
 * 明确标注，绝不无提示地拿原图冒充版本视觉状态。
 */
function SnapshotPanel({
  panel,
  style,
  mtime,
  renderOverrides,
  onApproximate,
}: {
  panel: PanelObject
  style: React.CSSProperties
  mtime?: number
  renderOverrides: boolean
  onApproximate?: (approximate: boolean) => void
}) {
  const variant = useVariantPng(
    panel.fileId,
    panel.overrides,
    200,
    renderOverrides && panel.overrides.length > 0,
  )
  useEffect(() => {
    if (renderOverrides) onApproximate?.(variant.approximate)
  }, [variant.approximate, renderOverrides, onApproximate])

  // panelSrc 可能给不出地址（替代传输里没有 HTTP 服务）：一个都拿不到就不画
  // <img>，绝不留一个空 src 让缩略图挂一个碎图标
  const src = variant.url || panelSrc(panel.fileId, panel.fileKind, 200, mtime)
  if (!src) return null
  return (
    <img
      src={src}
      alt=""
      className="absolute object-fill"
      style={{
        ...style,
        transform: panel.rotation ? `rotate(${panel.rotation}deg)` : undefined,
        opacity: panel.opacity,
      }}
    />
  )
}

/* -------------------------------- 差异计算 -------------------------------- */

interface DiffLine {
  kind: 'add' | 'remove' | 'geom' | 'z' | 'vis' | 'page' | 'overrides' | 'other'
  text: string
}

const near = (a: number, b: number, eps = 0.05) => Math.abs(a - b) <= eps

/** 差异描述文案；对象名是用户内容，作为插值原样带过去 */
const df = (key: string, values?: Record<string, unknown>) =>
  translate(`versions.diff.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 图内修改的差异摘要。
 *
 * 「override 从 8 条变成 8 条」等于什么都没说（issue #131：用户就是靠时间线
 * 判断恢复有没有生效的）。这里按**类别**数：位置、文字、axes 布局、其他，
 * 外加受影响的元素个数与一份 gid/prop 样本。
 *
 * **只出技术标识，绝不出值**：override 的 value 里可能是用户写的图内文字，
 * 版本对比面板不该把它显示出来。
 */
const POS_PROPS = new Set(['pos_frac', 'loc_frac', 'endpoints_frac', 'position'])
const TEXT_PROPS = new Set([
  'text', 'fontsize', 'fontfamily', 'fontweight', 'fontstyle',
  'ha', 'va', 'rotation', 'linespacing',
])
const AXES_PROPS = new Set(['position', 'size_mm', 'aspect', 'figsize'])

function summarizeOverrideDiff(
  a: readonly { gid: string; prop: string; value: unknown }[],
  b: readonly { gid: string; prop: string; value: unknown }[],
) {
  const key = (o: { gid: string; prop: string }) => `${o.gid}\u0000${o.prop}`
  const mapA = new Map(a.map((o) => [key(o), JSON.stringify(o.value)]))
  const mapB = new Map(b.map((o) => [key(o), JSON.stringify(o.value)]))
  const touched: { gid: string; prop: string }[] = []
  for (const k of new Set([...mapA.keys(), ...mapB.keys()])) {
    if (mapA.get(k) === mapB.get(k)) continue
    const [gid, prop] = k.split('\u0000')
    touched.push({ gid, prop })
  }
  let pos = 0
  let text = 0
  let axes = 0
  let other = 0
  for (const { gid, prop } of touched) {
    // position 同时是 axes 布局与位置类：axes 元素上算 axes 布局
    if (prop === 'position' || AXES_PROPS.has(prop)) axes += 1
    else if (POS_PROPS.has(prop)) pos += 1
    else if (TEXT_PROPS.has(prop)) text += 1
    else other += 1
    void gid
  }
  return {
    elements: new Set(touched.map((o) => o.gid)).size,
    pos,
    text,
    axes,
    other,
    sample: touched.slice(0, 6).map((o) => `${o.gid}.${o.prop}`),
  }
}

/** 对象级差异（a = 版本快照，b = 当前文档），文案面向用户 */
export function diffDocs(a: FigureDocument, b: FigureDocument): DiffLine[] {
  const out: DiffLine[] = []
  const byIdA = new Map(a.objects.map((o) => [o.id, o]))
  const byIdB = new Map(b.objects.map((o) => [o.id, o]))

  if (!near(a.page.w, b.page.w) || !near(a.page.h, b.page.h)) {
    out.push({
      kind: 'page',
      text: df('pageSize', { aw: a.page.w, ah: a.page.h, bw: b.page.w, bh: b.page.h }),
    })
  }
  if ((a.page.bg ?? '#FFFFFF') !== (b.page.bg ?? '#FFFFFF') ||
      !!a.page.transparent !== !!b.page.transparent) {
    out.push({ kind: 'page', text: df('pageBackground') })
  }
  if ((a.page.margin ?? 0) !== (b.page.margin ?? 0)) {
    out.push({ kind: 'page', text: df('pageMargin', { a: a.page.margin ?? 0, b: b.page.margin ?? 0 }) })
  }

  for (const o of a.objects) {
    if (!byIdB.has(o.id)) out.push({ kind: 'remove', text: df('removed', { name: objectLabel(o) }) })
  }
  for (const o of b.objects) {
    if (!byIdA.has(o.id)) out.push({ kind: 'add', text: df('added', { name: objectLabel(o) }) })
  }

  const orderA = a.objects.filter((o) => byIdB.has(o.id)).map((o) => o.id)
  const orderB = b.objects.filter((o) => byIdA.has(o.id)).map((o) => o.id)
  if (orderA.join() !== orderB.join()) {
    out.push({ kind: 'z', text: df('zorder') })
  }

  for (const oa of a.objects) {
    const ob = byIdB.get(oa.id)
    if (!ob) continue
    const name = objectLabel(ob)
    const moved = !near(oa.x, ob.x) || !near(oa.y, ob.y)
    const resized = !near(oa.w, ob.w) || !near(oa.h, ob.h)
    if (moved && resized) out.push({ kind: 'geom', text: df('movedAndResized', { name }) })
    else if (moved) {
      out.push({
        kind: 'geom',
        text: df('moved', {
          name,
          dx: (ob.x - oa.x).toFixed(1),
          dy: (ob.y - oa.y).toFixed(1),
        }),
      })
    } else if (resized) out.push({ kind: 'geom', text: df('resized', { name }) })
    if (!!oa.hidden !== !!ob.hidden) {
      out.push({ kind: 'vis', text: df(ob.hidden ? 'hidden' : 'shown', { name }) })
    }
    if (!!oa.locked !== !!ob.locked) {
      out.push({ kind: 'vis', text: df(ob.locked ? 'locked' : 'unlocked', { name }) })
    }
    if (oa.type === 'panel' && ob.type === 'panel') {
      const ca = JSON.stringify(oa.overrides)
      const cb = JSON.stringify(ob.overrides)
      if (ca !== cb) {
        const sum = summarizeOverrideDiff(oa.overrides, ob.overrides)
        out.push({
          kind: 'overrides',
          text: df('overridesDetail', {
            name,
            elements: sum.elements,
            pos: sum.pos,
            text: sum.text,
            axes: sum.axes,
            other: sum.other,
          }),
        })
        // 具体改了哪几个 gid 的哪几条属性——**只列技术标识，不列值**，
        // 用户图内文字的正文一个字都不进这里
        if (sum.sample.length) {
          out.push({ kind: 'overrides', text: df('overridesSample', { list: sum.sample.join('、') }) })
        }
      }
      if (oa.fileId !== ob.fileId) out.push({ kind: 'other', text: df('assetReplaced', { name }) })
      if (JSON.stringify(oa.crop ?? null) !== JSON.stringify(ob.crop ?? null)) {
        out.push({ kind: 'other', text: df('cropChanged', { name }) })
      }
      if ((oa.rotation ?? 0) !== (ob.rotation ?? 0)) {
        out.push({
          kind: 'other',
          text: df('rotationChanged', { name, from: oa.rotation ?? 0, to: ob.rotation ?? 0 }),
        })
      }
    }
    if (oa.type === 'text' && ob.type === 'text') {
      if (oa.text !== ob.text) out.push({ kind: 'other', text: df('textChanged', { name }) })
      else if (
        oa.sizePt !== ob.sizePt || oa.bold !== ob.bold || oa.color !== ob.color ||
        oa.align !== ob.align || (oa.italic ?? false) !== (ob.italic ?? false)
      ) {
        out.push({ kind: 'other', text: df('textStyleChanged', { name }) })
      }
    }
  }

  if (a.guides.length !== b.guides.length) {
    out.push({ kind: 'other', text: df('guides', { from: a.guides.length, to: b.guides.length }) })
  }
  return out
}
