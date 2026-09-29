<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  adminApi,
  type AdminConversationSummary,
  type AdminGuide,
  type AdminGuideIndexStatus,
  type AdminGuideSummary,
  type AdminUser,
  ApiError,
} from '../api'

const users = ref<AdminUser[]>([])
const selectedUser = ref<AdminUser | null>(null)
const conversations = ref<AdminConversationSummary[]>([])
const selectedConversation = ref<AdminConversationSummary | null>(null)
const conversationDetail = ref<any>(null)
const guides = ref<AdminGuideSummary[]>([])
const selectedGuide = ref<AdminGuide | null>(null)
const guideContent = ref('')
const guideIndexStatus = ref<AdminGuideIndexStatus | null>(null)
const creatingGuide = ref(false)
const newGuideFilename = ref('')
const newGuideContent = ref('')
const guideFileInput = ref<HTMLInputElement | null>(null)
const importedGuideFilename = ref('')
const busy = ref(false)
const notice = ref('')
const error = ref('')

function readableError(caught: unknown) {
  return caught instanceof ApiError ? caught.message : '操作失败，请稍后再试'
}

async function loadUsers() {
  busy.value = true
  error.value = ''
  try {
    users.value = await adminApi.users()
  } catch (caught) {
    error.value = readableError(caught)
  } finally {
    busy.value = false
  }
}

async function chooseUser(user: AdminUser) {
  selectedUser.value = user
  selectedConversation.value = null
  conversationDetail.value = null
  try {
    conversations.value = await adminApi.userConversations(user.id)
  } catch (caught) {
    error.value = readableError(caught)
  }
}

async function chooseConversation(item: AdminConversationSummary) {
  selectedConversation.value = item
  try {
    conversationDetail.value = await adminApi.conversation(item.id, item.mode)
  } catch (caught) {
    error.value = readableError(caught)
  }
}

async function loadGuides() {
  try {
    guides.value = await adminApi.guides()
    if (!creatingGuide.value && !selectedGuide.value && guides.value.length) {
      await chooseGuide(guides.value[0])
    }
  } catch (caught) {
    error.value = readableError(caught)
  }
}

async function loadGuideIndexStatus() {
  try {
    guideIndexStatus.value = await adminApi.guideIndexStatus()
  } catch (caught) {
    error.value = readableError(caught)
  }
}

function startCreateGuide() {
  creatingGuide.value = true
  selectedGuide.value = null
  newGuideFilename.value = '新知识文档.md'
  newGuideContent.value = '# 新知识文档\n\n'
  importedGuideFilename.value = ''
  if (guideFileInput.value) guideFileInput.value.value = ''
  error.value = ''
  notice.value = ''
}

async function importGuideDocument(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.md')) {
    error.value = '请选择 Markdown 文档（.md 文件）。'
    return
  }

  try {
    const content = (await file.text()).replace(/^\uFEFF/, '')
    if (!content.trim()) {
      error.value = '所选文档没有内容，请选择非空的 Markdown 文档。'
      return
    }
    newGuideFilename.value = file.name
    newGuideContent.value = content
    importedGuideFilename.value = file.name
    error.value = ''
    notice.value = '文档已读取，点击“创建并同步”后会加入旅游知识库。'
  } catch {
    error.value = '读取文档失败，请检查文件编码后重试。'
  }
}

async function cancelCreateGuide() {
  creatingGuide.value = false
  newGuideFilename.value = ''
  newGuideContent.value = ''
  importedGuideFilename.value = ''
  if (guideFileInput.value) guideFileInput.value.value = ''
  selectedGuide.value = null
  await loadGuides()
}

async function refreshAdminData() {
  await Promise.all([loadUsers(), loadGuides(), loadGuideIndexStatus()])
  notice.value = '用户列表和知识库状态已刷新。'
}

async function chooseGuide(guide: AdminGuideSummary) {
  try {
    selectedGuide.value = await adminApi.guide(guide.filename)
    guideContent.value = selectedGuide.value.content
  } catch (caught) {
    error.value = readableError(caught)
  }
}

