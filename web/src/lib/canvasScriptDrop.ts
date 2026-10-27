/**
 * 编辑器里「把 .py 拖到画布上」的纯判据（2026-10-27 用户拍板：拖到哪张画布，跑出来的图就放进哪张，不新建画布）。
 *
 * 同一次放下从两条路到：画布自己的 DOM `drop`（有落点与画布，没有路径），以及 macOS 壳旁听到的系统拖放事件
 * （有真实路径，没有落点；ADR 0092）。两者先后不定，所以这里**配对**：先到的那条等另一条最多 `graceMs`；
 * 配上了就一起用。只等到 DOM = 拿不到路径，走降级；**只等到壳的事件 = 什么都不做**：页面别处接受了文件拖放
 * （例如联系作者的附件区）时壳同样会发这条事件，那不是拖到画布上，不能拿它去运行脚本。
 * 主页那条仲裁（`createDropArbiter`）只要路径、谁到都行，这里两样都要，所以不复用它。
 */
import { isScriptName } from './scriptImport'

export interface CanvasDropPlace {
  canvasId: string
  /** 放下的位置（页面毫米坐标）；单图编辑这一屏没有页面坐标，不给 */
  at?: { x: number; y: number }
}

/** 一条路径看起来是 Windows 的（盘符 / UNC）：比较时不分大小写 */
const windowsLike = (p: string) => /^[a-z]:[\\/]/i.test(p) || /^[\\/]{2}[^\\/]/.test(p)

/** 统一成正斜杠、去掉结尾分隔符（盘符根 `C:/` 留着那一个） */
function normalize(p: string): string {
  const s = p.replace(/\\/g, '/')
  return /^[a-z]:\/$/i.test(s) ? s : s.replace(/\/+$/, '')
}

/**
 * 拖进来的脚本（绝对路径）相对项目根是哪个脚本；不在项目里、或不是 .py 就 null。
 * 只按字面比（壳交来的路径 canonicalize 过，项目根是后端解析过的路径）：解不开的符号链接宁可说「不在项目里」，不猜。
 */
export function projectRelativeScript(root: string, path: string): string | null {
  if (!isScriptName(path)) return null
  const r = normalize(root)
  const p = normalize(path)
  const fold = windowsLike(r) || windowsLike(p)
  const prefix = r.endsWith('/') ? r : `${r}/`
  const head = p.slice(0, prefix.length)
  if (p.length <= prefix.length || (fold ? head.toLowerCase() !== prefix.toLowerCase() : head !== prefix)) return null
  const rel = p.slice(prefix.length)
  return rel.split('/').some((seg) => seg === '..' || seg === '.' || seg === '') ? null : rel
}

/** 配对器：`run` 拿到路径与配上的画布落点；`degrade` 只有 DOM 那条、没等到路径；只有壳的事件则丢掉 */
export function createCanvasDropPairer<N>(opts: {
  graceMs: number
  run: (native: N, place: CanvasDropPlace) => void
}) {
  let dom: { place: CanvasDropPlace; degrade: () => void; timer: ReturnType<typeof setTimeout> } | null = null
  let native: { drop: N; timer: ReturnType<typeof setTimeout> } | null = null
  return {
    dom(place: CanvasDropPlace, degrade: () => void) {
      if (native) {
        clearTimeout(native.timer)
        const n = native.drop
        native = null
        opts.run(n, place)
        return
      }
      if (dom) clearTimeout(dom.timer)
      const timer = setTimeout(() => {
        const d = dom
        dom = null
        d?.degrade()
      }, opts.graceMs)
      dom = { place, degrade, timer }
    },
    native(drop: N) {
      if (dom) {
        clearTimeout(dom.timer)
        const place = dom.place
        dom = null
        opts.run(drop, place)
        return
      }
      if (native) clearTimeout(native.timer)
      const timer = setTimeout(() => {
        native = null
      }, opts.graceMs)
      native = { drop, timer }
    },
    dispose() {
      if (dom) clearTimeout(dom.timer)
      if (native) clearTimeout(native.timer)
      dom = native = null
    },
  }
}
