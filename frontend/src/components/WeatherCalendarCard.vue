<script setup lang="ts">
import { computed } from 'vue'
import type { WeatherResult } from '../types'

const props = defineProps<{ weather: WeatherResult | WeatherResult[] }>()

const forecasts = computed(() => Array.isArray(props.weather) ? props.weather : [props.weather])

function dateParts(weather: WeatherResult) {
  const value = weather.date || ''
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!match) return { month: '--', day: '--', weekday: '待定', full: value || '日期待定' }
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const weekday = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][new Date(year, month - 1, day).getDay()]
  return {
    month: String(month).padStart(2, '0'),
    day: String(day).padStart(2, '0'),
    weekday,
    full: `${year}年${month}月${day}日`,
  }
}

function temperature(weather: WeatherResult) {
  const { temperature_min: min, temperature_max: max } = weather
  if (min === null || max === null) return '—'
  const format = (value: number) => Number.isInteger(value) ? String(value) : value.toFixed(1)
  return `${format(min)}° — ${format(max)}°`
}

function precipitation(weather: WeatherResult) {
  const value = weather.precipitation_probability
  if (value === null) return '降水概率待定'
  const percentage = Math.round(value <= 1 ? value * 100 : value)
  return `降水概率 ${percentage}%`
}
</script>

<template>
  <section class="weather-calendar-card" aria-label="天气日历卡片">
    <article
      v-for="(item, index) in forecasts"
      :key="`${item.city}-${item.date || 'unknown'}-${index}`"
      class="weather-calendar-entry"
    >
      <div class="weather-calendar-date">
        <span class="weather-calendar-month">{{ dateParts(item).month }}月</span>
        <strong>{{ dateParts(item).day }}</strong>
        <span>{{ dateParts(item).weekday }}</span>
      </div>
      <div class="weather-calendar-main">
        <header>
          <div>
            <span class="weather-calendar-eyebrow">WEATHER NOTE</span>
            <strong>{{ item.city }}</strong>
          </div>
          <small>{{ dateParts(item).full }}</small>
        </header>
        <div class="weather-calendar-condition">
          <span>{{ item.condition || '天气状况待定' }}</span>
          <strong>{{ temperature(item) }}</strong>
        </div>
        <div class="weather-calendar-facts">
          <span>{{ precipitation(item) }}</span>
          <span>{{ item.wind_level ? `风力 ${item.wind_level}级` : '风力待定' }}</span>
        </div>
      </div>
    </article>
  </section>
</template>
