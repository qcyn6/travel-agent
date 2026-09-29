// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'


describe('ChatPanel', () => {
  it('starts planning when the conversational confirmation action is clicked', async () => {
    const wrapper = mount(ChatPanel, {
      props: {
        messages: [{ role: 'assistant', content: '我帮你把这次旅行需求整理好了，请确认一下：' }],
        suggestions: ['确认生成计划'],
        sending: false,
        mode: 'planning',
      },
    })

    await wrapper.get('.suggestions button').trigger('click')

    expect(wrapper.emitted('confirm')).toHaveLength(1)
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('accepts a natural conversational confirmation typed by the user', async () => {
    const wrapper = mount(ChatPanel, {
      props: {
        messages: [{ role: 'assistant', content: '如果这样没问题，就开始生成计划吧。' }],
        suggestions: ['确认生成计划'],
        sending: false,
        mode: 'planning',
      },
    })

    await wrapper.get('textarea').setValue('好的，开始吧')
    await wrapper.get('textarea').trigger('keydown', { key: 'Enter' })

    expect(wrapper.emitted('confirm')).toHaveLength(1)
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('keeps planning information collection inside the conversation', () => {
    const wrapper = mount(ChatPanel, {
      props: {
        messages: [{ role: 'assistant', content: '先从目的地开始吧。' }],
        suggestions: ['北京'],
        sending: false,
        mode: 'planning',
      },
    })

    expect(wrapper.find('.conversation-context').exists()).toBe(false)
    expect(wrapper.find('input, select, fieldset').exists()).toBe(false)
  })

  it('does not render a mode switch in the composer', () => {
    const wrapper = mount(ChatPanel, {
      props: {
        messages: [],
        suggestions: [],
        sending: false,
        mode: 'general',
      },
    })

    expect(wrapper.find('.conversation-mode').exists()).toBe(false)
    expect(wrapper.find('.conversation-mode-tabs').exists()).toBe(false)
    expect(wrapper.get('textarea').attributes('placeholder')).toContain('杭州明天天气')
    expect(wrapper.get('textarea').attributes('placeholder')).toContain('怎么走')
  })

  it('keeps ordinary mode free of planning profile and confirmation actions', async () => {
    const wrapper = mount(ChatPanel, {
      props: {
        messages: [{ role: 'assistant', content: '可以聊聊目的地。' }],
        suggestions: ['你能做什么？', '介绍一个目的地'],
        sending: false,
        mode: 'general',
      },
    })

    expect(wrapper.find('.conversation-context').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('确认生成计划')

    await wrapper.get('textarea').setValue('确认生成计划')
    await wrapper.get('textarea').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('confirm')).toBeUndefined()
    expect(wrapper.emitted('send')).toEqual([['确认生成计划']])
  })

  it('scrolls again when a delayed weather card is attached to the reply', async () => {
    const wrapper = mount(ChatPanel, {
      props: {
        messages: [{ role: 'assistant', content: '福州未来三天天气如下：' }],
        suggestions: [],
        sending: false,
        mode: 'general',
      },
    })
    const messageList = wrapper.get('.message-list').element as HTMLElement
    const scrollTo = vi.fn()
    messageList.scrollTo = scrollTo

    await wrapper.setProps({
      messages: [{
        role: 'assistant',
        content: '福州未来三天天气如下：',
        card: {
          type: 'weather',
          weather: [{
            city: '福州市',
            date: '2026-09-23',
            condition: '多云',
            temperature_min: 25,
            temperature_max: 32,
            precipitation_probability: 0.03,
            wind_level: '2',
            status: 'available',
            source: 'qweather',
          }],
        },
      }],
    })
    await nextTick()
    await nextTick()

    expect(wrapper.find('.weather-calendar-card').exists()).toBe(true)
    expect(scrollTo).toHaveBeenCalled()
  })
})
