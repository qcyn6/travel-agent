<script setup lang="ts">
import type { DestinationChoice, PlanningStep } from '../types'

defineProps<{
  steps: PlanningStep[]
  choice: DestinationChoice | null
  error: string
}>()
defineEmits<{
  choose: [scope: 'single_city' | 'multi_city']
  back: []
}>()

function stayText(stays: { city: string; days: number }[] | null) {
  return stays?.map((item) => `${item.city} ${item.days}天`).join(' · ') || ''
}
</script>

<template>
  <main class="planning-screen">
    <section class="planning-card glass-panel">
      <div v-if="!choice" class="compass-loader" aria-hidden="true">
        <span></span><i></i>
      </div>
      <span class="eyebrow">CRAFTING YOUR JOURNEY</span>
      <h1>{{ choice ? '选择更适合你的旅行方式' : '正在为你绘制旅程' }}</h1>
      <p v-if="!choice">我们正在从真实景点资料中，寻找与你偏好最契合的路线。</p>

      <div v-if="choice" class="choice-grid">
        <button class="choice-card" type="button" @click="$emit('choose', 'single_city')">
          <span class="choice-index">01</span>
          <strong>一个城市慢慢玩</strong>
          <p>{{ stayText(choice.options.city_stays) }}</p>
          <em>选择单城市方案 →</em>
        </button>
        <button class="choice-card featured" type="button" @click="$emit('choose', 'multi_city')">
          <span class="choice-index">02</span>
          <strong>多个城市都看看</strong>
          <p>{{ stayText(choice.options.alternative_city_stays) }}</p>
          <em>选择多城市方案 →</em>
        </button>
      </div>

      <ol v-else class="progress-list">
        <li v-for="item in steps" :key="item.step" :class="item.status">
          <span class="progress-dot"><i></i></span>
          <span>{{ item.label }}</span>
          <b>{{ item.status === 'completed' ? '完成' : '进行中' }}</b>
        </li>
        <li v-if="!steps.length" class="running">
          <span class="progress-dot"><i></i></span><span>正在启动规划工作流</span><b>进行中</b>
        </li>
      </ol>

      <div v-if="error" class="inline-error">
        <span>!</span><p>{{ error }}</p><button type="button" @click="$emit('back')">返回修改需求</button>
      </div>
    </section>
  </main>
</template>
