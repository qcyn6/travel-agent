<script setup lang="ts">
import { computed, ref } from 'vue'
import { conversationApi } from '../api'
import type {
  AttractionReplacementOption,
  AttractionReplacementPreview,
  EvaluationIssue,
  RouteSegment,
  TravelDay,
  TravelPlanResponse,
} from '../types'
import { fallbackWeatherAdvice } from '../utils/weatherAdvice'
import DailyRouteMap from './DailyRouteMap.vue'

const props = defineProps<{ result: TravelPlanResponse; conversationId?: string }>()
const emit = defineEmits<{ restart: []; revise: []; updateResult: [result: TravelPlanResponse] }>()

const activeDay = ref(1)
const failedImages = ref<string[]>([])
const replacementTarget = ref<{ day: number; poiId: string; name: string } | null>(null)
const replacementOptions = ref<AttractionReplacementOption[]>([])
const selectedReplacement = ref('')
const replacementPreview = ref<AttractionReplacementPreview | null>(null)
const replacementLoading = ref(false)
const replacementError = ref('')
const currentDay = computed(() => props.result.plan.itinerary.find((item) => item.day === activeDay.value) || props.result.plan.itinerary[0])
const currentRouteStops = computed(() => [
  ...(currentDay.value.start_hotel ? [currentDay.value.start_hotel] : []),
  ...currentDay.value.attractions,
  ...(currentDay.value.hotel ? [currentDay.value.hotel] : []),
])
const totalAttractions = computed(() => new Set(props.result.plan.itinerary.flatMap((item) => item.attractions.map((attraction) => attraction.poi_id))).size)
const visibleCheckTypes = new Set<EvaluationIssue['type']>(['route', 'schedule', 'manual_adjustment', 'review'])
function genericReviewHint(message: string) {
  const text = message.replace(/\s+/g, '')
  return /复核提示不会自动修改|确定性评估已提示|请结合行程详情自行判断|按情况决定是否保留|手动调整后的结果/.test(text)
    || (text.includes('手动调整') && /是否保留|按情况|自行判断|仅作提醒/.test(text))
}
function routeFactKey(issue: EvaluationIssue) {
  if (!['route', 'manual_adjustment', 'review'].includes(issue.type) || issue.day === null) return null
  const day = props.result.plan.itinerary.find((item) => item.day === issue.day)
  if (!day) return null
  const message = issue.message.replace(/\s+/g, '')
  const routes = [
    ...(day.start_hotel_route ? [day.start_hotel_route] : []),
    ...day.routes,
  ]
  const matchedRoute = routes.find((route) => {
    if (!route.origin_name || !route.destination_name || route.duration_min === null) return false
    const duration = String(route.duration_min)
    return message.includes(route.origin_name.replace(/\s+/g, ''))
      && message.includes(route.destination_name.replace(/\s+/g, ''))
      && new RegExp(`(^|\\D)${duration}(?=\\D|$)`).test(message)
      && message.includes('分钟')
  })
  return matchedRoute
    ? `${issue.day}:${matchedRoute.origin_poi_id}:${matchedRoute.destination_poi_id}:${matchedRoute.duration_min}`
    : null
}
const planCheckIssues = computed(() => {
  // Older saved plans stored AI review text without a day field. Recover the
  // day from its legacy "第 N 天" wording so it is not repeated on every tab.
  const issues = (props.result.plan.evaluation?.issues || [])
    .filter((issue) => visibleCheckTypes.has(issue.type))
    .map((issue) => {
      if (issue.type !== 'review' || issue.day !== null) return issue
      const dayMatch = issue.message.match(/第\s*(\d+)\s*天/)
      return dayMatch ? { ...issue, day: Number(dayMatch[1]) } : issue
    })
  const seenRouteFacts = new Set<string>()
  const visibleIssues = issues.filter((issue) => {
    if (issue.type === 'review' && genericReviewHint(issue.message)) return false
    const routeKey = routeFactKey(issue)
    if (!routeKey) return true
    if (seenRouteFacts.has(routeKey)) return false
    seenRouteFacts.add(routeKey)
    return true
  })
  const seen = new Set(visibleIssues.map((issue) => issue.message))
  for (const [day, warnings] of Object.entries(props.result.plan.manual_adjustment_warnings || {})) {
    for (const message of warnings) {
      if (seen.has(message)) continue
      const issue: EvaluationIssue = { type: 'manual_adjustment', severity: 'medium', day: Number(day), message, suggestion: null }
      const routeKey = routeFactKey(issue)
      if (routeKey && seenRouteFacts.has(routeKey)) continue
      if (routeKey) seenRouteFacts.add(routeKey)
      seen.add(message)
      visibleIssues.push(issue)
    }
  }
  return visibleIssues
})
const currentIssues = computed(() => planCheckIssues.value.filter((issue) => issue.day === currentDay.value.day))
const globalIssues = computed(() => planCheckIssues.value.filter((issue) => issue.day === null))
const currentNotices = computed(() => (props.result.plan.notices || []).filter((notice) => notice.day === currentDay.value.day || notice.day === null))
const displayedRoutes = computed(() => [
  ...(currentDay.value.start_hotel_route ? [currentDay.value.start_hotel_route] : []),
  ...currentDay.value.routes,
  ...(currentDay.value.hotel_route ? [currentDay.value.hotel_route] : []),
])
const estimatedRouteRatio = computed(() => {
  if (!displayedRoutes.value.length) return 0
  const estimatedCount = displayedRoutes.value.filter(
    (route) => route.estimated || route.source === 'haversine',
  ).length
  return estimatedCount / displayedRoutes.value.length
})
const routeMinutes = computed(() => (
  (currentDay.value.start_hotel_route?.duration_min || 0)
  + currentDay.value.routes.reduce((total, route) => total + (route.duration_min || 0), 0)
))
const preferenceLabel = computed(() => {
  const parts = [props.result.requirement.interests.join('、')].filter(Boolean)
  if (props.result.requirement.preference_text && props.result.requirement.preference_text !== '没有额外体验偏好') {
    parts.push(props.result.requirement.preference_text)
  }
  return parts.join(' · ') || props.result.requirement.preference_text || ''
})
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
    ? currentDay.value.start_hotel_route
      ? `含前一晚酒店到首站，预计移动 ${routeMinutes.value} 分钟`
      : `景点之间预计移动 ${routeMinutes.value} 分钟`
    : '景点之间按游览节奏自然衔接'
  return `围绕${interest}安排 ${day.attractions.length} 处景点，${movement}，中间留出游览和休息的空间。`
}

