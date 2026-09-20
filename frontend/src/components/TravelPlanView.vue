<script setup lang="ts">
import { computed, ref } from 'vue'
import type { EvaluationIssue, TravelDay, TravelPlanResponse } from '../types'

const props = defineProps<{ result: TravelPlanResponse }>()
defineEmits<{ restart: []; revise: [] }>()

const activeDay = ref(1)
const currentDay = computed(() => props.result.plan.itinerary.find((item) => item.day === activeDay.value) || props.result.plan.itinerary[0])
const totalAttractions = computed(() => new Set(props.result.plan.itinerary.flatMap((item) => item.attractions.map((attraction) => attraction.poi_id))).size)
const currentIssues = computed(() => props.result.plan.evaluation?.issues.filter((issue) => issue.day === currentDay.value.day) || [])
const globalIssues = computed(() => props.result.plan.evaluation?.issues.filter((issue) => issue.day === null) || [])
const currentNotices = computed(() => (props.result.plan.notices || []).filter((notice) => notice.day === currentDay.value.day || notice.day === null))
const routeMinutes = computed(() => currentDay.value.routes.reduce((total, route) => total + (route.duration_min || 0), 0))
const preferenceLabel = computed(() => props.result.requirement.interests.join('、') || props.result.requirement.preference_text || '')
const preferenceLine = computed(() => preferenceLabel.value ? `${preferenceLabel.value} · 按你的节奏安排` : '按你的偏好安排 · 留出自由调整空间')

function dayText(day: TravelDay) {
  return [
    day.description,
    ...day.attractions.flatMap((attraction) => [attraction.name, attraction.category || '', attraction.description || '']),
  ].join('')
}

function dayTheme(day: TravelDay) {
  const text = dayText(day)
  const natural = /自然|山水|公园|湿地|湖|山|森林|江|滨江|植物|园林/.test(text)
  const human = /历史|文化|博物馆|古城|古镇|寺|遗址|故居|人文/.test(text)
  if (/自然|山水/.test(preferenceLabel.value) && natural) return '自然漫步'
  if (/历史|文化|人文|古城|古镇/.test(preferenceLabel.value) && human) return '人文拾光'
  if (/美食|小吃|餐厅|老街|市井/.test(text)) return '城市风味'
  if (/摄影|拍照|光影|日落|夜景/.test(text)) return '光影漫游'
  if (natural) return '自然漫步'
  if (human) return '人文拾光'
  return '城市探索'
}

function dayReason(day: TravelDay) {
  const interest = preferenceLabel.value || '你的综合偏好'
  const movement = routeMinutes.value > 0
    ? `景点之间预计移动 ${routeMinutes.value} 分钟`
    : '景点之间按游览节奏自然衔接'
  return `围绕${interest}安排 ${day.attractions.length} 处景点，${movement}，中间留出游览和休息的空间。`
}

const currentDayTheme = computed(() => dayTheme(currentDay.value))
const currentDayReason = computed(() => dayReason(currentDay.value))
const recommendationText = computed(() => `第 ${currentDay.value.day} 天围绕${preferenceLabel.value || '你的综合偏好'}安排了 ${currentDay.value.attractions.length} 处景点，${routeMinutes.value > 0 ? `预计移动 ${routeMinutes.value} 分钟，` : ''}整体节奏留有调整空间。`)
const issueFor = (type: EvaluationIssue['type']) => currentIssues.value.find((issue) => issue.type === type)
const checkItems = computed(() => [
  issueFor('schedule_density') || { type: 'schedule_density', severity: 'ok', message: '每日安排合理' },
  issueFor('route') || { type: 'route', severity: 'ok', message: '路线无明显冲突' },
  issueFor('weather') || (currentDay.value.weather?.status === 'available'
    ? { type: 'weather', severity: 'ok', message: '天气信息已获取，请结合当天情况安排' }
    : { type: 'weather', severity: 'unknown', message: '天气待出发前确认' }),
])
</script>

