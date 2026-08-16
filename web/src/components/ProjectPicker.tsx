import { useEffect, useState } from 'react'
import {
  AlertTriangle,
  ArrowUp,
  Folder,
  FolderOpen,
  FolderPlus,
  X,
} from 'lucide-react'
import { browseDirs, type BrowseResult, type RecentProject } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { cn } from '@/lib/utils'
import { useProjectStore } from '@/store/projectStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { TextInput } from './ui/Input'

/**
 * Project Picker：没有打开的项目时替代整个工作台。
 * 项目 = 论文图所在的目录（figures 目录）；打开即切换后端当前项目。
 */
export function ProjectPicker() {
  const recent = useProjectStore((s) => s.recent)
  const open = useProjectStore((s) => s.open)
  const remove = useProjectStore((s) => s.remove)
  // 从设置「切换项目」进来时后端仍有打开的项目——允许原路返回
  const currentOpen = useProjectStore((s) => s.project?.open === true)
  const [browse, setBrowse] = useState<null | 'open' | 'create'>(null)
  const [error, setError] = useState<string | null>(null)
  const [busyPath, setBusyPath] = useState<string | null>(null)

  const openPath = async (path: string, create = false) => {
    setError(null)
    setBusyPath(path)
    try {
      await open(path, create)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyPath(null)
    }
  }

  return (
    <div className="flex h-full items-center justify-center overflow-y-auto bg-bg">
      <main aria-label="选择项目" className="w-[440px] max-w-[calc(100vw-3rem)] py-10">
        <h1 className="text-lg font-semibold tracking-tight text-ink">{PRODUCT_NAME}</h1>
        <p className="mt-1 text-xs leading-relaxed text-ink-3">
          项目就是论文图所在的目录；选择一个目录开始排版。
        </p>

        <div className="mt-5 flex gap-2">
          <Button variant="primary" size="md" onClick={() => setBrowse('create')}>
            <FolderPlus size={14} />
            新建项目
          </Button>
          <Button variant="outline" size="md" onClick={() => setBrowse('open')}>
            <FolderOpen size={14} />
            打开目录…
          </Button>
          {currentOpen && (
            <Button
              size="md"
              className="ml-auto text-ink-2"
              onClick={() => useProjectStore.setState({ phase: 'open' })}
            >
              返回当前项目
            </Button>
          )}
        </div>

        {error && (
          <p role="alert" className="mt-3 text-xs leading-relaxed text-danger">
            {error}
          </p>
        )}

        {recent.length > 0 && (
          <section aria-label="最近项目" className="mt-7">
            <h2 className="mb-1.5 text-xs font-medium text-ink-2">最近项目</h2>
            <ul className="flex flex-col">
              {recent.map((r) => (
                <RecentRow
                  key={r.path}
                  entry={r}
                  busy={busyPath === r.path}
                  onOpen={() => void openPath(r.path)}
                  onRemove={() => void remove(r.path)}
                />
              ))}
            </ul>
          </section>
        )}

        {browse && (
          <DirBrowser
            mode={browse}
            onClose={() => setBrowse(null)}
            onPick={(path, create) => {
              setBrowse(null)
              void openPath(path, create)
            }}
          />
        )}
      </main>
    </div>
  )
}

function RecentRow({
  entry,
  busy,
  onOpen,
  onRemove,
}: {
  entry: RecentProject
  busy: boolean
  onOpen: () => void
  onRemove: () => void
}) {
  return (
    <li className="group flex items-center gap-2 border-b border-border py-2 last:border-b-0">
      <Folder size={14} className="shrink-0 text-ink-3" />
      <button
        onClick={onOpen}
        disabled={busy || !entry.exists}
        className={cn(
          'min-w-0 flex-1 text-left outline-none focus-visible:focus-ring',
          entry.exists ? 'cursor-pointer' : 'cursor-default',
        )}
        aria-label={`打开项目 ${entry.name}`}
      >
        <span className="flex items-center gap-1.5">
          <span className="truncate text-xs font-medium text-ink">{entry.name}</span>
          {!entry.exists && (
            <span className="flex shrink-0 items-center gap-1 text-xs text-danger">
              <AlertTriangle size={11} />
              目录不存在
            </span>
          )}
          {busy && <span className="shrink-0 text-xs text-ink-3">打开中…</span>}
        </span>
        <span className="block truncate font-mono text-xs text-ink-3">{entry.path}</span>
      </button>
      <button
        onClick={onRemove}
        aria-label={`从列表移除 ${entry.name}（不删除磁盘内容）`}
        title="从列表移除（不删除磁盘内容）"
        className={cn(
          'flex h-6 w-6 shrink-0 items-center justify-center rounded-sm text-ink-3',
          'opacity-0 outline-none transition-opacity hover:bg-ink/[.055] hover:text-ink',
          'focus-visible:opacity-100 focus-visible:focus-ring group-hover:opacity-100',
        )}
      >
        <X size={13} />
      </button>
    </li>
  )
}

/**
 * 服务器端目录浏览器（本地单用户应用，浏览的就是本机磁盘）。
 * open 模式选中即打开；create 模式在当前目录下按名字新建。
 */
function DirBrowser({
  mode,
  onClose,
  onPick,
}: {
  mode: 'open' | 'create'
  onClose: () => void
  onPick: (path: string, create: boolean) => void
}) {
  const [state, setState] = useState<BrowseResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')

  const nav = async (path?: string) => {
    try {
      setState(await browseDirs(path))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    void nav()
  }, [])

  const createDisabled = mode === 'create' && !name.trim()

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title={mode === 'create' ? '新建项目' : '打开项目目录'}
      size="md"
      footer={
        <>
          <Button variant="outline" size="md" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="primary"
            size="md"
            disabled={!state || createDisabled}
            onClick={() => {
              if (!state) return
              onPick(
                mode === 'create' ? `${state.path}/${name.trim()}` : state.path,
                mode === 'create',
              )
            }}
          >
            {mode === 'create' ? '在此新建' : '打开此目录'}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-1.5">
          <Button
            size="icon-sm"
            disabled={!state?.parent}
            onClick={() => {
              if (state?.parent) void nav(state.parent)
            }}
            aria-label="上一级目录"
          >
            <ArrowUp size={13} />
          </Button>
          <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-2" title={state?.path}>
            {state?.path ?? '…'}
          </span>
        </div>

        <ul
          aria-label="子目录"
          className="h-56 overflow-y-auto rounded-sm border border-border"
        >
          {state?.dirs.map((d) => (
            <li key={d.path}>
              <button
                onDoubleClick={() => void nav(d.path)}
                onClick={() => void nav(d.path)}
                className={cn(
                  'flex h-7 w-full items-center gap-2 px-2 text-left text-xs text-ink',
                  'outline-none hover:bg-ink/[.045] focus-visible:focus-ring',
                )}
              >
                <Folder size={13} className="shrink-0 text-ink-3" />
                <span className="truncate">{d.name}</span>
              </button>
            </li>
          ))}
          {state && state.dirs.length === 0 && (
            <li className="flex h-full items-center justify-center text-xs text-ink-3">
              没有子目录
            </li>
          )}
        </ul>

        {mode === 'create' && (
          <label className="flex items-center gap-2 text-xs text-ink-2">
            项目名
            <TextInput
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my_paper_figures"
              className="flex-1"
            />
          </label>
        )}

        {error && <p className="text-xs text-danger">{error}</p>}
      </div>
    </Dialog>
  )
}
