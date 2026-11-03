/**
 * 已打开的项目里“生成可编辑版本”的两个入口（素材卡、脚本库）共用的动作（ADR 0143 修订「项目内入口」）。
 *
 * 事实（哪张图旁有同名外语脚本、脚本是什么语言）由后端报告，这里只把它交给**与主页那张卡同一个**转换入口：
 * 提议记在 `foreignScriptStore`（画布右下的 `ForeignScriptCard` 展示），生成流水归 `convertStore.start`
 * （`POST /api/ai/convert`，`source_language` = 后端给的语言）。不新起第二套流程，也不运行 R / MATLAB。
 */
import type { ForeignSource } from '@/lib/api'
import { useConvertStore } from '@/store/convertStore'
import { useForeignScriptStore } from '@/store/foreignScriptStore'

/** 点开：出那张一句话卡（主按钮「生成可编辑版本」），还不开始生成 */
export function offerForeignConvert(source: ForeignSource): void {
  useForeignScriptStore.getState().setOffer({ script: source.script, language: source.language })
}

/** 素材卡的主按钮：出卡并直接开始生成；这个脚本已有进行中 / 待处理的转换时只出卡（卡片接着展示，不重发请求） */
export function startForeignConvert(source: ForeignSource): void {
  offerForeignConvert(source)
  if (useConvertStore.getState().entries[source.script]) return
  void useConvertStore.getState().start(source.script, null, null, source.language)
}
