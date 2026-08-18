/* eslint-disable */
/**
 * **真实 matplotlib 输出**的 SVG fixture，由 `scripts/dump_svg_fixture.py` 生成
 * （不要手改）。样式适配器的全部断言都打在这份上——手写一份「看起来像
 * matplotlib」的 SVG 只能验证我们对它的想象。
 *
 * 与真产物的唯一差别：字形/曲线的 `d=` 路径数据与内嵌 PNG 的 base64 被压短
 * （与样式无关，只是让 fixture 能读）。**`style=` 属性一个字节都没动**——
 * 适配器判断的正是它们的形状：
 *
 *   line      `<path style="fill: none; stroke: #1f77b4; stroke-width: 1.5">`
 *   bar       `<path style="fill: #ff7f0e; stroke: #333333; …">`（**没有**
 *             stroke-width：线宽等于默认值时 matplotlib 不输出，所以线宽的
 *             判据必须是 stroke 而不是 stroke-width）
 *   scatter   `<defs><path style="stroke…"/></defs>` + `<use style="fill…; stroke…">`
 *             （`<use>` 影子树里被引用元素自带的样式优先，两处都要改）
 *   fill      `<use style="fill: …; fill-opacity: 0.5; stroke: …; stroke-opacity: 0.5">`
 *             ——alpha 是分开的两条，不是一个 opacity
 *   arrow     杆 `fill: none` + 帽 `fill: <色>`，颜色要同时作用于两者
 *   text      `<g style="fill: #123456" transform="…">`；**默认黑色时没有这条 style**
 *   image     gid 落在 `<image>` 自身，且自带 transform（alpha 烤进 PNG，改不了）
 *
 * 这些 gid 在 manifest 里有、在 SVG 里**没有**（SeriesGroup / TickSet 伪元素），
 * 只能回退后端：axes_0.errorbar_* / axes_0.barseries_N / axes_0.[xy]ticks /
 * axes_0.[xy]ticklabels_*。
 */
