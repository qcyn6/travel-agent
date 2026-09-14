<script setup lang="ts">
import { computed } from 'vue'
import type { DestinationChoice, PlanningStep } from '../types'

const props = withDefaults(defineProps<{
  steps: PlanningStep[]
  choice: DestinationChoice | null
  error: string
  elapsedSeconds?: number
  busy?: boolean
}>(), {
  elapsedSeconds: 0,
  busy: false,
})
defineEmits<{
  choose: [scope: 'single_city' | 'multi_city']
  retry: []
  back: []
}>()

const completedSteps = computed(() => props.steps.filter((item) => item.status === 'completed').length)

function elapsedText(seconds: number) {
  const safeSeconds = Math.max(0, Math.floor(seconds || 0))
  if (safeSeconds < 60) return `已用时 ${safeSeconds} 秒`
  const minutes = Math.floor(safeSeconds / 60)
  return `已用时 ${minutes} 分 ${safeSeconds % 60} 秒`
}

function waitHint(seconds: number) {
  return seconds >= 30 ? '资料源响应较慢，仍在继续核验' : '通常需要 10～30 秒'
}

function friendlyError(message: string) {
  const match = message.match(/^(.+?)\s*仅准备好\s*(\d+)\s*条景点知识，目标为\s*(\d+)\s*条$/)
  if (!match) return message
  const [, city, ready, target] = match
  const missing = Math.max(1, Number(target) - Number(ready))
  return `${city}目前只核验到 ${ready} 个可靠景点，还需要 ${missing} 个才能生成完整行程。`
}

function stayText(stays: { city: string; days: number }[] | null) {
  return stays?.map((item) => `${item.city} ${item.days}天`).join(' · ') || ''
}

function statusText(status: PlanningStep['status']) {
  if (status === 'completed') return '完成'
  if (status === 'failed') return '需要重试'
  return '进行中'
}
</script>

<template>
  <main class="planning-screen">
    <section class="planning-card glass-panel">
      <div v-if="!choice && !error" class="compass-loader" aria-hidden="true">
        <span></span><i></i>
      </div>
      <span class="eyebrow">CRAFTING YOUR JOURNEY</span>
      <h1>{{ choice ? '选择更适合你的旅行方式' : error ? '旅程准备遇到问题' : '正在为你绘制旅程' }}</h1>
      <p v-if="!choice">{{ error ? '已保留完成的景点资料，你可以直接重试或返回调整需求。' : '我们正在从真实景点资料中，寻找与你偏好最契合的路线。' }}</p>
      <div v-if="!choice" class="planning-meta" aria-live="polite">
        <span v-if="completedSteps">已完成 {{ completedSteps }} 项</span>
        <span v-if="completedSteps" aria-hidden="true">·</span>
        <span>{{ error ? '本次生成已停止' : waitHint(props.elapsedSeconds) }}</span>
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
        <li v-for="item in steps" :key="item.step" :class="item.status">
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
          <button class="retry" type="button" :disabled="props.busy" @click="$emit('retry')">{{ props.busy ? '正在结束…' : '重新尝试' }}</button>
          <button type="button" :disabled="props.busy" @click="$emit('back')">返回修改需求</button>
        </div>
      </div>
    </section>
  </main>
</template>
