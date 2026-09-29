// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

describe('GeneralRouteMap', () => {
  it('shows a route fallback when the Amap JS key is not configured', async () => {
    vi.stubEnv('VITE_AMAP_JS_KEY', '')
    vi.resetModules()
    const { default: GeneralRouteMap } = await import('./GeneralRouteMap.vue')
    const wrapper = mount(GeneralRouteMap, {
      props: {
        route: {
          origin_poi_id: 'origin',
          origin_name: '杭州东站',
          destination_poi_id: 'destination',
          destination_name: '西湖',
          origin_longitude: 120.21,
          origin_latitude: 30.29,
          destination_longitude: 120.14,
          destination_latitude: 30.25,
          distance_m: 8300,
          duration_min: 32,
          mode: 'transit',
          source: 'amap_mcp',
          estimated: false,
        },
      },
    })

    expect(wrapper.text()).toContain('杭州东站')
    expect(wrapper.text()).toContain('西湖')
    expect(wrapper.text()).toContain('8.3公里')
    expect(wrapper.text()).toContain('高德地图 JS Key 尚未配置')
    expect(wrapper.text()).not.toContain('数据来源')
    vi.unstubAllEnvs()
  })
})
