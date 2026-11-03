import { useEffect } from 'react'
import { useUiStore } from '@/store/uiStore'

/**
 * 定位要找的字段收在这一折里时，把这一折打开（`uiStore.fieldLocate`，由
 * `lib/issueFocus.focusField` 挂上）。
 *
 * 摘要行收起时不挂子控件（`ui/SummaryRow`），落点根本不在 DOM 里：不打开的话，
 * 问题面板点「图例字号偏小」只会选中图例、焦点哪儿也没落，用户顺手改了首屏那个
 * 字号（管的是图例项，不是标题），提醒还在（2026-10-23 用户反馈）。
 *
 * 只认**这个元素**的请求（`gid`），只开不关；开了就是用户此刻要看的，之后收不收由他。
 */
export function useLocateReveal(
  gid: string | null,
  props: readonly string[],
  open: boolean,
  reveal: () => void,
): void {
  const locate = useUiStore((s) => s.fieldLocate)
  const wanted = !!locate && locate.gid === gid && props.includes(locate.prop)
  useEffect(() => {
    if (wanted && !open) reveal()
    // 每张请求只响应一次：token 变了才是新的一次定位
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, locate?.token])
}
