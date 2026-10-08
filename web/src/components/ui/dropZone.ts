import { cn } from '@/lib/utils'

/**
 * 拖放「接收态」的唯一外观（2026-10-07 设计审计 §4.2 / §10.4，学 OpenBitFun 的拖放接受态）：
 * 静态时什么都不画（没有常驻虚线框——那是 2018 年的上传区）；**只在有东西拖到它上面时**
 * 出现 1.5px accent 虚线 + accent-subtle 底 + 一圈柔和外发光。
 *
 * 画在 outline 上而不是 border 上：接收态出现 / 消失时几何不变，里面的字不跳。
 * 主页拖放区、/try 的上传区与案例试验台共用这一份；调用方自己给圆角与静态底色。
 */
export function dropZoneClass(accepting: boolean): string {
  return cn(
    'outline-1 -outline-offset-1 outline-transparent transition-[outline-color,background-color,box-shadow] duration-fast',
    accepting &&
      'bg-accent-subtle outline-[1.5px] outline-dashed outline-accent shadow-[0_0_0_4px_color-mix(in_srgb,var(--color-accent)_14%,transparent)]',
  )
}
