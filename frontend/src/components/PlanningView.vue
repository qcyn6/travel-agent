<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { DestinationChoice, PlanningStep, RequirementDraft } from '../types'

const props = withDefaults(defineProps<{
  steps: PlanningStep[]
  choice: DestinationChoice | null
  error: string
  elapsedSeconds?: number
  busy?: boolean
  requirement?: RequirementDraft | null
}>(), {
  elapsedSeconds: 0,
  busy: false,
  requirement: null,
})
defineEmits<{
  choose: [scope: 'single_city' | 'multi_city']
  retry: []
  back: []
}>()

const completedSteps = computed(() => props.steps.filter((item) => item.status === 'completed').length)
const failureReasonVisible = ref(false)

watch(() => props.error, () => {
  failureReasonVisible.value = false
})
const destinationLabel = computed(() => {
  if (!props.requirement) return ''
  return props.requirement.cities.join('、') || props.requirement.province || '目的地待定'
})
const preferenceLabel = computed(() => {
  if (!props.requirement) return ''
  const parts = [props.requirement.interests.join('、')].filter(Boolean)
  if (props.requirement.preference_text && props.requirement.preference_text !== '没有额外体验偏好') {
    parts.push(props.requirement.preference_text)
  }
  return parts.join(' · ') || props.requirement.preference_text || ''
})

function elapsedText(seconds: number) {
  const safeSeconds = Math.max(0, Math.floor(seconds || 0))
  if (safeSeconds < 60) return `已用时 ${safeSeconds} 秒`
  const minutes = Math.floor(safeSeconds / 60)
  return `已用时 ${minutes} 分 ${safeSeconds % 60} 秒`
}

function friendlyError(message: string) {
  const match = message.match(/^(.+?)\s*仅准备好\s*(\d+)\s*条景点知识，目标为\s*(\d+)\s*条$/)
  if (!match) return message
  const [, city, ready, target] = match
  const missing = Math.max(1, Number(target) - Number(ready))
  return `${city}目前只整理到 ${ready} 个符合条件的景点，还需要 ${missing} 个才能生成完整行程。`
}

function stayText(stays: { city: string; days: number }[] | null) {
  return stays?.map((item) => `${item.city} ${item.days}天`).join(' · ') || ''
}

function statusText(status: PlanningStep['status']) {
  if (status === 'completed') return '完成'
  if (status === 'failed') return '需要重试'
  return '进行中'
}

function cityFromStep(step: string) {
  const parts = step.split(':')
  return parts.length >= 3 ? parts.slice(2).join(':') : ''
}

function displayStepLabel(item: PlanningStep) {
  const city = cityFromStep(item.step)
  const running = item.status === 'running'
  switch (item.step.split(':')[0]) {
    case 'requirement':
      return item.status === 'completed' ? '已了解你的旅行需求' : '正在了解你的旅行需求'
    case 'destination':
      return running ? '正在匹配目的地与旅行节奏' : item.status === 'failed' ? '目的地与旅行节奏匹配失败' : '目的地与旅行节奏已确定'
    case 'poi_search':
      return running ? `正在整理${city}的景点` : item.status === 'failed' ? `${city}的景点资料需要重试` : `已整理${city}的景点`
    case 'retrieval':
      return running ? `正在挑选符合偏好的${city}景点` : item.status === 'failed' ? `${city}的景点筛选失败` : `已挑选${city}景点`
    case 'attraction_details':
      return running ? `正在由 AI 补充${city}景点介绍` : item.status === 'failed' ? `${city}的景点介绍补充失败` : `已补充${city}景点介绍`
    case 'city_plan':
      return running ? `正在安排${city}的每日行程` : item.status === 'failed' ? `${city}的每日行程安排失败` : `已安排${city}的每日行程`
    case 'route_enrichment':
      return running ? '正在检查景点之间的移动' : item.status === 'failed' ? '景点之间的路线检查失败' : '景点之间的路线已检查'
    case 'hotel_enrichment':
      if (!running && (item.label.includes('暂不可用') || item.label.includes('暂未找到'))) return item.label
      return running ? '正在为每天最后一站匹配附近酒店' : item.status === 'failed' ? '酒店信息获取失败' : '每天最后一站酒店与收尾路线已补充'
    case 'weather_enrichment':
      return running ? '正在查看每天的天气' : item.status === 'failed' ? '天气信息获取失败' : '每天的天气信息已处理'
    case 'schedule':
      return running ? '正在安排每天的时间' : item.status === 'failed' ? '每日时间安排失败' : '每天的时间已安排'
    case 'evaluation':
      return running ? '正在检查行程是否轻松可执行' : item.status === 'failed' ? '行程可执行性检查失败' : '行程可执行性已检查'
    case 'complete':
      return '旅行方案已经准备好'
    default:
      return item.label.replaceAll('真实景点', '景点')
  }
}

