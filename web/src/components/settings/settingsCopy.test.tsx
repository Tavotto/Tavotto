/**
 * 设置页的「说明文字专项补查」（UI 审计 T38 / T39 / T40 / T43 + 18 条说明弹层）。
 *
 * 这一组钉的是**措辞与控件的对应关系**，不是像素：
 *   1. 常规页不再用问号解释自己（`settingsDisclosure.test.tsx` 判「一个都没有」，
 *      这里判剩下的那几行说的是动作与结果）；
 *   2. 侧栏开关用结果式名称、标签指着开关、限制只在**真的受限时**出现；
 *   3. 路径可核实：默认末级目录、能展开成完整路径、默认值由控件表达；
 *   4. 「允许写回原始文件」这个反转必须与**真实保护**同步——设置页的开关与
 *      属性栏那个会碰磁盘的按钮读同一个字段（这条是 T40 的验收原文：
 *      「不能只改文案」）；
 *   5. 导出偏好与导出对话框是同一批名字、同一个单位。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { t } from '@/i18n'
import { SettingsDialog } from '@/components/SettingsDialog'
import { UpdateSourceButton } from '@/components/inspector/UpdateSourceButton'
import { lastSegment } from '@/components/settings/PathValue'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { useOnboardingStore } from '@/store/onboardingStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

Element.prototype.scrollIntoView ??= function scrollIntoView() {}
Element.prototype.hasPointerCapture ??= () => false
Element.prototype.releasePointerCapture ??= () => {}
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as never

const st = (key: string, values?: Record<string, unknown>) =>
  t(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })
const ex = (key: string, values?: Record<string, unknown>) =>
  t(`export.${key}`, { ns: 'dialogs', ...(values ?? {}) })

const FIGURES = '/Users/me/Library/Application Support/Tavotto/tutorial/v1-a42973/Tutorial'
const EXPORTS = '/Users/me/Library/Application Support/Tavotto/tutorial/v1-a42973/exports'
const BACKUPS = '/Users/me/Library/Application Support/Tavotto/cache/original_backups'

let root: Root
let host: HTMLDivElement

async function render(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(node)
  })
  await act(async () => {})
}

async function open(section: string) {
  useUiStore.setState({ settingsOpen: true, settingsSection: section })
  await render(<SettingsDialog />)
}

const body = () => document.querySelector('[role="dialog"]') as HTMLElement
const bodyText = () => body()?.textContent ?? ''
const buttons = () => [...document.querySelectorAll('button')] as HTMLButtonElement[]
const byText = (s: string) => buttons().find((b) => b.textContent?.trim() === s)
const byAria = (name: string) => buttons().find((b) => b.getAttribute('aria-label') === name)

function project(patch: Record<string, unknown> = {}) {
  useProjectStore.setState({
    project: {
      figures_dir: FIGURES,
      scripts: 2,
      export_dir: EXPORTS,
      backup_dir: BACKUPS,
      settings: { allow_write_back: true },
      ...patch,
    } as never,
  })
}

beforeEach(() => {
  document.body.innerHTML = ''
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            checks: [],
            settings: { allow_write_back: true },
            export_dir: EXPORTS,
            backup_dir: BACKUPS,
          }),
      } as Response),
    ),
  )
  project()
  useUiStore.setState({ layout: 'wide', leftPinned: false, rightPinned: true })
  useOnboardingStore.setState({ status: 'not_started', tutorialProjectId: null } as never)
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  useUiStore.setState({ settingsOpen: false, settingsSection: null })
  localStorage.removeItem('tavotto.export.defaults')
  document.body.innerHTML = ''
})

/* --------------------------------- T38 常规 -------------------------------- */

describe('T38 常规：说明改成动作与结果', () => {
  it('快捷键那一行直接给出键位，不用一段话介绍另一个帮助入口', async () => {
    await open('general')
    expect(bodyText()).toContain(st('shortcuts.label'))
    const kbd = body().querySelector('kbd')
    expect(kbd?.textContent?.trim()).toBe('?')
  })

  it('有教程项目时「重置」单独一行，标签底下写清重置的是哪个对象', async () => {
    useOnboardingStore.setState({ status: 'completed', tutorialProjectId: 'p1' } as never)
    await open('general')
    // 进入教程与重置是两件事，各自一行：改动前它们挤在同一行，
    // 「再看一遍教程」与「重置教程项目」的区别得点开问号才知道
    expect(byText(st('tutorial.restart'))).toBeTruthy()
    expect(bodyText()).toContain(st('tutorial.reset'))
    expect(bodyText()).toContain(st('tutorial.resetScope'))
  })

  it('没有教程项目时不出现重置行', async () => {
    await open('general')
    expect(bodyText()).not.toContain(st('tutorial.resetScope'))
  })

  it('提示按钮说的是点了会怎样', async () => {
    await open('general')
    expect(byText(st('tutorial.resetHints'))?.textContent).toBe('重新显示操作提示')
  })
})

