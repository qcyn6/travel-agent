<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { ChatMessage } from '../types'

const props = defineProps<{
  messages: ChatMessage[]
  suggestions: string[]
  sending: boolean
  empty?: boolean
}>()
const emit = defineEmits<{
  send: [text: string]
  confirm: []
}>()

const input = ref('')
const inputElement = ref<HTMLTextAreaElement | null>(null)
const messageList = ref<HTMLElement | null>(null)

function submit(text = input.value) {
  const clean = text.trim()
  if (!clean || props.sending) return
  if (clean === '确认需求并生成计划' || clean === '查看并确认旅行需求') {
    emit('confirm')
  } else {
    emit('send', clean)
  }
  input.value = ''
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    submit()
  }
}

watch(
  () => props.messages.map((item) => item.content).join('|'),
  async () => {
    await nextTick()
    // 流式回复会频繁触发更新，直接定位到底部，避免连续平滑动画造成滚动滞后。
    messageList.value?.scrollTo({ top: messageList.value.scrollHeight, behavior: 'auto' })
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
    <div v-if="!empty" ref="messageList" class="message-list" aria-live="polite">
      <article
        v-for="(message, index) in messages"
        :key="message.id || index"
        class="message-row"
        :class="message.role"
      >
        <div v-if="message.role === 'assistant'" class="avatar">山</div>
        <div class="message-bubble">
          <template v-if="message.streaming && !message.content"><span class="streaming-label">正在生成回复</span><span class="typing-dots"><i></i><i></i><i></i></span></template>
          <template v-else>{{ message.content }}<span v-if="message.streaming" class="typing-caret"></span></template>
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
        :class="{ 'suggestion-primary': item === '确认需求并生成计划' }"
        :disabled="sending"
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
        placeholder="例如：云南玩 5 天，喜欢自然、古城和摄影……"
        aria-label="输入旅行需求"
        @keydown="onKeydown"
      ></textarea>
      <button class="send-button" type="button" :disabled="!input.trim() || sending" @mousedown.prevent @click="submit()" aria-label="发送">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 4 16 8-16 8-3-8 3-8Zm3.8 7h7.7L6.8 6.7 7.8 11Zm0 2-1 4.3 8.7-4.3H7.8Z"/></svg>
      </button>
    </div>
  </section>
</template>
