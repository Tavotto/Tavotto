/**
 * playground 运行时常量——数据源是 `packaging/playground-runtime.json`
 * （Pyodide 版本与包集合的**唯一权威**，构建脚本写产物 manifest 用的也是它）。
 * 这里只做形状收窄，不写第二份数字。
 */
import lock from '@playground-runtime'

export const PYODIDE_VERSION: string = lock.pyodide_version
export const PYTHON_VERSION: string = lock.python
export const PYODIDE_BASE_URL: string = lock.cdn_base
/** import 根名 → Pyodide 包名（分类器的输入） */
export const SUPPORTED_ROOTS: Record<string, string> = lock.import_roots
/** 官方承诺可用的包及精确版本（界面上的「浏览器环境」一栏） */
export const RUNTIME_PACKAGES: Record<string, string> = lock.packages

/** 源文件上限（与 engine/browser.py 的 MAX_SOURCE_BYTES 一致，两侧都拦） */
export const MAX_SOURCE_BYTES = 256 * 1024

/** engine.zip 的产物名（scripts/build_browser_playground.py 写出的就是它） */
export const ENGINE_ZIP_NAME = 'engine.zip'
