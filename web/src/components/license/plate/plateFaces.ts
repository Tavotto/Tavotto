// 创始会员铭牌的贴图：每一面画成三张 SVG 数据贴图（颜色 / 高度 / 粗糙度），交给 WebGL 的 PBR 材质。
// 逐字移植自官网 Tavotto_website `src/scripts/foundingPlate.ts`（质感与版式要与官网完全一致）；只改了类型与导出。
import { LOGO_ACCENT, LOGO_MAIN } from './plateLogo'

const W = 1020
const H = 642

export type Kind = 'color' | 'height' | 'rough'

export const PALETTE = {
  titanium: {
    top: '#1f242c',
    mid: '#2b313c',
    bot: '#21262f',
    etch: '#dce2ec',
    dim: '#9fa8b8',
    edge: '#566072',
  },
  silver: {
    top: '#cfd4dc',
    mid: '#e4e8ee',
    bot: '#d4d9e1',
    etch: '#383e48',
    dim: '#575e6b',
    edge: '#e8edf4',
  },
}

/**
 * One face as an SVG data texture in three passes: colour (metal albedo/F0 tint),
 * height (shallow laser cut + deep chamfered logo recess) and roughness (sleek
 * ~0.23 titanium sheen, frosted ~0.58 laser-etched type, and ~0.06 mirror-silver emblem).
 */
/** A fine-line, laser-engraved floral guilloché in the manner of a metal charge card: nested petal
 *  outlines and a woven rosette band, with hairline vines along the edges. It is cut into all three
 *  maps — glossier, a touch shallower and a whisper lighter than the bare face — so it reads as a
 *  quiet engraving head-on and flashes silver when a softbox strip slides across it. */
type Pt = [number, number]
function engraving(side: 'front' | 'back', kind: Kind, silver: boolean): string {
  const ink = {
    rough: silver ? '#2b2b2b' : '#2e2e2e',
    height: '#787878',
    color: silver ? '#ffffff' : '#c9d2e0',
  }[kind]
  const alpha = kind === 'color' ? (silver ? 0.09 : 0.13) : 1
  const f = (n: number) => n.toFixed(1)
  const polar = (cx: number, cy: number, r: (t: number) => number, n = 540) => {
    let d = ''
    for (let i = 0; i <= n; i++) {
      const t = (i / n) * Math.PI * 2
      const q = r(t)
      d += `${i ? 'L' : 'M'}${f(cx + q * Math.cos(t))} ${f(cy + q * Math.sin(t))}`
    }
    return `<path d="${d}Z"/>`
  }
  const [cx, cy] = side === 'front' ? [742, 286] : [W / 2, H / 2]
  let rose = ''
  // nested eight-petal outlines
  for (let j = 0; j < 15; j++) {
    const R = 34 + j * 15
    rose += polar(
      cx,
      cy,
      (t) => R * (0.6 + 0.4 * Math.pow(Math.abs(Math.cos(4 * t + j * 0.06)), 0.7)),
    )
  }
  // woven guilloché band around the flower
  for (let k = 0; k < 7; k++)
    rose += polar(
      cx,
      cy,
      (t) => 292 + 16 * Math.sin(40 * t + (k * Math.PI) / 7) + 6 * Math.sin(5 * t),
    )
  // outer fine rings
  for (let k = 0; k < 3; k++) rose += polar(cx, cy, () => 322 + k * 7, 240)

  // hairline vines along the edges, leaves and little flowers as outlines
  const stems: [Pt, Pt, Pt, Pt][] =
    side === 'front'
      ? [
          [
            [-30, 600],
            [180, 520],
            [300, 640],
            [470, 570],
          ],
          [
            [-30, 40],
            [140, 90],
            [220, 10],
            [360, 50],
          ],
        ]
      : [
          [
            [-30, 70],
            [200, 10],
            [340, 120],
            [520, 50],
          ],
          [
            [500, 600],
            [680, 640],
            [820, 540],
            [1050, 590],
          ],
        ]
  let vine = ''
  for (const c of stems) {
    vine += `<path d="M${c[0].join(' ')}C${c[1].join(' ')} ${c[2].join(' ')} ${c[3].join(' ')}"/>`
    for (let k = 1; k < 12; k++) {
      const t = k / 12
      const u = 1 - t
      const x =
        u * u * u * c[0][0] +
        3 * u * u * t * c[1][0] +
        3 * u * t * t * c[2][0] +
        t * t * t * c[3][0]
      const y =
        u * u * u * c[0][1] +
        3 * u * u * t * c[1][1] +
        3 * u * t * t * c[2][1] +
        t * t * t * c[3][1]
      const gx =
        3 * u * u * (c[1][0] - c[0][0]) +
        6 * u * t * (c[2][0] - c[1][0]) +
        3 * t * t * (c[3][0] - c[2][0])
      const gy =
        3 * u * u * (c[1][1] - c[0][1]) +
        6 * u * t * (c[2][1] - c[1][1]) +
        3 * t * t * (c[3][1] - c[2][1])
      const a = (Math.atan2(gy, gx) * 180) / Math.PI
      if (k % 4 === 2) {
        vine += `<g transform="translate(${f(x)} ${f(y)})">`
        for (let p = 0; p < 6; p++)
          vine += `<ellipse cy="-7" rx="3.4" ry="7" transform="rotate(${p * 60})"/>`
        vine += '<circle r="1.8"/></g>'
      } else {
        const s2 = k % 2 ? 1 : -1
        vine +=
          `<g transform="translate(${f(x)} ${f(y)}) rotate(${f(a + s2 * 40)})">` +
          '<path d="M0 0Q8 -6 20 0Q8 6 0 0ZM0 0H17"/></g>'
      }
    }
  }
  return (
    `<g fill="none" stroke="${ink}" stroke-opacity="${alpha}" stroke-linejoin="round">` +
    `<g stroke-width="1.05">${rose}</g><g stroke-width="1.15">${vine}</g></g>`
  )
}

