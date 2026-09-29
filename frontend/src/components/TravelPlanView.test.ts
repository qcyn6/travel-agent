// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { conversationApi } from '../api'
import TravelPlanView from './TravelPlanView.vue'

afterEach(() => vi.restoreAllMocks())

describe('TravelPlanView', () => {
  it('calculates the estimate percentage from all visible map legs, including the end hotel leg', () => {
    const wrapper = mount(TravelPlanView, {
      props: {
        result: {
          requirement: {
            province: '云南省', cities: ['昆明市'], province_preference_confirmed: true,
            city_preference_confirmed: true, days: 1, interests: [], preference_text: null,
            travel_scope: 'single_city', start_date: '2026-09-20', daily_start_time: '08:00',
            daily_end_time: '21:00', transport_mode: 'auto',
          },
          plan: {
            province: '云南省', city_stays: [{ city: '昆明市', days: 1 }], city_plans: [], notices: [],
            // Saved metrics may be stale; the displayed percentage must match the map.
            evaluation: {
              passed: true, issues: [],
              daily_metrics: [{ day: 1, attraction_count: 3, route_minutes: 60, route_segments: 3, estimated_route_ratio: 1 / 3 }],
            },
            itinerary: [{
              day: 1, city: '昆明市', city_adcode: '530100', city_day: 1,
              date: '2026-09-20', description: '安排三个景点。',
              schedule: {
                start_time: '08:00', end_time: '17:00', visit_minutes: 180,
                route_minutes: 60, meal_minutes: 40, buffer_minutes: 30,
                total_minutes: 310, status: 'complete',
              },
              start_hotel: { poi_id: 'H0', name: '出发酒店', city: '昆明市' },
              start_hotel_route: {
                origin_poi_id: 'H0', origin_name: '出发酒店', destination_poi_id: 'A1',
                destination_name: '景点一', duration_min: 10, mode: 'driving', source: 'amap_mcp', estimated: false,
              },
              attractions: [1, 2, 3].map((index) => ({
                poi_id: `A${index}`, name: `景点${index === 1 ? '一' : index === 2 ? '二' : '三'}`,
                city: '昆明市', visit_duration_minutes: 60,
              })),
              routes: [1, 2].map((index) => ({
                origin_poi_id: `A${index}`, origin_name: `景点${index === 1 ? '一' : '二'}`,
                destination_poi_id: `A${index + 1}`, destination_name: `景点${index === 1 ? '二' : '三'}`,
                duration_min: 20, mode: 'driving', source: 'amap_mcp', estimated: false,
              })),
              hotel: { poi_id: 'H1', name: '当晚酒店', city: '昆明市' },
              hotel_route: {
                origin_poi_id: 'A3', origin_name: '景点三', destination_poi_id: 'H1',
                destination_name: '当晚酒店', duration_min: 70, mode: 'driving', source: 'haversine', estimated: true,
              },
            }],
          },
        } as any,
      },
    })

    expect(wrapper.text()).toContain('估算路线 25%')
    expect(wrapper.text()).not.toContain('估算路线 33%')
  })

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
            evaluation: {
              passed: true,
              issues: [
                { type: 'preference', severity: 'medium', day: 1, message: 'legacy-interest-check', suggestion: null },
                { type: 'schedule_density', severity: 'high', day: 1, message: 'legacy-density-check', suggestion: null },
              ],
              daily_metrics: [{ day: 1, attraction_count: 1, route_minutes: 42, route_segments: 1, estimated_route_ratio: 1 }],
            },
            itinerary: [{
              day: 1, city: '杭州市', city_adcode: '330100', city_day: 1,
              date: '2026-09-20', description: '围绕西湖安排轻松的一日游览行程。',
              weather: { city: '杭州市', date: '2026-09-20', condition: '晴', temperature_min: 22,
                temperature_max: 28, precipitation_probability: 0.1, wind_level: '2',
                travel_advice: '晴天适合户外游览，记得防晒补水。',
                status: 'available', source: 'qweather' },
              schedule: { start_time: '09:00', end_time: '15:50', visit_minutes: 240,
                route_minutes: 42, meal_minutes: 90, buffer_minutes: 60, total_minutes: 432,
                status: 'estimated' },
              start_hotel: { poi_id: 'H0', name: '昆明古滇度假酒店', city: '昆明市', longitude: null, latitude: null, source: 'amap' },
              start_hotel_route: {
                origin_poi_id: 'H0', origin_name: '昆明古滇度假酒店',
                destination_poi_id: 'A1', destination_name: '斗南七彩花海',
                distance_m: 18211, duration_min: 42, mode: 'driving', source: 'haversine', estimated: true,
              },
              attractions: [{ poi_id: 'A1', name: '西湖', city: '杭州市', category: '风景名胜',
                description: '自然与人文景观。', longitude: 120.1, latitude: 30.2,
                image_url: 'https://example.com/west-lake.jpg',
                visit_duration_minutes: 240,
                start_time: '09:00', end_time: '13:00' }],
              routes: [],
            }],
          },
        },
      },
    })

    expect(wrapper.text()).toContain('09:00—15:50')
    expect(wrapper.text()).toContain('交通 42 分钟')
    expect(wrapper.get('.hotel-transfer-meta').text()).toContain('约 42 分钟')
    expect(wrapper.get('.hotel-transfer-meta').text()).toContain('估算路线')
    expect(wrapper.get('.hotel-transfer-meta').text()).not.toContain('真实路线')
    expect(wrapper.text()).toContain('当前日期的路线、时间窗口和已知营业时间未发现明显冲突')
    expect(wrapper.get('.evaluation-card').text()).not.toContain('天气信息已获取')
    expect(wrapper.text()).toContain('AI 出行建议：晴天适合户外游览，记得防晒补水。')
    expect(wrapper.text()).not.toContain('兴趣匹配符合需求')
    expect(wrapper.text()).not.toContain('兴趣覆盖')
    expect(wrapper.text()).not.toContain('每日安排合理')
    expect(wrapper.text()).not.toContain('legacy-interest-check')
    expect(wrapper.text()).not.toContain('legacy-density-check')
    expect(wrapper.text()).toContain('游玩时长：240 分钟')
    expect(wrapper.text()).toContain('与“自然”偏好相关')
    expect(wrapper.text()).toContain('自然漫步')
    expect(wrapper.get('.toolbar-actions').text()).not.toContain('重新生成规划')
    expect(wrapper.get('.day-focus').text()).toContain('今日重点')
    expect(wrapper.get('.day-overview-label').text()).toBe('行程概览')
    expect(wrapper.get('.attraction-time').text()).toContain('09:00')
    expect(wrapper.get('.attraction-description').text()).toContain('自然与人文景观')
    expect(wrapper.get('.attraction-image img').attributes('src')).toBe('https://example.com/west-lake.jpg')
    expect(wrapper.text()).not.toContain('参考时长')
    expect(wrapper.text()).toContain('第 1 天围绕自然 · 轻松安排了 1 处景点')
    expect(wrapper.text()).not.toContain('开放时间')
    expect(wrapper.text()).not.toContain('已核验的景点知识')
  })

  it('shows every current-day check warning and a weather fallback for older plans', () => {
    const wrapper = mount(TravelPlanView, {
      props: {
        result: {
          requirement: {
            province: '浙江省', cities: ['杭州市'], province_preference_confirmed: true,
            city_preference_confirmed: true, days: 1, interests: [], preference_text: null,
            travel_scope: 'single_city', start_date: '2026-09-20', daily_start_time: '09:00',
            daily_end_time: '18:00', transport_mode: 'auto',
          },
          plan: {
            province: '浙江省', city_stays: [{ city: '杭州市', days: 1 }], city_plans: [], notices: [],
            manual_adjustment_warnings: { '1': ['你手动更换的景点可能超出时间窗口。'] },
            evaluation: {
              passed: true,
              issues: [
                { type: 'route', severity: 'medium', day: 1, message: '甲到乙预计需要 69 分钟', suggestion: '可以手动更换' },
                { type: 'route', severity: 'high', day: 1, message: '另有一段路线无法获取', suggestion: null },
                { type: 'schedule', severity: 'medium', day: 1, message: '预计结束时间超出窗口', suggestion: '请确认' },
                { type: 'preference', severity: 'medium', day: 1, message: '旧版兴趣提醒', suggestion: null },
              ],
            },
            itinerary: [{
              day: 1, city: '杭州市', city_adcode: '330100', city_day: 1, date: '2026-09-20',
              description: '行程安排。',
              weather: { city: '杭州市', date: '2026-09-20', condition: '小雨', temperature_min: 20,
                temperature_max: 24, precipitation_probability: 0.8, wind_level: '2',
                travel_advice: null, status: 'available', source: 'qweather' },
              schedule: { start_time: '09:00', end_time: '17:00', visit_minutes: 120,
                route_minutes: 69, meal_minutes: 40, buffer_minutes: 20, total_minutes: 249, status: 'complete' },
              attractions: [{ poi_id: 'A1', name: '甲', city: '杭州市', visit_duration_minutes: 120,
                start_time: '09:00', end_time: '11:00' }],
              routes: [],
            }],
          },
        } as any,
      },
    })

    const checks = wrapper.get('.evaluation-card').text()
    expect(checks).toContain('甲到乙预计需要 69 分钟')
    expect(checks).toContain('另有一段路线无法获取')
    expect(checks).toContain('预计结束时间超出窗口')
    expect(checks).toContain('你手动更换的景点可能超出时间窗口。')
    expect(checks).not.toContain('旧版兴趣提醒')
    expect(wrapper.text()).toContain('天气建议：有降水，建议带伞或雨衣')
    expect(wrapper.get('.plan-stats').text()).toContain('需留意')
  })

  it('collapses duplicate route warnings and hides generic review filler', () => {
    const route = {
      origin_poi_id: 'A1', origin_name: '甲景点', destination_poi_id: 'A2', destination_name: '乙景点',
      distance_m: 12000, duration_min: 66, mode: 'transit', source: 'amap_mcp', estimated: false,
    }
    const wrapper = mount(TravelPlanView, {
      props: {
        result: {
          requirement: {
            province: '浙江省', cities: ['杭州市'], province_preference_confirmed: true,
            city_preference_confirmed: true, days: 1, interests: [], preference_text: null,
            travel_scope: 'single_city', start_date: '2026-09-20', daily_start_time: '09:00',
            daily_end_time: '18:00', transport_mode: 'transit',
          },
          plan: {
            province: '浙江省', city_stays: [{ city: '杭州市', days: 1 }], city_plans: [], notices: [],
            manual_adjustment_warnings: { '1': ['甲景点到乙景点预计交通66分钟。'] },
            evaluation: {
              passed: true,
              issues: [
                { type: 'route', severity: 'medium', day: 1, message: '甲景点到乙景点预计需要 66 分钟', suggestion: null },
                { type: 'review', severity: 'medium', day: 1, message: '复核提示：甲景点到乙景点公共交通约66分钟，是确定性评估已提示的路线问题。', suggestion: null },
                { type: 'review', severity: 'medium', day: 1, message: '这是手动调整后的结果，请按情况决定是否保留。', suggestion: null },
                { type: 'review', severity: 'medium', day: 1, message: '甲景点暂无营业时间信息，建议出行前核实。', suggestion: null },
              ],
              daily_metrics: [],
            },
            itinerary: [{
              day: 1, city: '杭州市', city_adcode: '330100', city_day: 1, date: '2026-09-20',
              description: '今天游览两个景点。',
              attractions: [
                { poi_id: 'A1', name: '甲景点', city: '杭州市', visit_duration_minutes: 90 },
                { poi_id: 'A2', name: '乙景点', city: '杭州市', visit_duration_minutes: 90 },
              ],
              routes: [route],
            }],
          },
        } as any,
      },
    })

    const checks = wrapper.get('.evaluation-card').text()
    expect(checks.match(/66\s*分钟/g)).toHaveLength(1)
    expect(checks).not.toContain('手动调整后的结果')
    expect(checks).toContain('暂无营业时间信息')
  })

  it('shows review hints only on their matching day, including legacy saved hints', async () => {
    const wrapper = mount(TravelPlanView, {
      props: {
        result: {
          requirement: {
            province: '云南省', cities: ['昆明市'], province_preference_confirmed: true,
            city_preference_confirmed: true, days: 2, interests: [], preference_text: null,
            travel_scope: 'single_city', start_date: '2026-10-01', daily_start_time: '08:00',
            daily_end_time: '21:00', transport_mode: 'auto',
          },
          plan: {
            province: '云南省', city_stays: [{ city: '昆明市', days: 2 }], city_plans: [], notices: [],
            evaluation: {
              passed: true,
              issues: [
                { type: 'review', severity: 'medium', day: 1, message: '第一天的路线需要留意', suggestion: null },
                { type: 'review', severity: 'medium', day: null, message: '复核提示：第 2 天：营业时间需要确认', suggestion: null },
              ],
              daily_metrics: [],
            },
            itinerary: [1, 2].map((day) => ({
              day, city: '昆明市', city_adcode: '530100', city_day: day,
              date: `2026-10-0${day}`, description: `第${day}天行程安排。`,
              weather: { city: '昆明市', date: `2026-10-0${day}`, status: 'skipped', source: 'none' },
              attractions: [{ poi_id: `A${day}`, name: `景点${day}`, city: '昆明市', visit_duration_minutes: 90 }],
              routes: [],
            })),
          },
        } as any,
      },
    })

    expect(wrapper.get('.evaluation-card').text()).toContain('第一天的路线需要留意')
    expect(wrapper.get('.evaluation-card').text()).not.toContain('营业时间需要确认')

    await wrapper.findAll('.day-tabs button')[1].trigger('click')
    expect(wrapper.get('.evaluation-card').text()).toContain('营业时间需要确认')
    expect(wrapper.get('.evaluation-card').text()).not.toContain('第一天的路线需要留意')
    expect(wrapper.get('.evaluation-card').text()).toContain('第 2 天方案检查')
  })

  it('lets the user confirm a replacement after warning about schedule conflicts', async () => {
    const candidate = {
      poi_id: 'B', name: '灵隐寺', city: '杭州市', category: '寺庙', description: '山林古寺',
      image_url: null, longitude: 120.1, latitude: 30.1, visit_duration_minutes: 90,
      opening_hours_weekly: null, opening_hours_today: null, opening_hours_status: 'not_checked' as const,
      opening_hours_applied: false, start_time: null, end_time: null,
      preference_score: 0.8, interest_tags: ['历史文化'], final_rank: 2,
    }
    const result = {
      requirement: {
        province: '浙江省', cities: ['杭州市'], province_preference_confirmed: true,
        city_preference_confirmed: true, days: 1, interests: ['自然'], preference_text: '轻松',
        travel_scope: 'single_city' as const, start_date: '2026-09-20',
        daily_start_time: '09:00', daily_end_time: '10:30', transport_mode: 'auto' as const,
      },
      plan: {
        province: '浙江省', city_stays: [{ city: '杭州市', days: 1 }], city_plans: [],
        notices: [], replacement_candidates_by_city: { 杭州市: [candidate] },
        itinerary: [{
          day: 1, city: '杭州市', city_adcode: '330100', city_day: 1, date: '2026-09-20',
          description: '围绕西湖安排一天轻松的自然游览。',
          attractions: [{ ...candidate, poi_id: 'A', name: '西湖' }], routes: [],
          schedule: { start_time: '09:00', end_time: '10:00', visit_minutes: 60,
            route_minutes: 0, meal_minutes: 0, buffer_minutes: 0, total_minutes: 60, status: 'complete' },
        }],
      },
    }
    const preview = { result, warnings: ['第1天预计到12:00结束，超过你设置的10:30。'] }
    vi.spyOn(conversationApi, 'replacementOptions').mockResolvedValue([
      { candidate, distance_m: 1200, distance_score: 0.8, priority_score: 0.8 },
    ])
    vi.spyOn(conversationApi, 'previewAttractionReplacement').mockResolvedValue(preview as any)
    vi.spyOn(conversationApi, 'replaceAttraction').mockResolvedValue(preview as any)
    const wrapper = mount(TravelPlanView, {
      props: { conversationId: 'conversation-1', result: result as any },
    })

    await wrapper.get('.replacement-trigger').trigger('click')
    await vi.waitFor(() => expect(document.body.textContent).toContain('灵隐寺'))
    ;(document.body.querySelector('.replacement-options button') as HTMLButtonElement).click()
    await nextTick()
    ;(document.body.querySelector('.replacement-actions .replacement-confirm') as HTMLButtonElement).click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('仍可确认替换'))

    const confirmButton = document.body.querySelector('.replacement-actions .replacement-confirm') as HTMLButtonElement
    expect(confirmButton.disabled).toBe(false)
    confirmButton.click()
    await vi.waitFor(() => expect(conversationApi.replaceAttraction).toHaveBeenCalledOnce())
    expect(wrapper.emitted('updateResult')).toHaveLength(1)
  })
})
