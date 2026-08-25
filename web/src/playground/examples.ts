/**
 * playground 内置案例的元数据。**源码的唯一真源是 `examples/*.py`**——
 * 这里经 vite 的 `?raw` import 读同一份文件，封面由
 * `scripts/generate_playground_examples.py` 对同一份文件真实执行后生成
 * （`generated/examples-manifest.json` 记着源码哈希，改了 .py 不重新生成
 * 封面在 CI 是红灯）。TypeScript 里不再抄第二份 Python。
 *
 * 三条纪律（ADR 0007）：
 *   * 案例就是普通的科研 matplotlib 脚本——不 import pyodide / js / 任何
 *     Tavotto 专有 API，Tavotto 接的是用户**本来就在写**的图；
 *   * 小到能读完；
 *   * 封面只用于卡片首屏展示。**启动案例仍然把源码交给 Pyodide 真实执行**，
 *     不是预烤的 manifest（那是在演示假东西）。
 *
 * 三张都在 savefig 前 `tight_layout()`：matplotlib 的默认边距在这个尺寸下
 * 会把 x/y 轴标签裁掉（实测三张全中）。轴标签恰恰是访客第一件想点的东西。
 */
import kineticsSource from './examples/kinetics.py?raw'
import calibrationSource from './examples/calibration.py?raw'
import spectrumSource from './examples/spectrum.py?raw'
import kineticsThumb from './generated/kinetics.webp'
import calibrationThumb from './generated/calibration.webp'
import spectrumThumb from './generated/spectrum.webp'
import coversManifest from './generated/examples-manifest.json'

/**
 * 进编辑器后的首次引导：一个**用户亲手完成**的两步小任务。
 * 完成判据全部是真实状态（选中 gid / override 值 / 渲染成功 / 完整性核对），
 * 绝不代用户点击或伪造完成态——GuidedTask.tsx 只观察，不代劳。
 */
export interface GuidedTaskSpec {
  /** 第一步要选中的元素 gid */
  targetGid: string
  /** 第二步要修改的属性 */
  prop: string
  /** 达到这个值即算完成 */
  targetValue: unknown
  /** 第一步文案的 i18n key 尾段（dialogs:playground.*） */
  selectKey: string
  /** 第二步文案的 i18n key 尾段 */
  editKey: string
}

export interface PlaygroundExample {
  id: string
  /** 虚拟工作区里的脚本名（用户可见，保留原文，不翻译） */
  filename: string
  /** Python 源码——`examples/<id>.py` 的原样字节 */
  source: string
  /** 案例名称的 i18n key 尾段（dialogs:playground.*） */
  titleKey: string
  /** 一句说明的 i18n key 尾段 */
  descriptionKey: string
  /** 可编辑对象提示行的 i18n key 尾段（如「标题 · 曲线 · 图例」） */
  editableKey: string
  /** 构建期从真实执行生成的封面（webp url） */
  thumbnail: string
  /** 封面固有尺寸（防 layout shift；与 generated manifest 同源） */
  thumbWidth: number
  thumbHeight: number
  /** starter = 新手推荐（首页主推，有且只有一个）；explore = 其余 */
  difficulty: 'starter' | 'explore'
  /** 首页主推案例。**有且只有一个**（examples.test.ts 看护） */
  featured?: true
  guidedTask?: GuidedTaskSpec
}

const cover = (id: keyof typeof coversManifest) => coversManifest[id]

export const EXAMPLES: PlaygroundExample[] = [
  {
    id: 'kinetics',
    filename: 'kinetics.py',
    source: kineticsSource,
    titleKey: 'exampleKinetics',
    descriptionKey: 'exampleKineticsDesc',
    editableKey: 'exampleKineticsEditable',
    thumbnail: kineticsThumb,
    thumbWidth: cover('kinetics').width,
    thumbHeight: cover('kinetics').height,
    difficulty: 'starter',
    // 主推案例：标题 / 轴标签 / 图例 / 两条曲线，点开就有东西可选可拖，
    // 又不至于复杂到第一眼看不懂。别为了「功能多」换成更花的那张。
    featured: true,
    // 源码里明确写了 fontsize=9——任务「9 pt 调到 12 pt」的起点是钉死的，
    // 不依赖 matplotlib 版本的默认标题字号
    guidedTask: {
      targetGid: 'axes_0.title',
      prop: 'fontsize',
      targetValue: 12,
      selectKey: 'taskSelectTitle',
      editKey: 'taskEditFontsize',
    },
  },
  {
    id: 'calibration',
    filename: 'calibration.py',
    source: calibrationSource,
    titleKey: 'exampleCalibration',
    descriptionKey: 'exampleCalibrationDesc',
    editableKey: 'exampleCalibrationEditable',
    thumbnail: calibrationThumb,
    thumbWidth: cover('calibration').width,
    thumbHeight: cover('calibration').height,
    difficulty: 'explore',
  },
  {
    id: 'spectrum',
    filename: 'spectrum.py',
    source: spectrumSource,
    titleKey: 'exampleSpectrum',
    descriptionKey: 'exampleSpectrumDesc',
    editableKey: 'exampleSpectrumEditable',
    thumbnail: spectrumThumb,
    thumbWidth: cover('spectrum').width,
    thumbHeight: cover('spectrum').height,
    difficulty: 'explore',
  },
]

/** 首页主推的那个案例（`featured`，有且只有一个）。 */
export const FEATURED_EXAMPLE: PlaygroundExample =
  EXAMPLES.find((e) => e.featured) ?? EXAMPLES[0]

export const exampleById = (id: string): PlaygroundExample | undefined =>
  EXAMPLES.find((e) => e.id === id)
