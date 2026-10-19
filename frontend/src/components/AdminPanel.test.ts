// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { adminApi, ApiError } from '../api'
import AdminPanel from './AdminPanel.vue'

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string, public status: number) { super(message) }
  },
  adminApi: {
    users: vi.fn(), guides: vi.fn(), guide: vi.fn(), guideIndexStatus: vi.fn(),
    updateGuide: vi.fn(), createGuide: vi.fn(), deleteGuide: vi.fn(),
  },
}))

const guide = { filename: 'guide.md', title: '旅行指南', content: '# 原文', revision: 'a'.repeat(64) }
const summary = { filename: guide.filename, title: guide.title, size: 10, updated_at: null }

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(adminApi.users).mockResolvedValue([])
  vi.mocked(adminApi.guides).mockResolvedValue([summary])
  vi.mocked(adminApi.guide).mockResolvedValue({ ...guide })
  vi.mocked(adminApi.guideIndexStatus).mockResolvedValue({
    source_files: 1, source_documents: 1, indexed_documents: 1, stale_documents: 0, in_sync: true,
  })
})

describe('AdminPanel guide editor', () => {
  it('ignores an earlier document request after entering create mode', async () => {
    const wrapper = mount(AdminPanel)
    await flushPromises()
    let finish!: (value: typeof guide) => void
    vi.mocked(adminApi.guide).mockReturnValue(new Promise((resolve) => { finish = resolve }))
    await wrapper.get('.guide-list button').trigger('click')
    const create = wrapper.findAll('.knowledge-actions button').find((button) => button.text() === '新增指南')!
    await create.trigger('click')
    finish({ ...guide, content: '# 延迟的旧文档' })
    await flushPromises()
    expect(wrapper.find('.guide-filename').exists()).toBe(true)
    expect((wrapper.get('.guide-editor textarea').element as HTMLTextAreaElement).value).toBe('# 新知识文档\n\n')
    expect(wrapper.find('.guide-list button.active').exists()).toBe(false)
    wrapper.unmount()
  })

  it('preserves unsaved text and reports a stale-version conflict', async () => {
    const wrapper = mount(AdminPanel)
    await flushPromises()
    vi.mocked(adminApi.updateGuide).mockRejectedValue(new ApiError('文档已被其他操作修改，请保留当前文字', 409))
    await wrapper.get('.guide-editor textarea').setValue('# 未保存的内容')
    await wrapper.get('.guide-editor .admin-action').trigger('click')
    await flushPromises()
    expect(adminApi.updateGuide).toHaveBeenCalledWith('guide.md', '# 未保存的内容', guide.revision)
    expect((wrapper.get('.guide-editor textarea').element as HTMLTextAreaElement).value).toBe('# 未保存的内容')
    expect(wrapper.get('.auth-error').text()).toContain('文档已被其他操作修改')
    wrapper.unmount()
  })

  it('locks the editor while a save is pending and adopts the returned version', async () => {
    const wrapper = mount(AdminPanel)
    await flushPromises()
    let finish!: (value: typeof guide) => void
    vi.mocked(adminApi.updateGuide).mockReturnValue(new Promise((resolve) => { finish = resolve }))
    await wrapper.get('.guide-editor textarea').setValue('# 更新')
    await wrapper.get('.guide-editor .admin-action').trigger('click')
    expect((wrapper.get('.guide-editor textarea').element as HTMLTextAreaElement).disabled).toBe(true)
    expect((wrapper.get('.guide-list button').element as HTMLButtonElement).disabled).toBe(true)
    finish({ ...guide, content: '# 更新', revision: 'b'.repeat(64) })
    await flushPromises()
    vi.mocked(adminApi.updateGuide).mockResolvedValue({ ...guide, revision: 'c'.repeat(64) })
    await wrapper.get('.guide-editor .admin-action').trigger('click')
    await flushPromises()
    expect(vi.mocked(adminApi.updateGuide).mock.calls[1][2]).toBe('b'.repeat(64))
    wrapper.unmount()
  })
})
