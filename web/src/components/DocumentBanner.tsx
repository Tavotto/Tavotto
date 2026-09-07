import { useTranslation } from 'react-i18next'
import { TriangleAlert, RotateCcwClock, Lock } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { formatTime } from '@/i18n/format'
import {
  discardLocalCopy,
  dismissDocNotice,
  overwriteDisk,
  recoverLocalCopy,
  reloadFromDisk,
  saveNow,
  useDocumentStore,
} from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from './ui/Button'

/**
 * 文档级的常驻提示条 —— 保存状态机里**需要用户裁决**的那几种情况。
 *
 * 为什么是常驻条而不是 toast：这四件事全都"刷新一次就还在"（磁盘上那份
 * 确实被改过、本机确实还躺着一份副本、那份文档确实读不了）。用一个 4.5 秒
 * 后自己消失的状态条报告它们，等于把一个持续存在的事实说成一次事件——
 * 改造前就是这么做的，用户回到界面时什么都看不到，而磁盘上那份还落后半小时。
 *
 * 与 `UpdateBanner` 同形（同一条高度、同一处挂载点），因为它们是同一类东西：
 * 不打断编辑、不抢焦点、说明 + 一到三个出口。
 */
export function DocumentBanner() {
  const { t } = useTranslation('workspace')
  const saveState = useDocumentStore((s) => s.saveState)
  const saveIssue = useDocumentStore((s) => s.saveIssue)
  const notice = useDocumentStore((s) => s.docNotice)

  // 冲突最急：磁盘上那份不是我以为的那份，编辑正堆在本机等裁决
  if (saveState === 'conflict') {
    const disk = saveIssue?.disk
    return (
      <Banner tone="danger" icon={<TriangleAlert size={ICON_SIZE.sm} className="shrink-0 text-danger" />}>
        <span className="min-w-0 flex-1 truncate">
          {t(saveIssue?.kind === 'stale' ? 'docBanner.conflictStale' : 'docBanner.conflictExternal')}
        </span>
        <span className="hidden shrink-0 opacity-80 min-[900px]:inline">
          {disk && typeof disk.objects === 'number'
            ? t('docBanner.conflictDisk', {
                canvases: disk.canvases,
                objects: disk.objects,
                time: formatTime(disk.mtime ?? disk.updatedAt ?? Date.now()),
              })
            : t('docBanner.conflictDiskUnknown')}
        </span>
        <Button size="sm" className="shrink-0" onClick={() => void reloadFromDisk()}>
          {t('docBanner.reload')}
        </Button>
        <Button size="sm" className="shrink-0" onClick={() => void overwriteDisk()}>
          {t('docBanner.overwrite')}
        </Button>
        <Button
          size="sm"
          className="shrink-0"
          onClick={() => useUiStore.getState().setLayoutOpen(true, 'save')}
        >
          {t('docBanner.saveAs')}
        </Button>
      </Banner>
    )
  }

  if (saveState === 'save_error') {
    return (
      <Banner tone="danger" icon={<TriangleAlert size={ICON_SIZE.sm} className="shrink-0 text-danger" />}>
        <span className="min-w-0 flex-1 truncate">{t('docBanner.saveErrorBody')}</span>
        <Button size="sm" className="shrink-0" onClick={() => void saveNow()}>
          {t('docBanner.retry')}
        </Button>
      </Banner>
    )
  }

  if (notice?.kind === 'recovery') {
    const s = notice.summary
    return (
      <Banner tone="accent" icon={<RotateCcwClock size={ICON_SIZE.sm} className="shrink-0 text-accent" />}>
        <span className="min-w-0 flex-1 truncate">{t('docBanner.recoveryTitle')}</span>
        {/* 文档名是用户内容，作为插值原样透出 */}
        <span className="hidden shrink-0 opacity-80 min-[900px]:inline">
          {t('docBanner.recoveryBody', {
            name: s.name,
            canvases: s.canvases,
            objects: s.objects,
            time: formatTime(s.savedAt),
          })}
        </span>
        <Button size="sm" className="shrink-0" onClick={() => void recoverLocalCopy()}>
          {t('docBanner.recover')}
        </Button>
        <Button size="sm" className="shrink-0" onClick={discardLocalCopy}>
          {t('docBanner.keepMain')}
        </Button>
      </Banner>
    )
  }

  if (notice?.kind === 'schema_too_new') {
    return (
      <Banner tone="accent" icon={<Lock size={ICON_SIZE.sm} className="shrink-0 text-accent" />}>
        <span className="min-w-0 flex-1 truncate">
          {t('docBanner.tooNewTitle', { schema: notice.schema })}
        </span>
        <span className="hidden shrink-0 opacity-80 min-[900px]:inline">
          {t('docBanner.tooNewBody')}
        </span>
        <Button size="sm" className="shrink-0" onClick={dismissDocNotice}>
          {t('docBanner.dismiss')}
        </Button>
      </Banner>
    )
  }

  return <LastDocumentBanner />
}

/**
 * 切回项目时记着上次开的是哪份文档、却没读回来（T02）。**说出名字**并给
 * 重试——不说的话用户面对的是一份空白 fig_layout，还以为自己的版没了。
 */
function LastDocumentBanner() {
  const { t } = useTranslation('workspace')
  const issue = useProjectStore((s) => s.lastDocumentIssue)
  if (!issue) return null
  return (
    <Banner tone="accent" icon={<RotateCcwClock size={ICON_SIZE.sm} className="shrink-0 text-accent" />}>
      {/* 文档名是用户内容，作为插值原样透出 */}
      <span className="min-w-0 flex-1 truncate">
        {t('docBanner.lastDocTitle', { name: issue.name || t('docBanner.lastDocUnnamed') })}
      </span>
      <span className="hidden shrink-0 opacity-80 min-[900px]:inline">{t('docBanner.lastDocBody')}</span>
      <Button
        size="sm"
        className="shrink-0"
        onClick={() => void useProjectStore.getState().openLastDocument()}
      >
        {t('docBanner.lastDocOpen')}
      </Button>
      <Button
        size="sm"
        className="shrink-0"
        onClick={() => useProjectStore.getState().dismissLastDocumentIssue()}
      >
        {t('docBanner.dismiss')}
      </Button>
    </Banner>
  )
}

function Banner({
  tone,
  icon,
  children,
}: {
  tone: 'danger' | 'accent'
  icon: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div
      role="status"
      className={
        tone === 'danger'
          ? 'flex min-h-6 shrink-0 items-center gap-2 border-b border-border bg-danger-subtle px-2.5 text-xs text-danger'
          : 'flex min-h-6 shrink-0 items-center gap-2 border-b border-border bg-accent-subtle px-2.5 text-xs text-accent'
      }
    >
      {icon}
      {children}
    </div>
  )
}