function attractionReason(attraction: TravelDay['attractions'][number], index: number) {
  const text = `${attraction.name} ${attraction.category || ''} ${attraction.description || ''}`
  const matchedInterest = props.result.requirement.interests.find((interest) => text.includes(interest))
  if (matchedInterest) return `与“${matchedInterest}”偏好相关`
  if (index === 0) return '作为当天首站，优先满足当天的游览主题'
  const route = currentDay.value.routes[index - 1]
  if (route?.duration_min !== null && route?.duration_min !== undefined) {
    return `与上一站约 ${route.duration_min} 分钟衔接${route.estimated || route.source === 'haversine' ? '（估算）' : ''}`
  }
  return '按当天时间窗口和游览节奏衔接'
}

function routeAfter(index: number): RouteSegment | null {
  const route = currentDay.value.routes[index]
  if (route) return route
  if (index === currentDay.value.attractions.length - 1) return currentDay.value.hotel_route || null
  return null
}

function isHotelTransfer(index: number) {
  return index === currentDay.value.attractions.length - 1 && Boolean(currentDay.value.hotel_route)
}

function routeModeLabel(route: RouteSegment) {
  return route.mode === 'walking' ? '步行' : route.mode === 'transit' ? '公共交通' : '驾车'
}

function routeDistanceLabel(route: RouteSegment) {
  return route.distance_m === null || route.distance_m === undefined
    ? ''
    : ` · ${(route.distance_m / 1000).toFixed(1)} 公里`
}

function routeStatusLabel(route: RouteSegment) {
  if (route.source === 'unavailable') return '路线暂不可用'
  return route.estimated || route.source === 'haversine' ? '估算路线' : '真实路线'
}

