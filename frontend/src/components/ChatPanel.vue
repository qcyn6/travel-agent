<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { ChatMessage, ConversationMode, RouteChatCard, SingleChatToolCard } from '../types'
import GeneralRouteMap from './GeneralRouteMap.vue'
import NearbyPlacesCard from './NearbyPlacesCard.vue'
import WeatherCalendarCard from './WeatherCalendarCard.vue'

const props = defineProps<{
  messages: ChatMessage[]
  suggestions: string[]
  sending: boolean
  modeSwitching?: boolean
  mode: ConversationMode
  empty?: boolean
}>()
const emit = defineEmits<{
  send: [text: string]
  confirm: []
  cancel: []
  cardUpdated: [index: number, card: RouteChatCard]
}>()

const input = ref('')
const inputElement = ref<HTMLTextAreaElement | null>(null)
const messageList = ref<HTMLElement | null>(null)
const followBottom = ref(true)
function onScroll() {
  const list = messageList.value
  if (list) followBottom.value = list.scrollHeight - list.scrollTop - list.clientHeight < 80
}
const generalInputPlaceholder = '例如：杭州明天天气、杭州东站到西湖怎么走，或者介绍一下云南……'
const planningInputPlaceholder = '告诉我想去哪里、玩几天、哪天出发，以及你喜欢怎样的旅行……'
const modePlaceholder = computed(() => props.mode === 'planning' ? planningInputPlaceholder : generalInputPlaceholder)
const inputPlaceholder = ref(modePlaceholder.value)

function cardsForMessage(card: ChatMessage['card']): SingleChatToolCard[] {
  if (!card) return []
  return card.type === 'collection' ? card.cards : [card]
}

const confirmationAvailable = computed(() => Boolean(
  props.mode === 'planning' && props.suggestions.includes('确认生成计划'),
))

function isConfirmationPhrase(text: string) {
  const normalized = text.replace(/[，。！？、,.!?；;：:\s]/g, '')
  return [
    '确认生成计划',
    '确认并生成计划',
    '确认开始规划',
    '开始生成计划',
    '开始规划',
    '就这样生成',
    '确认',
    '可以开始',
    '可以生成',
    '可以了',
    '好的开始吧',
    '好开始吧',
    '没问题开始吧',
    '没问题生成吧',
  ].includes(normalized)
}

function submit(text = input.value) {
  const clean = text.trim()
  if (!clean || props.sending || props.modeSwitching) return
  if (
    confirmationAvailable.value
    && isConfirmationPhrase(clean)
  ) {
    emit('confirm')
  } else {
    emit('send', clean)
  }
  input.value = ''
  inputPlaceholder.value = modePlaceholder.value
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    submit()
  }
}

watch(
  () => props.mode,
  () => { inputPlaceholder.value = modePlaceholder.value },
)

watch(
  () => props.messages.map((item) => `${item.content}|${item.card ? JSON.stringify(item.card) : ''}`).join('|'),
  async () => {
    await nextTick()
    // 流式回复会频繁触发更新，直接定位到底部，避免连续平滑动画造成滚动滞后。
    if (followBottom.value) messageList.value?.scrollTo({ top: messageList.value.scrollHeight, behavior: 'auto' })
  },
)

watch(
  () => props.sending,
  async (active) => {
    if (active) return
    await nextTick()
    window.setTimeout(() => inputElement.value?.focus(), 0)
  },
  { flush: 'post' },
)
</script>

<template>
  <section class="chat-panel" :class="{ 'chat-panel--empty': empty }">
    <div v-if="!empty" ref="messageList" class="message-list" aria-live="polite" @scroll="onScroll">
      <article
        v-for="(message, index) in messages"
        :key="message.id || index"
        class="message-row"
        :class="message.role"
      >
        <div v-if="message.role === 'assistant'" class="avatar">山</div>
        <div class="message-body">
          <div class="message-bubble">
            <template v-if="message.streaming && !message.content"><span class="streaming-label">{{ message.progress || '正在生成回复' }}</span><span class="typing-dots"><i></i><i></i><i></i></span></template>
            <template v-else>{{ message.content }}<span v-if="message.streaming" class="typing-caret"></span></template>
          </div>
          <template v-for="(card, cardIndex) in cardsForMessage(message.card)" :key="`${card.type}-${cardIndex}`">
            <WeatherCalendarCard
              v-if="card.type === 'weather'"
              :weather="card.weather"
            />
            <GeneralRouteMap
              v-else-if="card.type === 'route'"
              :route="card.route"
              :disabled="Boolean(message.streaming)"
              @update:route="emit('cardUpdated', index, { type: 'route', route: $event })"
            />
            <NearbyPlacesCard
              v-else-if="card.type === 'nearby'"
              :nearby="card.nearby"
            />
          </template>
        </div>
      </article>
      <article v-if="sending && !messages.at(-1)?.streaming" class="message-row assistant">
        <div class="avatar">山</div>
        <div class="message-bubble typing"><i></i><i></i><i></i></div>
      </article>
    </div>

    <div v-if="!empty && suggestions.length" class="suggestions">
        <button
          v-for="item in suggestions"
          :key="item"
          type="button"
          :class="{ 'suggestion-primary': mode === 'planning' && item === '确认生成计划' }"
        :disabled="sending || modeSwitching"
        @click="submit(item)"
      >
        {{ item }}
      </button>
    </div>

    <div class="chat-composer">
      <textarea
        ref="inputElement"
        v-model="input"
        :rows="empty ? 1 : 2"
        :placeholder="inputPlaceholder"
        :aria-label="mode === 'general' ? '输入普通对话内容' : '输入旅行信息'"
        @keydown="onKeydown"
      ></textarea>
      <button v-if="sending && mode === 'general'" class="send-button" type="button" @click="emit('cancel')" aria-label="停止生成" title="停止生成">■</button>
      <button v-else class="send-button" type="button" :disabled="!input.trim() || sending || modeSwitching" @mousedown.prevent @click="submit()" aria-label="发送">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 4 16 8-16 8-3-8 3-8Zm3.8 7h7.7L6.8 6.7 7.8 11Zm0 2-1 4.3 8.7-4.3H7.8Z"/></svg>
      </button>
    </div>
  </section>
</template>
