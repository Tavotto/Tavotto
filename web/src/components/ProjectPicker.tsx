import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  AlertTriangle,
  ArrowUp,
  BookOpen,
  CornerDownLeft,
  Folder,
  FolderOpen,
  FolderPlus,
  HardDrive,
  X,
} from 'lucide-react'
import {
  backendErrorText,
  ApiError,
  browseDirs,
  type BrowseResult,
  type DirEntry,
  type RecentProject,
} from '@/lib/api'
import { isDesktop, pickDirectory } from '@/lib/desktop'
import { t as translate } from '@/i18n'
import { useFormatMessage } from '@/i18n/react'
import { PRODUCT_NAME } from '@/lib/brand'
import {
  loadTutorialStatus,
  runTutorialEntry,
  tutorialEntry,
  useTutorialStore,
} from '@/lib/onboarding/tutorial'
import { cn } from '@/lib/utils'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'
import { BrandMark } from './ui/BrandMark'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { TextInput } from './ui/Input'

/**
 * Project Picker：没有打开的项目时替代整个工作台。
 * 项目 = 论文图所在的目录（figures 目录）；打开即切换本标签页的项目。
 */
export function ProjectPicker() {
  const { t } = useTranslation('project')
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
      setError(backendErrorText(e))
    } finally {
      setBusyPath(null)
    }
  }

  return (
    <div className="flex h-full items-center justify-center overflow-y-auto bg-bg">
      <main aria-label={t('picker.regionLabel')} className="w-[460px] max-w-[calc(100vw-3rem)] py-10">
        {/* 页面底是纸色 --color-bg：灰块用 paper 档才能与背景分开 */}
        <h1 className="flex items-center gap-2.5 text-lg font-semibold tracking-tight text-ink">
          <BrandMark size={24} variant="compact" tone="paper" />
          {PRODUCT_NAME}
        </h1>
        <p className="mt-1 text-xs leading-relaxed text-ink-3">{t('picker.tagline')}</p>

        <div className="mt-5 flex gap-2">
          <Button variant="primary" size="md" onClick={() => setBrowse('create')}>
            <FolderPlus size={14} />
            {t('picker.create')}
          </Button>
          <Button
            variant="outline"
            size="md"
            onClick={() => {
              // 桌面壳里用原生目录选择器；取消不是错误，什么都不发生。
              // 浏览器模式回退到服务器端目录浏览器（本地单用户应用，浏览的就是本机磁盘）。
              if (isDesktop()) {
                void pickDirectory(t('picker.nativePickerTitle')).then((dir) => {
                  if (dir) void openPath(dir)
                })
              } else setBrowse('open')
            }}
          >
            <FolderOpen size={14} />
            {t('picker.browse')}
          </Button>
          {currentOpen && (
            <Button
              size="md"
              className="ml-auto text-ink-2"
              onClick={() => useProjectStore.setState({ phase: 'open' })}
            >
              {t('picker.backToCurrent')}
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
            placeholder={t('picker.pathPlaceholder')}
            aria-label={t('picker.pathLabel')}
            className="flex-1 font-mono"
            spellCheck={false}
          />
          <Button type="submit" variant="outline" size="md" disabled={!typed.trim()}>
            <CornerDownLeft size={13} />
            {t('picker.openButton')}
          </Button>
        </form>

        {error && (
          <p role="alert" className="mt-3 text-xs leading-relaxed text-danger">
            {error}
          </p>
        )}

        <TutorialEntry />

        {recent.length > 0 && (
          <section aria-label={t('picker.recentLabel')} className="mt-7">
            <h2 className="mb-1.5 text-xs font-medium text-ink-2">{t('picker.recentHeading')}</h2>
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

/**
 * 「用示例了解 Tavotto」——低干扰的一行入口（ADR 0040）。
 *
 * 状态三档：宿主没有 Tutorial API（GET 失败）→ 整行不出现；资源坏了
 * （`available:false`）→ 按钮禁用 + 一句「请重新安装」；正常 → 按钮。
 * 点下去的全部逻辑在 `lib/onboarding/tutorial.ts`，这里只显示结果。
 */
function TutorialEntry() {
  const { t } = useTranslation('project')
  const fmt = useFormatMessage()
  const status = useTutorialStore((s) => s.status)
  const busy = useTutorialStore((s) => s.busy)
  const failure = useTutorialStore((s) => s.failure)
  const entry = useOnboardingStore((s) => tutorialEntry(s.status))

  useEffect(() => {
    void loadTutorialStatus()
  }, [])

  // 宿主没提供 Tutorial API（embedded / 老后端）：入口整行不出现
  if (failure?.reason === 'no_api' && !status) return null
  const unavailable = !!status && !status.available

  return (
    <section aria-label={t('picker.tutorialLabel')} className="mt-5 flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="md"
          className="-ml-2.5 text-ink-2"
          disabled={unavailable || busy === 'open'}
          loading={busy === 'open'}
          loadingLabel={t('picker.tutorialOpening')}
          data-onboarding-anchor="tutorial-entry"
          onClick={() => void runTutorialEntry('picker')}
        >
          <BookOpen size={14} />
          {t(`picker.tutorial.${entry}`)}
        </Button>
      </div>
      <p className="text-xs leading-relaxed text-ink-3">
        {unavailable ? t('picker.tutorialUnavailable') : t('picker.tutorialHint')}
      </p>
      {failure && failure.reason !== 'no_api' && failure.reason !== 'cancelled' && (
        <p role="alert" className="text-xs leading-relaxed text-danger">
          {fmt(failure.message)}
        </p>
      )}
    </section>
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
  const { t } = useTranslation('project')
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
        aria-label={t('picker.openProject', { name: entry.name })}
      >
        <span className="flex items-center gap-1.5">
          <span className="truncate text-xs font-medium text-ink">{entry.name}</span>
          {!entry.exists && (
            <span className="flex shrink-0 items-center gap-1 text-xs text-danger">
              <AlertTriangle size={11} />
              {t('picker.missingDir')}
            </span>
          )}
          {busy && <span className="shrink-0 text-xs text-ink-3">{t('picker.opening')}</span>}
        </span>
        {/* 教程副本躺在数据目录里：显示「教程」而不是那条路径（T-104） */}
        {entry.tutorial ? (
          <span className="block text-xs text-ink-3">{t('picker.tutorialBadge')}</span>
        ) : (
          <span className="block truncate font-mono text-xs text-ink-3">{entry.path}</span>
        )}
      </button>
      <button
        onClick={onRemove}
        aria-label={t('picker.removeFromList', { name: entry.name })}
        title={t('picker.removeFromListTitle')}
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

  const createDisabled = mode === 'create' && !name.trim()
  const target = state?.is_roots ? '' : (state?.path ?? '')

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title={t(mode === 'create' ? 'browser.titleCreate' : 'browser.titleOpen')}
      size="md"
      footer={
        <>
          <Button variant="outline" size="md" onClick={onClose}>
            {translate('actions.cancel')}
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
            placeholder={t('browser.pathPlaceholder')}
            aria-label={t('browser.pathLabel')}
            className="min-w-0 flex-1 font-mono"
            spellCheck={false}
          />
          <Button type="submit" size="icon-sm" aria-label={t('browser.goToPath')}>
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

        <ul aria-label={t('browser.subdirsLabel')} className="h-56 overflow-y-auto rounded-sm border border-border">
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
              {t('browser.noSubdirs')}
            </li>
          )}
        </ul>

        {mode === 'create' && (
          <label className="flex items-center gap-2 text-xs text-ink-2">
            {t('browser.projectName')}
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
                {t('browser.goTo', { path: nearest })}
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
