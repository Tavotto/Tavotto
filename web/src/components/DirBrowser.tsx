import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowUp, CornerDownLeft, Folder, HardDrive } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { backendErrorText, ApiError, browseDirs, type BrowseResult, type DirEntry } from '@/lib/api'
import { t as translate } from '@/i18n'
import { checkProjectName, type ProjectNameProblem } from '@/lib/projectName'
import { cn } from '@/lib/utils'
import { FormRow } from './FormRow'
import { Button, IconButton } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { TextInput } from './ui/Input'
import { listRowClass } from './ui/listRow'
import { Notice } from './ui/Notice'

/*
 * 服务器端目录浏览器、桌面「新建项目」的起名框与「留尾巴的路径」：Project Picker（全部项目）、主页的「导入我的脚本」
 * （浏览器模式）与左栏工作区列表共用。单独成文件是为了主页能用它而不与 ProjectPicker
 * 互相 import（`importArchitecture.test` 不许新环）；ProjectPicker 照旧把它们导出。
 */

/**
 * 项目名被拒的原因 → 一句本地化的话。动态子键，闭集来自
 * `lib/projectName.ProjectNameProblem`（i18n-check 按前缀
 * `project:browser.nameError.` 认这一片）。
 */
const nameErrorText = (reason: ProjectNameProblem) =>
  translate(`browser.nameError.${reason}`, { ns: 'project' })

/**
 * 放不下时**留尾巴**的路径：`/Users/…/pytest-12/figs` 里能认出项目的是尾部，
 * 默认的省略号却切掉的正是尾部。`dir="rtl"` 让溢出从左边裁；两端的 U+200E
 * 把路径钉在从左到右，免得末尾的 `/` 或 `.` 被双向算法搬到另一头。
 */
