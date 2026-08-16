import { useEffect, useState } from 'react'
import { CheckCircle2, XCircle } from 'lucide-react'
import { patchAiSettings, patchProjectSettings } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { readExportDefaults, writeExportDefaults } from '@/lib/exportDefaults'
import { cn } from '@/lib/utils'
import { useAiStore } from '@/store/aiStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { useUpdateStore } from '@/store/updateStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { TextInput } from './ui/Input'
import { Toggle } from './ui/Toggle'

type SectionId =
  | 'general'
  | 'project'
  | 'canvas'
  | 'sidebars'
  | 'ai'
  | 'export'
  | 'shortcuts'
  | 'update'
  | 'about'

const SECTIONS: { id: SectionId; label: string }[] = [
  { id: 'general', label: '常规' },
  { id: 'project', label: '项目与路径' },
  { id: 'canvas', label: '画布与吸附' },
  { id: 'sidebars', label: '侧栏行为' },
  { id: 'ai', label: 'AI 工具' },
  { id: 'export', label: '导出默认值' },
  { id: 'shortcuts', label: '快捷键' },
  { id: 'update', label: '检查更新' },
  { id: 'about', label: '隐私、诊断与 About' },
]

export function SettingsDialog() {
  const open = useUiStore((s) => s.settingsOpen)
  const setOpen = useUiStore((s) => s.setSettingsOpen)
  const requested = useUiStore((s) => s.settingsSection)
  const [section, setSection] = useState<SectionId>('general')
  // 调用方指定分区时（如顶栏「有新版本」）跳过去，之后仍由用户自由切换
  useEffect(() => {
    if (open && requested) setSection(requested as SectionId)
  }, [open, requested])

  if (!open) return null
  return (
    <Dialog open onOpenChange={setOpen} title="设置" size="lg">
      <div className="flex min-h-72 gap-3">
        <nav aria-label="设置分区" className="flex w-32 shrink-0 flex-col gap-0.5">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              onClick={() => setSection(s.id)}
              aria-current={section === s.id || undefined}
              className={cn(
                'relative h-7 rounded-sm px-2 text-left text-xs outline-none focus-visible:focus-ring',
                section === s.id
                  ? 'bg-accent-subtle font-medium text-ink'
                  : 'text-ink-2 hover:bg-ink/[.045]',
              )}
            >
              {section === s.id && (
                <span aria-hidden className="absolute left-0 top-1.5 h-4 w-0.5 rounded-full bg-accent" />
              )}
              {s.label}
            </button>
          ))}
        </nav>
        <div className="min-w-0 flex-1 overflow-y-auto pr-1">
          {section === 'general' && <GeneralSection />}
          {section === 'project' && <ProjectSection />}
          {section === 'canvas' && <CanvasSection close={() => setOpen(false)} />}
          {section === 'sidebars' && <SidebarsSection />}
          {section === 'ai' && <AiSection />}
          {section === 'export' && <ExportSection />}
          {section === 'shortcuts' && <ShortcutsSection close={() => setOpen(false)} />}
          {section === 'update' && <UpdateSection />}
          {section === 'about' && <AboutSection />}
        </div>
      </div>
    </Dialog>
  )
}

const Row = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <label className="flex min-h-7 items-center gap-2">
    <span className="w-24 shrink-0 text-xs text-ink-2">{label}</span>
    {children}
  </label>
)

function GeneralSection() {
  const setStatus = useUiStore((s) => s.setStatus)
  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-xs leading-relaxed text-ink-3">
        改动自动保存：编辑停顿 1 秒后写入本机磁盘（layouts/_autosave/），
        无须手动保存；命名版本走「保存为布局文件」。
      </p>
      <div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            try {
              localStorage.removeItem('magplot.ui')
            } catch {
              /* 忽略 */
            }
            setStatus('界面布局已重置，刷新页面后生效')
          }}
        >
          恢复界面默认布局
        </Button>
        <p className="mt-1 text-xs text-ink-3">侧栏宽度、吸附开关等界面偏好回到出厂值。</p>
      </div>
    </div>
  )
}

