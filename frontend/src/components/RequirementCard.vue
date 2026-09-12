<script setup lang="ts">
import type { RequirementDraft } from '../types'

defineProps<{ requirement: RequirementDraft; ready: boolean }>()
defineEmits<{ edit: [] }>()

const scopeLabel = {
  auto: '由 AI 推荐',
  single_city: '单城市深度游',
  multi_city: '同省多城市',
}
</script>

<template>
  <aside class="requirement-card glass-panel">
    <div class="panel-heading compact">
      <div>
        <span class="eyebrow">TRAVEL PROFILE</span>
        <h2>你的旅行画像</h2>
      </div>
      <span class="sparkle">✦</span>
    </div>
    <p class="profile-caption">旅行画像正在慢慢变清晰</p>

    <div class="profile-fields">
      <div class="profile-field" :class="{ empty: !requirement.province && !requirement.cities.length }">
        <span class="field-icon">⌖</span>
        <div><small>目的地</small><strong>{{ requirement.cities.join('、') || requirement.province || '等待你的灵感' }}</strong></div>
      </div>
      <div class="profile-field" :class="{ empty: !requirement.days }">
        <span class="field-icon">◷</span>
        <div><small>旅行天数</small><strong>{{ requirement.days ? `${requirement.days} 天` : '暂未确定' }}</strong></div>
      </div>
      <div class="profile-field" :class="{ empty: !requirement.interests.length }">
        <span class="field-icon">♡</span>
        <div><small>兴趣偏好</small><strong>{{ requirement.interests.join(' / ') || '继续聊聊你的喜好' }}</strong></div>
      </div>
      <div class="profile-field" :class="{ empty: !requirement.preference_text }">
        <span class="field-icon">≈</span>
        <div><small>旅行风格</small><strong>{{ requirement.preference_text || '暂未补充' }}</strong></div>
      </div>
      <div class="profile-field">
        <span class="field-icon">◇</span>
        <div><small>城市模式</small><strong>{{ scopeLabel[requirement.travel_scope] }}</strong></div>
      </div>
    </div>

    <button class="profile-action" type="button" :class="{ ready }" @click="$emit('edit')">
      {{ ready ? '查看并确认旅行需求' : '查看 / 编辑需求' }}
      <span>→</span>
    </button>
    <p v-if="!ready" class="ready-hint">目的地、天数和旅行偏好明确后即可开始规划</p>
  </aside>
</template>
