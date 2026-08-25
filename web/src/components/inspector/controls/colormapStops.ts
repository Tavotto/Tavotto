/**
 * 内置 colormap 的渐变 stops——**离线采样自真实 matplotlib**（3.10.8，
 * 每个 cmap 均匀取 9 个点 `to_hex(cm(i/8))`），不联网、不在运行时算色。
 *
 * 覆盖范围 == 引擎 `manifest.CMAPS` 的白名单；两边都是手工维护的短表，
 * 由 colormapStops.test.ts 钉住已知 cmap 的端点值。重新生成：
 *
 *   <worker-python> - <<'EOF'
 *   import json, matplotlib
 *   from matplotlib.colors import to_hex
 *   names = [...]  # 同下
 *   print(json.dumps({n: [to_hex(matplotlib.colormaps[n](i/8)) for i in range(9)] for n in names}))
 *   EOF
 */
export const COLORMAP_STOPS: Record<string, string[]> = {
  viridis: ['#440154', '#472d7b', '#3b528b', '#2c728e', '#21918c', '#28ae80', '#5ec962', '#addc30', '#fde725'],
  plasma: ['#0d0887', '#4c02a1', '#7e03a8', '#aa2395', '#cc4778', '#e66c5c', '#f89540', '#fdc527', '#f0f921'],
  inferno: ['#000004', '#210c4a', '#57106e', '#8a226a', '#bc3754', '#e45a31', '#f98e09', '#f9cb35', '#fcffa4'],
  magma: ['#000004', '#1d1147', '#51127c', '#832681', '#b73779', '#e75263', '#fc8961', '#fec488', '#fcfdbf'],
  cividis: ['#00224e', '#1a386f', '#434e6c', '#61656f', '#7d7c78', '#9b9476', '#bcae6c', '#dec958', '#fee838'],
  Greys: ['#ffffff', '#f0f0f0', '#d9d9d9', '#bdbdbd', '#959595', '#727272', '#515151', '#242424', '#000000'],
  gray: ['#000000', '#202020', '#404040', '#606060', '#808080', '#a0a0a0', '#c0c0c0', '#e0e0e0', '#ffffff'],
  hot: ['#0b0000', '#5f0000', '#b30000', '#ff0800', '#ff5c00', '#ffb000', '#ffff07', '#ffff85', '#ffffff'],
  afmhot: ['#000000', '#400000', '#800000', '#c04000', '#ff8001', '#ffc041', '#ffff81', '#ffffc1', '#ffffff'],
  coolwarm: ['#3b4cc0', '#6282ea', '#8db0fe', '#b9d0f9', '#dddcdc', '#f5c4ac', '#f4987a', '#dd5f4b', '#b40426'],
  RdBu_r: ['#053061', '#2a71b2', '#6bacd1', '#c2ddec', '#f7f6f6', '#fbccb4', '#e48066', '#ba2832', '#67001f'],
  seismic: ['#00004c', '#0000a6', '#0101ff', '#8181ff', '#fffdfd', '#ff7d7d', '#fe0000', '#be0000', '#800000'],
  jet: ['#000080', '#0000ff', '#0080ff', '#16ffe1', '#7dff7a', '#e4ff13', '#ff9400', '#ff1e00', '#800000'],
  turbo: ['#30123b', '#466be3', '#28bceb', '#32f298', '#a4fc3c', '#eecf3a', '#fb7e21', '#d02f05', '#7a0403'],
}

/** CSS 渐变；未知 cmap（脚本自定义）返回 null → 调用方回落到名称显示 */
export function colormapGradient(name: string): string | null {
  const stops = COLORMAP_STOPS[name]
  if (!stops) return null
  return `linear-gradient(to right, ${stops.join(', ')})`
}