export function TailPath({ path, className }: { path: string; className?: string }) {
  return (
    <span dir="rtl" className={cn('block truncate text-left font-mono text-xs text-ink-3', className)}>
      {'\u200E' + path + '\u200E'}
    </span>
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
  title,
  onClose,
  onPick,
}: {
  mode: 'open' | 'create'
  initialPath?: string
  /** 换掉默认标题（主页「导入我的脚本」在浏览器里用它说「选择脚本所在的文件夹」） */
  title?: string
  onClose: () => void
  onPick: (path: string, create: boolean) => void
}) {
  const { t } = useTranslation('project')
  const [state, setState] = useState<BrowseResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [nearest, setNearest] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [pathText, setPathText] = useState(initialPath ?? '')
  const editingPath = useRef(false)
  // 新建：打开时焦点直接落在名字框（`initialFocusRef`，2026-10-07 设计审计 §10.2——`autoFocus` 被 Dialog 的「焦点落容器」盖掉）
  const nameRef = useRef<HTMLInputElement>(null)
  /** 子目录列表的漫游焦点：Tab 只停一行，↑↓ 走行、Enter 进入、⌫ 回上一级 */
  const [active, setActive] = useState(0)
  const listRef = useRef<HTMLUListElement>(null)
  /**
   * ⌫ 回上一级之后要把焦点放到**新清单**的第一行：只能等响应落地、新行渲染出来再挪（Codex #831 P2——
   * 以前下一帧就挪，响应慢于一帧时焦点落在旧目录的行上，随后那一行被换掉，焦点掉出清单）。
   */
  const focusFirstOnLand = useRef(false)

  const nav = async (path?: string, opts?: { focusFirst?: boolean }) => {
    try {
      const next = await browseDirs(path)
      if (opts?.focusFirst) focusFirstOnLand.current = true
      setState(next)
      setActive(0)
      setError(null)
      setNearest(null)
      if (!editingPath.current) setPathText(next.is_roots ? '' : next.path)
    } catch (e) {
      setError(backendErrorText(e))
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

  // 名字栏收的是**叶子名**，不是路径：`..` / `nested/name` 会把项目建到
  // 上面那个目录之外（评审 #299-2）。判据只有 `checkProjectName` 一份。
  const nameProblem = mode === 'create' ? checkProjectName(name) : null
  const createDisabled = mode === 'create' && nameProblem !== null
  const target = state?.is_roots ? '' : (state?.path ?? '')
  const dirs = state?.dirs ?? []

  const focusRow = (i: number) => {
    const next = Math.max(0, Math.min(dirs.length - 1, i))
    setActive(next)
    listRef.current?.querySelectorAll<HTMLButtonElement>('[data-dir-row]')[next]?.focus()
  }
  const goParent = (focusFirst = false) => {
    if (!state?.parent) return
    editingPath.current = false
    void nav(state.parent, { focusFirst })
  }
  // 新清单渲染之后才挪焦点；这期间用户把焦点挪去了别处（地址栏等）就不抢
  useEffect(() => {
    if (!focusFirstOnLand.current) return
    focusFirstOnLand.current = false
    const list = listRef.current
    const at = document.activeElement
    if (!list || (at && at !== document.body && !list.contains(at))) return
    const first = list.querySelector<HTMLButtonElement>('[data-dir-row]')
    if (!first) return
    setActive(0)
    first.focus()
  }, [state])
  const onListKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      focusRow(active + (e.key === 'ArrowDown' ? 1 : -1))
    } else if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault()
      focusRow(e.key === 'Home' ? 0 : dirs.length - 1)
    } else if (e.key === 'Backspace') {
      // 焦点在列表里时 ⌫ = 上一级（与访达 / 资源管理器一致）；地址栏里的 ⌫ 照常删字（事件不经过这里）
      e.preventDefault()
      goParent(true)
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      onEscape={onClose}
      title={title ?? t(mode === 'create' ? 'browser.titleCreate' : 'browser.titleOpen')}
      size="md"
      anchor="dir-browser"
      initialFocusRef={mode === 'create' ? nameRef : undefined}
      footer={{
        secondary: (
          <Button variant="secondary" size="lg" onClick={onClose}>
            {translate('actions.cancel')}
          </Button>
        ),
        primary: (
          <Button
            variant="primary"
            size="lg"
            data-dir-browser-confirm
            disabled={!target || createDisabled}
            onClick={() => {
              if (!target) return
              onPick(mode === 'create' ? `${target}/${name}` : target, mode === 'create')
            }}
          >
            {t(mode === 'create' ? 'browser.confirmCreate' : 'browser.confirmOpen')}
          </Button>
        ),
      }}
    >
      <div className="flex flex-col gap-2.5">
        {/* 新建：先问名字（这一步要回答的那件事），再选放在哪 */}
        {mode === 'create' && (
          <div className="flex flex-col gap-1">
            <FormRow label={t('browser.projectName')} asLabel>
              <TextInput
                ref={nameRef}
                data-dir-browser-name
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="my_paper_figures"
                className="min-w-0 flex-1"
                aria-invalid={nameProblem && nameProblem !== 'empty' ? true : undefined}
                aria-describedby={nameProblem && nameProblem !== 'empty' ? 'dir-browser-name-error' : undefined}
              />
            </FormRow>
            {/* 空着不算错（按钮已经是禁用的），只有真输错了才出话 */}
            {nameProblem && nameProblem !== 'empty' && (
              <p id="dir-browser-name-error" role="alert" className="pl-[92px] text-sm text-danger-content">
                {nameErrorText(nameProblem)}
              </p>
            )}
          </div>
        )}

        {/* 路径输入框就是地址栏：可读可改可粘贴 */}
        <form
          className="flex items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault()
            editingPath.current = false
            void nav(pathText.trim() || undefined)
          }}
        >
          <IconButton type="button" iconSize="sm" tip={false} disabled={!state?.parent} onClick={() => goParent()} label={t('browser.parentDir')}>
            <ArrowUp size={ICON_SIZE.sm} />
          </IconButton>
          <TextInput
            value={pathText}
            onChange={(e) => {
              editingPath.current = true
              setPathText(e.target.value)
            }}
            onBlur={() => {
              editingPath.current = false
            }}
            placeholder={t('browser.pathPlaceholder')}
            aria-label={t('browser.pathLabel')}
            className="min-w-0 flex-1 font-mono"
            spellCheck={false}
          />
          <IconButton type="submit" iconSize="sm" tip={false} label={t('browser.goToPath')}>
            <CornerDownLeft size={ICON_SIZE.sm} />
          </IconButton>
        </form>

        {/* 错误紧贴地址栏下面（它多半是地址栏里那条路径的事） */}
        {error && (
          <Notice
            tone="danger"
            action={
              nearest ? (
                <Button variant="ghost" size="sm" data-dir-browser-nearest onClick={() => void nav(nearest)}>
                  {t('browser.goTo', { path: nearest })}
                </Button>
              ) : undefined
            }
          >
            {error}
          </Notice>
        )}

        {/* 常用起点 + 驱动器：Windows 上跨盘全靠这一行（胶囊，与全站带字按钮同一族） */}
        <div className="flex flex-wrap gap-1">
          {(state?.shortcuts ?? []).map((s) => (
            <Chip key={s.path} entry={s} label={shortcutLabel(s)} onGo={() => void nav(s.path)} />
          ))}
          {(state?.roots ?? []).map((r) => (
            <Chip key={r.path} entry={r} icon onGo={() => void nav(r.path)} />
          ))}
        </div>

        {/* 清单不套外框（全面打磨 D44，§8）：32px 的列表行；漫游焦点（Tab 只停一行） */}
        <ul
          ref={listRef}
          aria-label={t('browser.subdirsLabel')}
          className="-mx-1 flex h-56 flex-col overflow-y-auto"
          onKeyDown={onListKey}
        >
          {dirs.map((d, i) => (
            <li key={d.path} className="flex">
              <button
                type="button"
                data-dir-row={d.name}
                tabIndex={i === active ? 0 : -1}
                onFocus={() => setActive(i)}
                onClick={() => {
                  editingPath.current = false
                  void nav(d.path)
                }}
                className={cn(listRowClass(), 'h-8 w-full gap-2 px-2 text-left text-sm')}
              >
                {state?.is_roots ? (
                  <HardDrive size={ICON_SIZE.sm} className="shrink-0 text-ink-3" />
                ) : (
                  <Folder size={ICON_SIZE.sm} className="shrink-0 text-ink-3" />
                )}
                <span className="truncate">{d.name}</span>
              </button>
            </li>
          ))}
          {state && dirs.length === 0 && (
            <li className="flex h-full items-center justify-center text-ink-3">{t('browser.noSubdirs')}</li>
          )}
        </ul>
      </div>
    </Dialog>
  )
}

