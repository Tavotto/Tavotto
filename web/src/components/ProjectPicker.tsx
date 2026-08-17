import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowUp,
  CornerDownLeft,
  Folder,
  FolderOpen,
  FolderPlus,
  HardDrive,
  X,
} from 'lucide-react'
import {
  ApiError,
  browseDirs,
  type BrowseResult,
  type DirEntry,
  type RecentProject,
} from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { cn } from '@/lib/utils'
import { useProjectStore } from '@/store/projectStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { TextInput } from './ui/Input'

/**
 * Project Picker：没有打开的项目时替代整个工作台。
 * 项目 = 论文图所在的目录（figures 目录）；打开即切换本标签页的项目。
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
  const [typed, setTyped] = useState('')

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
      <main aria-label="选择项目" className="w-[460px] max-w-[calc(100vw-3rem)] py-10">
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
            浏览目录…
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

        {/* 直接输入/粘贴路径：从文件管理器复制一个路径过来永远比一层层点快 */}
        <form
          className="mt-3 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            const path = typed.trim()
            if (path) void openPath(path)
          }}
        >
          <TextInput
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder="或直接粘贴路径，如 D:\\research\\figures"
            aria-label="项目路径"
            className="flex-1 font-mono"
            spellCheck={false}
          />
          <Button type="submit" variant="outline" size="md" disabled={!typed.trim()}>
            <CornerDownLeft size={13} />
            打开
          </Button>
        </form>

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
 *
 * 三件事缺一不可：
 *   * 路径可直接输入/粘贴——从资源管理器复制路径过来是最快的路;
 *   * 驱动器一层（Windows）——只能从主目录往下钻的话，**永远到不了 D 盘**;
 *   * 常用起点快捷入口——主目录/桌面/文档。
 */
export function DirBrowser({
  mode,
  initialPath,
  onClose,
  onPick,
}: {
  mode: 'open' | 'create'
  initialPath?: string
  onClose: () => void
  onPick: (path: string, create: boolean) => void
}) {
  const [state, setState] = useState<BrowseResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [nearest, setNearest] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [pathText, setPathText] = useState(initialPath ?? '')
  const editingPath = useRef(false)

  const nav = async (path?: string) => {
    try {
      const next = await browseDirs(path)
      setState(next)
      setError(null)
      setNearest(null)
      if (!editingPath.current) setPathText(next.is_roots ? '' : next.path)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      // 后端在「路径不存在」时附带最近的存在祖先，给一个一键跳转——
      // 手输路径打错一个字符不该只换来一句死报错
      const hint = e instanceof ApiError ? e.body.nearest : null
      setNearest(typeof hint === 'string' ? hint : null)
    }
  }

  useEffect(() => {
    void nav(initialPath)
    // 只在打开时定位一次：之后的位置由用户的导航决定，不该被 props 拉回去
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const createDisabled = mode === 'create' && !name.trim()
  const target = state?.is_roots ? '' : (state?.path ?? '')

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
            disabled={!target || createDisabled}
            onClick={() => {
              if (!target) return
              onPick(mode === 'create' ? `${target}/${name.trim()}` : target, mode === 'create')
            }}
          >
            {mode === 'create' ? '在此新建' : '打开此目录'}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        {/* 路径输入框就是地址栏：可读可改可粘贴 */}
        <form
          className="flex items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault()
            editingPath.current = false
            void nav(pathText.trim() || undefined)
          }}
        >
          <Button
            type="button"
            size="icon-sm"
            disabled={!state?.parent}
            onClick={() => {
              if (state?.parent) {
                editingPath.current = false
                void nav(state.parent)
              }
            }}
            aria-label="上一级目录"
          >
            <ArrowUp size={13} />
          </Button>
          <TextInput
            value={pathText}
            onChange={(e) => {
              editingPath.current = true
              setPathText(e.target.value)
            }}
            onBlur={() => {
              editingPath.current = false
            }}
            placeholder="输入或粘贴路径后回车"
            aria-label="当前路径"
            className="min-w-0 flex-1 font-mono"
            spellCheck={false}
          />
          <Button type="submit" size="icon-sm" aria-label="跳转到该路径">
            <CornerDownLeft size={13} />
          </Button>
        </form>

        {/* 常用起点 + 驱动器：Windows 上跨盘全靠这一行 */}
        <div className="flex flex-wrap gap-1">
          {(state?.shortcuts ?? []).map((s) => (
            <Chip key={s.path} entry={s} onGo={() => void nav(s.path)} />
          ))}
          {(state?.roots ?? []).map((r) => (
            <Chip key={r.path} entry={r} icon onGo={() => void nav(r.path)} />
          ))}
        </div>

        <ul aria-label="子目录" className="h-56 overflow-y-auto rounded-sm border border-border">
          {state?.dirs.map((d) => (
            <li key={d.path}>
              <button
                onClick={() => {
                  editingPath.current = false
                  void nav(d.path)
                }}
                className={cn(
                  'flex h-7 w-full items-center gap-2 px-2 text-left text-xs text-ink',
                  'outline-none hover:bg-ink/[.045] focus-visible:focus-ring',
                )}
              >
                {state.is_roots ? (
                  <HardDrive size={13} className="shrink-0 text-ink-3" />
                ) : (
                  <Folder size={13} className="shrink-0 text-ink-3" />
                )}
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

        {error && (
          <p className="text-xs text-danger">
            {error}
            {nearest && (
              <button
                className="ml-2 underline outline-none focus-visible:focus-ring"
                onClick={() => void nav(nearest)}
              >
                去 {nearest}
              </button>
            )}
          </p>
        )}
      </div>
    </Dialog>
  )
}

function Chip({ entry, icon, onGo }: { entry: DirEntry; icon?: boolean; onGo: () => void }) {
  return (
    <button
      onClick={onGo}
      title={entry.path}
      className={cn(
        'flex h-6 items-center gap-1 rounded-sm border border-border px-1.5 text-xs text-ink-2',
        'outline-none hover:border-border-strong hover:text-ink focus-visible:focus-ring',
      )}
    >
      {icon && <HardDrive size={11} className="text-ink-3" />}
      {entry.name}
    </button>
  )
}
