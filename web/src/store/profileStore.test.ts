/**
 * profile 清单的前端持有者（Session 10）。
 *
 * 盯着三件"错了没有任何界面信号"的事：
 *
 * 1. 后端不在时**规范退回内置**（预检还得说得出话），样式保持空；
 * 2. 一个 200 但形状不对的响应**不许**把清单抹成空的；
 * 3. 乐观并发撞车时把磁盘现值留下来，界面才说得出「已经被改过」。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '@/i18n'
import { useProfileStore } from './profileStore'
import { builtinCatalog } from '@/lib/specBinding'

const BUILTIN_SPECS = builtinCatalog().length
const initial = useProfileStore.getState()

function stub(handler: (url: string, init?: RequestInit) => Response) {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  ) as typeof fetch
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  useProfileStore.setState({ ...initial, styles: [], specs: initial.specs })
})

describe('load()', () => {
  it('拿到清单就用清单', async () => {
    stub((url) =>
      json({
        profiles: url.includes('/style')
          ? [{ id: 's1', kind: 'style', revision: 1, display_name: '我的', data: {} }]
          : [],
      }),
    )
    await useProfileStore.getState().load()
    expect(useProfileStore.getState().styles.map((r) => r.id)).toEqual(['s1'])
    expect(useProfileStore.getState().specs).toEqual([])
  })

  it('后端不在时规范退回内置，样式保持空，且**不当成错误**', async () => {
    stub(() => {
      throw new TypeError('Failed to fetch')
    })
    await useProfileStore.getState().load()
    const s = useProfileStore.getState()
    expect(s.specs).toHaveLength(BUILTIN_SPECS)
    expect(s.styles).toEqual([])
    expect(s.error).toBeNull()
    expect(s.loaded).toBe(true)
  })

  it('200 但没有 profiles：当作没拿到，不许把内置一起抹掉', async () => {
    stub(() => json({ figures_dir: '/figs', panels: [] }))
    await useProfileStore.getState().load()
    expect(useProfileStore.getState().specs).toHaveLength(BUILTIN_SPECS)
  })

  it('后端明确报错时记下 code', async () => {
    stub(() => json({ error: '不认识这种配置类型', code: 'profile_bad_kind' }, 400))
    await useProfileStore.getState().load()
    expect(useProfileStore.getState().error?.code).toBe('profile_bad_kind')
    expect(useProfileStore.getState().specs).toHaveLength(BUILTIN_SPECS)
  })
})

describe('写操作', () => {
  it('乐观并发撞车时留下磁盘现值', async () => {
    useProfileStore.setState({
      styles: [
        {
          id: 's1',
          kind: 'style',
          schema_version: 1,
          revision: 1,
          display_name: '旧的',
          name_key: '',
          version: '',
          created_at: 0,
          updated_at: 0,
          built_in: false,
          read_only: false,
          is_default: false,
          derived_from: '',
          warnings: [],
          data: {},
        },
      ],
    })
    stub(() =>
      json(
        {
          error: '这条配置已被改过',
          code: 'profile_revision_conflict',
          current: { id: 's1', display_name: '别人改成的名字', revision: 7 },
        },
        409,
      ),
    )
    const got = await useProfileStore.getState().save('style', 's1', { element: {} })
    expect(got).toBeNull()
    const s = useProfileStore.getState()
    expect(s.error?.code).toBe('profile_revision_conflict')
    expect(s.conflict?.display_name).toBe('别人改成的名字')
    // 本地那一条**没有被改成别人的**：冲突是让用户去看，不是自动接受
    expect(s.styles[0].display_name).toBe('旧的')
  })

  it('错误文案按界面语言渲染，不透传后端中文原文', async () => {
    // **必须换成英文界面来量**：zh-CN 下"透传原文"与"按 code 翻"给出同一句话，
    // 判据恒等成立。英文界面里泄漏中文正是这条要防的事（审计 P1-02）。
    await i18n.changeLanguage('en-US')
    try {
      stub(() =>
        json({ error: '内置配置不能直接改，请先复制一份', code: 'profile_read_only' }, 409),
      )
      await useProfileStore.getState().create('style', 'X', {})
      const err = useProfileStore.getState().error!
      expect(err.code).toBe('profile_read_only')
      expect(err.message).toContain('Duplicate')
      expect(err.message).not.toMatch(/[一-鿿]/)
    } finally {
      await i18n.changeLanguage('zh-CN')
    }
  })
})