function ProjectSection() {
  const project = useProjectStore((s) => s.project)
  const [exportDir, setExportDir] = useState(project?.settings?.export_dir ?? '')
  const [backupDir, setBackupDir] = useState(project?.settings?.backup_dir ?? '')
  const [error, setError] = useState<string | null>(null)
  const readonly = project?.settings?.allow_write_back === false

  const save = async (patch: Parameters<typeof patchProjectSettings>[0]) => {
    setError(null)
    try {
      const res = await patchProjectSettings(patch)
      useProjectStore.setState((s) =>
        s.project
          ? {
              project: {
                ...s.project,
                settings: res.settings,
                export_dir: res.export_dir,
                backup_dir: res.backup_dir,
              },
            }
          : s,
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="flex flex-col gap-2.5">
      <Row label="当前项目">
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-2" title={project?.figures_dir}>
          {project?.figures_dir ?? '—'}
        </span>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            useUiStore.getState().setSettingsOpen(false)
            useProjectStore.setState({ phase: 'none' }) // Picker 接管；可从最近项目回来
          }}
        >
          切换项目…
        </Button>
      </Row>
      <Row label="导出目录">
        <TextInput
          value={exportDir}
          onChange={(e) => setExportDir(e.target.value)}
          onBlur={() => void save({ export_dir: exportDir })}
          placeholder={`默认 ${project?.export_dir ?? 'exports/'}`}
          className="flex-1"
        />
      </Row>
      <Row label="备份目录">
        <TextInput
          value={backupDir}
          onChange={(e) => setBackupDir(e.target.value)}
          onBlur={() => void save({ backup_dir: backupDir })}
          placeholder={`默认 ${project?.backup_dir ?? 'cache/original_backups/'}`}
          className="flex-1"
        />
      </Row>
      <Row label="项目只读">
        <Toggle
          checked={readonly}
          onChange={(v) => void save({ allow_write_back: !v })}
          aria-label="项目只读（禁止写回原始文件）"
        />
        <span className="text-xs text-ink-3">开启后禁止「写回原始文件」，源图不会被覆盖</span>
      </Row>
      {error && <p className="text-xs text-danger">{error}</p>}
      <p className="text-xs leading-relaxed text-ink-3">
        目录留空 = 使用默认位置。设置按项目分别保存。
      </p>
    </div>
  )
}

function CanvasSection({ close }: { close: () => void }) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs leading-relaxed text-ink-3">
        网格、吸附、标尺与安全区域的开关在右栏「画布」页，与画布放在一起改。
      </p>
      <div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            close()
            useUiStore.getState().setRightTab('canvas')
          }}
        >
          打开画布设置
        </Button>
      </div>
    </div>
  )
}

function SidebarsSection() {
  const leftPinned = useUiStore((s) => s.leftPinned)
  const rightPinned = useUiStore((s) => s.rightPinned)
  return (
    <div className="flex flex-col gap-2.5">
      <Row label="左抽屉常驻">
        <Toggle
          checked={leftPinned}
          onChange={(v) => useUiStore.getState().setLeftPinned(v)}
          aria-label="左抽屉常驻"
        />
        <span className="text-xs text-ink-3">选中对象时素材抽屉不自动让位</span>
      </Row>
      <Row label="右栏常驻">
        <Toggle
          checked={rightPinned}
          onChange={(v) => useUiStore.getState().setRightPinned(v)}
          aria-label="右栏常驻"
        />
        <span className="text-xs text-ink-3">清空选择后属性栏保持展开</span>
      </Row>
      <p className="text-xs leading-relaxed text-ink-3">
        窗口 ≥1440px 时两侧可同时常驻；1024–1439px 左右互斥；更窄时侧栏以
        覆盖层临时显示，常驻不生效。
      </p>
    </div>
  )
}

