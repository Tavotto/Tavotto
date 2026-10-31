import { useTranslation } from 'react-i18next'
import { Ellipsis, TriangleAlert } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { formatTime } from '@/i18n/format'
import { overwriteDisk, reloadFromDisk, useDocumentStore } from '@/store/documentStore'
import { msg } from '@/i18n'
import { askConfirm, useUiStore } from '@/store/uiStore'
import { Button } from './ui/Button'
import { Menu, MenuItem } from './ui/Menu'
import type { DiskDocumentSummary } from '@/lib/api'

/**
 * 文档级的事此前在这里有一条常驻提示条（冲突 / 保存失败）；2026-10 设计审计第二轮起它们只在顶栏的文档状态芯片里
 * （`TopBar.SaveStateLabel`：进入时说明块自动展开一次、读屏 assertive 播报）。这个文件留下的是芯片说明块用的
 * 冲突摘要 / 冲突三出口。工作面板里不再有任何提示条——`Banner` / `BannerStack` 已删（2026-10-16 UI 2.0：构建版本不一致
 * 也进了顶栏状态芯片 `BuildMismatchChip`）。
 */

/** 磁盘上那份的一句摘要（顶栏芯片的说明块用） */
export function ConflictDetail({ disk }: { disk: DiskDocumentSummary | null | undefined }) {
  const { t } = useTranslation('workspace')
  return (
    <>
      {disk && typeof disk.objects === 'number'
        ? t('docBanner.conflictDisk', {
            canvases: disk.canvases,
            objects: disk.objects,
            time: formatTime(disk.mtime ?? disk.updatedAt ?? Date.now()),
          })
        : t('docBanner.conflictDiskUnknown')}
    </>
  )
}

/**
 * 冲突的三个出口，分轻重（2026-10-07 审计 P0 / §10.1）：「重新加载」是安全的那条（先把本机编辑存成恢复副本
 * 再读盘），主按钮；「另存为」两份都留，次按钮；「覆盖」会丢掉磁盘上那份——收进 ⋯ 里的危险项，并且先问一句
 * （`confirmOverwriteDisk`）。此前三颗同形同重，一下就点没了。冲突条与顶栏芯片的说明块用同一份。
 */
export function ConflictActions({ onAction }: { onAction?: () => void }) {
  const { t } = useTranslation('workspace')
  const stale = useDocumentStore((s) => s.saveIssue?.kind === 'stale')
  const then = (fn: () => unknown) => () => {
    onAction?.()
    void fn()
  }
  return (
    <>
      <Button
        variant="primary"
        size="sm"
        className="shrink-0"
        data-doc-conflict-action="reload"
        onClick={then(reloadFromDisk)}
      >
        {t('docBanner.reload')}
      </Button>
      <Button
        variant="secondary"
        size="sm"
        className="shrink-0"
        data-doc-conflict-action="save-as"
        onClick={then(() => useUiStore.getState().setLayoutOpen(true, 'save'))}
      >
        {t('docBanner.saveAs')}
      </Button>
      <Menu
        align="end"
        width={220}
        trigger={
          <Button size="icon-sm" data-doc-conflict-more aria-label={t('docBanner.moreActions')}>
            <Ellipsis size={ICON_SIZE.sm} />
          </Button>
        }
      >
        <MenuItem
          danger
          icon={TriangleAlert}
          data-doc-conflict-action="overwrite"
          onSelect={then(() => confirmOverwriteDisk(stale))}
        >
          {t('docBanner.overwrite')}
        </MenuItem>
      </Menu>
    </>
  )
}

/**
 * 「用这个窗口的覆盖」之前先问一句：它会让磁盘上那份（另一个窗口存的较新版本 / 外部改过的文件）
 * 被这个窗口的内容替换掉，而且不像「重新加载」那样先留恢复副本。危险档确认框。
 *
 * 等用户回答的那段时间里冲突可能已经被别处裁决了（另一颗按钮、换了文档）：回答之后**冲突还在、
 * 还是同一份文档**才覆盖，否则什么都不做。
 */
async function confirmOverwriteDisk(stale: boolean): Promise<boolean> {
  const before = useDocumentStore.getState().documentId
  const ok = await askConfirm({
    title: msg('docBanner.overwriteConfirmTitle', undefined, 'workspace'),
    body: msg(
      stale ? 'docBanner.overwriteConfirmBodyStale' : 'docBanner.overwriteConfirmBodyExternal',
      undefined,
      'workspace',
    ),
    confirmLabel: msg('docBanner.overwrite', undefined, 'workspace'),
    danger: true,
  })
  if (!ok) return false
  const now = useDocumentStore.getState()
  if (now.saveState !== 'conflict' || now.documentId !== before) return false
  await overwriteDisk()
  return true
}