<template>
  <main class="plan-screen">
    <header class="plan-hero">
      <div class="plan-hero-content">
        <span class="eyebrow light">YOUR PERSONAL JOURNEY</span>
        <h1>{{ result.plan.province || result.requirement.cities[0] }} · {{ result.requirement.days }} 日拾光之旅</h1>
        <p>{{ preferenceLine }}</p>
        <div class="plan-stats">
          <div><strong>{{ result.requirement.days }}</strong><span>旅行天数</span></div>
          <div><strong>{{ result.plan.city_stays.length }}</strong><span>目的城市</span></div>
          <div><strong>{{ totalAttractions }}</strong><span>精选景点</span></div>
          <div><strong>{{ result.plan.evaluation?.passed ? '通过' : '待确认' }}</strong><span>方案检查</span></div>
        </div>
      </div>
      <div class="mountain-art" aria-hidden="true"><span></span><i></i><b></b></div>
    </header>

    <section class="plan-content">
      <div class="plan-toolbar">
        <div>
          <span class="eyebrow">ITINERARY</span>
          <h2>逐日行程</h2>
        </div>
        <div class="toolbar-actions">
          <button type="button" @click="$emit('revise')">返回调整</button>
        </div>
      </div>

      <div class="city-route">
        <template v-for="(stay, index) in result.plan.city_stays" :key="stay.city">
          <div class="city-stop"><i></i><strong>{{ stay.city }}</strong><span>{{ stay.days }} 天</span></div>
          <span v-if="index < result.plan.city_stays.length - 1" class="route-line"></span>
        </template>
      </div>

      <div class="itinerary-layout">
        <nav class="day-tabs" aria-label="选择行程日期">
          <button
            v-for="day in result.plan.itinerary"
            :key="day.day"
            type="button"
            :class="{ active: activeDay === day.day }"
            @click="activeDay = day.day"
          >
            <small>DAY</small><strong>{{ String(day.day).padStart(2, '0') }}</strong><span>{{ day.city }}</span>
          </button>
        </nav>

        <article v-if="currentDay" class="day-detail glass-panel">
          <header>
            <div><span>第 {{ currentDay.day }} 天</span><h3>{{ currentDay.city }} · {{ currentDayTheme }}</h3></div>
            <b>{{ currentDay.attractions.length }} 个景点<template v-if="currentDay.schedule"> · {{ currentDay.schedule.start_time }}—{{ currentDay.schedule.end_time }}</template></b>
          </header>
          <p class="day-focus"><span>今日重点</span>{{ currentDayReason }}</p>
          <div class="day-overview">
            <span class="day-overview-label">行程概览</span>
            <p class="day-description">{{ currentDay.description }}</p>
          </div>
          <div v-if="currentDay.schedule" class="day-metrics">
            <span>游览 {{ currentDay.schedule.visit_minutes }} 分钟</span>
            <span>交通 {{ routeMinutes }} 分钟</span>
            <span>用餐 {{ currentDay.schedule.meal_minutes }} 分钟</span>
            <span>缓冲 {{ currentDay.schedule.buffer_minutes }} 分钟</span>
          </div>
          <div v-if="currentDay.attractions.length > 1" class="day-route-track" aria-label="当天游览顺序">
            <template v-for="(attraction, index) in currentDay.attractions" :key="attraction.poi_id">
              <div class="route-track-stop">
                <i>{{ String(index + 1).padStart(2, '0') }}</i>
                <strong>{{ attraction.name }}</strong>
              </div>
              <span v-if="index < currentDay.attractions.length - 1" class="route-track-arrow" aria-hidden="true">→</span>
            </template>
          </div>
          <ol class="attraction-timeline">
            <li v-for="(attraction, index) in currentDay.attractions" :key="attraction.poi_id">
              <span class="time-index">{{ String(index + 1).padStart(2, '0') }}</span>
              <div class="attraction-copy">
                <small class="attraction-time" v-if="attraction.start_time">{{ attraction.start_time }}—{{ attraction.end_time }}</small>
                <small class="attraction-time" v-else>{{ index === 0 ? '上午出发' : index === currentDay.attractions.length - 1 ? '傍晚时分' : '继续探索' }}</small>
                <div class="attraction-title"><strong>{{ attraction.name }}</strong><span v-if="attraction.category" class="attraction-category">{{ attraction.category }}</span></div>
                <small class="attraction-meta-line">游玩时长：{{ attraction.visit_duration_minutes }} 分钟</small>
              </div>
              <div v-if="currentDay.routes[index]" class="route-summary">
                <span v-if="currentDay.routes[index].duration_min !== null">
                  前往下一站 ·
                  {{ currentDay.routes[index].mode === 'walking' ? '步行' : currentDay.routes[index].mode === 'transit' ? '公共交通' : '驾车' }}
                  约 {{ currentDay.routes[index].duration_min }} 分钟 · {{ (currentDay.routes[index].distance_m! / 1000).toFixed(1) }} 公里
                  <em v-if="currentDay.routes[index].estimated">估算</em>
                </span>
                <span v-else>下一段路线暂不可用</span>
              </div>
              <i></i>
            </li>
          </ol>
        </article>

        <aside class="trip-notes">
          <div class="note-card"><span>✦</span><h3>为你推荐</h3><p>{{ recommendationText }}</p></div>
          <div class="note-card warm"><span>⌁</span><h3>天气</h3><p v-if="currentDay.weather?.status === 'available'">{{ currentDay.weather.condition }} · {{ currentDay.weather.temperature_min }}～{{ currentDay.weather.temperature_max }}℃<template v-if="currentDay.weather.precipitation_probability !== null"> · 降水概率 {{ Math.round(currentDay.weather.precipitation_probability * 100) }}%</template></p><p v-else-if="currentDay.weather?.status === 'skipped'">未填写出发日期，本次跳过天气查询。</p><p v-else>当前日期暂时无法获取可靠预报，出发前请再次确认。</p></div>
          <div class="note-card evaluation-card">
            <span>✓</span><h3>方案检查</h3>
            <p v-for="item in checkItems" :key="item.type" :class="`check-${item.severity}`">
              {{ item.severity === 'ok' ? '✓' : item.severity === 'unknown' ? '○' : '⚠' }} {{ item.message }}
            </p>
            <p v-for="issue in globalIssues" :key="issue.message" :class="`check-${issue.severity}`">⚠ {{ issue.message }}</p>
          </div>
          <div v-if="currentNotices.length" class="note-card notice-card">
            <span>i</span><h3>出行提醒</h3>
            <p v-for="notice in currentNotices" :key="`${notice.code}-${notice.day}`" :class="notice.level">{{ notice.level === 'warning' ? '⚠' : '○' }} {{ notice.message }}</p>
          </div>
        </aside>
      </div>

      <button class="restart-link" type="button" @click="$emit('restart')">＋ 开始一段新的旅行计划</button>
    </section>
  </main>
</template>
