<script setup lang="ts">
import { computed, ref } from 'vue'
import type { TravelPlanResponse } from '../types'

const props = defineProps<{ result: TravelPlanResponse }>()
defineEmits<{ restart: []; revise: [] }>()

const activeDay = ref(1)
const currentDay = computed(() => props.result.plan.itinerary.find((item) => item.day === activeDay.value) || props.result.plan.itinerary[0])
const totalAttractions = computed(() => new Set(props.result.plan.itinerary.flatMap((item) => item.attractions)).size)
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
            <b>{{ currentDay.attractions.length }} 个景点</b>
          </header>
          <p class="day-description">{{ currentDay.description }}</p>
          <ol class="attraction-timeline">
            <li v-for="(attraction, index) in currentDay.attractions" :key="attraction">
              <span class="time-index">{{ String(index + 1).padStart(2, '0') }}</span>
              <div><small>{{ index === 0 ? '上午出发' : index === currentDay.attractions.length - 1 ? '傍晚时分' : '继续探索' }}</small><strong>{{ attraction }}</strong></div>
              <i></i>
            </li>
          </ol>
        </article>

        <aside class="trip-notes">
          <div class="note-card"><span>✦</span><h3>为你推荐</h3><p>这份路线根据你的兴趣和旅行节奏，从已核验的景点知识中个性化生成。</p></div>
          <div class="note-card warm"><span>⌁</span><h3>温馨提醒</h3><p>出发前请再次确认景点开放时间，并结合当天实际天气灵活调整。</p></div>
        </aside>
      </div>

      <button class="restart-link" type="button" @click="$emit('restart')">＋ 开始一段新的旅行计划</button>
    </section>
  </main>
</template>