/**
 * 桌面壳「新建项目」的第二步：上级目录已经由系统选择器选好，这里只问名字。
 * 路径拼接与浏览器模式的 DirBrowser 一致（`parent/name`）。
 */
export function NewProjectNameDialog({
  parent,
  onClose,
  onCreate,
}: {
  parent: string
  onClose: () => void
  onCreate: (name: string) => void
}) {
  const { t } = useTranslation('project')
  const [name, setName] = useState('')
  const nameRef = useRef<HTMLInputElement>(null)
  // 上级目录已经定了，这里收的是**叶子名**：`..` / `nested/name` 拼进去之后
  // 项目会建在 `parent` 之外（评审 #299-2）。与 DirBrowser 同一份判据。
  const problem = checkProjectName(name)
  const showProblem = problem !== null && problem !== 'empty'
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      onEscape={onClose}
      title={t('browser.titleCreate')}
      size="sm"
      anchor="new-project-name"
      // 只有一个输入框：打开时焦点直接在它上面
      initialFocusRef={nameRef}
      footer={{
        secondary: (
          <Button variant="secondary" size="lg" onClick={onClose}>
            {translate('actions.cancel')}
          </Button>
        ),
        primary: (
          <Button variant="primary" size="lg" disabled={problem !== null} onClick={() => onCreate(name)}>
            {t('browser.confirmCreate')}
          </Button>
        ),
      }}
    >
      <form
        className="flex flex-col gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (!problem) onCreate(name)
        }}
      >
        <FormRow label={t('browser.projectName')} asLabel>
          <TextInput
            ref={nameRef}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="my_paper_figures"
            className="min-w-0 flex-1"
            aria-invalid={showProblem ? true : undefined}
            aria-describedby={showProblem ? 'new-project-name-error' : undefined}
          />
        </FormRow>
        {showProblem && (
          <p id="new-project-name-error" role="alert" className="pl-[92px] text-sm text-danger-content">
            {nameErrorText(problem)}
          </p>
        )}
        <p className="truncate font-mono text-sm text-ink-3" title={parent}>
          {t('picker.createIn', { dir: parent })}
        </p>
      </form>
    </Dialog>
  )
}

/**
 * 常用起点的显示名按界面语言走（审计 #668）：后端的 `name` 是中文写死的，英文界面上
 * 会冒出「主目录 / 桌面」，而且「文档」违反界面名词约定。按 `id` 查自己的语言包，
 * 认不出的 id / 老后端（没有 id）回退 `name`。key 逐条写成字面量，i18n 检查才看得见。
 */
function shortcutLabel(entry: DirEntry): string {
  switch (entry.id) {
    case 'home':
      return translate('browser.shortcut.home', { ns: 'project' })
    case 'desktop':
      return translate('browser.shortcut.desktop', { ns: 'project' })
    case 'documents':
      return translate('browser.shortcut.documents', { ns: 'project' })
    case 'downloads':
      return translate('browser.shortcut.downloads', { ns: 'project' })
    default:
      return entry.name
  }
}

function Chip({
  entry,
  label,
  icon,
  onGo,
}: {
  entry: DirEntry
  label?: string
  icon?: boolean
  onGo: () => void
}) {
  // 胶囊 = 全站带字按钮那一族（Button secondary sm），不再是自画的 6px 圆角描边块
  return (
    <Button variant="secondary" size="sm" onClick={onGo} title={entry.path} data-dir-chip>
      {icon && <HardDrive size={ICON_SIZE.xs} className="text-ink-3" />}
      {label ?? entry.name}
    </Button>
  )
}
