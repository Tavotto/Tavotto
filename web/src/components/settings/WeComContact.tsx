import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { currentLocale, t as translate } from '@/i18n'
import qrUrl from '@/assets/wecom-contact-qr.png'
import { WECOM_CONTACT_URL } from '@/lib/contact'
import { Details, Summary } from '../ui/Details'

const wc = (key: string) => translate(`settings.diagnostics.send.contact.${key}`, { ns: 'dialogs' })

/** 联系入口只在中文界面出现：判据是界面语言（`currentLocale()`），不看系统区域。 */
export function useWeComContactVisible(): boolean {
  useTranslation('dialogs')
  return currentLocale() === 'zh-CN'
}

/** 「加企业微信联系我们」文字链（系统浏览器打开，与其余外链同一写法）。 */
export function WeComLink() {
  return (
    <a
      href={WECOM_CONTACT_URL}
      target="_blank"
      rel="noreferrer"
      data-wecom-link
      className="underline underline-offset-2 hover:text-ink"
    >
      {wc('link')}
    </a>
  )
}

/** 默认折叠的二维码：展开才挂 img。 */
export function WeComQr() {
  const [open, setOpen] = useState(false)
  return (
    <Details data-wecom-qr className="text-sm" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <Summary className="h-6 text-ink-2 hover:text-ink">{wc('showQr')}</Summary>
      {open && <WeComQrImage />}
    </Details>
  )
}

/** 二维码图（展开后才挂）。 */
export function WeComQrImage() {
  return (
    // 原图（sha256 钉死、不改）上下各自带一条 2 px 的细边线；用外层裁掉 1 个显示像素（约 3.7 个原图像素，
    // 只吃线与线内侧的留白），二维码本体与左右留白原样保留
    <div className="mt-1 h-[158px] w-[160px] overflow-hidden rounded-md bg-paper" data-wecom-qr-crop>
      <img src={qrUrl} alt={wc('qrAlt')} width={160} height={160} data-wecom-qr-img className="-mt-px block max-w-none" />
    </div>
  )
}

/**
 * 确认框里的一行：想直接沟通？加企业微信（失败时没有报告编号，换一句不提编号的）。
 * 链接与「显示二维码」同在一行（只占一行高）；二维码默认折叠，展开才挂 `<img>`。
 *
 * `active=false` 时**占位不显示**（`invisible` + `inert`，中文界面）：结果态才出现的这一行若是从无到有，
 * 对话框会变高、页脚「发送」按钮跟着跳位（#797 e2e 量的就是发送前后的按钮坐标）。英文界面一律不渲染。
 */
export function WeComContactNote({ withReportId, active }: { withReportId: boolean; active: boolean }) {
  const visible = useWeComContactVisible()
  const [qrOpen, setQrOpen] = useState(false)
  // 离开结果态（重试 / 换项目重新备包）就把二维码收起：下一次结果出来时从折叠开始，不自己展开把页脚顶走
  const [wasActive, setWasActive] = useState(active)
  if (wasActive !== active) {
    setWasActive(active)
    if (!active) setQrOpen(false)
  }
  if (!visible) return null
  return (
    <div
      className={active ? undefined : 'invisible'}
      data-wecom-contact={active ? 'active' : 'reserved'}
      aria-hidden={active ? undefined : true}
      inert={!active}
    >
      {/* 两句文案长短不一（带编号的 hint 更长），窄窗口 / 高缩放下会多折一行：叠一份最长文案的隐形副本占住高度，
          占位、失败、成功各态取同一个最大高度，页脚不跳位 */}
      <div className="grid">
        <p className="type-caption invisible [grid-area:1/1]" aria-hidden data-wecom-contact-sizer>
          {wc('hint')} <span className="underline underline-offset-2">{wc('link')}</span>
          {' · '}
          <span className="underline underline-offset-2">{wc('showQr')}</span>
        </p>
        <p className="type-caption [grid-area:1/1]">
          {wc(withReportId ? 'hint' : 'hintNoId')} <WeComLink />
          {' · '}
          <button
            type="button"
            aria-expanded={qrOpen}
            data-wecom-qr-toggle
            className="underline underline-offset-2 hover:text-ink"
            onClick={() => setQrOpen((v) => !v)}
          >
            {wc('showQr')}
          </button>
        </p>
      </div>
      {active && qrOpen && <WeComQrImage />}
    </div>
  )
}
