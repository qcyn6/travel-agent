import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ApiError, conversationApi, streamEvents } from '../api'
import type {
  AppStage,
  ChatMessage,
  DestinationChoice,
  PlanningStep,
  RequirementDraft,
  TravelPlanResponse,
  TravelScope,
} from '../types'

const emptyRequirement = (): RequirementDraft => ({
  province: null,
  cities: [],
  days: null,
  interests: [],
  preference_text: null,
  travel_scope: 'auto',
  start_date: null,
  transport_mode: 'auto',
})

const welcome: ChatMessage = {
  role: 'assistant',
  content: '你好，我是你的 AI 旅行规划师。告诉我你想去哪里，或者描述一下理想中的旅行吧。',
}

export const usePlanningStore = defineStore('planning', () => {
  const conversationId = ref('')
  const messages = ref<ChatMessage[]>([])
  const requirement = ref<RequirementDraft>(emptyRequirement())
  const requirementReady = ref(false)
  const suggestions = ref(['云南省', '浙江省', '陕西省'])
  const stage = ref<AppStage>('chat')
  const sending = ref(false)
  const initializing = ref(true)
  const error = ref('')
  const steps = ref<PlanningStep[]>([])
  const plan = ref<TravelPlanResponse | null>(null)
  const destinationChoice = ref<DestinationChoice | null>(null)

  const hasConversation = computed(() => Boolean(conversationId.value))

  async function initialize() {
    initializing.value = true
    error.value = ''
    const saved = sessionStorage.getItem('travel_conversation_id')
    if (saved) {
      try {
        const conversation = await conversationApi.get(saved)
        conversationId.value = conversation.id
        requirement.value = conversation.requirement
        requirementReady.value = conversation.requirement_ready
        messages.value = conversation.messages.length ? conversation.messages : [welcome]
        suggestions.value = conversation.requirement_ready
          ? ['查看并确认旅行需求', '我还想补充']
          : suggestions.value
        const savedPlan = sessionStorage.getItem(`travel_plan_${conversation.id}`)
        if (conversation.status === 'completed' && savedPlan) {
          plan.value = JSON.parse(savedPlan)
          stage.value = 'plan'
        }
        initializing.value = false
        return
      } catch {
        sessionStorage.removeItem('travel_conversation_id')
      }
    }
    await createConversation()
    initializing.value = false
  }

  async function createConversation() {
    const created = await conversationApi.create()
    conversationId.value = created.id
    sessionStorage.setItem('travel_conversation_id', created.id)
    for (const key of Object.keys(sessionStorage)) {
      if (key.startsWith('travel_plan_')) sessionStorage.removeItem(key)
    }
    messages.value = [welcome]
    requirement.value = created.requirement
    requirementReady.value = false
    suggestions.value = ['云南省', '浙江省', '陕西省']
    stage.value = 'chat'
    steps.value = []
    plan.value = null
    sessionStorage.removeItem(`travel_plan_${conversationId.value}`)
    destinationChoice.value = null
    error.value = ''
  }

  async function sendMessage(text: string) {
    const clean = text.trim()
    if (!clean || sending.value || !conversationId.value) return
    sending.value = true
    error.value = ''
    messages.value.push({ role: 'user', content: clean })
    const assistant: ChatMessage = { role: 'assistant', content: '', streaming: true }
    messages.value.push(assistant)
    let renderChain = Promise.resolve()
    try {
      await streamEvents(
        `/conversations/${conversationId.value}/messages`,
        { message: clean },
        (event, data) => {
          if (event === 'requirement_updated') requirement.value = data
          if (event === 'suggestions') suggestions.value = data.options || []
          if (event === 'message_delta') {
            const delta = String(data.text || '')
            // Preserve the server's real delta order while rendering each chunk visibly.
            renderChain = renderChain.then(
              () =>
                new Promise<void>((resolve) => {
                  let index = 0
                  const renderNext = () => {
                    if (index >= delta.length) {
                      resolve()
                      return
                    }
                    assistant.content += delta[index++]
                    window.setTimeout(renderNext, 18)
                  }
                  renderNext()
                }),
            )
          }
          if (event === 'message_done') {
            requirementReady.value = Boolean(data.requirement_ready)
          }
          if (event === 'requirement_ready') requirementReady.value = Boolean(data.ready)
        },
      )
      await renderChain
      assistant.streaming = false
    } catch (caught) {
      assistant.streaming = false
      if (!assistant.content) messages.value.pop()
      error.value = readableError(caught)
    } finally {
      sending.value = false
    }
  }

  async function saveRequirement(draft: RequirementDraft) {
    error.value = ''
    const conversation = await conversationApi.updateRequirement(conversationId.value, draft)
    requirement.value = conversation.requirement
    requirementReady.value = conversation.requirement_ready
  }

  async function confirmAndPlan(draft: RequirementDraft) {
    try {
      await saveRequirement(draft)
      await conversationApi.confirm(conversationId.value)
      await startPlanning()
    } catch (caught) {
      error.value = readableError(caught)
      throw caught
    }
  }

  async function startPlanning() {
    stage.value = 'planning'
    error.value = ''
    steps.value = []
    plan.value = null
    destinationChoice.value = null
    try {
      await streamEvents(`/conversations/${conversationId.value}/plan`, undefined, (event, data) => {
        if (event === 'planning_progress') upsertStep(data)
        if (event === 'plan_completed') {
          plan.value = data
          sessionStorage.setItem(`travel_plan_${conversationId.value}`, JSON.stringify(data))
          stage.value = 'plan'
        }
        if (event === 'destination_choice_required') destinationChoice.value = data
        if (event === 'planning_error') error.value = data.message
      })
    } catch (caught) {
      error.value = readableError(caught)
    }
  }

  async function chooseScope(scope: Exclude<TravelScope, 'auto'>) {
    try {
      const conversation = await conversationApi.updateRequirement(conversationId.value, { travel_scope: scope })
      requirement.value = conversation.requirement
      await conversationApi.confirm(conversationId.value)
      await startPlanning()
    } catch (caught) {
      error.value = readableError(caught)
    }
  }

  function upsertStep(next: PlanningStep) {
    const index = steps.value.findIndex((item) => item.step === next.step)
    if (index >= 0) steps.value[index] = next
    else steps.value.push(next)
  }

  function backToChat() {
    stage.value = 'chat'
    destinationChoice.value = null
    error.value = ''
  }

  function readableError(caught: unknown) {
    if (caught instanceof ApiError || caught instanceof Error) return caught.message
    return '操作失败，请稍后重试'
  }

  return {
    conversationId,
    messages,
    requirement,
    requirementReady,
    suggestions,
    stage,
    sending,
    initializing,
    error,
    steps,
    plan,
    destinationChoice,
    hasConversation,
    initialize,
    createConversation,
    sendMessage,
    saveRequirement,
    confirmAndPlan,
    chooseScope,
    backToChat,
  }
})
