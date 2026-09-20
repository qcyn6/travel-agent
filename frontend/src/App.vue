<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import ConversationSidebar from './components/ConversationSidebar.vue'
import PlanningView from './components/PlanningView.vue'
import TravelPlanView from './components/TravelPlanView.vue'
import { usePlanningStore } from './stores/planning'
import type { ConversationMode } from './types'

const store = usePlanningStore()
const sidebarOpen = ref(false)
const isFreshChat = computed(() => store.messages.length === 0 && !store.sending)
const activeConversationTitle = computed(() =>
  store.conversations.find((item) => item.id === store.conversationId)?.title
    || (store.conversationMode === 'general' ? '新的对话' : '新的旅行计划'),
)

onMounted(() => store.initialize())

async function restart() {
  sidebarOpen.value = false
  await store.createConversation()
}

async function openConversation(
  id: string,
  mode: ConversationMode,
  view: 'chat' | 'plan' = 'chat',
) {
  sidebarOpen.value = false
  await store.openConversation(id, mode, view)
}

async function deleteConversation(id: string) {
  const item = store.conversations.find((conversation) => conversation.id === id)
  const title = item?.title === '新的旅行计划'
    ? '新的旅行'
    : item?.title === '新的对话'
      ? '新的对话'
      : item?.title || '这段记录'
  if (!window.confirm(`确定删除“${title}”吗？删除后无法恢复。`)) return
  await store.deleteConversation(id)
}

async function selectHomeMode(mode: ConversationMode) {
  if (
    mode === store.conversationMode
    || store.modeSwitching
    || store.conversationCreating
    || store.conversationLoading
  ) return
  await store.setConversationMode(mode, { createNew: true })
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
      :mode="store.conversationMode"
      :open="sidebarOpen"
      :busy="store.conversationLoading || store.conversationCreating || Boolean(store.conversationDeleting) || store.sending || store.modeSwitching || store.stage === 'planning'"
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
              <small>AI 对话</small>
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
            <button v-if="!isFreshChat" class="chat-reset" type="button" aria-label="新的旅行对话" title="新的旅行对话" :disabled="store.conversationLoading || store.conversationCreating || store.sending || store.modeSwitching" @click="restart">↻</button>
          </div>

          <div class="solo-chat-content" :class="{ 'has-messages': !isFreshChat }">
            <div v-if="isFreshChat" class="solo-welcome">
              <h1>想聊点什么？</h1>
              <p>目的地、旅行灵感，或者其他问题都可以。</p>
              <div class="welcome-mode-switch" role="radiogroup" aria-label="选择对话模式">
                <button
                  type="button"
                  role="radio"
                  :aria-checked="store.conversationMode === 'general'"
                  :class="{ active: store.conversationMode === 'general' }"
                  :disabled="store.modeSwitching || store.conversationCreating || store.conversationLoading"
                  @click="selectHomeMode('general')"
                >
                  普通模式
                </button>
                <button
                  type="button"
                  role="radio"
                  :aria-checked="store.conversationMode === 'planning'"
                  :class="{ active: store.conversationMode === 'planning' }"
                  :disabled="store.modeSwitching || store.conversationCreating || store.conversationLoading"
                  @click="selectHomeMode('planning')"
                >
                  规划模式
                </button>
              </div>
            </div>
            <ChatPanel
              :messages="store.messages"
              :suggestions="store.suggestions"
              :sending="store.sending"
              :mode-switching="store.modeSwitching"
              :mode="store.conversationMode"
              :empty="isFreshChat"
              @send="store.sendMessage"
              @confirm="store.confirmAndPlan"
            />
          </div>
          <div v-if="store.error" class="global-error">{{ store.error }}</div>
        </main>
      </template>

      <PlanningView
        v-else-if="store.stage === 'planning'"
        :steps="store.steps"
        :choice="store.destinationChoice"
        :error="store.error"
        :elapsed-seconds="store.planningElapsedSeconds"
        :busy="store.planningRequestActive"
        :requirement="store.requirement"
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