/* --------------------------------- T39 界面 -------------------------------- */

describe('T39 界面：结果式名称 + 条件状态', () => {
  it('侧栏开关用结果式名称，标签是真的 label 且指着那个开关', async () => {
    await open('interface')
    const label = [...body().querySelectorAll('label')].find(
      (l) => l.textContent?.trim() === st('sidebars.leftPinned'),
    ) as HTMLLabelElement
    expect(label).toBeTruthy()
    const control = document.getElementById(label.htmlFor)
    expect(control?.getAttribute('role')).toBe('switch')
    // 可达名 = 看得见的那行标签：开关自己不再另挂一个 aria-label 把它盖掉
    expect(control?.getAttribute('aria-label')).toBeNull()
  })

  it('点标签文字等于点开关', async () => {
    await open('interface')
    const before = useUiStore.getState().leftPinned
    const label = [...body().querySelectorAll('label')].find(
      (l) => l.textContent?.trim() === st('sidebars.leftPinned'),
    ) as HTMLLabelElement
    await act(async () => {
      label.click()
    })
    expect(useUiStore.getState().leftPinned).toBe(!before)
  })

  it('宽窗口下不写任何窗口宽度的限制', async () => {
    useUiStore.setState({ layout: 'wide' })
    await open('interface')
    expect(bodyText()).not.toContain(st('sidebars.pinLimitedMedium'))
    expect(bodyText()).not.toContain(st('sidebars.pinLimitedNarrow'))
    // 像素断点整个从界面上撤掉了（旧文案写着 1440，而真实断点是 1280）
    expect(bodyText()).not.toContain('1440')
    expect(bodyText()).not.toContain('1280')
  })

  it('互斥断点下就近说明「只能固定一侧」', async () => {
    useUiStore.setState({ layout: 'medium' })
    await open('interface')
    expect(bodyText()).toContain(st('sidebars.pinLimitedMedium'))
  })

  it('窄窗口下说明常驻不生效', async () => {
    useUiStore.setState({ layout: 'narrow' })
    await open('interface')
    expect(bodyText()).toContain(st('sidebars.pinLimitedNarrow'))
  })

  it('联动开关配前后示意，而且随开关换说法', async () => {
    useUiStore.setState({ dragAxesWithCompanions: true })
    await open('interface')
    const svg = body().querySelector('svg[role="img"]')
    expect(svg?.getAttribute('aria-label')).toBe(st('canvas.diagramOn'))
    await act(async () => {
      byAria(st('helpAbout', { label: st('canvas.dragCompanions') }))
      document.getElementById('setting-drag-companions')?.click()
    })
    expect(body().querySelector('svg[role="img"]')?.getAttribute('aria-label')).toBe(
      st('canvas.diagramOff'),
    )
  })

  it('「画布设置」直接到右栏的画布页', async () => {
    useUiStore.setState({ rightTab: 'properties' })
    await open('interface')
    await act(async () => {
      byText(st('canvas.openCanvasSettings'))!.click()
    })
    expect(useUiStore.getState().rightTab).toBe('canvas')
  })
})

/* --------------------------------- T40 项目 -------------------------------- */

describe('T40 项目：路径可核实、默认值由控件表达', () => {
  it('末级目录的判据认两种分隔符，结尾的分隔符不算一级', () => {
    expect(lastSegment('/a/b/Tutorial')).toBe('Tutorial')
    expect(lastSegment('/a/b/Tutorial/')).toBe('Tutorial')
    expect(lastSegment('C:\\Users\\me\\figs')).toBe('figs')
    expect(lastSegment('exports')).toBe('exports')
  })

  it('默认只显示末级目录，展开之后才是完整路径', async () => {
    await open('project')
    expect(bodyText()).toContain(lastSegment(FIGURES))
    expect(bodyText()).not.toContain(FIGURES)
    const toggle = byAria(st('project.showFullPath', { name: st('project.current') }))!
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    await act(async () => {
      toggle.click()
    })
    expect(bodyText()).toContain(FIGURES)
  })

  it('目录留空时，这一刻真正在用的位置就写在输入框下面', async () => {
    await open('project')
    expect(bodyText()).toContain(st('project.effectivePath'))
    expect(bodyText()).toContain(lastSegment(EXPORTS))
    // 没设过就没有「恢复默认」——那个按钮只在有东西可恢复时才有意义
    expect(byText(st('project.useDefault'))).toBeUndefined()
  })

  it('设过之后出现「恢复默认」，点了清空并回存', async () => {
    project({ settings: { allow_write_back: true, export_dir: '/tmp/mine' } })
    await open('project')
    const reset = byText(st('project.useDefault'))!
    expect(reset).toBeTruthy()
    await act(async () => {
      reset.click()
    })
    const calls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
    const patch = calls.find(([url]) => String(url).includes('/api/project/settings'))!
    expect(JSON.parse(String((patch[1] as RequestInit).body))).toEqual({ export_dir: '' })
  })

  it('浏览器模式不渲染系统文件夹选择——点了什么都不会发生的按钮不摆出来', async () => {
    await open('project')
    expect(byText(st('project.chooseFolder'))).toBeUndefined()
  })

  it('源脚本那一行报结果，不讲登记规则', async () => {
    await open('project')
    expect(bodyText()).toContain(st('project.scripts'))
    expect(bodyText()).toContain(st('project.scriptCount', { count: 2 }))
    expect(bodyText()).toContain(st('project.registry'))
  })
})

