<script setup lang="ts">
import { onMounted, ref } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import PlanningView from './components/PlanningView.vue'
import RequirementCard from './components/RequirementCard.vue'
import RequirementModal from './components/RequirementModal.vue'
import TravelPlanView from './components/TravelPlanView.vue'
import { usePlanningStore } from './stores/planning'
import type { RequirementDraft } from './types'

const store = usePlanningStore()
const modalOpen = ref(false)
const saving = ref(false)

onMounted(() => store.initialize())

async function saveDraft(draft: RequirementDraft) {
  saving.value = true
  try {
    await store.saveRequirement(draft)
    modalOpen.value = false
  } finally {
    saving.value = false
  }
}

async function confirmDraft(draft: RequirementDraft) {
  saving.value = true
  try {
    await store.confirmAndPlan(draft)
    modalOpen.value = false
  } finally {
    saving.value = false
  }
}

async function restart() {
  sessionStorage.removeItem('travel_conversation_id')
  await store.createConversation()
}
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <button class="brand" type="button" @click="store.stage === 'chat' ? undefined : store.backToChat()">
        <span class="brand-mark"><i></i></span>
        <span><strong>山海拾光</strong><small>AI TRAVEL PLANNER</small></span>
      </button>
      <div class="topbar-center">让每一次出发，都更懂你</div>
      <button class="new-plan-button" type="button" :disabled="store.stage === 'planning'" @click="restart">＋ 新的旅行</button>
    </header>

    <div v-if="store.initializing" class="startup-screen"><span></span><p>正在准备你的旅行空间…</p></div>

    <template v-else-if="store.stage === 'chat'">
      <main class="chat-screen">
        <div class="intro-copy">
          <span class="eyebrow">START WITH A THOUGHT</span>
          <h1>从一句想法，抵达<br /><em>属于你的远方</em></h1>
          <p>不必填写复杂表格，像聊天一样告诉我你的旅行期待。</p>
        </div>
        <div class="conversation-layout">
          <ChatPanel
            :messages="store.messages"
            :suggestions="store.suggestions"
            :sending="store.sending"
            @send="store.sendMessage"
            @confirm="modalOpen = true"
          />
          <RequirementCard :requirement="store.requirement" :ready="store.requirementReady" @edit="modalOpen = true" />
        </div>
        <div v-if="store.error" class="global-error">{{ store.error }}</div>
      </main>
      <RequirementModal
        :open="modalOpen"
        :requirement="store.requirement"
        :saving="saving"
        @close="modalOpen = false"
        @save="saveDraft"
        @confirm="confirmDraft"
      />
    </template>

    <PlanningView
      v-else-if="store.stage === 'planning'"
      :steps="store.steps"
      :choice="store.destinationChoice"
      :error="store.error"
      @choose="store.chooseScope"
      @back="store.backToChat"
    />

    <TravelPlanView
      v-else-if="store.plan"
      :result="store.plan"
      @restart="restart"
      @revise="store.backToChat"
    />
  </div>
</template>