function AiSection() {
  const caps = useAiStore((s) => s.caps)
  const [codexPath, setCodexPath] = useState('')
  const [claudePath, setClaudePath] = useState('')
  const [busy, setBusy] = useState(false)

  const apply = async (patch: { codex_path?: string; claude_path?: string }) => {
    setBusy(true)
    try {
      await patchAiSettings(patch)
      await useAiStore.getState().loadCaps(true)
    } finally {
      setBusy(false)
    }
  }

  const provider = (name: 'codex' | 'claude', label: string) => {
    const p = caps?.providers[name]
    return (
      <div className="rounded-sm border border-border p-2">
        <div className="flex items-center gap-1.5">
          {p?.installed ? (
            <CheckCircle2 size={13} className="text-ink-2" aria-hidden />
          ) : (
            <XCircle size={13} className="text-danger" aria-hidden />
          )}
          <span className="text-xs font-medium text-ink">{label}</span>
          <span className="truncate font-mono text-xs text-ink-3">
            {p?.installed ? p.version : '未检测到'}
          </span>
        </div>
        {p?.path && (
          <p className="mt-0.5 truncate font-mono text-xs text-ink-3" title={p.path}>
            {p.path}
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2.5">
      {provider('codex', 'Codex')}
      {provider('claude', 'Claude')}
      <Row label="Codex 路径">
        <TextInput
          value={codexPath}
          onChange={(e) => setCodexPath(e.target.value)}
          onBlur={() => void apply({ codex_path: codexPath })}
          placeholder="留空 = 自动查找（PATH）"
          className="flex-1"
        />
      </Row>
      <Row label="Claude 路径">
        <TextInput
          value={claudePath}
          onChange={(e) => setClaudePath(e.target.value)}
          onBlur={() => void apply({ claude_path: claudePath })}
          placeholder="留空 = 自动查找（PATH）"
          className="flex-1"
        />
      </Row>
      <div>
        <Button variant="outline" size="sm" loading={busy} onClick={() => void apply({})}>
          重新探测
        </Button>
      </div>
    </div>
  )
}

function ExportSection() {
  const [defaults, setDefaults] = useState(readExportDefaults)
  const update = (patch: Partial<typeof defaults>) => setDefaults(writeExportDefaults(patch))
  const toggleFormat = (f: string) => {
    const next = defaults.formats.includes(f)
      ? defaults.formats.filter((x) => x !== f)
      : [...defaults.formats, f]
    if (next.length) update({ formats: next })
  }
  return (
    <div className="flex flex-col gap-2.5">
      <Row label="默认 DPI">
        <select
          value={defaults.dpi}
          onChange={(e) => update({ dpi: e.target.value })}
          aria-label="默认 DPI"
          className="h-7 rounded-sm border border-border bg-surface px-1.5 text-xs text-ink outline-none focus-visible:focus-ring"
        >
          {['300', '600', '900', '1200'].map((d) => (
            <option key={d} value={d}>
              {d} dpi
            </option>
          ))}
        </select>
      </Row>
      <Row label="默认格式">
        <span className="flex items-center gap-3">
          {['pdf', 'png'].map((f) => (
            <label key={f} className="flex items-center gap-1.5 text-xs text-ink-2">
              <input
                type="checkbox"
                checked={defaults.formats.includes(f)}
                onChange={() => toggleFormat(f)}
              />
              {f.toUpperCase()}
            </label>
          ))}
        </span>
      </Row>
      <Row label="Proof 留档">
        <Toggle
          checked={defaults.withProof}
          onChange={(v) => update({ withProof: v })}
          aria-label="默认随导出生成 proof report"
        />
        <span className="text-xs text-ink-3">随成图生成 proof report（JSON 留档）</span>
      </Row>
      <p className="text-xs leading-relaxed text-ink-3">这里是导出对话框的初始值；单次导出仍可临时改。</p>
    </div>
  )
}

function ShortcutsSection({ close }: { close: () => void }) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs leading-relaxed text-ink-3">全部快捷键见速查表（按 ? 随时打开）。</p>
      <div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            close()
            useUiStore.getState().setShortcutHelpOpen(true)
          }}
        >
          打开快捷键速查表
        </Button>
      </div>
    </div>
  )
}

