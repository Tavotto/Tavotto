import { useEffect, useState, type Dispatch, type SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCircle2, XCircle } from 'lucide-react'
import { apiUrl, withProject } from '@/lib/session'
import {
  backendErrorText,
  deleteAiEndpoint,
  fetchAiInstallStatus,
  patchAiSettings,
  patchProjectSettings,
  saveAiEndpoint,
  setAiEndpointActive,
  startAiInstall,
  type AiEndpoint,
  type AiEndpointPreset,
  type AiInstallState,
  type UpdateStatus,
} from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { msg, setLocale, SUPPORTED_LOCALES, LOCALE_LABELS, t as translate } from '@/i18n'
import { formatDateTime } from '@/i18n/format'
import { useLocale } from '@/i18n/react'
import { readExportDefaults, writeExportDefaults } from '@/lib/exportDefaults'
import { cn } from '@/lib/utils'
import { useAiStore } from '@/store/aiStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { useTelemetryStore } from '@/store/telemetryStore'
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

/** 本对话框的文案在 dialogs:settings.* 下 */
const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

const SECTIONS: SectionId[] = [
  'general',
  'project',
  'canvas',
  'sidebars',
  'ai',
  'export',
  'shortcuts',
  'update',
  'about',
]

export function SettingsDialog() {
  useTranslation('dialogs')
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
    <Dialog open onOpenChange={setOpen} title={st('title')} size="lg">
      <div className="flex min-h-72 gap-3">
        <nav aria-label={st('navLabel')} className="flex w-32 shrink-0 flex-col gap-0.5">
          {SECTIONS.map((id) => (
            <button
              key={id}
              onClick={() => setSection(id)}
              aria-current={section === id || undefined}
              className={cn(
                'relative h-7 rounded-sm px-2 text-left text-xs outline-none focus-visible:focus-ring',
                section === id
                  ? 'bg-accent-subtle font-medium text-ink'
                  : 'text-ink-2 hover:bg-ink/[.045]',
              )}
            >
              {section === id && (
                <span aria-hidden className="absolute left-0 top-1.5 h-4 w-0.5 rounded-full bg-accent" />
              )}
              {st(`section.${id}`)}
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
  useTranslation('dialogs')
  const setStatus = useUiStore((s) => s.setStatus)
  const locale = useLocale()
  return (
    <div className="flex flex-col gap-2.5">
      {/*
        语言：选完立刻生效（i18next 的 languageChanged 会让整棵树重渲染），
        偏好写在独立的 tavotto.locale 里，不进任何文档或项目数据。
      */}
      <Row label={st('general.language')}>
        <select
          value={locale}
          onChange={(e) => void setLocale(e.target.value as (typeof SUPPORTED_LOCALES)[number])}
          aria-label={st('general.language')}
          className="h-7 rounded-sm border border-border bg-surface px-1.5 text-xs text-ink outline-none focus-visible:focus-ring"
        >
          {SUPPORTED_LOCALES.map((l) => (
            <option key={l} value={l}>
              {LOCALE_LABELS[l]}
            </option>
          ))}
        </select>
      </Row>
      <p className="text-xs leading-relaxed text-ink-3">{st('general.languageHint')}</p>
      <p className="text-xs leading-relaxed text-ink-3">{st('general.autosaveHint')}</p>
      <div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            try {
              localStorage.removeItem('tavotto.ui')
            } catch {
              /* 忽略 */
            }
            setStatus(msg('settings.general.layoutReset', undefined, 'dialogs'))
          }}
        >
          {st('general.resetLayout')}
        </Button>
        <p className="mt-1 text-xs text-ink-3">{st('general.resetLayoutHint')}</p>
      </div>
    </div>
  )
}

function ProjectSection() {
  useTranslation('dialogs')
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
      setError(backendErrorText(e))
    }
  }

  return (
    <div className="flex flex-col gap-2.5">
      <Row label={st('project.current')}>
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
          {st('project.switch')}
        </Button>
      </Row>
      <Row label={st('project.scripts')}>
        <span className="flex-1 text-xs text-ink-2">
          {st('project.scriptCount', { count: project?.scripts ?? 0 })}
          {(project?.scripts ?? 0) === 0 && st('project.noScriptsSuffix')}
        </span>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            useUiStore.getState().setSettingsOpen(false)
            useUiStore.getState().setRegistryOpen(true)
          }}
        >
          {st('project.registry')}
        </Button>
      </Row>
      <Row label={st('project.exportDir')}>
        <TextInput
          value={exportDir}
          onChange={(e) => setExportDir(e.target.value)}
          onBlur={() => void save({ export_dir: exportDir })}
          placeholder={st('project.defaultPlaceholder', { path: project?.export_dir ?? 'exports/' })}
          className="flex-1"
        />
      </Row>
      <Row label={st('project.backupDir')}>
        <TextInput
          value={backupDir}
          onChange={(e) => setBackupDir(e.target.value)}
          onBlur={() => void save({ backup_dir: backupDir })}
          placeholder={st('project.defaultPlaceholder', {
            path: project?.backup_dir ?? 'cache/original_backups/',
          })}
          className="flex-1"
        />
      </Row>
      <Row label={st('project.readOnly')}>
        <Toggle
          checked={readonly}
          onChange={(v) => void save({ allow_write_back: !v })}
          aria-label={st('project.readOnlyAria')}
        />
        <span className="text-xs text-ink-3">{st('project.readOnlyHint')}</span>
      </Row>
      {error && <p className="text-xs text-danger">{error}</p>}
      <p className="text-xs leading-relaxed text-ink-3">{st('project.dirHint')}</p>
    </div>
  )
}

