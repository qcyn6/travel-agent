import { ref } from 'vue'
import { defineStore } from 'pinia'
import { ApiError, conversationApi, streamEvents } from '../api'
import type {
  AppStage,
  ChatMessage,
  Conversation,
  ConversationSummary,
  DestinationChoice,
  PlanningStep,
  RequirementDraft,
  SavedTravelPlan,
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

function suggestionsFor(next: RequirementDraft): string[] {
  if (!next.province && !next.cities.length) return ['云南省', '浙江省', '陕西省']
  if (next.days === null) return ['3天', '5天', '7天', '还没确定']
  if (!next.interests.length && !next.preference_text) {
    return ['自然风光', '历史文化', '古城', '摄影', '美食', '休闲慢游']
  }
  return ['查看并确认旅行需求', '我还想补充']
}

function normalizeMessages(items: ChatMessage[]): ChatMessage[] {
  return items.map((message) => {
    if (message.role !== 'assistant') return message
    return {
      ...message,
      content: message.content
        .replaceAll('查看并确认右侧旅行需求卡', '点击“查看并确认旅行需求”按钮')
        .replace(/(?:查看|看看|看)右侧旅行需求卡(?:并确认)?(?:一下)?/g, '点击“查看并确认旅行需求”按钮'),
    }
  })
}

export const usePlanningStore = defineStore('planning', () => {
  const conversationId = ref('')
  const conversations = ref<ConversationSummary[]>([])
  const messages = ref<ChatMessage[]>([])
  const requirement = ref<RequirementDraft>(emptyRequirement())
  const suggestions = ref(['云南省', '浙江省', '陕西省'])
  const stage = ref<AppStage>('chat')
  const sending = ref(false)
  const initializing = ref(true)
  const error = ref('')
  const steps = ref<PlanningStep[]>([])
  const plan = ref<TravelPlanResponse | null>(null)
  const destinationChoice = ref<DestinationChoice | null>(null)
  const conversationLoading = ref(false)
  const conversationDeleting = ref<string | null>(null)
  const planningElapsedSeconds = ref(0)
  const planningRequestActive = ref(false)
  let planningStartedAt: number | null = null
  let planningTimer: number | null = null

  function updatePlanningElapsed() {
    if (planningStartedAt === null) return
    planningElapsedSeconds.value = Math.max(0, Math.floor((Date.now() - planningStartedAt) / 1000))
  }

  function stopPlanningTimer(reset = false) {
    if (planningTimer !== null) window.clearInterval(planningTimer)
    planningTimer = null
    updatePlanningElapsed()
    planningStartedAt = null
    if (reset) planningElapsedSeconds.value = 0
  }

  function startPlanningTimer() {
    stopPlanningTimer(true)
    planningStartedAt = Date.now()
    planningTimer = window.setInterval(updatePlanningElapsed, 1000)
  }

  function setConversationStatus(status: ConversationSummary['status']) {
    const current = conversations.value.find((item) => item.id === conversationId.value)
    if (current) current.status = status
  }

  async function refreshConversations() {
    try {
      conversations.value = await conversationApi.list()
    } catch {
      // History is optional; keep the active conversation usable if it is unavailable.
    }
  }

  function applyConversation(conversation: Conversation) {
    stopPlanningTimer(true)
    conversationId.value = conversation.id
    sessionStorage.setItem('travel_conversation_id', conversation.id)
    requirement.value = conversation.requirement
    messages.value = normalizeMessages(conversation.messages)
    suggestions.value = suggestionsFor(conversation.requirement)
    steps.value = []
    destinationChoice.value = null
    error.value = ''
  }

  async function restorePlan(conversation: Conversation) {
    plan.value = null
    stage.value = 'chat'
    const cached = sessionStorage.getItem(`travel_plan_${conversation.id}`)
    if (cached) {
      try {
        plan.value = JSON.parse(cached) as TravelPlanResponse
        stage.value = 'plan'
        return
      } catch {
        sessionStorage.removeItem(`travel_plan_${conversation.id}`)
      }
    }
    if (conversation.status !== 'completed') return
    try {
      const savedPlans: SavedTravelPlan[] = await conversationApi.listPlans(conversation.id)
      const latest = savedPlans[0]
      if (!latest) return
      plan.value = { requirement: latest.requirement, plan: latest.plan }
      sessionStorage.setItem(`travel_plan_${conversation.id}`, JSON.stringify(plan.value))
      stage.value = 'plan'
    } catch {
      // A missing saved plan should fall back to the conversation view.
    }
  }

  async function initialize() {
    initializing.value = true
    error.value = ''
    const saved = sessionStorage.getItem('travel_conversation_id')
    if (saved) {
      try {
        const conversation = await conversationApi.get(saved)
        applyConversation(conversation)
        await restorePlan(conversation)
        await refreshConversations()
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
    stopPlanningTimer(true)
    const created = await conversationApi.create()
    conversationId.value = created.id
    sessionStorage.setItem('travel_conversation_id', created.id)
    for (const key of Object.keys(sessionStorage)) {
      if (key.startsWith('travel_plan_')) sessionStorage.removeItem(key)
    }
    messages.value = []
    requirement.value = created.requirement
    suggestions.value = suggestionsFor(created.requirement)
    stage.value = 'chat'
    steps.value = []
    plan.value = null
    sessionStorage.removeItem(`travel_plan_${conversationId.value}`)
    destinationChoice.value = null
    error.value = ''
    await refreshConversations()
  }

  async function sendMessage(text: string) {
    const clean = text.trim()
    if (!clean || sending.value || !conversationId.value) return
    sending.value = true
    error.value = ''
    messages.value.push({ role: 'user', content: clean })
    messages.value.push({ role: 'assistant', content: '', streaming: true })
    const assistantIndex = messages.value.length - 1
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
                    // Read the item back from the reactive array. Mutating the raw
                    // object that was passed to push() does not reliably trigger Vue
                    // updates, so the UI used to show the whole reply at the end.
                    const assistant = messages.value[assistantIndex]
                    if (!assistant) {
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
        },
      )
      await renderChain
      const assistant = messages.value[assistantIndex]
      if (assistant) assistant.streaming = false
      await refreshConversations()
    } catch (caught) {
      const assistant = messages.value[assistantIndex]
      if (assistant) assistant.streaming = false
      if (assistant && !assistant.content && messages.value.at(-1) === assistant) messages.value.pop()
      error.value = readableError(caught)
    } finally {
      sending.value = false
    }
  }

  async function saveRequirement(draft: RequirementDraft) {
    error.value = ''
    const conversation = await conversationApi.updateRequirement(conversationId.value, draft)
    requirement.value = conversation.requirement
    suggestions.value = suggestionsFor(conversation.requirement)
    await refreshConversations()
  }

  async function openConversation(id: string, view: 'chat' | 'plan' = 'chat') {
    if (conversationLoading.value || sending.value || stage.value === 'planning') return
    if (id === conversationId.value) {
      if (view === 'plan') showPlan()
      else backToChat()
      return
    }
    conversationLoading.value = true
    error.value = ''
    try {
      const conversation = await conversationApi.get(id)
      applyConversation(conversation)
      await restorePlan(conversation)
      if (view === 'chat') stage.value = 'chat'
      await refreshConversations()
    } catch (caught) {
      error.value = readableError(caught)
    } finally {
      conversationLoading.value = false
    }
  }

  async function deleteConversation(id: string) {
    if (conversationDeleting.value || conversationLoading.value || sending.value) return
    conversationDeleting.value = id
    error.value = ''
    try {
      await conversationApi.remove(id)
      sessionStorage.removeItem(`travel_plan_${id}`)
      const wasActive = id === conversationId.value
      conversations.value = conversations.value.filter((item) => item.id !== id)
      if (!wasActive) return

      const next = conversations.value[0]
      if (next) await openConversation(next.id)
      else await createConversation()
    } catch (caught) {
      error.value = readableError(caught)
    } finally {
      conversationDeleting.value = null
    }
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
    if (planningRequestActive.value) return
    planningRequestActive.value = true
    stage.value = 'planning'
    error.value = ''
    steps.value = []
    plan.value = null
    destinationChoice.value = null
    setConversationStatus('planning')
    startPlanningTimer()
    try {
      await streamEvents(`/conversations/${conversationId.value}/plan`, undefined, (event, data) => {
        if (event === 'planning_progress') upsertStep(data)
        if (event === 'plan_completed') {
          plan.value = data
          sessionStorage.setItem(`travel_plan_${conversationId.value}`, JSON.stringify(data))
          setConversationStatus('completed')
          stage.value = 'plan'
        }
        if (event === 'destination_choice_required') {
          destinationChoice.value = data
          setConversationStatus('confirmed')
        }
        if (event === 'planning_error') {
          error.value = data.message
          setConversationStatus('confirmed')
          failRunningSteps()
        }
      })
    } catch (caught) {
      error.value = readableError(caught)
      setConversationStatus('confirmed')
      failRunningSteps()
    } finally {
      stopPlanningTimer()
      planningRequestActive.value = false
      // The backend marks the conversation completed before emitting
      // plan_completed. Refresh after the stream so the sidebar moves the
      // record into the travel-plan history immediately.
      await refreshConversations()
    }
  }

  async function chooseScope(scope: Exclude<TravelScope, 'auto'>) {
    try {
      const conversation = await conversationApi.updateRequirement(conversationId.value, { travel_scope: scope })
      requirement.value = conversation.requirement
      await refreshConversations()
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

  function failRunningSteps() {
    steps.value = steps.value.map((item) =>
      item.status === 'running' ? { ...item, status: 'failed' as const } : item,
    )
  }

  async function retryPlanning() {
    if (sending.value || conversationLoading.value || planningRequestActive.value) return
    await startPlanning()
  }

  function backToChat() {
    stopPlanningTimer(true)
    stage.value = 'chat'
    destinationChoice.value = null
    error.value = ''
  }

  function showPlan() {
    if (!plan.value) return
    stopPlanningTimer(true)
    stage.value = 'plan'
    error.value = ''
  }

  function readableError(caught: unknown) {
    if (caught instanceof ApiError || caught instanceof Error) return caught.message
    return '操作失败，请稍后重试'
  }

  return {
    conversationId,
    conversations,
    messages,
    requirement,
    suggestions,
    stage,
    sending,
    initializing,
    error,
    steps,
    plan,
    destinationChoice,
    conversationLoading,
    conversationDeleting,
    planningElapsedSeconds,
    planningRequestActive,
    initialize,
    createConversation,
    sendMessage,
    openConversation,
    deleteConversation,
    showPlan,
    saveRequirement,
    confirmAndPlan,
    retryPlanning,
    chooseScope,
    backToChat,
  }
})
