// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import WeatherCalendarCard from './WeatherCalendarCard.vue'

describe('WeatherCalendarCard', () => {
  it('renders a calendar-style weather summary without source labels', () => {
    const wrapper = mount(WeatherCalendarCard, {
      props: {
        weather: {
          city: '大理市',
          date: '2026-09-23',
          condition: '小雨',
          temperature_min: 15.96,
          temperature_max: 25.07,
          precipitation_probability: 0.73,
          wind_level: '3',
          status: 'available',
          source: 'qweather',
        },
      },
    })

    expect(wrapper.text()).toContain('09月')
    expect(wrapper.text()).toContain('23')
    expect(wrapper.text()).toContain('大理市')
    expect(wrapper.text()).toContain('小雨')
    expect(wrapper.text()).toContain('16.0° — 25.1°')
    expect(wrapper.text()).not.toContain('数据来源')
  })

  it('keeps every forecast date in one calendar card', () => {
    const wrapper = mount(WeatherCalendarCard, {
      props: {
        weather: [
          {
            city: '福州市', date: '2026-09-23', condition: '多云',
            temperature_min: 25, temperature_max: 32,
            precipitation_probability: 0.03, wind_level: '2',
            status: 'available', source: 'qweather',
          },
          {
            city: '福州市', date: '2026-09-24', condition: '小雨',
            temperature_min: 24, temperature_max: 31,
            precipitation_probability: 0.4, wind_level: '3',
            status: 'available', source: 'qweather',
          },
        ],
      },
    })

    expect(wrapper.findAll('.weather-calendar-entry')).toHaveLength(2)
    expect(wrapper.text()).toContain('23')
    expect(wrapper.text()).toContain('24')
    expect(wrapper.text()).toContain('2026年9月23日')
    expect(wrapper.text()).toContain('2026年9月24日')
  })
})