function routeStopLabel(index: number) {
  if (currentDay.value.start_hotel && index === 0) return '昨'
  const attractionIndex = index - (currentDay.value.start_hotel ? 1 : 0)
  if (attractionIndex >= currentDay.value.attractions.length) return '住'
  return String(attractionIndex + 1).padStart(2, '0')
}

const currentDayTheme = computed(() => dayTheme(currentDay.value))
const currentDayReason = computed(() => dayReason(currentDay.value))
const recommendationText = computed(() => `第 ${currentDay.value.day} 天围绕${preferenceLabel.value || '你的综合偏好'}安排了 ${currentDay.value.attractions.length} 处景点，${routeMinutes.value > 0 ? `预计移动 ${routeMinutes.value} 分钟，` : ''}整体节奏留有调整空间。`)
const currentWeatherAdvice = computed(() => {
  const weather = currentDay.value.weather
  if (!weather || weather.status !== 'available') return null
  return weather.travel_advice || fallbackWeatherAdvice(weather)
})
const currentWeatherAdviceSource = computed(() => {
  const weather = currentDay.value.weather
  if (weather?.travel_advice) return weather.travel_advice_source || 'ai'
  return 'rule_based'
})
const planCheckStatus = computed(() => {
  if (!props.result.plan.evaluation) return '待检查'
  if (!props.result.plan.evaluation.passed) return '待确认'
  return planCheckIssues.value.length ? '需留意' : '通过'
})

function hasReplacementOptions(day: TravelDay, poiId: string) {
  const pool = props.result.plan.replacement_candidates_by_city?.[day.city] || []
  const scheduled = new Set(
    props.result.plan.itinerary.flatMap((item) => item.attractions
      .filter((stop) => !(item.day === day.day && stop.poi_id === poiId))
      .map((stop) => stop.poi_id)),
  )
  return pool.some((candidate) => candidate.poi_id !== poiId && !scheduled.has(candidate.poi_id))
}

function distanceLabel(option: AttractionReplacementOption) {
  return option.distance_m === null ? '按兴趣排序' : `距上一站直线约 ${(option.distance_m / 1000).toFixed(1)} 公里`
}

async function openReplacement(day: TravelDay, poiId: string, name: string) {
  if (!props.conversationId) return
  replacementTarget.value = { day: day.day, poiId, name }
  replacementOptions.value = []
  selectedReplacement.value = ''
  replacementPreview.value = null
  replacementError.value = ''
  replacementLoading.value = true
  try {
    replacementOptions.value = await conversationApi.replacementOptions(
      props.conversationId,
      day.day,
      poiId,
    )
    if (!replacementOptions.value.length) replacementError.value = '没有尚未安排的备选景点了。'
  } catch (error) {
    replacementError.value = error instanceof Error ? error.message : '读取备选景点失败，请稍后重试。'
  } finally {
    replacementLoading.value = false
  }
}

function closeReplacement() {
  if (replacementLoading.value) return
  replacementTarget.value = null
  replacementPreview.value = null
  replacementError.value = ''
}

async function previewReplacement() {
  if (!props.conversationId || !replacementTarget.value || !selectedReplacement.value) return
  replacementLoading.value = true
  replacementError.value = ''
  try {
    replacementPreview.value = await conversationApi.previewAttractionReplacement(
      props.conversationId,
      {
        day: replacementTarget.value.day,
        current_poi_id: replacementTarget.value.poiId,
        replacement_poi_id: selectedReplacement.value,
      },
    )
  } catch (error) {
    replacementError.value = error instanceof Error ? error.message : '路线预览失败，请稍后重试。'
  } finally {
    replacementLoading.value = false
  }
}

async function confirmReplacement() {
  if (!props.conversationId || !replacementTarget.value || !selectedReplacement.value) return
  replacementLoading.value = true
  replacementError.value = ''
  try {
    const updated = await conversationApi.replaceAttraction(
      props.conversationId,
      {
        day: replacementTarget.value.day,
        current_poi_id: replacementTarget.value.poiId,
        replacement_poi_id: selectedReplacement.value,
      },
    )
    emit('updateResult', updated.result)
    replacementTarget.value = null
    replacementPreview.value = null
  } catch (error) {
    replacementError.value = error instanceof Error ? error.message : '保存调整失败，请稍后重试。'
  } finally {
    replacementLoading.value = false
  }
}

