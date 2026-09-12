import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import { pendingUpdateNotice } from '@/lib/updateNotice'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
import { useTelemetryStore } from '@/store/telemetryStore'
import { useUpdateStore } from '@/store/updateStore'
import { InlineWarning } from './settings/SettingRow'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'

/** 本对话框的文案在 dialogs:updateNotice.* 下 */
const tt = (key: string, values?: Record<string, unknown>) =>
  translate(`updateNotice.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 打开 Tavotto 时的「有新版本」询问：启动那次静默检查查到新版，就弹一次，
 * 说清楚是哪一版、有什么新东西，给两个出口——立即更新 / 稍后。
 *
 * 三条纪律：
 *   ① **每个版本只问一次**。「稍后」与随手关掉（× / Esc）都按版本记下来
 *      （`updateStore.dismiss`，落 localStorage），同一版本下次启动不再弹；出了
 *      更新的版本才再问。「⋯」上的圆点与设置页照旧，想更新随时能找到。
 *   ② **让位给更急的框**。首启的遥测同意（`TelemetryConsentDialog`）先问、
 *      `tavotto run` 的交接确认（那个终端正阻塞着）先答，它们关掉之后这个框
 *      才出现——两个模态叠在一起，用户不知道该先答哪个。
 *   ③ **没有第二套升级逻辑**。三个通道（桌面 Tauri / pip·pipx / 源码检出）的
 *      按钮全部落到 `updateStore` 既有的 action 上，进度、失败、装完要重启
 *      这几档也从同一份状态读——与「设置 → 检查更新」看到的是同一件事。
 *      下载 / 升级进行中锁住关闭：中途关掉会让人以为已取消，其实没有。
 */
export function UpdateNoticeDialog() {
  useTranslation('dialogs')
  const store = useUpdateStore()
  const consentPending = useTelemetryStore((s) => s.askOpen)
  const nativeConfirmPending = useNativeSessionStore((s) => s.pendingQueue.length > 0)
  const notice = pendingUpdateNotice(store)
  if (!notice || consentPending || nativeConfirmPending) return null

  const { version, kind } = notice
  const later = () => store.dismiss(version)
  const busy = store.desktopPhase === 'downloading' || store.applying

  /* ------------------------------ 内容 ------------------------------ */
  let body: React.ReactNode
  let footer: React.ReactNode

  if (kind === 'desktop' && store.desktopPhase === 'installed') {
    body = <p className="text-xs leading-relaxed text-ink-2">{tt('installed', { version })}</p>
    footer = (
      <>
        <Button variant="secondary" onClick={later}>
          {tt('relaunchLater')}
        </Button>
        <Button variant="primary" onClick={() => void store.relaunch()}>
          {tt('relaunch')}
        </Button>
      </>
    )
  } else if (kind === 'self' && store.restartRequired) {
    // 装上了但进程还跑着旧代码：这句话要说清楚，别让人以为点完就换了版本
    body = <p className="text-xs leading-relaxed text-ink-2">{tt('upgraded', { version })}</p>
    footer = (
      <Button variant="primary" onClick={later}>
        {tt('gotIt')}
      </Button>
    )
  } else if (kind === 'desktop' && store.desktopPhase === 'downloading') {
    const pct = store.desktopProgress === null ? null : Math.round(store.desktopProgress * 100)
    body = (
      <div className="flex flex-col gap-1.5">
        {/* 拿不到 Content-Length 就走不确定态，不假装卡在某个百分比上 */}
        <div
          role="progressbar"
          aria-label={tt('downloadProgressAria')}
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
          {pct === null ? tt('downloading') : tt('downloadingPct', { pct })}
        </span>
      </div>
    )
    footer = null
  } else if (kind === 'self' && store.applying) {
    body = <p className="text-xs text-ink-3">{tt('upgrading')}</p>
    footer = null
  } else {
    // 还没动手（含失败后再来一次）：说明 + 出口
    const failed = kind === 'desktop' ? store.desktopError : store.applyFailed ? store.applyLog : null
    body = (
      <div className="flex flex-col gap-2.5">
        {notice.notes ? (
          <section>
            <h3 className="mb-1 text-xs font-medium text-ink-2">{tt('notesTitle')}</h3>
            {/* Release 正文原样透出（后端已截到 4000 字），不在前端解析 markdown */}
            <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap break-words rounded-sm bg-surface-2 p-2 text-xs leading-relaxed text-ink-2">
              {notice.notes}
            </pre>
          </section>
        ) : (
          <p className="text-xs leading-relaxed text-ink-2">{tt('intro')}</p>
        )}
        {kind === 'manual' && (
          <p className="text-xs leading-relaxed text-ink-2">
            {tt('sourceBody')}{' '}
            <code className="font-mono text-ink">{notice.upgradeCommand ?? 'git pull'}</code>
          </p>
        )}
        {notice.notesUrl && (
          <a
            href={notice.notesUrl}
            target="_blank"
            rel="noreferrer"
            className="self-start text-xs text-accent hover:underline"
          >
            {tt('releaseNotes')}
          </a>
        )}
        {/* 失败必须看得出是失败，并把后端 / 更新器说的那句话原样给出来 */}
        {failed !== null && failed !== undefined && (
          <div className="flex flex-col gap-1">
            <InlineWarning tone="danger">{tt('failed')}</InlineWarning>
            {failed && (
              <pre className="max-h-32 overflow-y-auto whitespace-pre-wrap break-words rounded-sm bg-surface-2 p-1.5 font-mono text-xs text-ink-3">
                {failed}
              </pre>
            )}
          </div>
        )}
      </div>
    )
    const run = kind === 'desktop' ? () => void store.installDesktop() : () => void store.apply()
    footer = (
      <>
        <Button variant="secondary" onClick={later}>
          {tt('later')}
        </Button>
        {kind !== 'manual' && (
          <Button variant="primary" onClick={run}>
            {tt(failed ? 'retry' : 'install')}
          </Button>
        )}
      </>
    )
  }

  return (
    <Dialog
      open
      // 随手关掉 = 稍后：同一版本不再问（锁住时 Dialog 自己不会走到这里）
      onOpenChange={(v) => {
        if (!v) later()
      }}
      title={tt('title', { version })}
      description={notice.current ? tt('current', { version: notice.current }) : undefined}
      size="md"
      busy={busy}
      anchor="update-notice"
      footer={footer && <div className="flex items-center justify-end gap-2">{footer}</div>}
    >
      {body}
    </Dialog>
  )
}
