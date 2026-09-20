// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import TravelPlanView from './TravelPlanView.vue'

describe('TravelPlanView', () => {
  it('shows executable schedule, totals and evaluator checks', async () => {
    Object.defineProperty(window, 'print', { value: vi.fn(), configurable: true })
    const wrapper = mount(TravelPlanView, {
      props: {
        result: {
          requirement: {
            province: '浙江省', cities: ['杭州市'], province_preference_confirmed: true, city_preference_confirmed: true, days: 1, interests: ['自然'],
            preference_text: '轻松', travel_scope: 'single_city', start_date: '2026-09-20',
            daily_start_time: '08:00', daily_end_time: '21:00',
            transport_mode: 'auto',
          },
          plan: {
            province: '浙江省', city_stays: [{ city: '杭州市', days: 1 }], city_plans: [],
            notices: [],
            evaluation: { passed: true, issues: [] },
            itinerary: [{
              day: 1, city: '杭州市', city_adcode: '330100', city_day: 1,
              date: '2026-09-20', description: '围绕西湖安排轻松的一日游览行程。',
              weather: { city: '杭州市', date: '2026-09-20', condition: '晴', temperature_min: 22,
                temperature_max: 28, precipitation_probability: 0.1, wind_level: '2',
                status: 'available', source: 'qweather' },
              schedule: { start_time: '09:00', end_time: '15:50', visit_minutes: 240,
                route_minutes: 20, meal_minutes: 90, buffer_minutes: 60, total_minutes: 410,
                status: 'estimated' },
              attractions: [{ poi_id: 'A1', name: '西湖', city: '杭州市', category: '风景名胜',
                description: '自然与人文景观。', longitude: 120.1, latitude: 30.2,
                visit_duration_minutes: 240,
                start_time: '09:00', end_time: '13:00' }],
              routes: [],
            }],
          },
        },
      },
    })

    expect(wrapper.text()).toContain('09:00—15:50')
    expect(wrapper.text()).toContain('交通 0 分钟')
    expect(wrapper.text()).toContain('兴趣匹配符合需求')
    expect(wrapper.text()).toContain('每日安排合理')
    expect(wrapper.text()).toContain('游玩时长：240 分钟')
    expect(wrapper.text()).toContain('自然漫步')
    expect(wrapper.get('.toolbar-actions').text()).not.toContain('重新生成规划')
    expect(wrapper.get('.day-focus').text()).toContain('今日重点')
    expect(wrapper.get('.day-overview-label').text()).toBe('行程概览')
    expect(wrapper.get('.attraction-time').text()).toContain('09:00')
    expect(wrapper.text()).not.toContain('参考时长')
    expect(wrapper.text()).toContain('第 1 天围绕自然安排了 1 处景点')
    expect(wrapper.text()).not.toContain('开放时间')
    expect(wrapper.text()).not.toContain('已核验的景点知识')
  })
})
