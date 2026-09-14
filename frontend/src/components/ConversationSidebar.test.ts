// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ConversationSummary } from '../types'
import ConversationSidebar from './ConversationSidebar.vue'

function conversation(id: string, status: ConversationSummary['status']): ConversationSummary {
  return {
    id,
    title: `${id}旅行计划`,
    status,
    updated_at: new Date().toISOString(),
  }
}

describe('ConversationSidebar', () => {
  it('separates conversations from plans and keeps the expand control available for collapsing', async () => {
    const conversations = [
      ...Array.from({ length: 12 }, (_, index) => conversation(`对话${index + 1}`, 'draft')),
      conversation('方案1', 'completed'),
      conversation('方案2', 'completed'),
    ]
    const wrapper = mount(ConversationSidebar, {
      props: {
        conversations,
        activeId: '对话1',
        stage: 'chat',
        hasPlan: false,
      },
    })

    expect(wrapper.findAll('.conversation-item')).toHaveLength(10)
    expect(wrapper.get('.history-more').text()).toContain('查看全部')

    await wrapper.get('.history-more').trigger('click')
    expect(wrapper.findAll('.conversation-item')).toHaveLength(14)
    expect(wrapper.get('.history-more').text()).toContain('收起记录')

    await wrapper.get('.history-more').trigger('click')
    expect(wrapper.findAll('.conversation-item')).toHaveLength(10)

    await wrapper.findAll('.history-filter-button')[1].trigger('click')
    expect(wrapper.findAll('.conversation-item')).toHaveLength(2)
    expect(wrapper.text()).toContain('方案1旅行计划')
    expect(wrapper.text()).not.toContain('对话1旅行计划')
  })

  it('opens a completed session as chat from conversation history and as a plan from plan history', async () => {
    const wrapper = mount(ConversationSidebar, {
      props: {
        conversations: [conversation('方案1', 'completed')],
        activeId: 'other',
        stage: 'chat',
        hasPlan: false,
      },
    })

    await wrapper.get('.conversation-item-main').trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual(['方案1', 'chat'])

    await wrapper.findAll('.history-filter-button')[1].trigger('click')
    await wrapper.get('.conversation-item-main').trigger('click')
    expect(wrapper.emitted('select')?.[1]).toEqual(['方案1', 'plan'])
  })
})
