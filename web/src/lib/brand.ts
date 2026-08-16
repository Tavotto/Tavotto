/**
 * 品牌与格式标识的唯一出处。产品正式名称 Magplot，拼写与大小写固定，
 * 界面任何地方不得再手写产品名。
 *
 * 写出端一律用新标识；读取端同时接受 LEGACY_*（Magic Matplot 时代的
 * 存量项目包 / 剪贴板 / proof 必须继续可用）。
 */
export const PRODUCT_NAME = 'Magplot'

export const PACKAGE_KIND = 'magplot-package'
export const PROOF_KIND = 'magplot-proof'
export const CLIPBOARD_FORMAT = 'magplot/objects@1'
export const PACKAGE_EXT = '.magplot'

export const LEGACY_PACKAGE_KIND = 'magic-matplot-package'
export const LEGACY_PROOF_KIND = 'magic-matplot-proof'
export const LEGACY_CLIPBOARD_FORMAT = 'magic-matplot/objects@1'
export const LEGACY_PACKAGE_EXT = '.mmpack.zip'