function CanvasSection({ close }: { close: () => void }) {
  useTranslation('dialogs')
  const withCompanions = useUiStore((s) => s.dragAxesWithCompanions)
  return (
    <div className="flex flex-col gap-2.5">
      <Row label={st('canvas.dragCompanions')}>
        <Toggle
          checked={withCompanions}
          onChange={(v) => useUiStore.getState().setCanvasPref({ dragAxesWithCompanions: v })}
          aria-label={st('canvas.dragCompanionsAria')}
        />
        <span className="text-xs text-ink-3">{st('canvas.dragCompanionsHint')}</span>
      </Row>
      <p className="text-xs leading-relaxed text-ink-3">{st('canvas.companionsExplain')}</p>
      <p className="text-xs leading-relaxed text-ink-3">{st('canvas.elsewhere')}</p>
      <div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            close()
            useUiStore.getState().setRightTab('canvas')
          }}
        >
          {st('canvas.openCanvasSettings')}
        </Button>
      </div>
    </div>
  )
}

function SidebarsSection() {
  useTranslation('dialogs')
  const leftPinned = useUiStore((s) => s.leftPinned)
  const rightPinned = useUiStore((s) => s.rightPinned)
  return (
    <div className="flex flex-col gap-2.5">
      <Row label={st('sidebars.leftPinned')}>
        <Toggle
          checked={leftPinned}
          onChange={(v) => useUiStore.getState().setLeftPinned(v)}
          aria-label={st('sidebars.leftPinned')}
        />
        <span className="text-xs text-ink-3">{st('sidebars.leftPinnedHint')}</span>
      </Row>
      <Row label={st('sidebars.rightPinned')}>
        <Toggle
          checked={rightPinned}
          onChange={(v) => useUiStore.getState().setRightPinned(v)}
          aria-label={st('sidebars.rightPinned')}
        />
        <span className="text-xs text-ink-3">{st('sidebars.rightPinnedHint')}</span>
      </Row>
      <p className="text-xs leading-relaxed text-ink-3">{st('sidebars.breakpoints')}</p>
    </div>
  )
}

