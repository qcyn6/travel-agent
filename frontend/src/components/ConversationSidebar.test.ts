// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ConversationSummary } from '../types'
import ConversationSidebar from './ConversationSidebar.vue'

function conversation(
  id: string,
  status: ConversationSummary['status'],
  mode: ConversationSummary['mode'] = 'general',
): ConversationSummary {
  return {
    id,
    title: mode === 'planning' ? `${id}旅行计划` : id,
    status,
    mode,
    updated_at: new Date().toISOString(),
  }
}

describe('ConversationSidebar', () => {
  it('separates conversations from plans and keeps the expand control available for collapsing', async () => {
    const conversations = [
      ...Array.from({ length: 12 }, (_, index) => conversation(`对话${index + 1}`, 'draft')),
      conversation('进行中1', 'confirmed', 'planning'),
      conversation('方案1', 'completed', 'planning'),
      conversation('方案2', 'completed', 'planning'),
    ]
    const wrapper = mount(ConversationSidebar, {
      props: {
        conversations,
        activeId: '对话1',
        stage: 'chat',
        hasPlan: false,
        mode: 'general',
      },
    })

    expect(wrapper.findAll('.conversation-item')).toHaveLength(10)
    expect(wrapper.get('.history-more').text()).toContain('查看全部')

    await wrapper.get('.history-more').trigger('click')
    expect(wrapper.findAll('.conversation-item')).toHaveLength(12)
    expect(wrapper.get('.history-more').text()).toContain('收起记录')

    await wrapper.get('.history-more').trigger('click')
    expect(wrapper.findAll('.conversation-item')).toHaveLength(10)

    await wrapper.findAll('.history-filter-button')[1].trigger('click')
    expect(wrapper.findAll('.conversation-item')).toHaveLength(3)
    expect(wrapper.text()).toContain('进行中1之旅')
    expect(wrapper.text()).toContain('方案1旅行计划')

    await wrapper.findAll('.history-filter-button')[2].trigger('click')
    expect(wrapper.findAll('.conversation-item')).toHaveLength(2)
    expect(wrapper.text()).toContain('方案1旅行计划')
    expect(wrapper.text()).not.toContain('进行中1之旅')
  })

  it('emits the database mode and opens completed planning sessions in the plan view', async () => {
    const wrapper = mount(ConversationSidebar, {
      props: {
        conversations: [
          conversation('普通1', 'draft'),
          conversation('进行中1', 'confirmed', 'planning'),
          conversation('方案1', 'completed', 'planning'),
        ],
        activeId: '普通1',
        stage: 'chat',
        hasPlan: false,
        mode: 'general',
      },
    })

    await wrapper.get('.conversation-item-main').trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual(['普通1', 'general', 'chat'])

    await wrapper.findAll('.history-filter-button')[1].trigger('click')
    await wrapper.get('.conversation-item-main').trigger('click')
    expect(wrapper.emitted('select')?.[1]).toEqual(['进行中1', 'planning', 'chat'])

    await wrapper.findAll('.conversation-item-main')[1].trigger('click')
    expect(wrapper.emitted('select')?.[2]).toEqual(['方案1', 'planning', 'chat'])

    await wrapper.findAll('.history-filter-button')[2].trigger('click')
    await wrapper.get('.conversation-item-main').trigger('click')
    expect(wrapper.emitted('select')?.[3]).toEqual(['方案1', 'planning', 'plan'])
  })
})