describe('T40 项目：写回权限与真实保护同步', () => {
  const panel = (overrides: number) =>
    ({
      id: 'o1',
      type: 'panel',
      fileId: 'fig1.pdf',
      fileKind: 'static',
      overrides: Array.from({ length: overrides }, (_, i) => ({ path: `p${i}` })),
    }) as never

  it('关掉之后属性栏的写回按钮真的停用，并就近说明原因', async () => {
    project({ settings: { allow_write_back: false } })
    await render(<UpdateSourceButton panel={panel(1)} />)
    const btn = byText(t('writeBack.buttonLabel', { ns: 'inspector' }))!
    expect(btn.disabled).toBe(true)
    expect(btn.title).toBe(t('writeBack.readOnlyTitle', { ns: 'inspector' }))
  })

  it('打开之后同一个按钮可用（说明上一条量的是这个字段，不是「按钮一直是灰的」）', async () => {
    project({ settings: { allow_write_back: true } })
    await render(<UpdateSourceButton panel={panel(1)} />)
    expect(byText(t('writeBack.buttonLabel', { ns: 'inspector' }))!.disabled).toBe(false)
  })

  it('设置页的开关读的就是那个字段：关着时 aria-checked=false', async () => {
    project({ settings: { allow_write_back: false } })
    await open('project')
    const toggle = document.getElementById('setting-allow-write-back')!
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    expect(bodyText()).toContain(st('project.writeBackOffHint'))
  })

  it('打开开关发出的是 allow_write_back: true，不是被取反的旧字段', async () => {
    project({ settings: { allow_write_back: false } })
    await open('project')
    await act(async () => {
      document.getElementById('setting-allow-write-back')!.click()
    })
    const calls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
    const patch = calls.find(([url]) => String(url).includes('/api/project/settings'))!
    expect(JSON.parse(String((patch[1] as RequestInit).body))).toEqual({ allow_write_back: true })
  })
})

/* -------------------------------- T43 导出 --------------------------------- */

describe('T43 导出偏好：与导出对话框同名同单位', () => {
  const defaults = (patch: Record<string, unknown>) =>
    localStorage.setItem(
      'tavotto.export.defaults',
      JSON.stringify({ dpi: '600', formats: ['pdf', 'png'], withProof: false, ...patch }),
    )

  it('分辨率与样式检查报告用的是导出对话框那两个名字，没有第二套叫法', async () => {
    defaults({})
    await open('export')
    expect(bodyText()).toContain(ex('ppiLabel'))
    expect(bodyText()).toContain(ex('reportToggle'))
    // 审计记的原症状：设置里叫「Proof 留档」，导出对话框里叫「样式检查报告」
    expect(bodyText()).not.toContain('Proof')
    expect(bodyText()).not.toContain('DPI')
  })

  it('单位是 ppi', async () => {
    defaults({ dpi: '600' })
    await open('export')
    expect(bodyText()).toContain('600 ppi')
    expect(bodyText()).not.toContain('600 dpi')
  })

  it('格式旁标注类型，用词与导出对话框逐字相同', async () => {
    defaults({})
    await open('export')
    expect(bodyText()).toContain(ex('pdfHint'))
    expect(bodyText()).toContain(ex('pngHint'))
  })

  it('只选了矢量格式时分辨率停用，并就近说明为什么', async () => {
    defaults({ formats: ['pdf'] })
    await open('export')
    expect(bodyText()).toContain(st('export.ppiNotForVector'))
    expect(byAria(ex('ppiSelectLabel'))!.hasAttribute('disabled')).toBe(true)
  })

  it('选上位图之后分辨率就可用了（说明上一条量的是格式，不是恒真）', async () => {
    defaults({ formats: ['pdf', 'png'] })
    await open('export')
    expect(bodyText()).not.toContain(st('export.ppiNotForVector'))
    expect(byAria(ex('ppiSelectLabel'))!.hasAttribute('disabled')).toBe(false)
  })

  it('报告的短说明不承诺「证明图没变过」', async () => {
    defaults({})
    await open('export')
    expect(bodyText()).toContain(st('export.reportScope'))
    expect(bodyText()).not.toContain('证明图没变过')
  })
})
