import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Archive, Check, ChevronRight, Download, ExternalLink, MoreHorizontal, TriangleAlert } from 'lucide-react'
import { createPackage, exportFigure, type ExportResponse } from '@/lib/api'
import { readExportDefaults } from '@/lib/exportDefaults'
import { toExportObjects } from '@/lib/exportPayload'
import { buildProofPayload, runPreflight } from '@/lib/preflight'
import { cn } from '@/lib/utils'
import { isDesktop, revealExportedFile } from '@/lib/desktop'
import { revealObjects } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useProjectStore } from '@/store/projectStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject, TextObject } from '@/types/document'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { Menu, MenuItem } from './ui/Menu'
import { Row } from './ui/Field'
import { TextInput } from './ui/Input'
import { Select } from './ui/Select'
import { Toggle } from './ui/Toggle'

const DPI_OPTIONS = [
  { value: '300', label: '300 dpi', hint: '投稿' },
  { value: '600', label: '600 dpi', hint: '出版' },
  { value: '900', label: '900 dpi', hint: '大幅' },
  { value: '1200', label: '1200 dpi', hint: '极限' },
]

/** 导出预设：dpi + 格式的组合，并按期刊常见版式校对页宽（只提示，不擅自改页面） */
const PRESETS: { id: string; label: string; dpi: string; formats: string[]; pageW?: number; hint: string }[] = [
  { id: 'single', label: '单栏投稿', dpi: '600', formats: ['pdf'], pageW: 85, hint: 'PDF · 85mm 单栏' },
  { id: 'double', label: '通栏投稿', dpi: '600', formats: ['pdf'], pageW: 150, hint: 'PDF · 150mm 通栏' },
  { id: 'full', label: '整页', dpi: '600', formats: ['pdf', 'png'], pageW: 180, hint: 'PDF+PNG · 180mm 版心' },
  { id: 'screen', label: '屏幕预览', dpi: '300', formats: ['png'], hint: 'PNG 300dpi' },
]