function AiSection() {
  useTranslation('dialogs')
  const caps = useAiStore((s) => s.caps)
  // 已存路径以后端为准（caps.settings，PATCH 后随 loadCaps 一起刷新）；本地
  // state 只承载「正在编辑的值」，null = 未编辑（输入框直接显示后端那份）。
  // 以前这里 useState('') 且 onBlur 无条件 PATCH——打开设置再移走一次焦点，
  // 空字符串就把用户存好的路径删掉了（issue #89）。
  const [codexPath, setCodexPath] = useState<string | null>(null)
  const [claudePath, setClaudePath] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<null | { id?: string; agent: 'codex' | 'claude' }>(null)
  const [error, setError] = useState<string | null>(null)

  const savedPaths = {
    codex_path: caps?.settings?.codex_path ?? '',
    claude_path: caps?.settings?.claude_path ?? '',
  }

  const apply = async (patch: { codex_path?: string; claude_path?: string }) => {
    setBusy(true)
    try {
      await patchAiSettings(patch)
      await useAiStore.getState().loadCaps(true)
    } finally {
      setBusy(false)
    }
  }

  /** 失焦提交：没编辑过或值没变就一个请求都不发（清空 = 显式删除，照发）。 */
  const commitPath = async (
    key: 'codex_path' | 'claude_path',
    edited: string | null,
    setPath: Dispatch<SetStateAction<string | null>>,
  ) => {
    if (edited === null) return
    const value = edited.trim()
    // 归位只在草稿仍是这次提交的值时发生：PATCH + 重探测要跑上几秒，期间
    // 用户可能已经重新聚焦继续编辑，无条件 reset(null) 会把更新的草稿顶掉
    const settle = () => setPath((cur) => (cur === edited ? null : cur))
    if (value === savedPaths[key]) {
      settle()
      return
    }
    try {
      await apply({ [key]: value })
      settle()
    } catch (e) {
      setError(backendErrorText(e))   // 提交失败：保留正在编辑的值
    }
  }

  const run = async (fn: () => Promise<unknown>) => {
    setError(null)
    setBusy(true)
    try {
      await fn()
      await useAiStore.getState().loadCaps(true)
    } catch (e) {
      setError(backendErrorText(e))
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
            {p?.installed ? p.version : st('ai.notDetected')}
          </span>
        </div>
        {p?.path && (
          <p className="mt-0.5 truncate font-mono text-xs text-ink-3" title={p.path}>
            {p.path}
          </p>
        )}
        {!p?.installed && (
          <div className="mt-1.5 flex flex-col gap-1.5">
            {p?.broken_path && (
              <p className="text-xs leading-relaxed text-ink-3">
                {st('ai.brokenBefore')}
                <span className="text-ink">{st('ai.brokenStrong')}</span>
                {st('ai.brokenAfter')}
                <span className="block truncate font-mono text-xs text-ink-faint" title={p.broken_path}>
                  {p.broken_path}
                </span>
              </p>
            )}
            <InstallCliButton agent={name} label={label} />
            {(p?.searched?.length ?? 0) > 0 && (
              <details>
                <summary className="cursor-default text-xs text-ink-3 outline-none focus-visible:focus-ring">
                  {st('ai.searchedSummary')}
                </summary>
                <ul className="mt-0.5 flex flex-col gap-0.5">
                  {p!.searched!.map((d) => (
                    <li key={d} className="truncate font-mono text-xs text-ink-faint" title={d}>
                      {d}
                    </li>
                  ))}
                </ul>
                <p className="mt-1 text-xs text-ink-3">{st('ai.searchedHint')}</p>
              </details>
            )}
          </div>
        )}

        {/* 接口选择：官方登录态 or 某个第三方网关 */}
        <label className="mt-1.5 flex items-center gap-2">
          <span className="w-14 shrink-0 text-xs text-ink-2">{st('ai.endpoint')}</span>
          <select
            value={caps?.active[name] ?? ''}
            onChange={(e) => void run(() => setAiEndpointActive(name, e.target.value))}
            aria-label={st('ai.endpointAria', { label })}
            className="h-7 min-w-0 flex-1 rounded-sm border border-border bg-surface px-1.5 text-xs text-ink outline-none focus-visible:focus-ring"
          >
            <option value="">{st('ai.officialLogin')}</option>
            {mine.map((e) => (
              <option key={e.id} value={e.id}>
                {e.label}
                {e.has_key ? '' : st('ai.noKeySuffix')}
              </option>
            ))}
          </select>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setEditing({ agent: name })}
          >
            {st('ai.addEndpoint')}
          </Button>
        </label>
        {mine.length > 0 && (
          <ul className="mt-1 flex flex-col gap-0.5">
            {mine.map((e) => (
              <li key={e.id} className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-3" title={e.base_url}>
                  {e.base_url || st('ai.officialBaseUrl')}
                </span>
                <button
                  onClick={() => setEditing({ id: e.id, agent: name })}
                  className="shrink-0 text-xs text-ink-3 outline-none hover:text-ink focus-visible:focus-ring"
                >
                  {st('ai.edit')}
                </button>
                <button
                  onClick={() => void run(() => deleteAiEndpoint(e.id))}
                  className="shrink-0 text-xs text-ink-3 outline-none hover:text-danger focus-visible:focus-ring"
                >
                  {st('ai.delete')}
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
        {st('ai.introBefore')}
        <strong className="font-medium text-ink-2">{st('ai.introStrong')}</strong>
        {st('ai.introAfter')}
      </p>
      {cli('codex', 'Codex')}
      {cli('claude', 'Claude')}
      <Row label={st('ai.codexPath')}>
        <TextInput
          value={codexPath ?? savedPaths.codex_path}
          onChange={(e) => setCodexPath(e.target.value)}
          onBlur={() => void commitPath('codex_path', codexPath, setCodexPath)}
          placeholder={st('ai.pathPlaceholder')}
          className="flex-1 font-mono"
          spellCheck={false}
        />
      </Row>
      <Row label={st('ai.claudePath')}>
        <TextInput
          value={claudePath ?? savedPaths.claude_path}
          onChange={(e) => setClaudePath(e.target.value)}
          onBlur={() => void commitPath('claude_path', claudePath, setClaudePath)}
          placeholder={st('ai.pathPlaceholder')}
          className="flex-1 font-mono"
          spellCheck={false}
        />
      </Row>
      {error && <p className="text-xs text-danger">{error}</p>}
      <div>
        <Button variant="outline" size="sm" loading={busy} onClick={() => void apply({})}>
          {st('ai.redetect')}
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
 * 一键安装 CLI：后台 `npm install -g <包名>`，前端轮询进度；装完自动重探测。
 * 没有 npm 时不静默装 Node——明确引导用户先装 Node.js LTS（那是另一个量级的
 * 系统改动，必须由用户自己做）。
 */
function InstallCliButton({ agent, label }: { agent: 'codex' | 'claude'; label: string }) {
  useTranslation('dialogs')
  const info = useAiStore((s) => s.caps?.providers[agent]?.install)
  const [state, setState] = useState<AiInstallState | null>(null)
  // 局部变量改名：st 已经是本文件的翻译助手
  const installState = state ?? (info && info.status !== 'idle' ? info : null)
  const running = installState?.status === 'running'

  useEffect(() => {
    if (!running) return
    const t = window.setInterval(() => {
      void (async () => {
        try {
          const s = await fetchAiInstallStatus(agent)
          setState(s)
          if (s.status === 'done') {
            // 装好后重探测；provider 变为已安装，这一块整体消失，
            // 绿勾 + 版本号就是完成反馈
            await useAiStore.getState().loadCaps(true)
          }
        } catch {
          /* 网络抖动：下一轮再问 */
        }
      })()
    }, 2000)
    return () => window.clearInterval(t)
  }, [running, agent])

  if (!info) return null

  const begin = async () => {
    setState({ status: 'running' })
    try {
      setState(await startAiInstall(agent))
    } catch (e) {
      setState({ status: 'error', code: 'spawn_failed', log: String(e) })
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Button size="sm" loading={running} disabled={!info.available} onClick={() => void begin()}>
          {running ? st('ai.installing') : st('ai.installWithNpm', { label })}
        </Button>
        <span className="min-w-0 truncate font-mono text-xs text-ink-faint">
          {st('ai.npmCommand', { package: info.package ?? agent })}
        </span>
      </div>
      {!info.available && (
        <p className="text-xs leading-relaxed text-ink-3">{st('ai.noNpm')}</p>
      )}
      {installState?.status === 'error' && (
        <p className="text-xs text-danger">
          {/* 后端给的是稳定 code，人话在这里按当前语言取 */}
          {st(
            `ai.installError.${
              installState.code === 'npm_missing' ||
              installState.code === 'installed_but_not_found' ||
              installState.code === 'timeout'
                ? installState.code
                : 'other'
            }`,
          )}
        </p>
      )}
      {installState?.status === 'error' && installState.log && (
        <details>
          <summary className="cursor-default text-xs text-ink-3 outline-none focus-visible:focus-ring">
            {st('ai.installLog')}
          </summary>
          <pre className="mt-0.5 max-h-40 overflow-auto whitespace-pre-wrap rounded-sm border border-border bg-surface p-1.5 font-mono text-xs text-ink-3">
            {installState.log}
          </pre>
        </details>
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
  const { t } = useTranslation(['dialogs', 'common'])
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
      title={
        existing
          ? st('ai.editEndpointTitle', { label: existing.label })
          : st('ai.addEndpointTitle', { agent })
      }
      size="md"
      footer={
        <>
          <Button variant="outline" size="md" onClick={onClose}>
            {t('common:actions.cancel')}
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
            {t('common:actions.save')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        {!existing && presets.length > 0 && (
          <Row label={st('ai.preset')}>
            <select
              defaultValue=""
              onChange={(e) => applyPreset(e.target.value)}
              aria-label={st('ai.presetAria')}
              className="h-7 flex-1 rounded-sm border border-border bg-surface px-1.5 text-xs text-ink outline-none focus-visible:focus-ring"
            >
              <option value="">{st('ai.presetPlaceholder')}</option>
              {presets.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </Row>
        )}
        <Row label={st('ai.name')}>
          <TextInput value={label} onChange={(e) => setLabel(e.target.value)} className="flex-1" />
        </Row>
        <Row label={st('ai.baseUrl')}>
          <TextInput
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={agent === 'claude' ? 'https://…/anthropic' : 'https://…/v1'}
            className="flex-1 font-mono"
            spellCheck={false}
          />
        </Row>
        <Row label={st('ai.apiKey')}>
          <TextInput
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={
              existing?.has_key ? st('ai.apiKeySaved', { hint: existing.key_hint }) : 'sk-…'
            }
            className="flex-1 font-mono"
            spellCheck={false}
          />
        </Row>
        <Row label={st('ai.models')}>
          <TextInput
            value={models}
            onChange={(e) => setModels(e.target.value)}
            placeholder={st('ai.modelsPlaceholder')}
            className="flex-1 font-mono"
            spellCheck={false}
          />
        </Row>
        {agent === 'codex' && (
          <Row label={st('ai.wire')}>
            <select
              value={wire}
              onChange={(e) => setWire(e.target.value as 'responses' | 'chat')}
              aria-label="wire api"
              className="h-7 flex-1 rounded-sm border border-border bg-surface px-1.5 text-xs text-ink outline-none focus-visible:focus-ring"
            >
              <option value="chat">{st('ai.wireChat')}</option>
              <option value="responses">{st('ai.wireResponses')}</option>
            </select>
          </Row>
        )}
        <p className="text-xs leading-relaxed text-ink-3">{st('ai.keyNote')}</p>
      </div>
    </Dialog>
  )
}

function ExportSection() {
  useTranslation('dialogs')
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
      <Row label={st('export.defaultDpi')}>
        <select
          value={defaults.dpi}
          onChange={(e) => update({ dpi: e.target.value })}
          aria-label={st('export.defaultDpi')}
          className="h-7 rounded-sm border border-border bg-surface px-1.5 text-xs text-ink outline-none focus-visible:focus-ring"
        >
          {['300', '600', '900', '1200'].map((d) => (
            <option key={d} value={d}>
              {translate('measure.dpi', { value: d })}
            </option>
          ))}
        </select>
      </Row>
      <Row label={st('export.defaultFormats')}>
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
      <Row label={st('export.proof')}>
        <Toggle
          checked={defaults.withProof}
          onChange={(v) => update({ withProof: v })}
          aria-label={st('export.proofAria')}
        />
        <span className="text-xs text-ink-3">{st('export.proofHint')}</span>
      </Row>
      <p className="text-xs leading-relaxed text-ink-3">{st('export.hint')}</p>
    </div>
  )
}

function ShortcutsSection({ close }: { close: () => void }) {
  useTranslation('dialogs')
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs leading-relaxed text-ink-3">{st('shortcuts.hint')}</p>
      <div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            close()
            useUiStore.getState().setShortcutHelpOpen(true)
          }}
        >
          {st('shortcuts.open')}
        </Button>
      </div>
    </div>
  )
}

function UpdateSection() {
  useTranslation('dialogs')
  const {
    status,
    checking,
    applying,
    restartRequired,
    applyLog,
    checkError,
    check,
    apply,
    setAutoCheck,
  } = useUpdateStore()
  useEffect(() => {
    if (!status) void check(false)
  }, [status, check])

  const checkedAt = status?.checked_at_ms
    ? formatDateTime(status.checked_at_ms)
    : st('update.neverChecked')

  // 桌面模式：Python updater 整个停用（升级归 Tauri 层），整段换成
  // 壳里的更新器——检查 / 下载 / 安装 / 重启都在软件内完成
  if (status?.desktop) return <DesktopUpdateSection status={status} />

  return (
    <div className="flex flex-col gap-2.5">
      <Row label={st('update.currentVersion')}>
        <span className="font-mono text-xs text-ink">{status?.current ?? '…'}</span>
      </Row>
      <Row label={st('update.installMethod')}>
        <span className="text-xs text-ink-2">
          {status?.method === 'pipx'
            ? 'pipx'
            : status?.method === 'source'
              ? st('update.methodSource')
              : 'pip'}
        </span>
      </Row>
      <Row label={st('update.autoCheck')}>
        <Toggle
          checked={status?.auto_check ?? true}
          onChange={(v) => void setAutoCheck(v)}
          aria-label={st('update.autoCheckAria')}
        />
        <span className="text-xs text-ink-3">{st('update.autoCheckHint')}</span>
      </Row>

      <div className="flex items-center gap-2">
        <Button onClick={() => void check(true)} disabled={checking}>
          {st(checking ? 'update.checking' : 'update.checkNow')}
        </Button>
        <span className="text-xs text-ink-3">{st('update.lastChecked', { time: checkedAt })}</span>
      </div>

      {status?.error && (
        <p role="alert" className="text-xs text-danger">
          {/* code 有本地文案时按界面语言渲染；error 中文原文只作回退（issue #30） */}
          {status.code === 'update_check_failed'
            ? translate('update.checkFailed', {
                ns: 'errors',
                error: String(status.params?.error ?? ''),
              })
            : status.error}
        </p>
      )}
      {checkError && <p className="text-xs text-danger">{checkError}</p>}

      {status?.update_available ? (
        <div className="flex flex-col gap-2 rounded-md border border-border p-2.5">
          <p className="text-xs text-ink">
            {st('update.available')} <span className="font-mono">{status.latest}</span>
            <span className="ml-2 text-ink-3">{st('update.currentIs', { version: status.current })}</span>
          </p>
          {status.notes && (
            <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-ink-2">
              {status.notes}
            </pre>
          )}
          {restartRequired ? (
            <p className="text-xs text-ink-2">
              {st('update.restartBefore')}
              <strong className="font-medium text-ink">{st('update.restartStrong')}</strong>
              {st('update.restartAfter')}
            </p>
          ) : status.can_self_update ? (
            <div className="flex items-center gap-2">
              <Button variant="primary" onClick={() => void apply()} disabled={applying}>
                {st(applying ? 'update.upgrading' : 'update.downloadAndUpgrade')}
              </Button>
              <a
                href={status.html_url}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-accent hover:underline"
              >
                {st('update.releaseNotes')}
              </a>
            </div>
          ) : (
            <p className="text-xs text-ink-2">
              {st('update.sourceUpgrade')}{' '}
              <code className="font-mono">{status.upgrade_command}</code>
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
        !status.error && <p className="text-xs text-ink-3">{st('update.upToDate')}</p>
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

/**
 * 桌面版的更新器（Tauri）。**整个过程留在软件里**：检查 → 下载（带进度）→
 * 安装 → 重启，用户不用去 Releases 页面手动下载覆盖安装。
 *
 * 三条纪律与 pip 那条一致：
 *   * 不静默——每一步都要用户按一下；
 *   * 装完不等于生效，重启才算换版本；
 *   * 失败要说人话并留退路（更新器连不上时仍给 Releases 链接）。
 *
 * 安装包的签名由壳里的公钥校验，校验不过当场失败——这里不做「忽略签名」的口子。
 */
function DesktopUpdateSection({ status }: { status: UpdateStatus }) {
  useTranslation('dialogs')
  const {
    desktopPhase,
    desktopUpdate,
    desktopProgress,
    desktopError,
    desktopChecked,
    checkDesktop,
    installDesktop,
    relaunch,
  } = useUpdateStore()
  useEffect(() => {
    if (!desktopChecked) void checkDesktop()
  }, [desktopChecked, checkDesktop])

  const busy = desktopPhase !== 'idle'
  const pct = desktopProgress === null ? null : Math.round(desktopProgress * 100)

  return (
    <div className="flex flex-col gap-2.5">
      <Row label={st('update.currentVersion')}>
        <span className="font-mono text-xs text-ink">{status.current}</span>
      </Row>

      <div className="flex items-center gap-2">
        <Button onClick={() => void checkDesktop()} disabled={busy}>
          {st(desktopPhase === 'checking' ? 'update.checking' : 'update.checkNow')}
        </Button>
        {desktopChecked && !desktopUpdate && !desktopError && (
          <span className="text-xs text-ink-3">{st('update.upToDate')}</span>
        )}
      </div>

      {desktopError && (
        <div className="flex flex-col gap-1">
          <p className="text-xs text-danger">{desktopError}</p>
          <a
            href={status.releases_url}
            target="_blank"
            rel="noreferrer"
            className="text-xs text-accent hover:underline"
          >
            {st('update.manualDownload')}
          </a>
        </div>
      )}

      {desktopUpdate && (
        <div className="flex flex-col gap-2 rounded-md border border-border p-2.5">
          <p className="text-xs text-ink">
            {st('update.available')} <span className="font-mono">{desktopUpdate.version}</span>
            <span className="ml-2 text-ink-3">{st('update.currentIs', { version: status.current })}</span>
          </p>
          {desktopUpdate.notes && (
            <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-ink-2">
              {desktopUpdate.notes}
            </pre>
          )}

          {desktopPhase === 'installed' ? (
            <div className="flex items-center gap-2">
              <Button variant="primary" onClick={() => void relaunch()}>
                {st('update.relaunch')}
              </Button>
              <span className="text-xs text-ink-2">{st('update.installedHint')}</span>
            </div>
          ) : desktopPhase === 'downloading' ? (
            <div className="flex flex-col gap-1">
              {/* 拿不到 Content-Length 就走不确定态，不假装卡在某个百分比 */}
              <div
                role="progressbar"
                aria-label={st('update.downloadProgressAria')}
                aria-valuenow={pct ?? undefined}
                aria-valuemin={0}
                aria-valuemax={100}
                className="h-1 overflow-hidden rounded-full bg-surface-2"
              >
                <div
                  className={cn('h-full bg-accent', pct === null && 'w-1/3 animate-pulse')}
                  style={pct === null ? undefined : { width: `${pct}%` }}
                />
              </div>
              <span className="text-xs text-ink-3">
                {pct === null ? st('update.downloading') : st('update.downloadingPct', { pct })}
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Button variant="primary" onClick={() => void installDesktop()}>
                {st('update.downloadAndInstall')}
              </Button>
              <a
                href={status.releases_url}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-accent hover:underline"
              >
                {st('update.releaseNotes')}
              </a>
            </div>
          )}
        </div>
      )}

      <p className="text-xs leading-relaxed text-ink-3">
        {st('update.signatureNote')}
      </p>
    </div>
  )
}

/**
 * 匿名用量统计的开关。**放在「隐私、诊断与 About」这一档里**，而不是新开一个
 * 分区：这一档本来就是隐私相关的落点（隐私声明 + 诊断包就在同一屏），
 * 用户找「这东西会不会上传我的图」时会来这里。
 *
 * 描述必须同时写清楚**发什么**和**绝不发什么**——只写前者的开关等于没解释。
 */
function TelemetrySection() {
  useTranslation('dialogs')
  const settings = useTelemetryStore((s) => s.settings)
  const choose = useTelemetryStore((s) => s.choose)
  const load = useTelemetryStore((s) => s.load)
  useEffect(() => {
    if (!settings) void load()
  }, [settings, load])

  const hard = settings?.hard_disabled ?? false
  return (
    <div className="rounded-md border border-border p-2.5">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 className="text-xs font-medium text-ink-2">{st('about.telemetry.title')}</h3>
        <Toggle
          checked={settings?.enabled ?? false}
          // 管理员关掉时开关是死的：还能点的话用户会以为自己打开了，
          // 而实际上一个字节都不会发
          disabled={hard || !settings}
          aria-label={st('about.telemetry.toggle')}
          onChange={(v) => void choose(v ? 'enabled' : 'disabled', 'settings')}
        />
      </div>
      {/*
        「跨会话稳定」这一句要突出：它是这段话诚实性的关键——没有它，读者会以为
        每次启动都是全新的匿名身份，而我们确实靠它算留存。
        **强调走 JSX 的 <strong>，不是文案里的 Markdown `**`**：这个 <p> 是纯文本
        插值，不是 Markdown 渲染器，写 `**` 用户就会看到两个字面星号
        （与 about.diagnosticsHint* 同一套写法）。
      */}
      <p className="text-xs leading-relaxed text-ink-3">
        {st('about.telemetry.sendsBefore')}
        <strong className="font-medium text-ink-2">{st('about.telemetry.sendsPersist')}</strong>
        {st('about.telemetry.sendsAfter')}
      </p>
      <p className="mt-1 text-xs leading-relaxed text-ink-3">
        <strong className="font-medium text-ink-2">{st('about.telemetry.neverLabel')}</strong>
        {st('about.telemetry.never')}
      </p>
      {hard && (
        <p className="mt-1 text-xs leading-relaxed text-ink-2">
          {st('about.telemetry.hardDisabled')}
        </p>
      )}
      <a
        href="https://github.com/Tavotto/Tavotto/blob/main/docs/privacy.md"
        target="_blank"
        rel="noreferrer"
        className="mt-1 inline-block text-xs text-accent hover:underline"
      >
        {st('about.telemetry.policy')}
      </a>
    </div>
  )
}

function AboutSection() {
  useTranslation('dialogs')
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
          <span className="ml-2 text-ink-3">{st('about.tagline')}</span>
        </p>
      </div>
      <p className="text-xs leading-relaxed text-ink-3">{st('about.privacy')}</p>
      <TelemetrySection />
      <p className="text-xs leading-relaxed text-ink-3">
        {st('about.licenseBefore')}{' '}
        <a
          href="https://github.com/Tavotto/Tavotto"
          target="_blank"
          rel="noreferrer"
          className="text-accent hover:underline"
        >
          {st('about.source')}
        </a>
        {st('about.licenseAfter')}
      </p>
      <EngineEnvironmentCard />

      <div>
        <div className="mb-1 flex items-center justify-between gap-2">
          <h3 className="text-xs font-medium text-ink-2">{st('about.diagnosticsTitle')}</h3>
          <Button variant="outline" size="sm" onClick={downloadDiagnostics}>
            {st('about.exportBundle')}
          </Button>
        </div>
        <p className="mb-1.5 text-xs leading-relaxed text-ink-3">
          {st('about.diagnosticsHintBefore')}
          <strong className="font-medium text-ink-2">{st('about.diagnosticsHintStrong')}</strong>
          {st('about.diagnosticsHintAfter')}
        </p>
        {checks === null ? (
          <p className="text-xs text-ink-3">{st('about.detecting')}</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {checks.map((c) => (
              <li key={c.id} className="flex items-start gap-1.5">
                {c.ok ? (
                  <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-ink-2" aria-hidden />
                ) : (
                  <XCircle size={13} className="mt-0.5 shrink-0 text-danger" aria-hidden />
                )}
                {/*
                  后端给的是稳定 id + 中文 label；已知 id 在这里换成当前语言，
                  没登记的 id 原样用后端那条（新增检查项不会变成空白）。
                  detail 是诊断数据（路径 / 版本），刻意不翻。
                */}
                <span className="text-xs text-ink-2">
                  {translate(`settings.about.check.${c.id}`, { ns: 'dialogs', defaultValue: c.label })}
                </span>
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
