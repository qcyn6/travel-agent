// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PlanningView from './PlanningView.vue'

describe('PlanningView', () => {
  it('shows a terminal failure state and provides retry', async () => {
    const wrapper = mount(PlanningView, {
      props: {
        steps: [
          { step: 'requirement', label: '已确认旅行需求', status: 'completed' },
          { step: 'poi_search:0:宝鸡市', label: '正在准备宝鸡市真实景点', status: 'failed' },
        ],
        error: '宝鸡市仅准备好2条景点知识，目标为3条',
        elapsedSeconds: 42,
        requirement: {
          province: '陕西省',
          cities: ['宝鸡市'],
          province_preference_confirmed: false,
          city_preference_confirmed: true,
          days: 3,
          interests: ['历史文化'],
          preference_text: null,
          travel_scope: 'single_city',
          start_date: null,
          daily_start_time: null,
          daily_end_time: null,
          transport_mode: 'auto',
        },
      },
    })

    expect(wrapper.get('h1').text()).toBe('旅程准备遇到问题')
    expect(wrapper.find('.compass-loader').exists()).toBe(false)
    expect(wrapper.get('.progress-list li.failed').text()).toContain('需要重试')
    expect(wrapper.get('.progress-list li.failed').text()).toContain('宝鸡市的景点资料需要重试')
    expect(wrapper.text()).not.toContain('真实景点')
    expect(wrapper.get('.planning-profile').text()).toContain('宝鸡市')
    expect(wrapper.get('.planning-meta').text()).toContain('已用时 42 秒')
    expect(wrapper.get('.inline-error').text()).toContain('还需要 1 个')
    expect(wrapper.get('.error-details').text()).toContain('查看失败原因')
    expect(wrapper.find('.failure-detail').exists()).toBe(false)

    await wrapper.get('.error-details').trigger('click')
    expect(wrapper.get('.failure-detail').text()).toContain('宝鸡市仅准备好2条景点知识')
    expect(wrapper.get('.error-details').text()).toContain('收起失败原因')

    await wrapper.get('button.retry').trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)
  })

  it('does not leave the startup row looking active after an early error', () => {
    const wrapper = mount(PlanningView, {
      props: {
        steps: [],
        error: '旅游方案生成失败，请稍后重试',
      },
    })

    expect(wrapper.get('.progress-list li.failed').text()).toContain('规划工作流未能启动')
    expect(wrapper.get('.progress-list li.failed').text()).toContain('需要重试')
  })
})