export function ExportDialog() {
  const open = useUiStore((s) => s.exportOpen)
  const setOpen = useUiStore((s) => s.setExportOpen)
  const doc = useDocumentStore((s) => s.doc)
  const renderByFile = useRenderStore((s) => s.byFile)
  const assets = useAssetStore((s) => s.byId)

  // 初始值来自「设置 → 导出默认值」；对话框内的改动只影响本次
  const [formats, setFormats] = useState<string[]>(() => readExportDefaults().formats)
  const [dpi, setDpi] = useState(() => readExportDefaults().dpi)
  const [stem, setStem] = useState(doc.name)
  const [preset, setPreset] = useState<string | null>(null)
  const [withProof, setWithProof] = useState(() => readExportDefaults().withProof)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<ExportResponse | null>(null)
  const [packResult, setPackResult] = useState<{ name: string; url: string; assets: number } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setStem(doc.name)
    setResult(null)
    setPackResult(null)
    setError(null)
  }, [open, doc.name])

  const visible = doc.objects.filter((o) => !o.hidden)
  const panels = visible.filter((o): o is PanelObject => o.type === 'panel')
  const texts = visible.filter((o): o is TextObject => o.type === 'text')
  const marks = visible.filter((o) => o.type === 'arrow' || o.type === 'shape')

  const pxW = Math.round((doc.page.w / 25.4) * Number(dpi))
  const pxH = Math.round((doc.page.h / 25.4) * Number(dpi))

  const issues = useMemo(
    () => (open ? runPreflight(doc, assets, renderByFile) : []),
    [open, doc, assets, renderByFile],
  )
  const errors = issues.filter((i) => i.severity === 'error')

  const activePreset = PRESETS.find((p) => p.id === preset)
  const pageMismatch =
    activePreset?.pageW != null && Math.abs(doc.page.w - activePreset.pageW) > 0.5

  const applyPreset = (p: (typeof PRESETS)[number]) => {
    setPreset(p.id)
    setDpi(p.dpi)
    setFormats([...p.formats])
  }

  const toggleFormat = (f: string) => {
    setPreset(null)
    setFormats((prev) => (prev.includes(f) ? prev.filter((v) => v !== f) : [...prev, f]))
  }

  const locate = (ids: string[]) => {
    setOpen(false)
    revealObjects(ids)
  }

  const run = async () => {
    if (!formats.length) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const settings = { dpi: Number(dpi), formats, stem: stem.trim() || 'composed' }
      const res = await exportFigure({
        page_w_mm: doc.page.w,
        page_h_mm: doc.page.h,
        dpi: settings.dpi,
        formats,
        stem: settings.stem,
        objects: toExportObjects(doc.objects),
        proof: withProof ? buildProofPayload(doc, assets, issues, settings) : undefined,
      })
      setResult(res)
      useUiStore.getState().setStatus(`导出完成：${res.files.map((f) => f.name).join('、')}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const pack = async () => {
    setBusy(true)
    setError(null)
    setPackResult(null)
    try {
      // 项目包带上整个项目文档（schema 3，含全部画布）
      const res = await createPackage(
        stem.trim() || doc.name,
        useDocumentStore.getState().buildProject(),
        { dpi: Number(dpi), formats },
      )
      setPackResult(res)
      useUiStore
        .getState()
        .setStatus(`已生成项目包 ${res.name}（${res.assets} 个素材），换机器可从「文档菜单 → 导入项目包」打开`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title="导出"
      description={`${doc.page.w}×${doc.page.h} mm · ${panels.length} 面板 · ${texts.length} 文字 · ${marks.length} 标注`}
      width={500}
      busy={busy}
      footer={
        <>
          <Menu
            width={200}
            align="start"
            trigger={
              <Button size="icon" disabled={busy} aria-label="更多导出选项">
                <MoreHorizontal size={14} className="text-ink-2" />
              </Button>
            }
          >
            <MenuItem disabled={!doc.objects.length} onSelect={() => void pack()}>
              <span className="flex items-center gap-2">
                <Archive size={13} className="text-ink-3" />
                打包项目（.magplot）
              </span>
            </MenuItem>
          </Menu>
          <span className="flex-1" />
          <Button variant="outline" size="md" disabled={busy} onClick={() => setOpen(false)}>
            关闭
          </Button>
          <Button
            variant="primary"
            size="md"
            disabled={!formats.length}
            loading={busy}
            loadingLabel="正在合成…"
            title={errors.length ? '存在阻断性问题（缺素材 / 渲染失败），仍可导出但建议先处理' : undefined}
            onClick={run}
          >
            <Download size={14} />
            开始导出
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2.5">
        <Row label="预设" labelWidth={52}>
          <div className="flex min-w-0 flex-1 flex-wrap gap-1">
            {PRESETS.map((p) => (
              <button
                key={p.id}
                onClick={() => applyPreset(p)}
                title={p.hint}
                className={cn(
                  'h-6 rounded-sm border px-2 text-xs transition-colors',
                  preset === p.id
                    ? 'border-accent bg-accent-subtle text-accent'
                    : 'border-border bg-surface text-ink-2 hover:border-border-strong',
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
        </Row>
        {pageMismatch && activePreset?.pageW != null && (
          <p className="pl-[60px] text-xs leading-relaxed text-ink-3">
            当前页面宽 {doc.page.w}mm，与「{activePreset.label}」的 {activePreset.pageW}mm
            不一致——预设不改页面，需要的话去「画布」标签页调整。
          </p>
        )}

        <PreflightBlock issues={issues} errors={errors.length} onLocate={locate} />

        <Row label="格式" labelWidth={52}>
          <FormatToggle
            checked={formats.includes('pdf')}
            onClick={() => toggleFormat('pdf')}
            title="PDF"
            hint="真矢量，投稿首选"
          />
          <FormatToggle
            checked={formats.includes('png')}
            onClick={() => toggleFormat('png')}
            title="PNG"
            hint="位图，按 dpi 渲染"
          />
        </Row>

        <Row label="分辨率" labelWidth={52}>
          <Select
            value={dpi}
            onChange={(v) => {
              setPreset(null)
              setDpi(v)
            }}
            options={DPI_OPTIONS}
            disabled={!formats.includes('png')}
            ariaLabel="导出分辨率"
            className="w-28"
          />
          <span className="shrink-0 font-mono text-xs text-ink-3">
            {formats.includes('png') ? `${pxW} × ${pxH} px` : 'PDF 与 dpi 无关'}
          </span>
        </Row>

        <Row label="文件名" labelWidth={52}>
          <TextInput value={stem} onChange={(e) => setStem(e.target.value)} placeholder="composed" />
          <span className="shrink-0 font-mono text-xs text-ink-3">_时间戳</span>
        </Row>

        <Row label="留档" labelWidth={52}>
          <label
            className="flex items-center gap-1.5 text-xs text-ink-2"
            title="JSON 留档：预检结果 + 素材清单 + 导出设置，随成图写入 exports/"
          >
            <Toggle checked={withProof} onChange={setWithProof} />
            随成图生成 proof report
          </label>
        </Row>

        {error && <p className="text-xs text-danger">操作失败：{error}</p>}

        {(result || packResult) && (
          <div className="flex flex-col gap-1 rounded-sm border border-border bg-surface-2 p-2">
            <p className="text-xs text-ink-3">已保存到 exports/</p>
            {[...(result?.files ?? []), ...(packResult ? [packResult] : [])].map((f) =>
              isDesktop() ? (
                // 桌面里不开浏览器式文件标签页：在系统文件管理器中显示
                <button
                  key={f.name}
                  type="button"
                  onClick={() => {
                    const dir = result?.export_dir ?? useProjectStore.getState().project?.export_dir
                    if (dir) void revealExportedFile(dir, f.name)
                  }}
                  className="flex items-center gap-1.5 font-mono text-xs text-accent hover:underline"
                >
                  <ExternalLink size={12} />
                  {f.name}
                </button>
              ) : (
                <a
                  key={f.name}
                  href={f.url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1.5 font-mono text-xs text-accent hover:underline"
                >
                  <ExternalLink size={12} />
                  {f.name}
                </a>
              ),
            )}
          </div>
        )}
      </div>
    </Dialog>
  )
}

/** 预检：先一句摘要，问题明细按需展开；有问题时默认展开错误 */
function PreflightBlock({
  issues,
  errors,
  onLocate,
}: {
  issues: { id: string; text: string; severity: string; objectIds: string[] }[]
  errors: number
  onLocate: (ids: string[]) => void
}) {
  const [expanded, setExpanded] = useState(false)
  if (issues.length === 0) {
    return (
      <p className="flex items-center gap-1.5 rounded-sm bg-surface-2 px-2 py-1.5 text-xs text-ink-2">
        <Check size={12} className="shrink-0 text-accent" />
        导出前检查通过
      </p>
    )
  }
  return (
    <div className="rounded-sm bg-surface-2 px-2 py-1.5">
      <button
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-1.5 rounded-sm text-left text-xs outline-none focus-visible:focus-ring"
      >
        <TriangleAlert size={12} className={errors ? 'shrink-0 text-danger' : 'shrink-0 text-ink-3'} />
        <span className={cn('min-w-0 flex-1', errors ? 'text-danger' : 'text-ink-2')}>
          预检发现 {issues.length} 类问题{errors ? `（${errors} 类阻断性）` : ''}
        </span>
        <ChevronRight
          size={11}
          className={cn('shrink-0 text-ink-3 transition-transform', expanded && 'rotate-90')}
        />
      </button>
      {expanded && (
        <ul className="mt-1.5 flex flex-col gap-1.5 border-t border-border pt-1.5">
          {issues.map((it) => (
            <Warning key={it.id} ids={it.objectIds} error={it.severity === 'error'} onLocate={onLocate}>
              {it.text}（{it.objectIds.length} 个）
            </Warning>
          ))}
        </ul>
      )}
    </div>
  )
}

function FormatToggle({
  checked,
  onClick,
  title,
  hint,
}: {
  checked: boolean
  onClick: () => void
  title: string
  hint: string
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={checked}
      className={cn(
        'flex flex-1 items-center gap-1.5 rounded-sm border px-2 py-1 text-left transition-colors',
        checked
          ? 'border-accent bg-accent-subtle'
          : 'border-border bg-surface hover:border-border-strong',
      )}
    >
      <span
        className={cn(
          'flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-[3px] border',
          checked ? 'border-accent bg-accent text-white' : 'border-border-strong',
        )}
      >
        {checked && <Check size={10} strokeWidth={3} />}
      </span>
      <span className="min-w-0">
        <span className={cn('block text-xs', checked ? 'text-accent' : 'text-ink')}>{title}</span>
        <span className="block text-xs text-ink-3">{hint}</span>
      </span>
    </button>
  )
}

/** 预检警告点得动：点一下就关掉弹窗、选中并把问题对象挪进视野 */
function Warning({
  ids,
  error = false,
  onLocate,
  children,
}: {
  ids: string[]
  error?: boolean
  onLocate: (ids: string[]) => void
  children: ReactNode
}) {
  return (
    <li>
      <button
        onClick={() => onLocate(ids)}
        className="group flex w-full items-start gap-1.5 text-left text-xs leading-relaxed text-ink-2 hover:text-ink"
      >
        <TriangleAlert
          size={12}
          className={cn('mt-px shrink-0', error ? 'text-danger' : 'text-ink-3')}
        />
        <span className="min-w-0 flex-1">{children}</span>
        <span className="shrink-0 text-ink-3 group-hover:text-accent">定位</span>
      </button>
    </li>
  )
}