function markImageFailed(poiId: string) {
  if (!failedImages.value.includes(poiId)) {
    failedImages.value = [...failedImages.value, poiId]
  }
}
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
          <div><strong>{{ planCheckStatus }}</strong><span>方案检查</span></div>
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
            <span v-if="displayedRoutes.length">估算路线 {{ Math.round(estimatedRouteRatio * 100) }}%</span>
          </div>
          <div v-if="currentRouteStops.length > 1" class="day-route-track" aria-label="当天游览顺序">
            <template v-for="(stop, index) in currentRouteStops" :key="stop.poi_id">
              <div class="route-track-stop">
                <i>{{ routeStopLabel(index) }}</i>
                <strong>{{ stop.name }}</strong>
              </div>
              <span v-if="index < currentRouteStops.length - 1" class="route-track-arrow" aria-hidden="true">→</span>
            </template>
          </div>
          <DailyRouteMap v-if="currentRouteStops.length > 1" :day="currentDay" />
          <ol class="attraction-timeline">
            <li v-for="(attraction, index) in currentDay.attractions" :key="attraction.poi_id">
              <span class="time-index">{{ String(index + 1).padStart(2, '0') }}</span>
              <div class="attraction-copy">
                <small class="attraction-time" v-if="attraction.start_time">{{ attraction.start_time }}—{{ attraction.end_time }}</small>
                <small class="attraction-time" v-else>{{ index === 0 ? '上午出发' : index === currentDay.attractions.length - 1 ? '傍晚时分' : '继续探索' }}</small>
                <small v-if="index === 0 && currentDay.start_hotel" class="hotel-transfer-meta">
                  前一晚酒店 {{ currentDay.start_hotel.name }} 出发
                  <template v-if="currentDay.start_hotel_route">
                    <template v-if="currentDay.start_hotel_route.duration_min !== null && currentDay.start_hotel_route.duration_min !== undefined">
                      · {{ routeModeLabel(currentDay.start_hotel_route) }}约 {{ currentDay.start_hotel_route.duration_min }} 分钟{{ routeDistanceLabel(currentDay.start_hotel_route) }}
                    </template>
                    <template v-else>· 路线时间暂不可用，未计入交通时间</template>
                    <em>{{ routeStatusLabel(currentDay.start_hotel_route) }}</em>
                  </template>
                  <template v-else>· 酒店到首站路线暂不可用，交通时间未计入</template>
                </small>
                <div class="attraction-title">
                  <strong>{{ attraction.name }}</strong><span v-if="attraction.category" class="attraction-category">{{ attraction.category }}</span>
                  <button
                    v-if="conversationId && hasReplacementOptions(currentDay, attraction.poi_id)"
                    class="replacement-trigger"
                    type="button"
                    @click="openReplacement(currentDay, attraction.poi_id, attraction.name)"
                  >换一个</button>
                </div>
                <div class="attraction-image">
                  <img
                    v-if="attraction.image_url && !failedImages.includes(attraction.poi_id)"
                    :src="attraction.image_url"
                    :alt="`${attraction.name}景点图片`"
                    loading="lazy"
                    referrerpolicy="no-referrer"
                    @error="markImageFailed(attraction.poi_id)"
                  />
                  <div v-else class="attraction-image-placeholder" aria-hidden="true">
                    <span>{{ String(index + 1).padStart(2, '0') }}</span>
                    <strong>{{ attraction.name }}</strong>
                  </div>
                </div>
                <p v-if="attraction.description" class="attraction-description">{{ attraction.description }}</p>
                <small class="attraction-meta-line">游玩时长：{{ attraction.visit_duration_minutes }} 分钟</small>
                <small v-if="attraction.opening_hours_weekly" class="attraction-meta-line">高德营业时间：{{ attraction.opening_hours_weekly }}</small>
                <small v-else-if="attraction.opening_hours_today" class="attraction-meta-line">高德今日营业时间：{{ attraction.opening_hours_today }}</small>
                <small v-if="!attraction.opening_hours_applied" class="attraction-meta-line opening-hours-note">
                  {{ attraction.opening_hours_status === 'unavailable' ? '高德未提供可用的周营业时间' : '周营业时间尚未核实' }}，本次按不限制时段安排，出行前建议核实
                </small>
                <small class="attraction-reason">{{ attractionReason(attraction, index) }}</small>
              </div>
              <div v-if="routeAfter(index)" class="route-summary">
                <span v-if="routeAfter(index)?.duration_min !== null && routeAfter(index)?.duration_min !== undefined">
                  {{ isHotelTransfer(index) ? '前往酒店' : '前往下一站' }} ·
                  {{ routeModeLabel(routeAfter(index)!) }}
                  约 {{ routeAfter(index)!.duration_min }} 分钟{{ routeDistanceLabel(routeAfter(index)!) }}
                  <em>{{ routeStatusLabel(routeAfter(index)!) }}</em>
                </span>
                <span v-else>{{ isHotelTransfer(index) ? '前往酒店' : '下一段' }}路线暂不可用</span>
              </div>
              <i></i>
            </li>
            <li v-if="currentDay.hotel" class="hotel-timeline-item">
              <span class="time-index hotel-index">住</span>
              <div class="attraction-copy">
                <small class="attraction-time">游玩结束后 · 入住</small>
                <div class="attraction-title"><strong>{{ currentDay.hotel.name }}</strong><span class="attraction-category hotel-badge">住宿</span></div>
                <div class="attraction-image hotel-image">
                  <img
                    v-if="currentDay.hotel.image_url && !failedImages.includes(currentDay.hotel.poi_id)"
                    :src="currentDay.hotel.image_url"
                    :alt="`${currentDay.hotel.name}酒店图片`"
                    loading="lazy"
                    referrerpolicy="no-referrer"
                    @error="markImageFailed(currentDay.hotel.poi_id)"
                  />
                  <div v-else class="attraction-image-placeholder hotel-image-placeholder" aria-hidden="true">
                    <span>住</span>
                    <strong>{{ currentDay.hotel.name }}</strong>
                  </div>
                </div>
                <p v-if="currentDay.hotel.address" class="hotel-address">{{ currentDay.hotel.address }}</p>
                <small v-if="currentDay.hotel.rating !== null && currentDay.hotel.rating !== undefined" class="attraction-meta-line">高德评分：{{ currentDay.hotel.rating }}</small>
                <small class="hotel-note">
                  当日结束后前往酒店不计入本日时间窗口。
                  <template v-if="currentDay.start_hotel_route?.duration_min !== null && currentDay.start_hotel_route?.duration_min !== undefined">前一晚酒店到首站路线计入本日窗口。</template>
                </small>
              </div>
              <i></i>
            </li>
          </ol>
        </article>

        <aside class="trip-notes">
          <div class="note-card"><span>✦</span><h3>为你推荐</h3><p>{{ recommendationText }}</p></div>
          <div class="note-card warm">
            <span>⌁</span><h3>天气</h3>
            <p v-if="currentDay.weather?.status === 'available'">{{ currentDay.weather.condition }} · {{ currentDay.weather.temperature_min }}～{{ currentDay.weather.temperature_max }}℃<template v-if="currentDay.weather.precipitation_probability !== null"> · 降水概率 {{ Math.round(currentDay.weather.precipitation_probability * 100) }}%</template></p>
            <p v-else-if="currentDay.weather?.status === 'skipped'">未填写出发日期，本次跳过天气查询。</p>
            <p v-else>当前日期暂时无法获取可靠预报，出发前请再次确认。</p>
            <p v-if="currentWeatherAdvice" class="weather-travel-advice"><strong>{{ currentWeatherAdviceSource === 'ai' ? 'AI 出行建议' : '天气建议' }}：</strong>{{ currentWeatherAdvice }}</p>
          </div>
          <div class="note-card evaluation-card">
            <span>{{ currentIssues.length || globalIssues.length ? '!' : '✓' }}</span><h3>第 {{ currentDay.day }} 天方案检查</h3>
            <p v-for="issue in currentIssues" :key="`${issue.type}-${issue.message}`" :class="`check-${issue.severity}`">
              {{ issue.severity === 'low' ? 'i' : '⚠' }} {{ issue.message }}
              <small v-if="issue.suggestion">{{ issue.suggestion }}</small>
            </p>
            <p v-for="issue in globalIssues" :key="`${issue.type}-${issue.message}`" :class="`check-${issue.severity}`">
              {{ issue.severity === 'low' ? 'i' : '⚠' }} {{ issue.message }}
              <small v-if="issue.suggestion">{{ issue.suggestion }}</small>
            </p>
            <p v-if="!currentIssues.length && !globalIssues.length" class="check-ok">✓ 当前日期的路线、时间窗口和已知营业时间未发现明显冲突</p>
          </div>
          <div v-if="currentNotices.length" class="note-card notice-card">
            <span>i</span><h3>出行提醒</h3>
            <p v-for="notice in currentNotices" :key="`${notice.code}-${notice.day}`" :class="notice.level">{{ notice.level === 'warning' ? '⚠' : '○' }} {{ notice.message }}</p>
          </div>
        </aside>
      </div>

      <button class="restart-link" type="button" @click="$emit('restart')">＋ 开始一段新的旅行计划</button>
    </section>

    <Teleport to="body">
      <div v-if="replacementTarget" class="replacement-backdrop" @click.self="closeReplacement">
        <section class="replacement-dialog" role="dialog" aria-modal="true" aria-labelledby="replacement-title">
          <header class="replacement-dialog-header">
            <div><span class="eyebrow">CHANGE A STOP</span><h2 id="replacement-title">更换「{{ replacementTarget.name }}」</h2></div>
            <button type="button" aria-label="关闭" :disabled="replacementLoading" @click="closeReplacement">×</button>
          </header>
          <p class="replacement-help">只显示这个城市里尚未安排的候选景点。候选顺序会根据这个位置的上一站变化；按路线/距离 0.6、偏好 0.4 排序。距离排序使用直线距离，选中后再查高德真实路线。</p>
          <p v-if="replacementLoading" class="replacement-status">{{ replacementPreview ? '正在保存调整…' : '正在计算路线和时间…' }}</p>
          <p v-if="replacementError" class="replacement-error">{{ replacementError }}</p>
          <div v-if="!replacementPreview" class="replacement-options">
            <button
              v-for="option in replacementOptions"
              :key="option.candidate.poi_id"
              type="button"
              :class="{ selected: selectedReplacement === option.candidate.poi_id }"
              :disabled="replacementLoading"
              @click="selectedReplacement = option.candidate.poi_id"
            >
              <span><strong>{{ option.candidate.name }}</strong><small>{{ option.candidate.category || '景点' }} · {{ distanceLabel(option) }}</small></span>
              <i>{{ selectedReplacement === option.candidate.poi_id ? '已选' : '选择' }}</i>
            </button>
          </div>
          <div v-else class="replacement-preview">
            <p>替换后预计当天 {{ replacementPreview.result.plan.itinerary.find((day) => day.day === replacementTarget?.day)?.schedule?.end_time || '时间待定' }} 结束。</p>
            <p v-if="replacementPreview.warnings.length" class="replacement-warning-title">有这些情况需要你留意（仍可确认替换）：</p>
            <p v-for="warning in replacementPreview.warnings" :key="warning" class="replacement-warning">⚠ {{ warning }}</p>
            <p v-if="!replacementPreview.warnings.length" class="replacement-ok">路线和时间没有发现特别提醒。</p>
          </div>
          <footer class="replacement-actions">
            <button type="button" :disabled="replacementLoading" @click="replacementPreview ? replacementPreview = null : closeReplacement()">{{ replacementPreview ? '返回选景点' : '取消' }}</button>
            <button
              v-if="replacementPreview"
              type="button"
              class="replacement-confirm"
              :disabled="replacementLoading"
              @click="confirmReplacement"
            >确认替换</button>
            <button
              v-else
              type="button"
              class="replacement-confirm"
              :disabled="replacementLoading || !selectedReplacement"
              @click="previewReplacement"
            >预览路线</button>
          </footer>
        </section>
      </div>
    </Teleport>
  </main>
</template>
