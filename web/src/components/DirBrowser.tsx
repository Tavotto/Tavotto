import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowUp, CornerDownLeft, Folder, HardDrive } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { backendErrorText, ApiError, browseDirs, type BrowseResult, type DirEntry } from '@/lib/api'
import { t as translate } from '@/i18n'
import { checkProjectName, type ProjectNameProblem } from '@/lib/projectName'
import { cn } from '@/lib/utils'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { TextInput } from './ui/Input'

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

  const nav = async (path?: string) => {
    try {
      const next = await browseDirs(path)
      setState(next)
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

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title={title ?? t(mode === 'create' ? 'browser.titleCreate' : 'browser.titleOpen')}
      size="md"
      footer={
        <>
          <Button variant="secondary" size="md" onClick={onClose}>
            {translate('actions.cancel')}
          </Button>
          <Button
            variant="primary"
            size="md"
            disabled={!target || createDisabled}
            onClick={() => {
              if (!target) return
              onPick(mode === 'create' ? `${target}/${name}` : target, mode === 'create')
            }}
          >
            {t(mode === 'create' ? 'browser.confirmCreate' : 'browser.confirmOpen')}
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
            aria-label={t('browser.parentDir')}
          >
            <ArrowUp size={ICON_SIZE.sm} />
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
            placeholder={t('browser.pathPlaceholder')}
            aria-label={t('browser.pathLabel')}
            className="min-w-0 flex-1 font-mono"
            spellCheck={false}
          />
          <Button type="submit" size="icon-sm" aria-label={t('browser.goToPath')}>
            <CornerDownLeft size={ICON_SIZE.sm} />
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

        {/* 清单不套外框（全面打磨 D44，§8）：行之间的 hairline 已经把它分开了 */}
        <ul aria-label={t('browser.subdirsLabel')} className="h-56 overflow-y-auto">
          {state?.dirs.map((d) => (
            <li key={d.path}>
              <button
                onClick={() => {
                  editingPath.current = false
                  void nav(d.path)
                }}
                className={cn(
                  'flex h-7 w-full items-center gap-2 px-2 text-left text-xs text-ink',
                  'outline-none hover:bg-surface-hover focus-visible:focus-ring',
                )}
              >
                {state.is_roots ? (
                  <HardDrive size={ICON_SIZE.sm} className="shrink-0 text-ink-3" />
                ) : (
                  <Folder size={ICON_SIZE.sm} className="shrink-0 text-ink-3" />
                )}
                <span className="truncate">{d.name}</span>
              </button>
            </li>
          ))}
          {state && state.dirs.length === 0 && (
            <li className="flex h-full items-center justify-center text-xs text-ink-3">
              {t('browser.noSubdirs')}
            </li>
          )}
        </ul>

        {mode === 'create' && (
          <div className="flex flex-col gap-1">
            <label className="flex items-center gap-2 text-xs text-ink-2">
              {t('browser.projectName')}
              <TextInput
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="my_paper_figures"
                className="flex-1"
                aria-invalid={nameProblem && nameProblem !== 'empty' ? true : undefined}
                aria-describedby={
                  nameProblem && nameProblem !== 'empty' ? 'dir-browser-name-error' : undefined
                }
              />
            </label>
            {/* 空着不算错（按钮已经是禁用的），只有真输错了才出话 */}
            {nameProblem && nameProblem !== 'empty' && (
              <p id="dir-browser-name-error" role="alert" className="text-xs text-danger">
                {nameErrorText(nameProblem)}
              </p>
            )}
          </div>
        )}

        {error && (
          <p className="text-xs text-danger">
            {error}
            {nearest && (
              <button
                className="ml-2 underline outline-none focus-visible:focus-ring"
                onClick={() => void nav(nearest)}
              >
                {t('browser.goTo', { path: nearest })}
              </button>
            )}
          </p>
        )}
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
  // 上级目录已经定了，这里收的是**叶子名**：`..` / `nested/name` 拼进去之后
  // 项目会建在 `parent` 之外（评审 #299-2）。与 DirBrowser 同一份判据。
  const problem = checkProjectName(name)
  const showProblem = problem !== null && problem !== 'empty'
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title={t('browser.titleCreate')}
      size="sm"
      footer={
        <>
          <Button variant="secondary" size="md" onClick={onClose}>
            {translate('actions.cancel')}
          </Button>
          <Button
            variant="primary"
            size="md"
            disabled={problem !== null}
            onClick={() => onCreate(name)}
          >
            {t('browser.confirmCreate')}
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (!problem) onCreate(name)
        }}
      >
        <label className="flex items-center gap-2 text-xs text-ink-2">
          {t('browser.projectName')}
          <TextInput
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="my_paper_figures"
            className="flex-1"
            aria-invalid={showProblem ? true : undefined}
            aria-describedby={showProblem ? 'new-project-name-error' : undefined}
          />
        </label>
        {showProblem && (
          <p id="new-project-name-error" role="alert" className="text-xs text-danger">
            {nameErrorText(problem)}
          </p>
        )}
        <p className="truncate font-mono text-xs text-ink-3" title={parent}>
          {t('picker.createIn', { dir: parent })}
        </p>
      </form>
    </Dialog>
  )
}

function Chip({ entry, icon, onGo }: { entry: DirEntry; icon?: boolean; onGo: () => void }) {
  return (
    <button
      onClick={onGo}
      title={entry.path}
      className={cn(
        'flex h-7 items-center gap-1 rounded-sm border border-border px-2 text-xs text-ink-2',
        'outline-none transition-colors duration-fast hover:border-border-strong hover:text-ink focus-visible:focus-ring',
      )}
    >
      {icon && <HardDrive size={ICON_SIZE.xs} className="text-ink-3" />}
      {entry.name}
    </button>
  )
}
