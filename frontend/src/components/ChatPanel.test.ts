// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ChatPanel from './ChatPanel.vue'


describe('ChatPanel', () => {
  it('opens confirmation when the complete-requirement action is clicked', async () => {
    const wrapper = mount(ChatPanel, {
      props: {
        messages: [{ role: 'assistant', content: '我帮你把这次旅行需求整理好了，请确认一下：' }],
        suggestions: ['确认需求并生成计划', '我还想补充'],
        sending: false,
      },
    })

    await wrapper.get('.suggestions button').trigger('click')

    expect(wrapper.emitted('confirm')).toHaveLength(1)
    expect(wrapper.emitted('send')).toBeUndefined()
  })
})
