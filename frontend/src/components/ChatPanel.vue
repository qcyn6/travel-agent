<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { ChatMessage } from '../types'

const props = defineProps<{
  messages: ChatMessage[]
  suggestions: string[]
  sending: boolean
}>()
const emit = defineEmits<{
  send: [text: string]
  confirm: []
}>()

const input = ref('')
const messageList = ref<HTMLElement | null>(null)

function submit(text = input.value) {
  const clean = text.trim()
  if (!clean || props.sending) return
  if (clean === '查看并确认旅行需求') {
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
    messageList.value?.scrollTo({ top: messageList.value.scrollHeight, behavior: 'smooth' })
  },
)
</script>

<template>
  <section class="chat-panel glass-panel">
    <div class="panel-heading">
      <div>
        <span class="eyebrow">CONVERSATION</span>
        <h2>和旅行灵感聊一聊</h2>
      </div>
      <span class="online-pill"><i></i> AI 在线</span>
    </div>

    <div ref="messageList" class="message-list" aria-live="polite">
      <article
        v-for="(message, index) in messages"
        :key="message.id || index"
        class="message-row"
        :class="message.role"
      >
        <div v-if="message.role === 'assistant'" class="avatar">山</div>
        <div class="message-bubble">
          {{ message.content }}<span v-if="message.streaming" class="typing-caret"></span>
        </div>
      </article>
      <article v-if="sending && !messages.at(-1)?.streaming" class="message-row assistant">
        <div class="avatar">山</div>
        <div class="message-bubble typing"><i></i><i></i><i></i></div>
      </article>
    </div>

    <div v-if="suggestions.length" class="suggestions">
      <button v-for="item in suggestions" :key="item" type="button" @click="submit(item)">
        {{ item }}
      </button>
    </div>

    <div class="chat-composer">
      <textarea
        v-model="input"
        rows="2"
        :disabled="sending"
        placeholder="例如：云南玩 5 天，喜欢自然、古城和摄影……"
        aria-label="输入旅行需求"
        @keydown="onKeydown"
      ></textarea>
      <button class="send-button" type="button" :disabled="!input.trim() || sending" @click="submit()">
        <span>{{ sending ? '思考中' : '发送' }}</span>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 4 16 8-16 8 3-8-3-8Zm3.8 7h7.7L6.8 6.7 7.8 11Zm0 2-1 4.3 8.7-4.3H7.8Z"/></svg>
      </button>
    </div>
    <p class="composer-tip">Enter 发送 · Shift + Enter 换行</p>
  </section>
</template>
