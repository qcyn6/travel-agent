<script setup lang="ts">
import { computed, ref } from 'vue'
import type { EvaluationIssue, TravelPlanResponse } from '../types'

const props = defineProps<{ result: TravelPlanResponse }>()
defineEmits<{ restart: []; revise: [] }>()

const activeDay = ref(1)
const currentDay = computed(() => props.result.plan.itinerary.find((item) => item.day === activeDay.value) || props.result.plan.itinerary[0])
const totalAttractions = computed(() => new Set(props.result.plan.itinerary.flatMap((item) => item.attractions.map((attraction) => attraction.poi_id))).size)
const currentIssues = computed(() => props.result.plan.evaluation?.issues.filter((issue) => issue.day === currentDay.value.day) || [])
const globalIssues = computed(() => props.result.plan.evaluation?.issues.filter((issue) => issue.day === null) || [])
const currentNotices = computed(() => (props.result.plan.notices || []).filter((notice) => notice.day === currentDay.value.day || notice.day === null))
const routeMinutes = computed(() => currentDay.value.routes.reduce((total, route) => total + (route.duration_min || 0), 0))
const issueFor = (type: EvaluationIssue['type']) => currentIssues.value.find((issue) => issue.type === type)
const checkItems = computed(() => [
  issueFor('schedule_density') || { type: 'schedule_density', severity: 'ok', message: '每日安排合理' },
  issueFor('route') || { type: 'route', severity: 'ok', message: '路线无明显冲突' },
  issueFor('weather') || (currentDay.value.weather?.status === 'available'
    ? { type: 'weather', severity: 'ok', message: '天气与游览场景无明显冲突' }
    : { type: 'weather', severity: 'unknown', message: '天气待出发前确认' }),
  issueFor('opening_hours') || (currentDay.value.attractions.some((item) => item.opening_hours?.status === 'unknown')
    ? { type: 'opening_hours', severity: 'unknown', message: '部分开放时间待出发前核验' }
    : { type: 'opening_hours', severity: 'ok', message: '无已知开放时间冲突' }),
])
const printPlan = () => window.print()
</script>

<template>
  <main class="plan-screen">
    <header class="plan-hero">
      <div class="plan-hero-content">
        <span class="eyebrow light">YOUR PERSONAL JOURNEY</span>
        <h1>{{ result.plan.province || result.requirement.cities[0] }} · {{ result.requirement.days }} 日拾光之旅</h1>
        <p>{{ result.requirement.interests.join(' · ') }}<template v-if="result.requirement.preference_text"> · {{ result.requirement.preference_text }}</template></p>
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
          <button type="button" class="dark" @click="printPlan">打印方案</button>
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
            <div><span>第 {{ currentDay.day }} 天</span><h3>{{ currentDay.city }} · 城市漫游</h3></div>
            <b>{{ currentDay.attractions.length }} 个景点<template v-if="currentDay.schedule"> · {{ currentDay.schedule.start_time }}—{{ currentDay.schedule.end_time }}</template></b>
          </header>
          <p class="day-description">{{ currentDay.description }}</p>
          <p v-if="result.plan.replan_count" class="day-description">
            已自动完成 {{ result.plan.replan_count }} 次局部调整<template v-if="result.plan.replan_limit_reached">，仍有问题需要你确认</template>。
          </p>
          <div v-if="currentDay.schedule" class="day-metrics">
            <span>游览 {{ currentDay.schedule.visit_minutes }} 分钟</span>
            <span>交通 {{ routeMinutes }} 分钟</span>
            <span>用餐 {{ currentDay.schedule.meal_minutes }} 分钟</span>
            <span>缓冲 {{ currentDay.schedule.buffer_minutes }} 分钟</span>
          </div>
          <ol class="attraction-timeline">
            <li v-for="(attraction, index) in currentDay.attractions" :key="attraction.poi_id">
              <span class="time-index">{{ String(index + 1).padStart(2, '0') }}</span>
              <div>
                <small v-if="attraction.start_time">{{ attraction.start_time }}—{{ attraction.end_time }}</small>
                <small v-else>{{ index === 0 ? '上午出发' : index === currentDay.attractions.length - 1 ? '傍晚时分' : '继续探索' }}</small>
                <strong>{{ attraction.name }}</strong>
                <small v-if="attraction.estimated_visit_minutes">建议游玩约 {{ Math.round(attraction.estimated_visit_minutes / 30) / 2 }} 小时<template v-if="attraction.duration_source === 'category_fallback'"> · 类型估算</template></small>
                <small v-if="attraction.opening_hours?.status === 'known' && attraction.opening_hours.raw_text">开放时间：{{ attraction.opening_hours.raw_text }}</small>
                <small v-else>开放时间待出发前核验</small>
                <a v-if="attraction.opening_hours?.source_url" :href="attraction.opening_hours.source_url" target="_blank" rel="noreferrer">查看开放时间来源</a>
              </div>
              <div v-if="currentDay.routes[index]" class="route-summary">
                <span v-if="currentDay.routes[index].duration_min !== null">
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
          <div class="note-card"><span>✦</span><h3>为你推荐</h3><p>这份路线根据你的兴趣和旅行节奏，从已核验的景点知识中个性化生成。</p></div>
          <div class="note-card warm"><span>⌁</span><h3>天气</h3><p v-if="currentDay.weather?.status === 'available'">{{ currentDay.weather.condition }} · {{ currentDay.weather.temperature_min }}～{{ currentDay.weather.temperature_max }}℃<template v-if="currentDay.weather.precipitation_probability !== null"> · 降水概率 {{ Math.round(currentDay.weather.precipitation_probability * 100) }}%</template></p><p v-else-if="currentDay.weather?.status === 'skipped'">未填写出发日期，本次跳过天气查询。</p><p v-else>当前日期暂时无法获取可靠预报，出发前请再次确认。</p></div>
          <div class="note-card evaluation-card">
            <span>✓</span><h3>方案检查</h3>
            <p v-for="item in checkItems" :key="item.type" :class="`check-${item.severity}`">
              {{ item.severity === 'ok' ? '✓' : item.severity === 'unknown' ? '○' : '⚠' }} {{ item.message }}
            </p>
            <p v-for="issue in globalIssues" :key="issue.message" :class="`check-${issue.severity}`">⚠ {{ issue.message }}</p>
          </div>
          <div v-if="currentNotices.length" class="note-card notice-card">
            <span>i</span><h3>数据说明</h3>
            <p v-for="notice in currentNotices" :key="`${notice.code}-${notice.day}`" :class="notice.level">{{ notice.level === 'warning' ? '⚠' : '○' }} {{ notice.message }}</p>
          </div>
        </aside>
      </div>

      <button class="restart-link" type="button" @click="$emit('restart')">＋ 开始一段新的旅行计划</button>
    </section>
  </main>
</template>
