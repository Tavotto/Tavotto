import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Braces, Check, Play, RefreshCw } from 'lucide-react'
import {
  fetchRegistry,
  probeScript,
  scanRegistry,
  writeRegistryEntry,
  type RegistryCandidate,
  type RegistryView,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { msg, t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
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
 * 目录的 mm_registry.json 里——用户既看不到现状，也不知道该往里写什么。
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

function RegistryBody() {
  useTranslation('dialogs')
  const [view, setView] = useState<RegistryView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [probed, setProbed] = useState<Record<string, string>>({})

  const reload = async () => {
    try {
      setView(await fetchRegistry())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
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
      setError(e instanceof Error ? e.message : String(e))
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
        setProbed((p) => ({ ...p, [script]: res.error! }))
        throw new Error(rg('probeFailed', { script, error: res.error }))
      }
      setProbed((p) => ({ ...p, [script]: rg('probeRegistered', { stems: listJoin(res.stems) }) }))
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

      <p className="text-xs leading-relaxed text-ink-3">
        {rg('sourcePrefix')}<span className="font-mono">{view?.source ?? rg('none')}</span>
      </p>
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
  note?: string
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

      {note && <p className="mt-1 whitespace-pre-wrap text-xs text-ink-3">{note}</p>}
    </li>
  )
}
