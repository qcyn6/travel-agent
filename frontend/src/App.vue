<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import ConversationSidebar from './components/ConversationSidebar.vue'
import PlanningView from './components/PlanningView.vue'
import RequirementModal from './components/RequirementModal.vue'
import TravelPlanView from './components/TravelPlanView.vue'
import { usePlanningStore } from './stores/planning'
import type { RequirementDraft } from './types'

const store = usePlanningStore()
const modalOpen = ref(false)
const saving = ref(false)
const sidebarOpen = ref(false)
const isFreshChat = computed(() => store.messages.length === 0 && !store.sending)
const activeConversationTitle = computed(() =>
  store.conversations.find((item) => item.id === store.conversationId)?.title || '新的旅行计划',
)

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
  sidebarOpen.value = false
  modalOpen.value = false
  sessionStorage.removeItem('travel_conversation_id')
  await store.createConversation()
}

async function openConversation(id: string, view: 'chat' | 'plan' = 'chat') {
  sidebarOpen.value = false
  await store.openConversation(id, view)
}

async function deleteConversation(id: string) {
  const item = store.conversations.find((conversation) => conversation.id === id)
  const title = item?.title === '新的旅行计划' ? '新的旅行' : item?.title || '这段旅行对话'
  if (!window.confirm(`确定删除“${title}”吗？删除后无法恢复。`)) return
  await store.deleteConversation(id)
}
</script>

<template>
  <div class="app-shell">
    <ConversationSidebar
      v-if="!store.initializing"
      :conversations="store.conversations"
      :active-id="store.conversationId"
      :stage="store.stage"
      :has-plan="Boolean(store.plan)"
      :open="sidebarOpen"
      :busy="store.conversationLoading || Boolean(store.conversationDeleting) || store.sending || store.stage === 'planning'"
      :deleting-id="store.conversationDeleting"
      @new-conversation="restart"
      @select="openConversation"
      @delete="deleteConversation"
      @show-chat="store.backToChat"
      @show-plan="store.showPlan"
      @close="sidebarOpen = false"
    />
    <div v-if="sidebarOpen" class="sidebar-backdrop" aria-hidden="true" @click="sidebarOpen = false"></div>
    <button
      v-if="!store.initializing && !sidebarOpen"
      class="mobile-sidebar-toggle"
      type="button"
      aria-label="打开旅行对话侧边栏"
      @click="sidebarOpen = true"
    >
      <span></span><span></span><span></span>
    </button>

    <div class="app-main">
      <div v-if="store.initializing" class="startup-screen"><span></span><p>正在准备你的旅行空间…</p></div>

      <template v-else-if="store.stage === 'chat'">
        <main class="solo-chat-screen">
          <div class="chat-toolbar">
            <div class="chat-context">
              <strong>{{ activeConversationTitle === '新的旅行计划' ? '新的旅行' : activeConversationTitle }}</strong>
              <small>旅行对话</small>
            </div>
            <button
              v-if="store.plan"
              class="chat-plan-link"
              type="button"
              @click="store.showPlan"
            >
              查看旅行计划
              <span aria-hidden="true">→</span>
            </button>
            <button v-if="!isFreshChat" class="chat-reset" type="button" aria-label="新的旅行对话" title="新的旅行对话" @click="restart">↻</button>
          </div>

          <div class="solo-chat-content" :class="{ 'has-messages': !isFreshChat }">
            <div v-if="isFreshChat" class="solo-welcome">
              <h1>你今天想去哪里？</h1>
            </div>
            <ChatPanel
              :messages="store.messages"
              :suggestions="store.suggestions"
              :sending="store.sending"
              :empty="isFreshChat"
              @send="store.sendMessage"
              @confirm="modalOpen = true"
            />
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
        :elapsed-seconds="store.planningElapsedSeconds"
        :busy="store.planningRequestActive"
        @choose="store.chooseScope"
        @retry="store.retryPlanning"
        @back="store.backToChat"
      />

      <TravelPlanView
        v-else-if="store.plan"
        :result="store.plan"
        @restart="restart"
        @revise="store.backToChat"
      />
    </div>
  </div>
</template>