export const MATPLOTLIB_SVG = String.raw`<svg xmlns:xlink="http://www.w3.org/1999/xlink" width="288pt" height="216pt" viewBox="0 0 288 216" xmlns="http://www.w3.org/2000/svg" version="1.1">
 <defs>
  <style type="text/css">*{stroke-linejoin: round; stroke-linecap: butt}</style>
 </defs>
 <g id="figure_1">
  <g id="patch_1">
   <path d="M 0 216 
L 288 216 
L 288 0 
L 0 0 
z
" style="fill: #ffffff"/>
  </g>
  <g id="axes_0">
   <g id="patch_2">
    <path d="M 36 136.98 
L 259.2 136.98 
L 259.2 81.18 
L 36 81.18 
z
" style="fill: #ffffff"/>
   </g>
   <g clip-path="url(#p000)">
    <image xlink:href="data:image/png;base64,iVBORw0KGgo=" id="axes_0.images_0" transform="scale(1 -1) translate(0 -56.16)" x="36" y="-80.82" width="223.2" height="56.16"/>
   </g>
   <g id="axes_0.scatter_0">
    <defs>
     <path id="m001" d="M 0 0 L 1 1" style="stroke: #000000; stroke-width: 0.8"/>
    </defs>
    <g clip-path="url(#p000)">
     <use xlink:href="#m001" x="-1" y="301.425228" style="fill: #2ca02c; stroke: #000000; stroke-width: 0.8"/>
     <use xlink:href="#m001" x="147.6" y="347.474936" style="fill: #2ca02c; stroke: #000000; stroke-width: 0.8"/>
    </g>
   </g>
   <g id="axes_0.barseries_0.bar_0">
    <path d="M 0 0 L 1 1" clip-path="url(#p000)" style="fill: #ff7f0e; stroke: #333333; stroke-linejoin: miter"/>
   </g>
   <g id="axes_0.barseries_0.bar_1">
    <path d="M 0 0 L 1 1" clip-path="url(#p000)" style="fill: #ff7f0e; stroke: #333333; stroke-linejoin: miter"/>
   </g>
   <g id="axes_0.barseries_0.bar_2">
    <path d="M 0 0 L 1 1" clip-path="url(#p000)" style="fill: #ff7f0e; stroke: #333333; stroke-linejoin: miter"/>
   </g>
   <g id="axes_0.fill_2">
    <path d="M 0 0 L 1 1" clip-path="url(#p000)" style="fill: #8c564b; fill-opacity: 0.5; stroke: #111111; stroke-opacity: 0.5; stroke-width: 0.5"/>
   </g>
   <g id="axes_0.arrows_3">
    <path d="M 0 0 L 1 1" clip-path="url(#p000)" style="fill: none; stroke: #e377c2; stroke-width: 1.4; stroke-linecap: round"/>
    <path d="M 0 0 L 1 1" clip-path="url(#p000)" style="fill: #e377c2; stroke: #e377c2; stroke-width: 1.4; stroke-linecap: round"/>
   </g>
   <g id="matplotlib.axis_1">
    <g id="xtick_1">
     <g id="line2d_1">
      <defs>
       <path id="m002" d="M 0 0 
L 0 3.5 
" style="stroke: #000000; stroke-width: 0.8"/>
      </defs>
      <g>
       <use xlink:href="#m002" x="42.975" y="136.98" style="stroke: #000000; stroke-width: 0.8"/>
      </g>
     </g>
     <g id="text_1">
      <!-- 4.25 -->
      <g transform="translate(31.842188 151.578438) scale(0.1 -0.1)">
       <defs>
        <path id="DejaVuSans-34" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
        <path id="DejaVuSans-2e" d="M 684 794 
L 1344 794 
L 1344 0 
L 684 0 
L 684 794 
z
" transform="scale(0.015625)"/>
        <path id="DejaVuSans-32" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
        <path id="DejaVuSans-35" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
       </defs>
       <use xlink:href="#DejaVuSans-34"/>
       <use xlink:href="#DejaVuSans-2e" transform="translate(63.623047 0)"/>
       <use xlink:href="#DejaVuSans-32" transform="translate(95.410156 0)"/>
       <use xlink:href="#DejaVuSans-35" transform="translate(159.033203 0)"/>
      </g>
     </g>
    </g>
    <g id="xtick_2">
     <g id="line2d_2">
      <g>
       <use xlink:href="#m002" x="77.85" y="136.98" style="stroke: #000000; stroke-width: 0.8"/>
      </g>
     </g>
     <g id="text_2">
      <!-- 4.50 -->
      <g transform="translate(66.717188 151.578438) scale(0.1 -0.1)">
       <defs>
        <path id="DejaVuSans-30" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
       </defs>
       <use xlink:href="#DejaVuSans-34"/>
       <use xlink:href="#DejaVuSans-2e" transform="translate(63.623047 0)"/>
       <use xlink:href="#DejaVuSans-35" transform="translate(95.410156 0)"/>
       <use xlink:href="#DejaVuSans-30" transform="translate(159.033203 0)"/>
      </g>
     </g>
    </g>
    <g id="xtick_3">
     <g id="line2d_3">
      <g>
       <use xlink:href="#m002" x="112.725" y="136.98" style="stroke: #000000; stroke-width: 0.8"/>
      </g>
     </g>
     <g id="text_3">
      <!-- 4.75 -->
      <g transform="translate(101.592188 151.578438) scale(0.1 -0.1)">
       <defs>
        <path id="DejaVuSans-37" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
       </defs>
       <use xlink:href="#DejaVuSans-34"/>
       <use xlink:href="#DejaVuSans-2e" transform="translate(63.623047 0)"/>
       <use xlink:href="#DejaVuSans-37" transform="translate(95.410156 0)"/>
       <use xlink:href="#DejaVuSans-35" transform="translate(159.033203 0)"/>
      </g>
     </g>
    </g>
    <g id="xtick_4">
     <g id="line2d_4">
      <g>
       <use xlink:href="#m002" x="147.6" y="136.98" style="stroke: #000000; stroke-width: 0.8"/>
      </g>
     </g>
     <g id="text_4">
      <!-- 5.00 -->
      <g transform="translate(136.467188 151.578438) scale(0.1 -0.1)">
       <use xlink:href="#DejaVuSans-35"/>
       <use xlink:href="#DejaVuSans-2e" transform="translate(63.623047 0)"/>
       <use xlink:href="#DejaVuSans-30" transform="translate(95.410156 0)"/>
       <use xlink:href="#DejaVuSans-30" transform="translate(159.033203 0)"/>
      </g>
     </g>
    </g>
    <g id="xtick_5">
     <g id="line2d_5">
      <g>
       <use xlink:href="#m002" x="182.475" y="136.98" style="stroke: #000000; stroke-width: 0.8"/>
      </g>
     </g>
     <g id="text_5">
      <!-- 5.25 -->
      <g transform="translate(171.342188 151.578438) scale(0.1 -0.1)">
       <use xlink:href="#DejaVuSans-35"/>
       <use xlink:href="#DejaVuSans-2e" transform="translate(63.623047 0)"/>
       <use xlink:href="#DejaVuSans-32" transform="translate(95.410156 0)"/>
       <use xlink:href="#DejaVuSans-35" transform="translate(159.033203 0)"/>
      </g>
     </g>
    </g>
    <g id="xtick_6">
     <g id="line2d_6">
      <g>
       <use xlink:href="#m002" x="217.35" y="136.98" style="stroke: #000000; stroke-width: 0.8"/>
      </g>
     </g>
     <g id="text_6">
      <!-- 5.50 -->
      <g transform="translate(206.217188 151.578438) scale(0.1 -0.1)">
       <use xlink:href="#DejaVuSans-35"/>
       <use xlink:href="#DejaVuSans-2e" transform="translate(63.623047 0)"/>
       <use xlink:href="#DejaVuSans-35" transform="translate(95.410156 0)"/>
       <use xlink:href="#DejaVuSans-30" transform="translate(159.033203 0)"/>
      </g>
     </g>
    </g>
    <g id="xtick_7">
     <g id="line2d_7">
      <g>
       <use xlink:href="#m002" x="252.225" y="136.98" style="stroke: #000000; stroke-width: 0.8"/>
      </g>
     </g>
     <g id="text_7">
      <!-- 5.75 -->
      <g transform="translate(241.092188 151.578438) scale(0.1 -0.1)">
       <use xlink:href="#DejaVuSans-35"/>
       <use xlink:href="#DejaVuSans-2e" transform="translate(63.623047 0)"/>
       <use xlink:href="#DejaVuSans-37" transform="translate(95.410156 0)"/>
       <use xlink:href="#DejaVuSans-35" transform="translate(159.033203 0)"/>
      </g>
     </g>
    </g>
    <g id="axes_0.xlabel">
     <!-- X label -->
     <g transform="translate(130.492969 165.256563) scale(0.1 -0.1)">
      <defs>
       <path id="DejaVuSans-58" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
       <path id="DejaVuSans-20" transform="scale(0.015625)"/>
       <path id="DejaVuSans-6c" d="M 603 4863 
L 1178 4863 
L 1178 0 
L 603 0 
L 603 4863 
z
" transform="scale(0.015625)"/>
       <path id="DejaVuSans-61" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
       <path id="DejaVuSans-62" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
       <path id="DejaVuSans-65" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
      </defs>
      <use xlink:href="#DejaVuSans-58"/>
      <use xlink:href="#DejaVuSans-20" transform="translate(68.505859 0)"/>
      <use xlink:href="#DejaVuSans-6c" transform="translate(100.292969 0)"/>
      <use xlink:href="#DejaVuSans-61" transform="translate(128.076172 0)"/>
      <use xlink:href="#DejaVuSans-62" transform="translate(189.355469 0)"/>
      <use xlink:href="#DejaVuSans-65" transform="translate(252.832031 0)"/>
      <use xlink:href="#DejaVuSans-6c" transform="translate(314.355469 0)"/>
     </g>
    </g>
   </g>
   <g id="matplotlib.axis_2">
    <g id="ytick_1">
     <g id="line2d_8">
      <defs>
       <path id="m003" d="M 0 0 
L -3.5 0 
" style="stroke: #000000; stroke-width: 0.8"/>
      </defs>
      <g>
       <use xlink:href="#m003" x="36" y="130.005" style="stroke: #000000; stroke-width: 0.8"/>
      </g>
     </g>
     <g id="text_8">
      <!-- 0.6 -->
      <g transform="translate(13.096875 133.804219) scale(0.1 -0.1)">
       <defs>
        <path id="DejaVuSans-36" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
       </defs>
       <use xlink:href="#DejaVuSans-30"/>
       <use xlink:href="#DejaVuSans-2e" transform="translate(63.623047 0)"/>
       <use xlink:href="#DejaVuSans-36" transform="translate(95.410156 0)"/>
      </g>
     </g>
    </g>
    <g id="ytick_2">
     <g id="line2d_9">
      <g>
       <use xlink:href="#m003" x="36" y="102.105" style="stroke: #000000; stroke-width: 0.8"/>
      </g>
     </g>
     <g id="text_9">
      <!-- 0.8 -->
      <g transform="translate(13.096875 105.904219) scale(0.1 -0.1)">
       <defs>
        <path id="DejaVuSans-38" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
       </defs>
       <use xlink:href="#DejaVuSans-30"/>
       <use xlink:href="#DejaVuSans-2e" transform="translate(63.623047 0)"/>
       <use xlink:href="#DejaVuSans-38" transform="translate(95.410156 0)"/>
      </g>
     </g>
    </g>
    <g id="axes_0.ylabel">
     <!-- Y label -->
     <g transform="translate(7.017187 125.815937) rotate(-90) scale(0.1 -0.1)">
      <defs>
       <path id="DejaVuSans-59" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
      </defs>
      <use xlink:href="#DejaVuSans-59"/>
      <use xlink:href="#DejaVuSans-20" transform="translate(61.083984 0)"/>
      <use xlink:href="#DejaVuSans-6c" transform="translate(92.871094 0)"/>
      <use xlink:href="#DejaVuSans-61" transform="translate(120.654297 0)"/>
      <use xlink:href="#DejaVuSans-62" transform="translate(181.933594 0)"/>
      <use xlink:href="#DejaVuSans-65" transform="translate(245.410156 0)"/>
      <use xlink:href="#DejaVuSans-6c" transform="translate(306.933594 0)"/>
     </g>
    </g>
   </g>
   <g id="axes_0.lines_0">
    <path clip-path="url(#p000)" style="fill: none; stroke: #1f77b4; stroke-width: 1.5; stroke-linecap: square"/>
   </g>
   <g id="axes_0.lines_1">
    <path d="M 0 0 L 1 1" clip-path="url(#p000)" style="fill: none; stroke-dasharray: 7.4,3.2; stroke-dashoffset: 0; stroke: #d62728; stroke-width: 2"/>
   </g>
   <g id="LineCollection_1">
    <path clip-path="url(#p000)" style="fill: none; stroke: #9467bd; stroke-width: 1.2"/>
    <path clip-path="url(#p000)" style="fill: none; stroke: #9467bd; stroke-width: 1.2"/>
   </g>
   <g id="line2d_10">
    <defs>
     <path id="m004" d="M 3 0 
L -3 -0 
" style="stroke: #9467bd"/>
    </defs>
    <g clip-path="url(#p000)">
     <use xlink:href="#m004" x="-1" y="337.89" style="fill: #9467bd; stroke: #9467bd"/>
     <use xlink:href="#m004" x="8.1" y="339.255" style="fill: #9467bd; stroke: #9467bd"/>
    </g>
   </g>
   <g id="line2d_11">
    <g clip-path="url(#p000)">
     <use xlink:href="#m004" x="-1" y="283" style="fill: #9467bd; stroke: #9467bd"/>
     <use xlink:href="#m004" x="8.1" y="283.455" style="fill: #9467bd; stroke: #9467bd"/>
    </g>
   </g>
   <g id="line2d_12">
    <defs>
     <path id="m005" d="M 0 0 L 1 1" style="stroke: #9467bd"/>
    </defs>
    <g clip-path="url(#p000)">
     <use xlink:href="#m005" x="-1" y="310.445" style="fill: #9467bd; stroke: #9467bd"/>
     <use xlink:href="#m005" x="8.1" y="311.355" style="fill: #9467bd; stroke: #9467bd"/>
    </g>
   </g>
   <g id="patch_3">
    <path d="M 36 136.98 
L 36 81.18 
" style="fill: none; stroke: #000000; stroke-width: 0.8; stroke-linejoin: miter; stroke-linecap: square"/>
   </g>
   <g id="patch_4">
    <path d="M 259.2 136.98 
L 259.2 81.18 
" style="fill: none; stroke: #000000; stroke-width: 0.8; stroke-linejoin: miter; stroke-linecap: square"/>
   </g>
   <g id="patch_5">
    <path d="M 36 136.98 
L 259.2 136.98 
" style="fill: none; stroke: #000000; stroke-width: 0.8; stroke-linejoin: miter; stroke-linecap: square"/>
   </g>
   <g id="patch_6">
    <path d="M 36 81.18 
L 259.2 81.18 
" style="fill: none; stroke: #000000; stroke-width: 0.8; stroke-linejoin: miter; stroke-linecap: square"/>
   </g>
   <g id="axes_0.texts_0">
    <!-- inline text -->
    <g style="fill: #123456" transform="translate(-410.4 88.155) scale(0.1 -0.1)">
     <defs>
      <path id="DejaVuSans-69" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
      <path id="DejaVuSans-6e" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
      <path id="DejaVuSans-74" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
      <path id="DejaVuSans-78" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
     </defs>
     <use xlink:href="#DejaVuSans-69"/>
     <use xlink:href="#DejaVuSans-6e" transform="translate(27.783203 0)"/>
     <use xlink:href="#DejaVuSans-6c" transform="translate(91.162109 0)"/>
     <use xlink:href="#DejaVuSans-69" transform="translate(118.945312 0)"/>
     <use xlink:href="#DejaVuSans-6e" transform="translate(146.728516 0)"/>
     <use xlink:href="#DejaVuSans-65" transform="translate(210.107422 0)"/>
     <use xlink:href="#DejaVuSans-20" transform="translate(271.630859 0)"/>
     <use xlink:href="#DejaVuSans-74" transform="translate(303.417969 0)"/>
     <use xlink:href="#DejaVuSans-65" transform="translate(342.626953 0)"/>
     <use xlink:href="#DejaVuSans-78" transform="translate(402.400391 0)"/>
     <use xlink:href="#DejaVuSans-74" transform="translate(461.580078 0)"/>
    </g>
   </g>
   <g id="axes_0.texts_1">
    <!-- faded -->
    <g style="fill: #aa0000; opacity: 0.4" transform="translate(-270.9 116.055) scale(0.1 -0.1)">
     <defs>
      <path id="DejaVuSans-66" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
      <path id="DejaVuSans-64" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
     </defs>
     <use xlink:href="#DejaVuSans-66"/>
     <use xlink:href="#DejaVuSans-61" transform="translate(35.205078 0)"/>
     <use xlink:href="#DejaVuSans-64" transform="translate(96.484375 0)"/>
     <use xlink:href="#DejaVuSans-65" transform="translate(159.960938 0)"/>
     <use xlink:href="#DejaVuSans-64" transform="translate(221.484375 0)"/>
    </g>
   </g>
   <g id="axes_0.title">
    <!-- Title here -->
    <g transform="translate(119.320312 75.18) scale(0.12 -0.12)">
     <defs>
      <path id="DejaVuSans-54" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
      <path id="DejaVuSans-68" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
      <path id="DejaVuSans-72" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
     </defs>
     <use xlink:href="#DejaVuSans-54"/>
     <use xlink:href="#DejaVuSans-69" transform="translate(57.958984 0)"/>
     <use xlink:href="#DejaVuSans-74" transform="translate(85.742188 0)"/>
     <use xlink:href="#DejaVuSans-6c" transform="translate(124.951172 0)"/>
     <use xlink:href="#DejaVuSans-65" transform="translate(152.734375 0)"/>
     <use xlink:href="#DejaVuSans-20" transform="translate(214.257812 0)"/>
     <use xlink:href="#DejaVuSans-68" transform="translate(246.044922 0)"/>
     <use xlink:href="#DejaVuSans-65" transform="translate(309.423828 0)"/>
     <use xlink:href="#DejaVuSans-72" transform="translate(370.947266 0)"/>
     <use xlink:href="#DejaVuSans-65" transform="translate(409.810547 0)"/>
    </g>
   </g>
   <g id="axes_0.legend">
    <g id="patch_7">
     <path d="M 0 0 L 1 1" style="fill: #ffffff; opacity: 0.8; stroke: #cccccc; stroke-linejoin: miter"/>
    </g>
    <g id="line2d_13">
     <path d="M 0 0 L 1 1" style="fill: none; stroke: #1f77b4; stroke-width: 1.5; stroke-linecap: square"/>
    </g>
    <g id="axes_0.legend.texts_0">
     <!-- sin -->
     <g transform="translate(228.403125 97.778437) scale(0.1 -0.1)">
      <defs>
       <path id="DejaVuSans-73" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
      </defs>
      <use xlink:href="#DejaVuSans-73"/>
      <use xlink:href="#DejaVuSans-69" transform="translate(52.099609 0)"/>
      <use xlink:href="#DejaVuSans-6e" transform="translate(79.882812 0)"/>
     </g>
    </g>
    <g id="line2d_14">
     <path d="M 0 0 L 1 1" style="fill: none; stroke-dasharray: 7.4,3.2; stroke-dashoffset: 0; stroke: #d62728; stroke-width: 2"/>
    </g>
    <g id="axes_0.legend.texts_1">
     <!-- cos -->
     <g transform="translate(228.403125 112.456562) scale(0.1 -0.1)">
      <defs>
       <path id="DejaVuSans-63" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
       <path id="DejaVuSans-6f" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
      </defs>
      <use xlink:href="#DejaVuSans-63"/>
      <use xlink:href="#DejaVuSans-6f" transform="translate(54.980469 0)"/>
      <use xlink:href="#DejaVuSans-73" transform="translate(116.162109 0)"/>
     </g>
    </g>
    <g id="PathCollection_1">
     <g>
      <use xlink:href="#m001" x="210.403125" y="124.509687" style="fill: #2ca02c; stroke: #000000; stroke-width: 0.8"/>
     </g>
    </g>
    <g id="axes_0.legend.texts_2">
     <!-- pts -->
     <g transform="translate(228.403125 127.134687) scale(0.1 -0.1)">
      <defs>
       <path id="DejaVuSans-70" d="M 0 0 L 1 1" transform="scale(0.015625)"/>
      </defs>
      <use xlink:href="#DejaVuSans-70"/>
      <use xlink:href="#DejaVuSans-74" transform="translate(63.476562 0)"/>
      <use xlink:href="#DejaVuSans-73" transform="translate(102.685547 0)"/>
     </g>
    </g>
    <g id="patch_8">
     <path d="M 0 0 L 1 1" style="fill: #ff7f0e; stroke: #333333; stroke-linejoin: miter"/>
    </g>
    <g id="axes_0.legend.texts_3">
     <!-- bars -->
     <g transform="translate(228.403125 141.812812) scale(0.1 -0.1)">
      <use xlink:href="#DejaVuSans-62"/>
      <use xlink:href="#DejaVuSans-61" transform="translate(63.476562 0)"/>
      <use xlink:href="#DejaVuSans-72" transform="translate(124.755859 0)"/>
      <use xlink:href="#DejaVuSans-73" transform="translate(165.869141 0)"/>
     </g>
    </g>
    <g id="LineCollection_2">
     <path d="M 210.403125 157.990937 
L 210.403125 147.990937 
" style="fill: none; stroke: #9467bd; stroke-width: 1.2"/>
    </g>
    <g id="line2d_15">
     <g>
      <use xlink:href="#m004" x="210.403125" y="157.990937" style="fill: #9467bd; stroke: #9467bd"/>
     </g>
    </g>
    <g id="line2d_16">
     <g>
      <use xlink:href="#m004" x="210.403125" y="147.990937" style="fill: #9467bd; stroke: #9467bd"/>
     </g>
    </g>
    <g id="line2d_17"/>
    <g id="line2d_18">
     <g>
      <use xlink:href="#m005" x="210.403125" y="152.990937" style="fill: #9467bd; stroke: #9467bd"/>
     </g>
    </g>
    <g id="axes_0.legend.texts_4">
     <!-- err -->
     <g transform="translate(228.403125 156.490937) scale(0.1 -0.1)">
      <use xlink:href="#DejaVuSans-65"/>
      <use xlink:href="#DejaVuSans-72" transform="translate(61.523438 0)"/>
      <use xlink:href="#DejaVuSans-72" transform="translate(100.886719 0)"/>
     </g>
    </g>
   </g>
  </g>
 </g>
 <defs>
  <clipPath id="p000">
   <rect x="36" y="81.18" width="223.2" height="55.8"/>
  </clipPath>
 </defs>
</svg>`