async function saveGuide() {
  if (!creatingGuide.value && !selectedGuide.value) return
  busy.value = true
  try {
    if (creatingGuide.value) {
      selectedGuide.value = await adminApi.createGuide(newGuideFilename.value.trim(), newGuideContent.value)
      guideContent.value = selectedGuide.value.content
      creatingGuide.value = false
      importedGuideFilename.value = ''
      notice.value = '指南已新增，并已同步到向量库。'
    } else {
      selectedGuide.value = await adminApi.updateGuide(selectedGuide.value!.filename, guideContent.value)
      notice.value = '指南已保存，并已同步到向量库。'
    }
    await loadGuides()
    await loadGuideIndexStatus()
  } catch (caught) {
    error.value = readableError(caught)
  } finally {
    busy.value = false
  }
}

async function deleteGuide() {
  if (!selectedGuide.value || creatingGuide.value) return
  if (!window.confirm(`确定删除“${selectedGuide.value.title}”吗？`)) return
  busy.value = true
  try {
    await adminApi.deleteGuide(selectedGuide.value.filename)
    selectedGuide.value = null
    guideContent.value = ''
    notice.value = '指南已删除，并已同步到向量库。'
    await loadGuides()
    await loadGuideIndexStatus()
  } catch (caught) {
    error.value = readableError(caught)
  } finally {
    busy.value = false
  }
}

async function rebuildGuideIndex() {
  busy.value = true
  try {
    await adminApi.rebuildGuideIndex()
    await loadGuideIndexStatus()
    notice.value = '旅游知识库已同步到向量库。'
  } catch (caught) {
    error.value = readableError(caught)
  } finally {
    busy.value = false
  }
}

async function generatePlan() {
  if (!selectedConversation.value || selectedConversation.value.mode !== 'planning') return
  busy.value = true
  notice.value = '正在生成旅行方案…'
  error.value = ''
  try {
    await adminApi.generatePlan(selectedConversation.value.id, (event, data) => {
      if (event === 'plan_completed') notice.value = '旅行方案已生成完成。'
      if (event === 'error') error.value = data?.message || '方案生成失败'
    })
    conversationDetail.value = await adminApi.conversation(selectedConversation.value.id, 'planning')
  } catch (caught) {
    error.value = readableError(caught)
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  void loadUsers()
  void loadGuides()
  void loadGuideIndexStatus()
})
</script>

