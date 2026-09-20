<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AppStage, ConversationMode, ConversationSummary } from '../types'

const props = defineProps<{
  conversations: ConversationSummary[]
  activeId: string
  stage: AppStage
  hasPlan: boolean
  mode: ConversationMode
  open?: boolean
  busy?: boolean
  deletingId?: string | null
}>()

const emit = defineEmits<{
  newConversation: []
  select: [id: string, mode: ConversationMode, view: 'chat' | 'plan']
  delete: [id: string]
  showChat: []
  showPlan: []
  close: []
}>()

const collapsedLimit = 10
type SidebarRecordType = ConversationMode | 'plan'

const recordType = ref<SidebarRecordType>('general')
const showAll = ref(false)
const openMenuId = ref<string | null>(null)

const generalRecords = computed(() => props.conversations.filter((item) => item.mode === 'general'))
const travelConversationRecords = computed(() => props.conversations.filter(
  (item) => item.mode === 'planning',
))
const planRecords = computed(() => props.conversations.filter(
  (item) => item.mode === 'planning' && item.status === 'completed',
))
const filteredRecords = computed(() => {
  if (recordType.value === 'general') return generalRecords.value
  if (recordType.value === 'planning') return travelConversationRecords.value
  return planRecords.value
})

const visibleRecords = computed(() => {
  if (showAll.value) return filteredRecords.value
  return filteredRecords.value.filter((item, index) => index < collapsedLimit || item.id === props.activeId)
})

const groupedConversations = computed(() => {
  const groups = new Map<string, ConversationSummary[]>()
  for (const item of visibleRecords.value) {
    const label = dayGroup(item.updated_at)
    const group = groups.get(label) || []
    group.push(item)
    groups.set(label, group)
  }
  return [...groups.entries()].map(([label, items]) => ({ label, items }))
})

const hasOverflow = computed(() => filteredRecords.value.length > collapsedLimit)
const hiddenCount = computed(() => Math.max(0, filteredRecords.value.length - collapsedLimit))
const emptyLabel = computed(() => {
  if (recordType.value === 'general') return '还没有保存的普通对话'
  if (recordType.value === 'planning') return '还没有旅行对话'
  return '还没有生成的旅游方案'
})

watch(
  () => [
    props.conversations.find((item) => item.id === props.activeId)?.mode,
    props.mode,
    props.stage,
  ] as const,
  ([activeMode, mode, stage]) => {
    if (stage === 'plan') {
      recordType.value = 'plan'
    } else {
      recordType.value = activeMode || mode
    }
    showAll.value = false
    openMenuId.value = null
  },
  { immediate: true },
)

function dayGroup(value: string) {
  const timestamp = new Date(value).getTime()
  if (Number.isNaN(timestamp)) return '更早'
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const date = new Date(timestamp)
  date.setHours(0, 0, 0, 0)
  const days = Math.floor((today.getTime() - date.getTime()) / 86_400_000)
  if (days <= 0) return '今天'
  if (days === 1) return '昨天'
  return '更早'
}

function displayTitle(item: ConversationSummary) {
  if (item.title === '新的对话') return '新的对话'
  if (item.title === '新的旅行计划') return '新的旅行'
  if (item.status !== 'completed' && item.title.endsWith('旅行计划')) {
    return item.title.replace(/[省市]?旅行计划$/, '之旅')
  }
  return item.title
}

function statusLabel(status: ConversationSummary['status']) {
  return {
    draft: '',
    active: '',
    confirmed: '待规划',
    planning: '生成中',
    completed: '已生成',
  }[status]
}

function relativeTime(value: string) {
  const timestamp = new Date(value).getTime()
  if (Number.isNaN(timestamp)) return ''
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60000))
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes}分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}小时前`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}天前`
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' }).format(timestamp)
}

function metadataLabel(item: ConversationSummary) {
  const status = statusLabel(item.status)
  const time = relativeTime(item.updated_at)
  return [status, time].filter(Boolean).join(' · ')
}

function selectConversation(id: string) {
  openMenuId.value = null
  const item = props.conversations.find((conversation) => conversation.id === id)
  if (!item) return
  const view = recordType.value === 'plan' && item.mode === 'planning' && item.status === 'completed'
    ? 'plan'
    : 'chat'
  emit('select', id, item.mode, view)
}

function toggleMenu(id: string) {
  openMenuId.value = openMenuId.value === id ? null : id
}

function requestDelete(id: string) {
  openMenuId.value = null
  emit('delete', id)
}

function selectRecordType(next: SidebarRecordType) {
  recordType.value = next
  showAll.value = false
  openMenuId.value = null
}
</script>

