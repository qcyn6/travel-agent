// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import TravelPlanView from './TravelPlanView.vue'

describe('TravelPlanView', () => {
  it('shows executable schedule, totals and evaluator checks', () => {
    Object.defineProperty(window, 'print', { value: vi.fn(), configurable: true })
    const wrapper = mount(TravelPlanView, {
      props: {
        result: {
          requirement: {
            province: '浙江省', cities: ['杭州市'], days: 1, interests: ['自然'],
            preference_text: '轻松', travel_scope: 'single_city', start_date: '2026-09-20',
            transport_mode: 'auto',
          },
          plan: {
            province: '浙江省', city_stays: [{ city: '杭州市', days: 1 }], city_plans: [],
            replan_count: 1, replan_limit_reached: false,
            notices: [{ code: 'opening_hours_unknown', level: 'info', day: 1,
              message: '第 1 天有 1 个景点的开放时间待核验' }],
            evaluation: { passed: true, replan_required: false, issues: [] },
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
                environment: 'outdoor', estimated_visit_minutes: 240,
                duration_source: 'category_fallback', opening_hours: { status: 'unknown', periods: [],
                  raw_text: null, applicability_text: null, confidence: 'unknown', source_url: null,
                  checked_at: null }, start_time: '09:00', end_time: '13:00' }],
              routes: [],
            }],
          },
        },
      },
    })

    expect(wrapper.text()).toContain('09:00—15:50')
    expect(wrapper.text()).toContain('交通 0 分钟')
    expect(wrapper.text()).toContain('每日安排合理')
    expect(wrapper.text()).toContain('部分开放时间待出发前核验')
    expect(wrapper.text()).toContain('已自动完成 1 次局部调整')
    expect(wrapper.text()).toContain('数据说明')
  })
})