function UpdateSection() {
  const { status, checking, applying, restartRequired, applyLog, check, apply, setAutoCheck } =
    useUpdateStore()
  useEffect(() => {
    if (!status) void check(false)
  }, [status, check])

  const checkedAt = status?.checked_at_ms
    ? new Date(status.checked_at_ms).toLocaleString()
    : '尚未检查'

  return (
    <div className="flex flex-col gap-2.5">
      <Row label="当前版本">
        <span className="font-mono text-xs text-ink">{status?.current ?? '…'}</span>
      </Row>
      <Row label="安装方式">
        <span className="text-xs text-ink-2">
          {status?.method === 'pipx'
            ? 'pipx'
            : status?.method === 'source'
              ? '源码检出（升级请用 git pull）'
              : 'pip'}
        </span>
      </Row>
      <Row label="自动检查">
        <Toggle
          checked={status?.auto_check ?? true}
          onChange={(v) => void setAutoCheck(v)}
          aria-label="每天自动检查更新"
        />
        <span className="text-xs text-ink-3">每天一次，关掉后不会有任何联网请求</span>
      </Row>

      <div className="flex items-center gap-2">
        <Button onClick={() => void check(true)} disabled={checking}>
          {checking ? '检查中…' : '立即检查'}
        </Button>
        <span className="text-xs text-ink-3">上次检查：{checkedAt}</span>
      </div>

      {status?.error && <p className="text-xs text-danger">{status.error}</p>}

      {status?.update_available ? (
        <div className="flex flex-col gap-2 rounded-md border border-border p-2.5">
          <p className="text-xs text-ink">
            有新版本 <span className="font-mono">{status.latest}</span>
            <span className="ml-2 text-ink-3">当前 {status.current}</span>
          </p>
          {status.notes && (
            <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-ink-2">
              {status.notes}
            </pre>
          )}
          {restartRequired ? (
            <p className="text-xs text-ink-2">
              升级完成。<strong className="font-medium text-ink">请重启 Magplot</strong>
              后生效——当前进程仍在运行旧版本代码。
            </p>
          ) : status.can_self_update ? (
            <div className="flex items-center gap-2">
              <Button variant="primary" onClick={() => void apply()} disabled={applying}>
                {applying ? '升级中…' : '下载并升级'}
              </Button>
              <a
                href={status.html_url}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-accent hover:underline"
              >
                查看发行说明
              </a>
            </div>
          ) : (
            <p className="text-xs text-ink-2">
              源码检出请自行执行 <code className="font-mono">{status.upgrade_command}</code>
            </p>
          )}
          {applyLog && (
            <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-sm bg-surface-2 p-1.5 font-mono text-xs text-ink-3">
              {applyLog}
            </pre>
          )}
        </div>
      ) : (
        status &&
        !status.error && <p className="text-xs text-ink-3">已是最新版本。</p>
      )}
    </div>
  )
}

function AboutSection() {
  const version = useUpdateStore((s) => s.status?.current)
  const [checks, setChecks] = useState<
    { id: string; ok: boolean; label: string; detail: string }[] | null
  >(null)
  useEffect(() => {
    void fetch('/api/diagnostics')
      .then((r) => r.json())
      .then((d) => setChecks(d.checks ?? []))
      .catch(() => setChecks([]))
  }, [])
  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-xs text-ink">
        {PRODUCT_NAME}
        {version && <span className="ml-1.5 font-mono text-ink-2">v{version}</span>}
        <span className="ml-2 text-ink-3">论文图排版与参数化图表编辑</span>
      </p>
      <p className="text-xs leading-relaxed text-ink-3">
        所有数据与渲染都在本机完成，不上传任何内容；改图助手调用的是你本机的
        Codex / Claude 命令行工具。唯一的对外请求是检查更新（可在「检查更新」里关闭）。
      </p>
      <p className="text-xs leading-relaxed text-ink-3">
        自由软件，以 AGPL-3.0-only 发布 ——{' '}
        <a
          href="https://github.com/erwanjun/magplot"
          target="_blank"
          rel="noreferrer"
          className="text-accent hover:underline"
        >
          获取源代码
        </a>
        。
      </p>
      <div>
        <h3 className="mb-1 text-xs font-medium text-ink-2">环境诊断</h3>
        {checks === null ? (
          <p className="text-xs text-ink-3">正在检测…</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {checks.map((c) => (
              <li key={c.id} className="flex items-start gap-1.5">
                {c.ok ? (
                  <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-ink-2" aria-hidden />
                ) : (
                  <XCircle size={13} className="mt-0.5 shrink-0 text-danger" aria-hidden />
                )}
                <span className="text-xs text-ink-2">{c.label}</span>
                <span className="min-w-0 flex-1 break-all text-right font-mono text-xs text-ink-3">
                  {c.detail}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