<template>
  <main class="admin-screen">
    <div class="admin-heading">
      <div><span class="eyebrow">ADMIN WORKSPACE</span><h1>管理后台</h1><p>查看用户对话、生成方案，维护旅行知识。</p></div>
      <button class="admin-refresh" type="button" :disabled="busy" title="重新读取用户列表、指南列表和向量库状态" @click="refreshAdminData">刷新数据</button>
    </div>
    <p v-if="error" class="auth-error">{{ error }}</p>
    <p v-if="notice" class="admin-notice">{{ notice }}</p>
    <div class="admin-grid">
      <section class="admin-card glass-panel">
        <div class="admin-card-heading"><h2>用户</h2><span>{{ users.length }}</span></div>
        <button v-for="user in users" :key="user.id" class="admin-list-item" :class="{ active: selectedUser?.id === user.id }" type="button" @click="chooseUser(user)">
          <span><strong>{{ user.display_name }}</strong><small>{{ user.username }}</small></span><em>{{ user.role }}</em>
        </button>
        <p v-if="!users.length && !busy" class="admin-empty">暂无用户</p>
      </section>
      <section class="admin-card glass-panel">
        <div class="admin-card-heading"><h2>{{ selectedUser ? selectedUser.display_name + ' 的对话' : '用户对话' }}</h2><span>{{ conversations.length }}</span></div>
        <button v-for="item in conversations" :key="item.mode + '-' + item.id" class="admin-list-item" :class="{ active: selectedConversation?.id === item.id }" type="button" @click="chooseConversation(item)">
          <span><strong>{{ item.title }}</strong><small>{{ item.mode === 'planning' ? '旅行规划' : '普通对话' }} · {{ item.status }}</small></span>
        </button>
        <p v-if="selectedUser && !conversations.length" class="admin-empty">该用户暂无对话</p>
        <p v-if="!selectedUser" class="admin-empty">选择左侧用户查看对话</p>
      </section>
      <section class="admin-card admin-detail glass-panel">
        <div class="admin-card-heading"><h2>对话详情</h2><button v-if="selectedConversation?.mode === 'planning'" class="admin-action" type="button" :disabled="busy" @click="generatePlan">生成方案</button></div>
        <div v-if="conversationDetail?.messages" class="admin-messages">
          <div v-for="message in conversationDetail.messages" :key="message.id" :class="['admin-message', message.role]"><b>{{ message.role === 'user' ? '用户' : 'AI' }}</b><p>{{ message.content }}</p></div>
        </div>
        <p v-else class="admin-empty">选择一条对话查看内容</p>
      </section>
    </div>
    <section class="admin-card admin-knowledge glass-panel">
      <div class="admin-card-heading">
        <div><h2>旅行知识库</h2><small>管理 Markdown 指南 · <span v-if="guideIndexStatus">文档 {{ guideIndexStatus.source_files }} 份 · 向量片段 {{ guideIndexStatus.indexed_documents }}/{{ guideIndexStatus.source_documents }} · {{ guideIndexStatus.in_sync ? '已同步' : '待同步' }}</span></small></div>
        <div class="knowledge-actions"><button v-if="creatingGuide" class="knowledge-button" type="button" :disabled="busy" title="放弃新增并返回指南编辑" @click="cancelCreateGuide">返回编辑</button><button v-else class="knowledge-button" type="button" :disabled="busy" title="把现有指南重新同步到向量库" @click="rebuildGuideIndex">同步向量库</button><button v-if="!creatingGuide" class="knowledge-button" type="button" :disabled="busy" title="新增一份 Markdown 指南" @click="startCreateGuide">新增指南</button><button v-if="selectedGuide && !creatingGuide" class="knowledge-button" type="button" :disabled="busy" title="删除当前选中的指南" @click="deleteGuide">删除指南</button></div>
      </div>
      <div class="knowledge-layout">
        <div class="guide-list"><button v-for="guide in guides" :key="guide.filename" class="admin-list-item" :class="{ active: selectedGuide?.filename === guide.filename }" type="button" @click="chooseGuide(guide)"><span><strong>{{ guide.title }}</strong><small>{{ guide.filename }}</small></span></button></div>
        <div class="guide-editor">
          <template v-if="creatingGuide">
            <div class="guide-import-row">
              <input ref="guideFileInput" class="guide-file-input" type="file" accept=".md,text/markdown" aria-label="选择 Markdown 文档" @change="importGuideDocument" />
              <button class="knowledge-button" type="button" :disabled="busy" @click="guideFileInput?.click()">选择 Markdown 文档</button>
              <small>{{ importedGuideFilename ? `已读取：${importedGuideFilename}` : '也可以直接在下方编写内容' }}</small>
            </div>
            <input v-model="newGuideFilename" class="guide-filename" placeholder="文件名，例如：海边旅行指南.md" />
            <textarea v-model="newGuideContent" spellcheck="false"></textarea>
          </template>
          <textarea v-else-if="selectedGuide" v-model="guideContent" spellcheck="false"></textarea>
          <p v-else class="admin-empty">暂无指南，请点击“新增指南”</p>
          <button v-if="creatingGuide || selectedGuide" class="admin-action" type="button" :disabled="busy" @click="saveGuide">{{ creatingGuide ? '创建并同步' : '保存并同步' }}</button>
        </div>
      </div>
    </section>
  </main>
</template>