<template>
  <aside class="conversation-sidebar" :class="{ 'is-open': open }" aria-label="旅行对话侧边栏">
    <div class="sidebar-header">
      <div class="sidebar-brand">
        <span class="sidebar-brand-mark">山</span>
        <span class="sidebar-brand-copy"><strong>山海拾光</strong><small>AI TRAVEL PLANNER</small></span>
      </div>
      <button class="sidebar-close" type="button" aria-label="关闭侧边栏" @click="$emit('close')">×</button>
    </div>

    <button class="sidebar-new" type="button" :disabled="busy" @click="$emit('newConversation')">
      <span class="sidebar-new-icon">＋</span>
      <span>{{ mode === 'general' ? '新的对话' : '新的旅行' }}</span>
    </button>

    <nav class="sidebar-navigation" aria-label="项目页面">
      <button
        class="sidebar-nav-item"
        :class="{ active: stage === 'chat' }"
        type="button"
        :disabled="busy"
        :aria-current="stage === 'chat' ? 'page' : undefined"
        @click="emit('showChat')"
      >
        <span class="sidebar-nav-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M5 4.5A2.5 2.5 0 0 1 7.5 2h9A2.5 2.5 0 0 1 19 4.5v8a2.5 2.5 0 0 1-2.5 2.5H12l-4.5 4v-4H7.5A2.5 2.5 0 0 1 5 12.5v-8ZM7.5 4a.8.8 0 0 0-.8.8v7a.8.8 0 0 0 .8.8h1.7v2l2.2-2h5.1a.8.8 0 0 0 .8-.8v-7a.8.8 0 0 0-.8-.8h-9Z"/></svg>
        </span>
        <span>{{ mode === 'general' ? '普通对话' : '旅行对话' }}</span>
      </button>
      <button
        v-if="hasPlan"
        class="sidebar-nav-item"
        :class="{ active: stage === 'plan' }"
        type="button"
        :disabled="busy"
        :aria-current="stage === 'plan' ? 'page' : undefined"
        @click="emit('showPlan')"
      >
        <span class="sidebar-nav-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M5 3.5h14A1.5 1.5 0 0 1 20.5 5v14a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19V5A1.5 1.5 0 0 1 5 3.5Zm0 2v12.8a.2.2 0 0 0 .2.2H18.5V5.5H5Zm2.5 2h8v1.7h-8V7.5Zm0 3.5h8v1.7h-8V11Zm0 3.5h5v1.7h-5v-1.7Z"/></svg>
        </span>
        <span class="sidebar-nav-copy"><strong>旅行计划</strong><small>{{ hasPlan ? '已生成，可查看' : '完成确认后可用' }}</small></span>
      </button>
    </nav>

    <nav class="sidebar-history" aria-label="历史记录">
      <div class="sidebar-section-heading">
        <span>历史记录</span>
        <span v-if="filteredRecords.length" class="sidebar-count">{{ filteredRecords.length }}</span>
      </div>

      <div class="history-filter" role="tablist" aria-label="历史记录类型">
        <button
          class="history-filter-button"
          :class="{ active: recordType === 'general' }"
          type="button"
          role="tab"
          :aria-selected="recordType === 'general'"
          @click="selectRecordType('general')"
        >
          <span>普通对话</span><small>{{ generalRecords.length }}</small>
        </button>
        <button
          class="history-filter-button"
          :class="{ active: recordType === 'planning' }"
          type="button"
          role="tab"
          :aria-selected="recordType === 'planning'"
          @click="selectRecordType('planning')"
        >
          <span>旅行对话</span><small>{{ travelConversationRecords.length }}</small>
        </button>
        <button
          class="history-filter-button"
          :class="{ active: recordType === 'plan' }"
          type="button"
          role="tab"
          :aria-selected="recordType === 'plan'"
          @click="selectRecordType('plan')"
        >
          <span>旅游方案</span><small>{{ planRecords.length }}</small>
        </button>
      </div>

      <p v-if="!filteredRecords.length" class="sidebar-empty">{{ emptyLabel }}</p>
      <div
        v-for="group in groupedConversations"
        :key="group.label"
        class="history-group"
      >
        <div class="history-group-heading">{{ group.label }}</div>
        <div
          v-for="item in group.items"
          :key="item.id"
          class="conversation-item"
          :class="{ active: item.id === activeId, 'menu-open': openMenuId === item.id }"
        >
          <button
            class="conversation-item-main"
            type="button"
            :disabled="busy"
            :aria-current="item.id === activeId ? 'page' : undefined"
            @click="selectConversation(item.id)"
          >
            <span class="conversation-item-icon" aria-hidden="true">
              <svg v-if="item.status === 'completed'" viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6V3Zm2 2v14h9V8h-3V5H8Zm2 6h5v1.7h-5V11Zm0 3.5h5v1.7h-5v-1.7Z"/></svg>
              <svg v-else viewBox="0 0 24 24"><path d="M5 5.5A2.5 2.5 0 0 1 7.5 3h9A2.5 2.5 0 0 1 19 5.5v7a2.5 2.5 0 0 1-2.5 2.5H11l-4.7 4v-4.1A2.5 2.5 0 0 1 4 12.5v-7A2.5 2.5 0 0 1 5 5.5Zm2.5-.8a.8.8 0 0 0-.8.8v7a.8.8 0 0 0 .8.8h.9v1.9l2.2-1.9h5.9a.8.8 0 0 0 .8-.8v-7a.8.8 0 0 0-.8-.8h-9Z"/></svg>
            </span>
            <span class="conversation-item-copy">
              <strong>{{ displayTitle(item) }}</strong>
              <small>{{ metadataLabel(item) }}</small>
            </span>
            <span v-if="item.id === activeId" class="conversation-item-dot" aria-hidden="true"></span>
          </button>
          <button
            class="conversation-menu"
            type="button"
            :disabled="busy"
            :aria-label="`打开${displayTitle(item)}的操作菜单`"
            :aria-expanded="openMenuId === item.id"
            @click.stop="toggleMenu(item.id)"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>
          </button>
          <div v-if="openMenuId === item.id" class="conversation-menu-popover" @click.stop>
            <button
              class="conversation-menu-delete"
              type="button"
              :disabled="busy || deletingId === item.id"
              @click="requestDelete(item.id)"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 7h8l-.6 12H8.6L8 7Zm2-3h4l1 2H9l1-2Zm-4 2h12v2H6V6Zm3 5h2v5H9v-5Zm4 0h2v5h-2v-5Z"/></svg>
              <span>删除记录</span>
            </button>
          </div>
        </div>
      </div>
      <button v-if="hasOverflow" class="history-more" type="button" @click="showAll = !showAll; openMenuId = null">
        {{ showAll ? '收起记录' : `查看全部（${hiddenCount}）` }}
      </button>
    </nav>
  </aside>
</template>
