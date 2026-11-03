import { useEffect, useRef, useState } from 'react'
import { t as translate } from '@/i18n'
import { fetchContactReplyDoc } from '@/lib/api'

/**
 * 作者的 HTML 回信（ADR 0145 第二期修订）：白色信纸里放一个沙盒 iframe。
 *
 * - 文档由本机引擎拼好（`engine/contactinbox.reply_doc`：CSP `default-src 'none'; img-src data:` + 基础样式 + 回信 HTML，
 *   附图已内联成 data: URI），这里只 `srcDoc` 它——**不联网**。
 * - `sandbox` 里**没有** `allow-scripts`：回信里即使混进脚本也不执行；`allow-same-origin` 只为父页面读
 *   `contentDocument.documentElement.scrollHeight` 自适应高度（iframe 内没有脚本、CSP 禁网络）。
 *   链接带 `target=_blank`（服务器强制），`allow-popups` 让它走新窗口——桌面壳的 `on_new_window` 交给系统浏览器。
 * - **深色模式也是白纸**（`bg-paper` 两套主题同值，信是印刷品；文档自己也是白底深字）。
 * - 文档取不到就退回纯文字（`fallback`），不留空白。
 */
export const REPLY_PAPER_SANDBOX = 'allow-same-origin allow-popups allow-popups-to-escape-sandbox'

const cs = (key: string) => translate(`contact.${key}`, { ns: 'dialogs' })

export function ReplyPaper({
  messageId,
  replyId,
  fallback,
}: {
  messageId: string
  replyId: string
  /** 文档取不到时显示的纯文字回信 */
  fallback: string
}) {
  const [doc, setDoc] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [height, setHeight] = useState(120)
  const frame = useRef<HTMLIFrameElement>(null)

  useEffect(() => {
    let alive = true
    setDoc(null)
    setFailed(false)
    fetchContactReplyDoc(messageId, replyId).then(
      (r) => alive && setDoc(r.doc),
      () => alive && setFailed(true),
    )
    return () => {
      alive = false
    }
  }, [messageId, replyId])

  const measure = () => {
    const h = frame.current?.contentDocument?.documentElement?.scrollHeight
    if (h && h > 0) setHeight(h)
  }
  useEffect(measure, [doc])
  useEffect(() => {
    const el = frame.current
    if (!el || typeof ResizeObserver === 'undefined') return
    // 宽度变了（窗口缩放）折行会变，高度要重量
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [doc])

  if (failed) {
    return (
      <div className="flex flex-col gap-1" data-contact-reply-fallback>
        <p className="type-caption">{cs('replyPaperFailed')}</p>
        <p className="whitespace-pre-wrap break-words text-sm text-ink">{fallback}</p>
      </div>
    )
  }
  if (doc === null) {
    return <p className="type-caption">{cs('replyLoading')}</p>
  }
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-paper" data-contact-reply-paper>
      <iframe
        ref={frame}
        title={cs('replyPaperTitle')}
        sandbox={REPLY_PAPER_SANDBOX}
        srcDoc={doc}
        onLoad={measure}
        className="block w-full border-0 bg-paper"
        style={{ height }}
      />
    </div>
  )
}