export function face(side: 'front' | 'back', d: Record<string, string>, kind: Kind): string {
  const SANS =
    "'PingFang SC','Source Han Sans SC','Noto Sans SC','Helvetica Neue','Segoe UI',sans-serif"
  const MONO = "ui-monospace,'SF Mono',Menlo,Consolas,monospace"
  const silver = d.tone === 'silver'
  const pal = silver ? PALETTE.silver : PALETTE.titanium
  const esc = (v: string) => v.replace(/&/g, '&amp;').replace(/</g, '&lt;')
  type Opt = {
    mono?: boolean
    end?: boolean
    mid?: boolean
    ls?: number
    w?: number
    dim?: boolean
    op?: number
  }
  // Every word is cut and polished the way the emblem is: sunk in the height map, mirror-smooth in
  // the roughness map, and silver with a dark cut-shadow and a bright lip in the colour map.
  const text = (x: number, y: number, size: number, body: string, o: Opt = {}) => {
    const a =
      `x="${x}" y="${y}" font-size="${size}" font-weight="${o.w ?? 500}" font-family="${o.mono ? MONO : SANS}" letter-spacing="${o.ls ?? 0}"` +
      (o.end ? ' text-anchor="end"' : o.mid ? ' text-anchor="middle"' : '')
    const op = o.op != null ? ` opacity="${kind === 'color' ? o.op : 0.4 + o.op * 0.6}"` : ''
    const t = esc(body)
    const lip = Math.max(0.5, size / 64)
    if (kind === 'height')
      return `<text ${a} fill="${o.dim ? '#222222' : '#141414'}"${op}>${t}</text>`
    if (kind === 'rough')
      return (
        `<g${op}><text ${a} fill="none" stroke="#141414" stroke-width="${lip * 2.2}" stroke-linejoin="round">${t}</text>` +
        `<text ${a} fill="${o.dim ? '#191919' : '#111111'}">${t}</text></g>`
      )
    return (
      `<g${op}><text ${a} fill="none" stroke="${silver ? '#5c6472' : '#090b0f'}" stroke-width="${lip * 2.6}" opacity=".55" stroke-linejoin="round">${t}</text>` +
      `<text ${a} fill="url(#${o.dim ? 'la' : 'lm'})" stroke="#ffffff" stroke-width="${lip * 0.8}" stroke-opacity=".55" stroke-linejoin="round">${t}</text></g>`
    )
  }
  const logo = (x: number, y: number, s: number) => {
    const p = `transform="translate(${x} ${y}) scale(${s})" fill-rule="evenodd"`
    if (kind === 'height')
      return (
        `<g ${p} filter="url(#bev)">` +
        `<path d="${LOGO_MAIN}" fill="#141414"/><path d="${LOGO_ACCENT}" fill="#222222"/></g>`
      )
    if (kind === 'rough')
      return (
        `<g ${p}>` +
        `<g fill="none" stroke="#141414" stroke-width="2.6" stroke-linejoin="round">` +
        `<path d="${LOGO_MAIN}"/><path d="${LOGO_ACCENT}"/></g>` +
        `<path d="${LOGO_MAIN}" fill="#111111"/><path d="${LOGO_ACCENT}" fill="#191919"/></g>`
      )
    return (
      `<g ${p}>` +
      `<g fill="none" stroke="${silver ? '#5c6472' : '#090b0f'}" stroke-width="3" opacity=".55" stroke-linejoin="round">` +
      `<path d="${LOGO_MAIN}"/><path d="${LOGO_ACCENT}"/></g>` +
      `<g stroke="#ffffff" stroke-width="1" stroke-opacity=".62" stroke-linejoin="round">` +
      `<path d="${LOGO_MAIN}" fill="url(#lm)"/><path d="${LOGO_ACCENT}" fill="url(#la)"/></g></g>`
    )
  }
  const dateOp = d.dateEmpty ? 0.25 : undefined
  const words =
    side === 'front'
      ? text(78, 106, 24, 'TAVOTTO', { ls: 13, w: 500, dim: true }) +
        text(W - 78, 105, 20, d.label ?? '', { end: true, ls: 7, dim: true }) +
        text(76, 424, 64, d.name ?? '', { ls: 2.5 }) +
        text(78, 554, 19, d.serial ?? '', { mono: true, ls: 5.5, dim: true })
      : text(W / 2, 322, 17, 'ISSUED ON', {
          mid: true,
          ls: 9,
          dim: true,
          op: 0.82,
        }) +
        text(W / 2, 370, 31, d.date ?? '', {
          mid: true,
          mono: true,
          ls: 2.5,
          op: dateOp,
        }) +
        text(W / 2, 428, 17, d.serial ?? '', {
          mid: true,
          mono: true,
          ls: 5.5,
          dim: true,
          op: 0.68,
        })
  const badge = side === 'front' ? logo(850.4, 464.4, 0.84) : logo(460.1, 178, 0.78)
  const plain = {
    color: 'url(#g)',
    height: '#808080',
    rough: silver ? '#383838' : '#3d3d3d',
  }[kind]
  const crops =
    kind === 'rough'
      ? '<path d="M48 68V48h20M972 68V48h-20M48 574v20h20M972 574v20h-20" fill="none" stroke="#4b4b4b" stroke-width="1.2"/>'
      : ''
  const textLayer = kind === 'height' ? `<g filter="url(#bev)">${words}</g>` : words
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="1530" height="963" viewBox="0 0 ${W} ${H}">` +
    `<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${pal.top}"/><stop offset=".52" stop-color="${pal.mid}"/><stop offset="1" stop-color="${pal.bot}"/></linearGradient>` +
    `<linearGradient id="lm" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f7faff"/><stop offset=".5" stop-color="#d8e0ed"/><stop offset="1" stop-color="#b6c0d1"/></linearGradient>` +
    `<linearGradient id="la" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#bcc6d7"/><stop offset="1" stop-color="#8b96aa"/></linearGradient>` +
    `<filter id="grain" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency="0.95" numOctaves="2" seed="5"/><feColorMatrix type="saturate" values="0"/></filter>` +
    `<filter id="soft"><feGaussianBlur stdDeviation="0.55"/></filter>` +
    `<filter id="bev"><feGaussianBlur stdDeviation="0.9"/></filter></defs>` +
    `<rect width="${W}" height="${H}" fill="${plain}"/>` +
    `<rect width="${W}" height="${H}" filter="url(#grain)" opacity="${{ color: 0.022, height: 0.01, rough: 0.038 }[kind]}" style="mix-blend-mode:${kind === 'color' ? 'multiply' : 'normal'}"/>` +
    crops +
    engraving(side, kind, silver) +
    textLayer +
    badge +
    '</svg>'
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
}

export const SHADOW_SVG =
  'data:image/svg+xml;charset=utf-8,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="128" viewBox="0 0 256 128">' +
      '<defs><radialGradient id="s" cx="50%" cy="50%" r="50%">' +
      '<stop offset="0" stop-color="#12151b" stop-opacity=".3"/>' +
      '<stop offset=".42" stop-color="#12151b" stop-opacity=".17"/>' +
      '<stop offset=".74" stop-color="#12151b" stop-opacity=".05"/>' +
      '<stop offset="1" stop-color="#12151b" stop-opacity="0"/>' +
      '</radialGradient></defs>' +
      '<ellipse cx="128" cy="64" rx="126" ry="60" fill="url(#s)"/></svg>',
  )
