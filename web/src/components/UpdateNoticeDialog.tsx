import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { useRef } from 'react'
import { pendingUpdateNotice, type UpdateNotice } from '@/lib/updateNotice'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
import { useTelemetryStore } from '@/store/telemetryStore'
import { useUpdateStore } from '@/store/updateStore'
import { Markdown } from './ai/Markdown'
import { LogDetails } from './UpdateLogDetails'
import { UpdateRestartError } from './UpdateRestartError'
import { Button } from './ui/Button'
import { ProgressBar } from './ui/ProgressBar'
import { Dialog, type DialogFooterSlots } from './ui/Dialog'
import { Notice } from './ui/Notice'

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
 *      进行中页脚不撤：主按钮原位置灰、字换成进度（「正在下载 42%」）。
 */
export function UpdateNoticeDialog() {
  useTranslation('dialogs')
  const store = useUpdateStore()
  const consentPending = useTelemetryStore((s) => s.askOpen)
  const nativeConfirmPending = useNativeSessionStore((s) => s.pendingQueue.length > 0)
  const current = pendingUpdateNotice(store)
  const open = !!current && !consentPending && !nativeConfirmPending
  // 常驻挂载：关的那 90ms 里 notice 已经是 null（版本记成「稍后」了），正文仍按最后一份载荷画
  const last = useRef<UpdateNotice | null>(null)
  if (open) last.current = current
  const notice = last.current
  if (!notice) return null

  const { version, kind } = notice
  const later = () => store.dismiss(version)
  const downloading = kind === 'desktop' && store.desktopPhase === 'downloading'
  const applying = kind === 'self' && store.applying
  const busy = downloading || applying || store.relaunching

  /* ------------------------------ 内容 ------------------------------ */
  let body: React.ReactNode
  let footer: DialogFooterSlots

  if (kind === 'desktop' && store.desktopPhase === 'installed') {
    body = (
      <div className="flex flex-col gap-2">
        <p className="text-ink-2">{tt('installed', { version })}</p>
        {store.relaunchFailed && <UpdateRestartError detail={store.desktopError} />}
      </div>
    )
    footer = {
      secondary: (
        <Button data-update-dismiss variant="secondary" size="lg" onClick={later} disabled={store.relaunching}>
          {tt('relaunchLater')}
        </Button>
      ),
      primary: (
        <Button
          data-update-relaunch
          variant="primary"
          size="lg"
          onClick={() => void store.relaunch()}
          loading={store.relaunching}
        >
          {tt('relaunch')}
        </Button>
      ),
    }
  } else if (kind === 'self' && store.restartRequired) {
    // 装上了但进程还跑着旧代码：这句话要说清楚，别让人以为点完就换了版本
    body = <p className="text-ink-2">{tt('upgraded', { version })}</p>
    footer = {
      primary: (
        <Button variant="primary" size="lg" onClick={later}>
          {tt('gotIt')}
        </Button>
      ),
    }
  } else {
    // 还没动手 / 下载中 / 升级中 / 失败后再来一次：**同一副页脚**（2026-10-07 设计审计 §10.2）——
    // 进行中不把按钮整排撤掉（页脚高度跳、焦点掉到 body），主按钮原位置灰并把进度写在自己身上
    const failed =
      busy ? null : kind === 'desktop' ? store.desktopError : store.applyFailed ? store.applyLog : null
    const pct = downloading && store.desktopProgress !== null ? Math.round(store.desktopProgress * 100) : null
    body = (
      <div className="flex flex-col gap-3">
        {/* 失败是对上一次点击的回应：放在最上面，不埋在发行说明后面 */}
        {failed !== null && failed !== undefined && (
          <div className="flex flex-col gap-1.5">
            <Notice tone="danger">{tt('failed')}</Notice>
            {failed && <LogDetails text={failed} />}
          </div>
        )}
        {downloading && (
          // 拿不到 Content-Length 就走不确定态（扫动），不假装卡在某个百分比上；
          // 与设置 › 更新页同一份进度条
          <ProgressBar pct={pct} label={tt('downloadProgressAria')} />
        )}
        {applying && <ProgressBar pct={null} label={tt('upgrading')} />}
        {notice.notes ? (
          <section className="flex flex-col gap-1.5">
            <h3 className="type-section">{tt('notesTitle')}</h3>
            {/* Release 正文是 markdown（后端已截到 4000 字）：与助手回答同一份渲染器（不开裸 HTML），
                此前原样塞进 <pre>，「## 新功能」「- 导出」这些记号全露在外面 */}
            <div data-update-notes className="max-h-56 overflow-y-auto rounded-md bg-surface-2 px-3 py-2">
              <Markdown text={notice.notes} />
            </div>
          </section>
        ) : null}
        {kind === 'manual' && (
          <p className="text-ink-2">
            {tt('sourceBody')}{' '}
            <code className="rounded-xs bg-surface-2 px-1 font-mono text-sm text-ink">
              {notice.upgradeCommand ?? 'git pull'}
            </code>
          </p>
        )}
        {notice.notesUrl && (
          <a
            href={notice.notesUrl}
            target="_blank"
            rel="noreferrer"
            className="self-start text-ink-2 underline underline-offset-2 hover:text-ink"
          >
            {tt('releaseNotes')}
          </a>
        )}
      </div>
    )
    const run = kind === 'desktop' ? () => void store.installDesktop() : () => void store.apply()
    const primaryLabel = downloading
      ? pct === null
        ? tt('downloading')
        : tt('downloadingPct', { pct })
      : applying
        ? tt('upgrading')
        : tt(failed ? 'retry' : 'install')
    footer = {
      secondary: (
        <Button variant="secondary" size="lg" onClick={later} disabled={busy}>
          {tt('later')}
        </Button>
      ),
      primary:
        kind !== 'manual' ? (
          <Button data-update-install variant="primary" size="lg" onClick={run} disabled={busy}>
            {primaryLabel}
          </Button>
        ) : undefined,
    }
  }

  return (
    <Dialog
      open={open}
      // 随手关掉 = 稍后：同一版本不再问（锁住时 Dialog 自己不会走到这里）
      onOpenChange={(v) => {
        if (!v) later()
      }}
      // Esc 的安全答案就是「稍后」（与 × 同一件事）
      onEscape={busy ? undefined : later}
      title={tt('title', { version })}
      description={notice.current ? tt('current', { version: notice.current }) : undefined}
      size="md"
      busy={busy}
      anchor="update-notice"
      footer={footer}
    >
      {body}
    </Dialog>
  )
}
