// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

describe('DailyRouteMap', () => {
  it('shows one daily route schematic when the JS key is not configured', async () => {
    vi.stubEnv('VITE_AMAP_JS_KEY', '')
    vi.resetModules()
    const { default: DailyRouteMap } = await import('./DailyRouteMap.vue')
    const wrapper = mount(DailyRouteMap, {
      props: {
        day: {
          day: 1,
          city: '杭州市',
          city_adcode: '330100',
          city_day: 1,
          date: null,
          weather: null,
          schedule: null,
          start_hotel: {
            poi_id: 'H0', name: '昨晚酒店', city: '杭州市', longitude: 120.09, latitude: 30.19,
            source: 'unavailable',
          },
          start_hotel_route: {
            origin_poi_id: 'H0', origin_name: '昨晚酒店',
            destination_poi_id: 'A1', destination_name: '西湖',
            origin_longitude: 120.09, origin_latitude: 30.19,
            destination_longitude: 120.1, destination_latitude: 30.2,
            distance_m: 900, duration_min: 15, mode: 'walking', source: 'amap_mcp', estimated: false,
          },
          attractions: [
            {
              poi_id: 'A1', name: '西湖', city: '杭州市', category: '自然', description: '测试',
              longitude: 120.1, latitude: 30.2, visit_duration_minutes: 90, start_time: '09:00', end_time: '10:30',
            },
            {
              poi_id: 'A2', name: '灵隐寺', city: '杭州市', category: '历史文化', description: '测试',
              longitude: 120.11, latitude: 30.21, visit_duration_minutes: 90, start_time: '11:00', end_time: '12:30',
            },
          ],
          routes: [{
            origin_poi_id: 'A1', origin_name: '西湖', destination_poi_id: 'A2', destination_name: '灵隐寺',
            distance_m: 1500, duration_min: 25, mode: 'transit', source: 'amap_mcp', estimated: false,
          }],
          hotel: {
            poi_id: 'H1', name: '湖滨酒店', city: '杭州市', longitude: 120.12, latitude: 30.22,
            source: 'amap',
          },
          hotel_route: {
            origin_poi_id: 'A2', origin_name: '灵隐寺', destination_poi_id: 'H1', destination_name: '湖滨酒店',
            distance_m: 1700, duration_min: 28, mode: 'driving', source: 'amap_mcp', estimated: false,
          },
          description: '测试行程',
        },
      },
    })

    expect(wrapper.findAll('.daily-route-map')).toHaveLength(1)
    expect(wrapper.text()).toContain('当天移动路线')
    expect(wrapper.text()).toContain('西湖')
    expect(wrapper.text()).toContain('灵隐寺')
    const navigationPanel = wrapper.get('.daily-route-map-navigation')
    expect(navigationPanel.text()).toContain('高德分段导航')
    expect(
      wrapper.get('.daily-route-map-canvas').element.compareDocumentPosition(navigationPanel.element)
        & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    const navigationLinks = wrapper.findAll('.daily-route-map-links a')
    expect(navigationLinks[0].text()).toContain('昨晚酒店 → 西湖')
    expect(navigationLinks[0].attributes('href')).toContain('mode=walk')
    expect(navigationLinks[1].text()).toContain('西湖 → 灵隐寺')
    expect(navigationLinks[1].attributes('href')).toContain('uri.amap.com/navigation')
    expect(navigationLinks[1].attributes('href')).toContain('mode=bus')
    expect(navigationLinks[1].attributes('href')).toContain('from=120.1%2C30.2%2C%E8%A5%BF%E6%B9%96')
    expect(navigationLinks[2].text()).toContain('灵隐寺 → 湖滨酒店')
    expect(navigationLinks[2].attributes('href')).toContain('mode=car')
    expect(navigationLinks[2].attributes('href')).toContain('to=120.12%2C30.22%2C%E6%B9%96%E6%BB%A8%E9%85%92%E5%BA%97')
    expect(wrapper.text()).toContain('高德地图 JS Key 尚未配置')
    vi.unstubAllEnvs()
  })
})
