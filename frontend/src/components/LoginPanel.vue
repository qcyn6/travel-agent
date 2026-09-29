<script setup lang="ts">
import { ref } from 'vue'
import { ApiError, authApi, type AuthUser } from '../api'
import loginBackgroundUrl from '../../../data/images/login.png'

const emit = defineEmits<{ authenticated: [user: AuthUser] }>()
const registerMode = ref(false)
const username = ref('')
const password = ref('')
const displayName = ref('')
const busy = ref(false)
const error = ref('')

function readableError(caught: unknown) {
  return caught instanceof ApiError ? caught.message : '请求失败，请稍后再试'
}

async function submit() {
  if (!username.value.trim() || !password.value) {
    error.value = '请填写用户名和密码'
    return
  }
  busy.value = true
  error.value = ''
  try {
    const user = registerMode.value
      ? await authApi.register(username.value, password.value, displayName.value)
      : await authApi.login(username.value, password.value)
    emit('authenticated', user)
  } catch (caught) {
    error.value = readableError(caught)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="auth-screen">
    <div class="auth-background" :style="{ backgroundImage: `url(${loginBackgroundUrl})` }" aria-hidden="true"></div>
    <div class="auth-motion" aria-hidden="true">
      <span class="auth-cloud auth-cloud-one"></span>
      <span class="auth-cloud auth-cloud-two"></span>
      <span class="auth-cloud auth-cloud-three"></span>
      <svg class="auth-plane" viewBox="0 0 64 40" role="presentation">
        <path d="M3 19.5 28 16 42 4.5l6 .6-9.5 12.3 16.7 3.9 4.1 3.6-23-2.4L30 36h-5l3-13.8-25-1.2z" />
      </svg>
      <span class="auth-lake-motion auth-lake-motion-one"></span>
      <span class="auth-lake-motion auth-lake-motion-two"></span>
    </div>
    <section class="auth-card glass-panel">
      <div class="sidebar-brand-mark auth-mark">游</div>
      <span class="eyebrow">TRAVEL AGENT</span>
      <h1>{{ registerMode ? '创建你的旅行空间' : '欢迎回来' }}</h1>
      <p class="auth-subtitle">登录后保存对话、旅行方案，并获得连续的规划体验。</p>
      <form class="auth-form" @submit.prevent="submit">
        <label v-if="registerMode">昵称<input v-model="displayName" type="text" maxlength="64" placeholder="怎么称呼你" /></label>
        <label>用户名<input v-model="username" type="text" autocomplete="username" placeholder="请输入用户名" /></label>
        <label>密码<input v-model="password" type="password" autocomplete="current-password" placeholder="至少 6 位" /></label>
        <p v-if="error" class="auth-error">{{ error }}</p>
        <button class="auth-submit" type="submit" :disabled="busy">{{ busy ? '处理中…' : (registerMode ? '注册并登录' : '登录') }}</button>
      </form>
      <button class="auth-switch" type="button" @click="registerMode = !registerMode; error = ''">
        {{ registerMode ? '已有账号？返回登录' : '还没有账号？立即注册' }}
      </button>
    </section>
  </main>
</template>
