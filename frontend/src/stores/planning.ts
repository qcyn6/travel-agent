import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  ApiError,
  conversationApi,
  generalConversationApi,
  streamEvents,
  streamGetEvents,
} from '../api'
import type {
  AppStage,
  ChatMessage,
  ConversationMode,
  Conversation,
  ConversationSummary,
  GeneralConversation,
  PlanningStep,
  RequirementDraft,
  RouteChatCard,
  SingleChatToolCard,
  SavedTravelPlan,
  TravelPlanResponse,
} from '../types'

import { emptyRequirement, isPlanCompatible, suggestionsFor } from './planning/policy'
import { migrateLegacyGeneralRouteCards, normalizeMessages } from './planning/history'

export const usePlanningStore = defineStore('planning', () => {
  const conversationId = ref('')
  const conversations = ref<ConversationSummary[]>([])
  const messages = ref<ChatMessage[]>([])
  const requirement = ref<RequirementDraft>(emptyRequirement())
  const conversationMode = ref<ConversationMode>('general')
  const suggestions = ref(['北京', '上海', '四川省', '云南省', '浙江省', '你来推荐一个省份'])
  const stage = ref<AppStage>('chat')
  const sending = ref(false)
  const initializing = ref(true)
  const error = ref('')
  const steps = ref<PlanningStep[]>([])
  const plan = ref<TravelPlanResponse | null>(null)
  const conversationLoading = ref(false)
  const conversationCreating = ref(false)
  const conversationDeleting = ref<string | null>(null)
  const planningElapsedSeconds = ref(0)
  const planningRequestActive = ref(false)
  const generalCardSaveQueues = new Map<string, Promise<void>>()
  const planningEventCursor = ref(0)
  const planningJobId = ref<string | null>(null)
  let planningStartedAt: number | null = null
  let planningTimer: number | null = null
  let planningNeedsReplay = false
  let messageController: AbortController | null = null

  function cancelMessage() {
    if (conversationMode.value === 'general') messageController?.abort()
  }

  function planningStateKey(id: string) {
    return `travel_planning_state_${id}`
  }

  const modeSwitching = ref(false)
  const activeModeStorageKey = 'travel_active_conversation_mode'

  function conversationStorageKey(mode: ConversationMode) {
    return mode === 'general' ? 'travel_general_conversation_id' : 'travel_conversation_id'
  }

  function setActiveConversation(mode: ConversationMode, id: string) {
    conversationMode.value = mode
    conversationId.value = id
    sessionStorage.setItem(activeModeStorageKey, mode)
    sessionStorage.setItem(conversationStorageKey(mode), id)
  }

  function persistPlanningState() {
    if (!conversationId.value) return
    sessionStorage.setItem(
      planningStateKey(conversationId.value),
      JSON.stringify({ jobId: planningJobId.value, cursor: planningEventCursor.value }),
    )
  }

  function loadPlanningState(id: string) {
    planningEventCursor.value = 0
    planningJobId.value = null
    const cached = sessionStorage.getItem(planningStateKey(id))
    if (!cached) return
    try {
      const state = JSON.parse(cached) as { jobId?: unknown; cursor?: unknown }
      if (typeof state.jobId === 'string' && state.jobId) planningJobId.value = state.jobId
      if (typeof state.cursor === 'number' && Number.isFinite(state.cursor) && state.cursor >= 0) {
        planningEventCursor.value = Math.floor(state.cursor)
      }
    } catch {
      sessionStorage.removeItem(planningStateKey(id))
    }
  }

  function resetPlanningState() {
    if (conversationId.value) sessionStorage.removeItem(planningStateKey(conversationId.value))
    planningEventCursor.value = 0
    planningJobId.value = null
  }

  function resetPlanningRuntime() {
    planningNeedsReplay = false
    planningEventCursor.value = 0
    planningJobId.value = null
  }

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
    const [planningResult, generalResult] = await Promise.allSettled([
      conversationApi.list(),
      generalConversationApi.list(),
    ])
    const previous = conversations.value
    const planning = planningResult.status === 'fulfilled'
      ? planningResult.value.map((item) => ({ ...item, mode: 'planning' as const }))
      : previous.filter((item) => item.mode === 'planning')
    const general = generalResult.status === 'fulfilled'
      ? generalResult.value.map((item) => ({ ...item, mode: 'general' as const }))
      : previous.filter((item) => item.mode === 'general')
    if (planningResult.status === 'rejected' && generalResult.status === 'rejected') return
    conversations.value = [...planning, ...general].sort(
      (left, right) => new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime(),
    )
  }

  function applyConversation(conversation: Conversation) {
    planningNeedsReplay = false
    stopPlanningTimer(true)
    setActiveConversation('planning', conversation.id)
    loadPlanningState(conversation.id)
    requirement.value = conversation.requirement
    messages.value = normalizeMessages(conversation.messages, true)
    suggestions.value = suggestionsFor(conversation.requirement, 'planning')
    stage.value = 'chat'
    steps.value = []
    plan.value = null
    error.value = ''
  }

  function applyGeneralConversation(conversation: GeneralConversation) {
    stopPlanningTimer(true)
    setActiveConversation('general', conversation.id)
    resetPlanningRuntime()
    requirement.value = emptyRequirement()
    messages.value = migrateLegacyGeneralRouteCards(
      conversation.id,
      normalizeMessages(conversation.messages),
    )
    suggestions.value = suggestionsFor(requirement.value, 'general')
    stage.value = 'chat'
    steps.value = []
    plan.value = null
    error.value = ''
  }

  async function setConversationMode(
    mode: ConversationMode,
    options: { createNew?: boolean } = {},
  ) {
    if (mode === conversationMode.value || modeSwitching.value || conversationCreating.value) return
    modeSwitching.value = true
    error.value = ''
    try {
      // The welcome-page mode selector means “start a new conversation in
      // this mode”. History recovery remains the default behavior below.
      if (options.createNew) {
        await createConversation(mode)
        return
      }
      const savedId = sessionStorage.getItem(conversationStorageKey(mode))
      if (savedId) {
        try {
          if (mode === 'general') {
            applyGeneralConversation(await generalConversationApi.get(savedId))
          } else {
            const conversation = await conversationApi.get(savedId)
            await restorePlanningConversation(conversation, 'chat')
          }
          await refreshConversations()
          return
        } catch {
          sessionStorage.removeItem(conversationStorageKey(mode))
        }
      }

      const latest = conversations.value.find((item) => item.mode === mode)
      if (latest) {
        if (mode === 'general') {
          applyGeneralConversation(await generalConversationApi.get(latest.id))
        } else {
          const conversation = await conversationApi.get(latest.id)
          await restorePlanningConversation(conversation, 'chat')
        }
        await refreshConversations()
        return
      }

      await createConversation(mode)
    } catch (caught) {
      error.value = readableError(caught)
    } finally {
      modeSwitching.value = false
    }
  }

  async function restorePlan(conversation: Conversation, options: { preferServer?: boolean } = {}) {
    plan.value = null
    stage.value = 'chat'
    const cached = sessionStorage.getItem(`travel_plan_${conversation.id}`)
    if (cached && !options.preferServer) {
      try {
        const cachedPlan = JSON.parse(cached) as TravelPlanResponse
        if (isPlanCompatible(cachedPlan, conversation)) {
          plan.value = cachedPlan
          stage.value = 'plan'
          return
        }
        sessionStorage.removeItem(`travel_plan_${conversation.id}`)
      } catch {
        sessionStorage.removeItem(`travel_plan_${conversation.id}`)
      }
    }
    if (conversation.status !== 'completed') return
    try {
      const savedPlans: SavedTravelPlan[] = await conversationApi.listPlans(conversation.id)
      const latest = savedPlans.find((savedPlan) =>
        isPlanCompatible(
          { requirement: savedPlan.requirement, plan: savedPlan.plan },
          conversation,
        ),
      )
      if (!latest) return
      plan.value = { requirement: latest.requirement, plan: latest.plan }
      sessionStorage.setItem(`travel_plan_${conversation.id}`, JSON.stringify(plan.value))
      stage.value = 'plan'
    } catch {
      // A missing saved plan should fall back to the conversation view.
    }
  }

  async function restorePlanningConversation(conversation: Conversation, view: 'chat' | 'plan' = 'plan') {
    applyConversation(conversation)
    let state: Awaited<ReturnType<typeof conversationApi.planningState>> = null
    try {
      state = await conversationApi.planningState(conversation.id)
    } catch {
      // Do not discard valid history or create a new conversation merely
      // because the read-only task state endpoint is temporarily unavailable.
      error.value = '暂时无法读取规划任务状态，请稍后刷新。'
    }
    const replay = state
      ? ['queued', 'running'].includes(state.status)
      : conversation.status === 'planning'
    if (replay) {
      // A loaded cursor has no corresponding in-memory progress after a
      // full reload. Rebuild from committed events, not just
      // the tail after that cursor. This GET never prepares a new job.
      planningJobId.value = state?.job_id ?? planningJobId.value
      planningEventCursor.value = 0
      persistPlanningState()
      await resumePlanning()
      return
    }
    await restorePlan(conversation, { preferServer: conversation.status === 'completed' })
    if (view === 'chat') stage.value = 'chat'
  }

  async function initialize() {
    initializing.value = true
    error.value = ''
    const storedMode = sessionStorage.getItem(activeModeStorageKey)
    const savedMode: ConversationMode = storedMode === 'planning'
      ? 'planning'
      : storedMode === 'general'
        ? 'general'
        : sessionStorage.getItem('travel_conversation_id')
          ? 'planning'
          : 'general'
    const saved = sessionStorage.getItem(conversationStorageKey(savedMode))
    if (saved) {
      try {
        if (savedMode === 'general') {
          applyGeneralConversation(await generalConversationApi.get(saved))
        } else {
          const conversation = await conversationApi.get(saved)
          await restorePlanningConversation(conversation)
        }
        await refreshConversations()
        initializing.value = false
        return
      } catch {
        sessionStorage.removeItem(conversationStorageKey(savedMode))
      }
    }
    await createConversation(savedMode)
    initializing.value = false
  }

  async function createConversation(mode: ConversationMode = conversationMode.value) {
    if (
      conversationCreating.value
      || conversationLoading.value
      || sending.value
      || stage.value === 'planning'
    ) return
    conversationCreating.value = true
    try {
      stopPlanningTimer(true)
      if (mode === 'general') {
        applyGeneralConversation(await generalConversationApi.create())
      } else {
        const created = await conversationApi.create()
        setActiveConversation('planning', created.id)
        resetPlanningState()
        messages.value = []
        requirement.value = created.requirement
        suggestions.value = suggestionsFor(created.requirement, 'planning')
        stage.value = 'chat'
        steps.value = []
        plan.value = null
        sessionStorage.removeItem(`travel_plan_${created.id}`)
        error.value = ''
      }
      await refreshConversations()
    } finally {
      conversationCreating.value = false
    }
  }

  async function sendMessage(text: string) {
    const clean = text.trim()
    if (!clean || sending.value || !conversationId.value) return
    const mode = conversationMode.value
    const controller = new AbortController()
    messageController = controller
    sending.value = true
    error.value = ''
    if (mode === 'planning' && conversationId.value && (plan.value || stage.value === 'plan')) {
      plan.value = null
      stage.value = 'chat'
      sessionStorage.removeItem(`travel_plan_${conversationId.value}`)
    }
    if (mode === 'planning') suggestions.value = []
    messages.value.push({ role: 'user', content: clean })
    messages.value.push({ role: 'assistant', content: '', streaming: true })
    const assistantIndex = messages.value.length - 1
    let renderChain = Promise.resolve()
    let messageDone = false
    let pendingCard: ChatMessage['card'] = null
    try {
      const path = mode === 'general'
        ? `/general-conversations/${conversationId.value}/messages`
        : `/conversations/${conversationId.value}/messages`
      const body = mode === 'general'
        ? { message: clean }
        : { message: clean, mode: 'planning' }
      await streamEvents(
        path,
        body,
        (event, data) => {
          if (controller.signal.aborted) return
          if (event === 'message_progress') {
            const assistant = messages.value[assistantIndex]
            if (assistant) assistant.progress = String(data.message || '正在查询')
          }
          if (event === 'message_done') messageDone = true
          if (event === 'error') throw new Error(String(data.message || '需求整理失败，请重试。'))
          if (event === 'message_error') error.value = String(data.message || '本次回复生成失败，请重试。')
          if (mode === 'planning' && event === 'requirement_updated') requirement.value = data
          if (event === 'suggestions') suggestions.value = data.options || []
          if (mode === 'general' && event === 'tool_card') {
            pendingCard = data
          }
          if (event === 'message_delta') {
            const delta = String(data.text || '')
            // Preserve the server's real delta order while rendering each chunk visibly.
            renderChain = renderChain.then(
              () =>
                new Promise<void>((resolve) => {
                  let index = 0
                  const renderNext = () => {
                    if (controller.signal.aborted) { resolve(); return }
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
        { signal: controller.signal },
      )
      await renderChain
      if (controller.signal.aborted) throw new DOMException('Stopped', 'AbortError')
      if (!messageDone) throw new Error('回复连接已中断，请重试。')
      if (!messages.value[assistantIndex]?.content.trim()) throw new Error('本次未收到有效回复，请重试。')
      const assistant = messages.value[assistantIndex]
      if (assistant) {
        assistant.streaming = false
        assistant.progress = undefined
        if (mode === 'general') assistant.card = pendingCard
      }
      await refreshConversations()
    } catch (caught) {
      // Drain already received chunks before finalizing a partial reply.
      await renderChain
      const assistant = messages.value[assistantIndex]
      if (assistant) assistant.streaming = false
      if (controller.signal.aborted) {
        if (assistant) {
          assistant.content = `${assistant.content.trim()}\n\n已停止生成。`.trim()
          assistant.progress = undefined
        }
        return
      }
      if (assistant && !assistant.content.trim() && messages.value.at(-1) === assistant) messages.value.pop()
      error.value = error.value || readableError(caught)
    } finally {
      sending.value = false
      if (messageController === controller) messageController = null
    }
  }

  async function openConversation(
    id: string,
    mode: ConversationMode = conversations.value.find((item) => item.id === id)?.mode || 'planning',
    view: 'chat' | 'plan' = 'chat',
  ) {
    if (conversationLoading.value || sending.value || stage.value === 'planning') return
    if (id === conversationId.value && mode === conversationMode.value) {
      if (view === 'plan') showPlan()
      else backToChat()
      return
    }
    conversationLoading.value = true
    error.value = ''
    try {
      if (mode === 'general') {
        applyGeneralConversation(await generalConversationApi.get(id))
      } else {
        const conversation = await conversationApi.get(id)
        await restorePlanningConversation(conversation, view)
      }
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
      const item = conversations.value.find((conversation) => conversation.id === id)
      const mode: ConversationMode = item?.mode || (id === conversationId.value ? conversationMode.value : 'planning')
      if (mode === 'general') await generalConversationApi.remove(id)
      else await conversationApi.remove(id)
      if (mode === 'planning') {
        sessionStorage.removeItem(`travel_plan_${id}`)
        sessionStorage.removeItem(planningStateKey(id))
      }
      if (mode === 'general') sessionStorage.removeItem(`travel_general_cards_${id}`)
      sessionStorage.removeItem(`travel_conversation_mode_${id}`)
      if (sessionStorage.getItem(conversationStorageKey(mode)) === id) {
        sessionStorage.removeItem(conversationStorageKey(mode))
      }
      const wasActive = id === conversationId.value
      conversations.value = conversations.value.filter((item) => item.id !== id)
      if (!wasActive) return

      const next = conversations.value.find((item) => item.mode === conversationMode.value) || conversations.value[0]
      if (next) await openConversation(next.id, next.mode)
      else await createConversation(conversationMode.value)
    } catch (caught) {
      error.value = readableError(caught)
    } finally {
      conversationDeleting.value = null
    }
  }

  async function confirmAndPlan() {
    if (conversationMode.value !== 'planning') {
      const blocked = new Error('请先切换到规划模式，再生成旅行计划')
      error.value = blocked.message
      throw blocked
    }
    try {
      await conversationApi.confirm(conversationId.value, requirement.value)
      await startPlanning()
    } catch (caught) {
      if (caught instanceof ApiError && [409, 422].includes(caught.status)) {
        // A date can expire overnight or another page can replace the draft.
        // Reload the authoritative draft so stale confirmation chips disappear.
        try {
          const current = await conversationApi.get(conversationId.value)
          applyConversation(current)
        } catch {
          suggestions.value = []
        }
      }
      error.value = readableError(caught)
      throw caught
    }
  }

  async function startPlanning() {
    return observePlanningRequest('start')
  }

  async function resumePlanning() {
    return observePlanningRequest('resume')
  }

  async function observePlanningRequest(action: 'start' | 'resume') {
    if (planningRequestActive.value || (action === 'resume' && !conversationId.value)) return
    planningRequestActive.value = true
    planningNeedsReplay = false
    stage.value = 'planning'
    error.value = ''
    if (!planningJobId.value) steps.value = []
    plan.value = null
    setConversationStatus('planning')
    startPlanningTimer()
    let receivedTerminal = false
    const onEvent = (event: string, data: any, sequence?: number) => {
      handlePlanningEvent(event, data, sequence)
      if (['plan_completed', 'planning_error'].includes(event)) receivedTerminal = true
    }
    const options = {
      jobId: planningJobId.value,
      onOpen: ({ jobId }: { jobId: string | null }) => {
        if (jobId && jobId !== planningJobId.value) {
          planningJobId.value = jobId
          planningEventCursor.value = 0
          steps.value = []
          persistPlanningState()
        }
      },
    }
    try {
      if (action === 'start') {
        await streamEvents(
          `/conversations/${conversationId.value}/plan`, undefined, onEvent,
          { ...options, after: planningEventCursor.value },
        )
      } else {
        await streamGetEvents(
          `/conversations/${conversationId.value}/plan/events`, planningEventCursor.value,
          onEvent, options,
        )
      }
      if (!receivedTerminal) {
        await handlePlanningConnectionError(new ApiError('进度连接已结束，但尚未收到最终结果，请重试恢复进度。', 0))
      }
    } catch (caught) {
      if (!receivedTerminal) await handlePlanningConnectionError(caught)
    } finally {
      stopPlanningTimer()
      planningRequestActive.value = false
      // Both fresh requests and replay must refresh the completed-plan history.
      await refreshConversations()
    }
  }

  function handlePlanningEvent(event: string, data: any, sequence?: number) {
    if (sequence !== undefined && sequence >= planningEventCursor.value) {
      planningEventCursor.value = sequence
      persistPlanningState()
    }
    if (event === 'planning_progress') upsertStep(data)
    if (event === 'plan_completed') {
      plan.value = data
      sessionStorage.setItem(`travel_plan_${conversationId.value}`, JSON.stringify(data))
      setConversationStatus('completed')
      stage.value = 'plan'
    }
    if (event === 'planning_error') {
      error.value = data.message
      setConversationStatus('confirmed')
      failRunningSteps()
    }
  }

  async function handlePlanningConnectionError(caught: unknown) {
    planningNeedsReplay = true
    try {
      const current = await conversationApi.get(conversationId.value)
      if (current.status === 'completed') {
        applyConversation(current)
        await restorePlan(current, { preferServer: true })
        if (!plan.value) {
          planningNeedsReplay = true
          stage.value = 'planning'
          error.value = '任务已完成，但暂时无法读取方案；请重新尝试恢复结果。'
        } else planningNeedsReplay = false
        return
      }
      if (current.status === 'planning') {
        error.value = '连接暂时中断，规划任务仍在后台运行；点击“重新尝试”可恢复进度。'
        setConversationStatus('planning')
        return
      }
      planningNeedsReplay = false
    } catch {
      // Fall through to the local error when the status cannot be refreshed.
    }
    error.value = readableError(caught)
    setConversationStatus('confirmed')
    failRunningSteps()
  }

  function upsertStep(next: PlanningStep) {
    // City retrieval, route planning and final description enrichment can now
    // run for up to three cities at the same time.  A newer event must not
    // close another city's running row; each row is closed only by its own
    // completed/failed event (or by failRunningSteps on a terminal error).
    const index = steps.value.findIndex((item) => item.step === next.step)
    if (index >= 0) steps.value[index] = next
    else steps.value.push(next)
  }

  async function updateGeneralCard(index: number, card: RouteChatCard) {
    if (conversationMode.value !== 'general' || !conversationId.value) return
    const message = messages.value[index]
    if (message?.role !== 'assistant') return
    if (message.card?.type === 'collection') {
      const cards: SingleChatToolCard[] = [...message.card.cards]
      const route = card.route
      const routeIndex = cards.findIndex((item) => (
        item.type === 'route'
        && item.route.origin_poi_id === route.origin_poi_id
        && item.route.destination_poi_id === route.destination_poi_id
      ))
      if (routeIndex >= 0) cards[routeIndex] = card
      else cards.push(card)
      message.card = { type: 'collection', cards }
    } else {
      message.card = card
    }
    const conversation = conversationId.value
    const sequenceNumber = message.sequence_number ?? index + 1
    const queueKey = `${conversation}:${sequenceNumber}`
    const previousSave = generalCardSaveQueues.get(queueKey) ?? Promise.resolve()
    let savedContent: string | null = null
    const save = previousSave
      .catch(() => undefined)
      .then(async () => {
        const result = await generalConversationApi.updateMessageCard(conversation, sequenceNumber, card)
        savedContent = result.content
      })
    generalCardSaveQueues.set(queueKey, save)
    try {
      await save
      if (conversationId.value === conversation && savedContent !== null) {
        const currentMessage = messages.value[index]
        if (currentMessage?.role === 'assistant') currentMessage.content = savedContent
      }
    } catch (caught) {
      if (conversationId.value === conversation) {
        error.value = `路线已切换，但保存到会话失败：${readableError(caught)}`
      }
    } finally {
      if (generalCardSaveQueues.get(queueKey) === save) generalCardSaveQueues.delete(queueKey)
    }
  }

  function failRunningSteps() {
    steps.value = steps.value.map((item) =>
      item.status === 'running' ? { ...item, status: 'failed' as const } : item,
    )
  }

  async function retryPlanning() {
    if (sending.value || conversationLoading.value || planningRequestActive.value) return
    if (planningNeedsReplay) await resumePlanning()
    else await startPlanning()
  }

  function backToChat() {
    stopPlanningTimer(true)
    stage.value = 'chat'
    error.value = ''
  }

  function showPlan() {
    if (!plan.value) return
    stopPlanningTimer(true)
    stage.value = 'plan'
    error.value = ''
  }

  function updatePlan(result: TravelPlanResponse) {
    plan.value = result
    if (conversationId.value) {
      sessionStorage.setItem(`travel_plan_${conversationId.value}`, JSON.stringify(result))
    }
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
    conversationMode,
    suggestions,
    stage,
    sending,
    initializing,
    error,
    steps,
    plan,
    conversationLoading,
    conversationCreating,
    conversationDeleting,
    modeSwitching,
    planningElapsedSeconds,
    planningRequestActive,
    initialize,
    createConversation,
    setConversationMode,
    sendMessage,
    cancelMessage,
    updateGeneralCard,
    openConversation,
    deleteConversation,
    showPlan,
    updatePlan,
    confirmAndPlan,
    retryPlanning,
    resumePlanning,
    backToChat,
  }
})