const displaySteps = computed(() => props.steps.map((item) => ({
  ...item,
  label: displayStepLabel(item),
})))
const activeStep = computed(() => displaySteps.value.find((item) => item.status === 'running'))
const activityHint = computed(() => activeStep.value?.label || (props.error ? '可以重试，或回到对话调整' : '正在启动旅行安排'))
</script>

<template>
  <main class="planning-screen">
    <section class="planning-card glass-panel">
      <div v-if="!choice && !error" class="compass-loader" aria-hidden="true">
        <span></span><i></i>
      </div>
      <span class="eyebrow">CRAFTING YOUR JOURNEY</span>
      <h1>{{ choice ? '选择更适合你的旅行方式' : error ? '旅程准备遇到问题' : '正在为你绘制旅程' }}</h1>
      <p v-if="!choice">{{ error ? '已经保留已完成的准备工作，你可以直接重试或回到对话调整。' : '我们正在根据你的偏好，整理景点、路线和每天的时间安排。' }}</p>
      <div v-if="requirement" class="planning-profile" aria-label="本次安排信息">
        <span class="planning-profile-label">本次安排</span>
        <span class="planning-profile-chip">{{ destinationLabel }}</span>
        <span v-if="requirement.days !== null" class="planning-profile-chip">{{ requirement.days }}天</span>
        <span v-if="preferenceLabel" class="planning-profile-chip">{{ preferenceLabel }}</span>
      </div>
      <div v-if="!choice" class="planning-meta" aria-live="polite">
        <span v-if="completedSteps">已完成 {{ completedSteps }} 项</span>
        <span v-if="completedSteps" aria-hidden="true">·</span>
        <span>{{ error ? '本次生成已停止' : activityHint }}</span>
        <span aria-hidden="true">·</span>
        <span>{{ elapsedText(props.elapsedSeconds) }}</span>
      </div>

      <div v-if="choice" class="choice-grid">
        <button class="choice-card" type="button" :disabled="props.busy" @click="$emit('choose', 'single_city')">
          <span class="choice-index">01</span>
          <strong>一个城市慢慢玩</strong>
          <p>{{ stayText(choice.options.city_stays) }}</p>
          <em>选择单城市方案 →</em>
        </button>
        <button class="choice-card featured" type="button" :disabled="props.busy" @click="$emit('choose', 'multi_city')">
          <span class="choice-index">02</span>
          <strong>多个城市都看看</strong>
          <p>{{ stayText(choice.options.alternative_city_stays) }}</p>
          <em>选择多城市方案 →</em>
        </button>
      </div>

      <ol v-else class="progress-list" aria-live="polite">
        <li v-for="item in displaySteps" :key="item.step" :class="item.status">
          <span class="progress-dot"><i></i></span>
          <span>{{ item.label }}</span>
          <b>{{ statusText(item.status) }}</b>
        </li>
        <li v-if="!steps.length" :class="error ? 'failed' : 'running'">
          <span class="progress-dot"><i></i></span>
          <span>{{ error ? '规划工作流未能启动' : '正在启动规划工作流' }}</span>
          <b>{{ error ? '需要重试' : '进行中' }}</b>
        </li>
      </ol>

      <div v-if="error" class="inline-error" role="alert">
        <span>!</span>
        <p>{{ friendlyError(error) }}</p>
        <div class="inline-error-actions">
          <button class="error-details" type="button" :aria-expanded="failureReasonVisible" @click="failureReasonVisible = !failureReasonVisible">
            {{ failureReasonVisible ? '收起失败原因' : '查看失败原因' }}
          </button>
          <button class="retry" type="button" :disabled="props.busy" @click="$emit('retry')">{{ props.busy ? '正在结束…' : '重新尝试' }}</button>
          <button type="button" :disabled="props.busy" @click="$emit('back')">回到对话修改</button>
        </div>
        <div v-if="failureReasonVisible" class="failure-detail" role="status">
          <strong>失败原因</strong>
          <p>{{ props.error }}</p>
        </div>
      </div>
    </section>
  </main>
</template>
