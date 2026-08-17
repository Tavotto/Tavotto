import { useEffect, useState } from 'react'
import { CheckCircle2, XCircle } from 'lucide-react'
import { apiUrl, withProject } from '@/lib/session'
import {
  deleteAiEndpoint,
  patchAiSettings,
  patchProjectSettings,
  saveAiEndpoint,
  setAiEndpointActive,
  type AiEndpoint,
  type AiEndpointPreset,
} from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { readExportDefaults, writeExportDefaults } from '@/lib/exportDefaults'
import { cn } from '@/lib/utils'
import { useAiStore } from '@/store/aiStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { useUpdateStore } from '@/store/updateStore'
import { BrandMark } from './ui/BrandMark'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { EngineEnvironmentCard } from './EngineEnvironmentCard'
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
      <Row label="可参数化脚本">
        <span className="flex-1 text-xs text-ink-2">
          {project?.scripts ?? 0} 个脚本已登记
          {(project?.scripts ?? 0) === 0 && '（面板不会进入图内编辑）'}
        </span>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            useUiStore.getState().setSettingsOpen(false)
            useUiStore.getState().setRegistryOpen(true)
          }}
        >
          脚本注册表…
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
  const [editing, setEditing] = useState<null | { id?: string; agent: 'codex' | 'claude' }>(null)
  const [error, setError] = useState<string | null>(null)

  const apply = async (patch: { codex_path?: string; claude_path?: string }) => {
    setBusy(true)
    try {
      await patchAiSettings(patch)
      await useAiStore.getState().loadCaps(true)
    } finally {
      setBusy(false)
    }
  }

  const run = async (fn: () => Promise<unknown>) => {
    setError(null)
    setBusy(true)
    try {
      await fn()
      await useAiStore.getState().loadCaps(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const cli = (name: 'codex' | 'claude', label: string) => {
    const p = caps?.providers[name]
    const mine = (caps?.endpoints ?? []).filter((e) => e.agent === name)
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
        {!p?.installed && (p?.searched?.length ?? 0) > 0 && (
          <details className="mt-1">
            <summary className="cursor-default text-xs text-ink-3 outline-none focus-visible:focus-ring">
              找过这些位置
            </summary>
            <ul className="mt-0.5 flex flex-col gap-0.5">
              {p!.searched!.map((d) => (
                <li key={d} className="truncate font-mono text-xs text-ink-faint" title={d}>
                  {d}
                </li>
              ))}
            </ul>
            <p className="mt-1 text-xs text-ink-3">
              装在别处就把可执行文件路径填在下面。微软商店版 Codex 请用
              <span className="font-mono"> %LOCALAPPDATA%\Microsoft\WindowsApps\codex.exe </span>
              这个执行别名，而不是 WindowsApps 里那个受保护的包体目录。
            </p>
          </details>
        )}

        {/* 接口选择：官方登录态 or 某个第三方网关 */}
        <label className="mt-1.5 flex items-center gap-2">
          <span className="w-14 shrink-0 text-xs text-ink-2">接口</span>
          <select
            value={caps?.active[name] ?? ''}
            onChange={(e) => void run(() => setAiEndpointActive(name, e.target.value))}
            aria-label={`${label} 使用的接口`}
            className="h-7 min-w-0 flex-1 rounded-sm border border-border bg-surface px-1.5 text-xs text-ink outline-none focus-visible:focus-ring"
          >
            <option value="">CLI 自带登录态（官方）</option>
            {mine.map((e) => (
              <option key={e.id} value={e.id}>
                {e.label}
                {e.has_key ? '' : '（未填密钥）'}
              </option>
            ))}
          </select>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setEditing({ agent: name })}
          >
            添加接口…
          </Button>
        </label>
        {mine.length > 0 && (
          <ul className="mt-1 flex flex-col gap-0.5">
            {mine.map((e) => (
              <li key={e.id} className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-3" title={e.base_url}>
                  {e.base_url || '（官方默认地址）'}
                </span>
                <button
                  onClick={() => setEditing({ id: e.id, agent: name })}
                  className="shrink-0 text-xs text-ink-3 outline-none hover:text-ink focus-visible:focus-ring"
                >
                  编辑
                </button>
                <button
                  onClick={() => void run(() => deleteAiEndpoint(e.id))}
                  className="shrink-0 text-xs text-ink-3 outline-none hover:text-danger focus-visible:focus-ring"
                >
                  删除
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-xs leading-relaxed text-ink-3">
        改图助手借用你已装好的 codex / claude 命令行工具。接第三方接口时只在
        启动它们时临时注入环境变量，<strong className="font-medium text-ink-2">
        不会改写你自己的 ~/.claude 或 ~/.codex 配置</strong>。
      </p>
      {cli('codex', 'Codex')}
      {cli('claude', 'Claude')}
      <Row label="Codex 路径">
        <TextInput
          value={codexPath}
          onChange={(e) => setCodexPath(e.target.value)}
          onBlur={() => void apply({ codex_path: codexPath })}
          placeholder="留空 = 自动查找（PATH 与常见安装位置）"
          className="flex-1 font-mono"
          spellCheck={false}
        />
      </Row>
      <Row label="Claude 路径">
        <TextInput
          value={claudePath}
          onChange={(e) => setClaudePath(e.target.value)}
          onBlur={() => void apply({ claude_path: claudePath })}
          placeholder="留空 = 自动查找（PATH 与常见安装位置）"
          className="flex-1 font-mono"
          spellCheck={false}
        />
      </Row>
      {error && <p className="text-xs text-danger">{error}</p>}
      <div>
        <Button variant="outline" size="sm" loading={busy} onClick={() => void apply({})}>
          重新探测
        </Button>
      </div>

      {editing && (
        <EndpointDialog
          agent={editing.agent}
          existing={caps?.endpoints.find((e) => e.id === editing.id) ?? null}
          presets={(caps?.presets ?? []).filter((p) => p.agent === editing.agent)}
          onClose={() => setEditing(null)}
          onSave={(rec) => {
            setEditing(null)
            void run(() => saveAiEndpoint(rec))
          }}
        />
      )}
    </div>
  )
}

/**
 * 第三方接口编辑。密钥只写不读——后端从不回传，留空即保留原值，
 * 所以编辑一个已有接口时不必重新粘贴密钥。
 */
function EndpointDialog({
  agent,
  existing,
  presets,
  onClose,
  onSave,
}: {
  agent: 'codex' | 'claude'
  existing: AiEndpoint | null
  presets: AiEndpointPreset[]
  onClose: () => void
  onSave: (rec: Parameters<typeof saveAiEndpoint>[0]) => void
}) {
  const [label, setLabel] = useState(existing?.label ?? '')
  const [baseUrl, setBaseUrl] = useState(existing?.base_url ?? '')
  const [apiKey, setApiKey] = useState('')
  const [models, setModels] = useState((existing?.models ?? []).join(', '))
  const [wire, setWire] = useState<'responses' | 'chat'>(existing?.wire_api ?? 'chat')

  const applyPreset = (id: string) => {
    const p = presets.find((x) => x.id === id)
    if (!p) return
    setLabel(p.label)
    setBaseUrl(p.base_url)
    setModels(p.models.join(', '))
    if (p.wire_api) setWire(p.wire_api)
  }

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title={existing ? `编辑接口 — ${existing.label}` : `为 ${agent} 添加接口`}
      size="md"
      footer={
        <>
          <Button variant="outline" size="md" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="primary"
            size="md"
            disabled={!label.trim()}
            onClick={() =>
              onSave({
                id: existing?.id,
                label: label.trim(),
                agent,
                base_url: baseUrl.trim(),
                api_key: apiKey.trim() || undefined,
                models: models
                  .split(/[,，\s]+/)
                  .map((s) => s.trim())
                  .filter(Boolean),
                wire_api: wire,
              })
            }
          >
            保存
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        {!existing && presets.length > 0 && (
          <Row label="预设">
            <select
              defaultValue=""
              onChange={(e) => applyPreset(e.target.value)}
              aria-label="接口预设"
              className="h-7 flex-1 rounded-sm border border-border bg-surface px-1.5 text-xs text-ink outline-none focus-visible:focus-ring"
            >
              <option value="">选一个预设填好地址…</option>
              {presets.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </Row>
        )}
        <Row label="名称">
          <TextInput value={label} onChange={(e) => setLabel(e.target.value)} className="flex-1" />
        </Row>
        <Row label="接口地址">
          <TextInput
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={agent === 'claude' ? 'https://…/anthropic' : 'https://…/v1'}
            className="flex-1 font-mono"
            spellCheck={false}
          />
        </Row>
        <Row label="密钥">
          <TextInput
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={existing?.has_key ? `已保存 ${existing.key_hint}，留空则不改` : 'sk-…'}
            className="flex-1 font-mono"
            spellCheck={false}
          />
        </Row>
        <Row label="模型">
          <TextInput
            value={models}
            onChange={(e) => setModels(e.target.value)}
            placeholder="逗号分隔；第一个是默认值"
            className="flex-1 font-mono"
            spellCheck={false}
          />
        </Row>
        {agent === 'codex' && (
          <Row label="协议">
            <select
              value={wire}
              onChange={(e) => setWire(e.target.value as 'responses' | 'chat')}
              aria-label="wire api"
              className="h-7 flex-1 rounded-sm border border-border bg-surface px-1.5 text-xs text-ink outline-none focus-visible:focus-ring"
            >
              <option value="chat">chat（OpenAI Chat Completions 兼容，多数网关）</option>
              <option value="responses">responses（OpenAI Responses 原生）</option>
            </select>
          </Row>
        )}
        <p className="text-xs leading-relaxed text-ink-3">
          密钥保存在本机配置文件里（目录权限已收到仅本人可读），只在启动 CLI
          时作为环境变量传入，不写进任何命令行、日志或项目文件。
        </p>
      </div>
    </Dialog>
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

/** 诊断包：交给浏览器直接下载，不经前端内存（zip 可能不小） */
function downloadDiagnostics() {
  const a = document.createElement('a')
  a.href = apiUrl('/api/diagnostics/bundle')
  a.download = ''
  a.click()
}

function AboutSection() {
  const version = useUpdateStore((s) => s.status?.current)
  const [checks, setChecks] = useState<
    { id: string; ok: boolean; label: string; detail: string }[] | null
  >(null)
  useEffect(() => {
    void fetch(apiUrl('/api/diagnostics'), withProject())
      .then((r) => r.json())
      .then((d) => setChecks(d.checks ?? []))
      .catch(() => setChecks([]))
  }, [])
  return (
    <div className="flex flex-col gap-2.5">
      {/* About 是标志唯一允许的 full 档界面位置（54px，弹窗白底用默认灰） */}
      <div className="flex items-center gap-3">
        <BrandMark size={54} variant="full" />
        <p className="text-xs text-ink">
          {PRODUCT_NAME}
          {version && <span className="ml-1.5 font-mono text-ink-2">v{version}</span>}
          <span className="ml-2 text-ink-3">论文图排版与参数化图表编辑</span>
        </p>
      </div>
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
      <EngineEnvironmentCard />

      <div>
        <div className="mb-1 flex items-center justify-between gap-2">
          <h3 className="text-xs font-medium text-ink-2">环境诊断</h3>
          <Button variant="outline" size="sm" onClick={downloadDiagnostics}>
            导出诊断包
          </Button>
        </div>
        <p className="mb-1.5 text-xs leading-relaxed text-ink-3">
          遇到问题时导出一个 zip 发给我们：里面是版本、系统、渲染解释器、
          AI CLI 探测结果与最近日志。<strong className="font-medium text-ink-2">
          密钥与你的主目录已自动抹掉</strong>。
        </p>
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
