import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Braces, Check, Play, RefreshCw } from 'lucide-react'
import {
  backendCodeMsg,
  backendErrorText,
  fetchRegistry,
  probeScript,
  scanRegistry,
  writeRegistryEntry,
  type CapturedFigureDescriptor,
  type RegistryCandidate,
  type RegistryView,
  type ScriptInventoryEntry,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { formatMessage, msg, t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import { addRuntimePanel } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { EmptyState } from './ui/EmptyState'
import { TextInput } from './ui/Input'

/**
 * 脚本注册表：stem（输出文件名主干）↔ 产出它的 matplotlib 脚本。
 *
 * 面板上没有 ⚡（不可参数化）几乎总是这张表的问题，而它以前只存在于图库根
 * 目录的 tavotto_registry.json 里——用户既看不到现状，也不知道该往里写什么。
 *
 * 三条路对应三种情况：
 *   * 重新扫描 —— 脚本改了名 / 新加了脚本，静态扫描能解出来的直接补上；
 *   * 试运行 —— 文件名只有运行时才知道（遍历数据目录、读配置），跑一遍按
 *     真实产出登记，这是唯一可靠的办法；
 *   * 手工填写 —— 归属冲突的裁决，以及前两条都失手时的兜底。
 */
/** 本对话框的文案在 dialogs:registry.* 下 */
const rg = (key: string, values?: Record<string, unknown>) =>
  translate(`registry.${key}`, { ns: 'dialogs', ...(values ?? {}) })

export function RegistryDialog() {
  useTranslation('dialogs')
  const open = useUiStore((s) => s.registryOpen)
  const setOpen = useUiStore((s) => s.setRegistryOpen)
  if (!open) return null
  return (
    <Dialog open onOpenChange={setOpen} title={rg('title')} size="lg">
      <RegistryBody />
    </Dialog>
  )
}

/** 一次试运行的界面记录：主文案按 code 翻成当前语言，traceback 只进诊断详情 */
interface ProbeNote {
  text: string
  traceback?: string
  /** 成功时：本次捕获的描述符（「添加到画布」把它们放成 runtime 面板） */
  descriptors?: CapturedFigureDescriptor[]
}

function RegistryBody() {
  useTranslation('dialogs')
  const [view, setView] = useState<RegistryView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [probed, setProbed] = useState<Record<string, ProbeNote>>({})

  const reload = async () => {
    try {
      setView(await fetchRegistry())
      setError(null)
    } catch (e) {
      setError(backendErrorText(e))
    }
  }

  useEffect(() => {
    void reload()
  }, [])

  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key)
    setError(null)
    try {
      await fn()
      await reload()
      // 注册表变了，面板的 ⚡ 状态跟着变——素材库必须重取
      await useAssetStore.getState().load()
    } catch (e) {
      setError(backendErrorText(e))
    } finally {
      setBusy(null)
    }
  }

  const scan = () =>
    run('scan', async () => {
      const res = await scanRegistry()
      const n = res.changes.added_scripts.length
      useUiStore
        .getState()
        .setStatus(
          n
            ? msg('registry.registered', { count: n }, 'dialogs')
            : msg('registry.nothingNew', undefined, 'dialogs'),
        )
    })

  const probe = (script: string) =>
    run(script, async () => {
      const res = await probeScript(script)
      if (res.error) {
        // 主文案先按稳定 code 翻成当前语言（后端中文原文只是回退）；
        // traceback 不进主文案，收在「诊断详情」里。
        const text = formatMessage(backendCodeMsg(res.error.code, res.error.params, res.error.message))
        setProbed((p) => ({ ...p, [script]: { text, traceback: res.error?.traceback } }))
        throw new Error(rg('probeFailed', { script, error: text }))
      }
      const parts = [rg('probeRegistered', { stems: listJoin(res.stems) })]
      if (res.dropped_figures) parts.push(rg('probeDropped', { count: res.dropped_figures }))
      setProbed((p) => ({
        ...p,
        [script]: { text: parts.join(' '), descriptors: res.descriptors },
      }))
    })

  const registered = Object.entries(view?.scripts ?? {})
  const conflicts = Object.entries(view?.conflicts ?? {})

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs leading-relaxed text-ink-3">
          {rg('introBefore')} <Braces size={11} className="inline -mt-0.5" />
          {rg('introAfter')}
        </p>
        <Button variant="outline" size="sm" disabled={busy !== null} onClick={() => void scan()}>
          <RefreshCw size={13} className={cn(busy === 'scan' && 'animate-spin')} />
          {rg('rescan')}
        </Button>
      </div>

      {error && (
        <p role="alert" className="text-xs leading-relaxed text-danger">
          {error}
        </p>
      )}

      {conflicts.length > 0 && (
        <section className="rounded-md border border-border bg-surface-2 p-2">
          <h3 className="mb-1 flex items-center gap-1.5 text-xs font-medium text-ink">
            <AlertTriangle size={12} className="text-danger" />
            {rg('conflictsTitle')}
          </h3>
          <ul className="flex flex-col gap-0.5">
            {conflicts.map(([stem, scripts]) => (
              <li key={stem} className="text-xs text-ink-2">
                <span className="font-mono text-ink">{stem}</span>
                {rg('conflictScripts', { scripts: scripts.join(' vs ') })}
              </li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-ink-3">{rg('conflictHint')}</p>
        </section>
      )}

      <section>
        <h3 className="mb-1 text-xs font-medium text-ink-2">{rg('candidatesTitle')}</h3>
        {view && view.candidates.length === 0 ? (
          <p className="text-xs text-ink-3">{rg('noCandidates')}</p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {view?.candidates.map((c) => (
              <CandidateRow
                key={c.script}
                candidate={c}
                busy={busy === c.script}
                disabled={busy !== null}
                note={probed[c.script]}
                onProbe={() => void probe(c.script)}
                onRegister={(stems, entry) =>
                  void run(c.script, async () => {
                    await writeRegistryEntry({ script: c.script, entry, stems })
                  })
                }
              />
            ))}
          </ul>
        )}
      </section>

      <section>
        <h3 className="mb-1 text-xs font-medium text-ink-2">
          {rg('registeredTitle', { count: registered.length })}
        </h3>
        {registered.length === 0 ? (
          <EmptyState
            icon={Braces}
            title={rg('emptyTitle')}
            hint={rg('emptyHint')}
          />
        ) : (
          <ul className="max-h-40 overflow-y-auto rounded-sm border border-border">
            {registered.map(([script, cfg]) => (
              <li
                key={script}
                className="flex items-baseline gap-2 border-b border-border px-2 py-1 last:border-b-0"
              >
                <span className="shrink-0 font-mono text-xs text-ink">{script}</span>
                <span className="shrink-0 text-xs text-ink-3">[{cfg.entry}]</span>
                <span
                  className="min-w-0 flex-1 truncate text-xs text-ink-2"
                  title={listJoin(cfg.stems)}
                >
                  {listJoin(cfg.stems)}
                </span>
                {/* 脚本改了、多出了图：重跑一遍按真实产出重新登记 */}
                <button
                  disabled={busy !== null}
                  onClick={() => void probe(script)}
                  className={cn(
                    'shrink-0 text-xs text-ink-3 outline-none',
                    'hover:text-ink focus-visible:focus-ring disabled:opacity-40',
                  )}
                >
                  {rg(busy === script ? 'running' : 'reprobe')}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {view && view.all_scripts.length > 0 && (
        <AllScriptsSection
          scripts={view.all_scripts}
          busy={busy}
          probed={probed}
          onProbe={(script) => void probe(script)}
        />
      )}

      <p className="text-xs leading-relaxed text-ink-3">
        {rg('sourcePrefix')}<span className="font-mono">{view?.source ?? rg('none')}</span>
      </p>
    </div>
  )
}

/**
 * 全部脚本（高级入口，默认收起）：项目里的每个 .py，包括静态识别不出产物的
 * ——show-only、动态命名、工具脚本。普通脚本不因静态分析返回 None 就从产品
 * 里消失；任意一条都可以「试运行」，按真实产出登记。
 */
function AllScriptsSection({
  scripts,
  busy,
  probed,
  onProbe,
}: {
  scripts: ScriptInventoryEntry[]
  busy: string | null
  probed: Record<string, ProbeNote>
  onProbe: (script: string) => void
}) {
  useTranslation('dialogs')
  return (
    <details className="rounded-md border border-border">
      <summary className="cursor-pointer select-none px-2 py-1 text-xs font-medium text-ink-2">
        {rg('allScriptsTitle', { count: scripts.length })}
      </summary>
      <p className="px-2 pb-1 text-xs leading-relaxed text-ink-3">{rg('allScriptsHint')}</p>
      <ul className="max-h-52 overflow-y-auto">
        {scripts.map((s) => (
          <li key={s.script} className="flex flex-col gap-0.5 border-t border-border px-2 py-1">
            <div className="flex items-baseline gap-2">
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink" title={s.script}>
                {s.script}
              </span>
              <span className="shrink-0 rounded-sm bg-surface-2 px-1 text-[10px] text-ink-3">
                {rg(`reason_${s.reason}`)}
              </span>
              {s.can_probe && (
                <button
                  disabled={busy !== null}
                  onClick={() => onProbe(s.script)}
                  className={cn(
                    'shrink-0 text-xs text-ink-3 outline-none',
                    'hover:text-ink focus-visible:focus-ring disabled:opacity-40',
                  )}
                >
                  {rg(busy === s.script ? 'running' : s.registered ? 'reprobe' : 'probeAndRegister')}
                </button>
              )}
            </div>
            {s.static_stems.length > 0 && (
              <span className="truncate text-xs text-ink-3" title={listJoin(s.static_stems)}>
                {listJoin(s.static_stems)}
              </span>
            )}
            <ProbeNoteView note={probed[s.script]} />
          </li>
        ))}
      </ul>
    </details>
  )
}

/** 试运行结果的一致展示：主文案一行，traceback 收在「诊断详情」里 */
function ProbeNoteView({ note }: { note?: ProbeNote }) {
  useTranslation('dialogs')
  const setStatus = useUiStore((s) => s.setStatus)
  const setOpen = useUiStore((s) => s.setRegistryOpen)
  if (!note) return null
  return (
    <div className="mt-1 text-xs text-ink-3">
      <p className="whitespace-pre-wrap">{note.text}</p>
      {/* 捕获成功的每张图可以直接作为 runtime 面板放上画布（开发/高级验证
          入口，与「全部脚本」折叠段同一档；普通素材库入口是下一轮的事）。
          没有磁盘产物的 show-only 图从这里第一次真正进入产品。 */}
      {note.descriptors && note.descriptors.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1.5">
          {note.descriptors.map((d) => (
            <Button
              key={d.asset_id}
              variant="outline"
              size="sm"
              onClick={() => {
                addRuntimePanel(d)
                setStatus(msg('registry.addedToCanvas', { stem: d.stem }, 'dialogs'))
                setOpen(false)
              }}
            >
              {rg('addToCanvas', { stem: d.stem })}
            </Button>
          ))}
        </div>
      )}
      {note.traceback && (
        <details className="mt-0.5">
          <summary className="cursor-pointer select-none">{rg('probeTraceback')}</summary>
          <pre className="max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[10px] leading-snug">
            {note.traceback}
          </pre>
        </details>
      )}
    </div>
  )
}

function CandidateRow({
  candidate,
  busy,
  disabled,
  note,
  onProbe,
  onRegister,
}: {
  candidate: RegistryCandidate
  busy: boolean
  disabled: boolean
  note?: ProbeNote
  onProbe: () => void
  onRegister: (stems: string[], entry: string) => void
}) {
  useTranslation('dialogs')
  const [manual, setManual] = useState('')
  const canRegisterStatically = candidate.new_stems.length > 0

  return (
    <li className="rounded-md border border-border p-2">
      <div className="flex items-baseline gap-2">
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink">
          {candidate.script}
        </span>
        <span className="shrink-0 text-xs text-ink-3">{rg('entry', { entry: candidate.entry })}</span>
      </div>

      {canRegisterStatically ? (
        <p className="mt-0.5 text-xs text-ink-2">
          {rg('canRegister')}
          <span className="font-mono">{listJoin(candidate.new_stems)}</span>
        </p>
      ) : (
        <p className="mt-0.5 text-xs text-ink-2">
          {rg('runtimeOnly', { count: candidate.save_calls })}
          {candidate.unresolved.length > 0 &&
            rg('unresolved', { names: listJoin(candidate.unresolved) })}
          {rg('runtimeOnlyTail')}
        </p>
      )}

      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {canRegisterStatically && (
          <Button
            variant="outline"
            size="sm"
            disabled={disabled}
            onClick={() => onRegister(candidate.new_stems, candidate.entry)}
          >
            <Check size={13} />
            {rg('register')}
          </Button>
        )}
        <Button variant="outline" size="sm" disabled={disabled} onClick={onProbe}>
          <Play size={13} className={cn(busy && 'animate-pulse')} />
          {rg(busy ? 'running' : 'probeAndRegister')}
        </Button>
        <TextInput
          value={manual}
          onChange={(e) => setManual(e.target.value)}
          placeholder={rg('manualPlaceholder')}
          aria-label={rg('manualAria', { script: candidate.script })}
          className="h-7 min-w-40 flex-1 font-mono"
          spellCheck={false}
        />
        <Button
          size="sm"
          disabled={disabled || !manual.trim()}
          onClick={() =>
            onRegister(
              manual
                .split(/[,，\s]+/)
                .map((s) => s.trim())
                .filter(Boolean),
              candidate.entry,
            )
          }
        >
          {rg('write')}
        </Button>
      </div>

      <ProbeNoteView note={note} />
    </li>
  )
}
