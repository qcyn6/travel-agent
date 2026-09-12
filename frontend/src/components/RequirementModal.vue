<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import type { RequirementDraft, TravelScope } from '../types'

const props = defineProps<{ open: boolean; requirement: RequirementDraft; saving: boolean }>()
const emit = defineEmits<{
  close: []
  save: [draft: RequirementDraft]
  confirm: [draft: RequirementDraft]
}>()

const form = reactive({
  province: '',
  cities: '',
  days: null as number | null,
  interests: [] as string[],
  preference_text: '',
  travel_scope: 'auto' as TravelScope,
})
const interestOptions = ['自然', '历史文化', '古城', '古镇', '摄影', '人文', '美食', '休闲']

watch(
  () => [props.open, props.requirement] as const,
  () => {
    if (!props.open) return
    form.province = props.requirement.province || ''
    form.cities = props.requirement.cities.join('、')
    form.days = props.requirement.days
    form.interests = [...props.requirement.interests]
    form.preference_text = props.requirement.preference_text || ''
    form.travel_scope = props.requirement.travel_scope
  },
  { immediate: true, deep: true },
)

const draft = computed<RequirementDraft>(() => ({
  province: form.province.trim() || null,
  cities: form.cities.split(/[、,，\s]+/).map((item) => item.trim()).filter(Boolean),
  days: form.days || null,
  interests: [...form.interests],
  preference_text: form.preference_text.trim() || null,
  travel_scope: form.travel_scope,
}))
const ready = computed(() =>
  Boolean((draft.value.province || draft.value.cities.length) && draft.value.days && (draft.value.interests.length || draft.value.preference_text)),
)

function toggleInterest(item: string) {
  const index = form.interests.indexOf(item)
  if (index >= 0) form.interests.splice(index, 1)
  else form.interests.push(item)
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="modal-backdrop" @click.self="$emit('close')">
      <section class="requirement-modal" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
        <button class="modal-close" type="button" aria-label="关闭" @click="$emit('close')">×</button>
        <span class="eyebrow">CONFIRM YOUR JOURNEY</span>
        <h2 id="confirm-title">确认旅行需求</h2>
        <p>你可以直接修改 AI 整理的内容，确认后再开始正式规划。</p>

        <div class="form-grid">
          <label><span>省份</span><input v-model="form.province" placeholder="例如：云南省" /></label>
          <label><span>城市（可选）</span><input v-model="form.cities" placeholder="多个城市用顿号分隔" /></label>
          <label><span>旅行天数</span><input v-model.number="form.days" type="number" min="1" max="30" placeholder="5" /></label>
          <label>
            <span>城市模式</span>
            <select v-model="form.travel_scope">
              <option value="auto">由 AI 推荐</option>
              <option value="single_city">单城市深度游</option>
              <option value="multi_city">同省多城市</option>
            </select>
          </label>
        </div>

        <fieldset>
          <legend>兴趣偏好</legend>
          <div class="interest-options">
            <button
              v-for="item in interestOptions"
              :key="item"
              type="button"
              :class="{ selected: form.interests.includes(item) }"
              @click="toggleInterest(item)"
            >{{ item }}</button>
          </div>
        </fieldset>

        <label class="wide-field">
          <span>旅行风格与其他偏好</span>
          <textarea v-model="form.preference_text" rows="3" placeholder="例如：希望节奏轻松，适合拍照，不想太赶"></textarea>
        </label>

        <div class="modal-actions">
          <button class="secondary-button" type="button" :disabled="saving" @click="$emit('save', draft)">保存并继续聊</button>
          <button class="primary-button" type="button" :disabled="!ready || saving" @click="$emit('confirm', draft)">
            {{ saving ? '正在确认…' : '确认并开始规划' }}
          </button>
        </div>
        <p v-if="!ready" class="form-warning">请至少填写目的地、旅行天数以及一项兴趣或偏好。</p>
      </section>
    </div>
  </Teleport>
</template>
