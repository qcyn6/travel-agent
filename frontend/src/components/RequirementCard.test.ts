// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import RequirementCard from './RequirementCard.vue'


describe('RequirementCard', () => {
  it('renders the structured travel profile', () => {
    const wrapper = mount(RequirementCard, {
      props: {
        ready: true,
        requirement: {
          province: '云南省',
          cities: ['大理市', '丽江市'],
          days: 5,
          interests: ['自然', '古城'],
          preference_text: '希望节奏轻松',
          travel_scope: 'multi_city',
          start_date: null,
          transport_mode: 'auto',
        },
      },
    })

    expect(wrapper.text()).toContain('大理市、丽江市')
    expect(wrapper.text()).toContain('5 天')
    expect(wrapper.text()).toContain('同省多城市')
    expect(wrapper.text()).toContain('查看并确认旅行需求')
  })
})
