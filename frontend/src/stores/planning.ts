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
  DestinationChoice,
  GeneralConversation,
  PlanningStep,
  RequirementDraft,
  SavedTravelPlan,
  TravelPlanResponse,
  TravelScope,
} from '../types'

const emptyRequirement = (): RequirementDraft => ({
  province: null,
  cities: [],
  province_preference_confirmed: false,
  city_preference_confirmed: false,
  days: null,
  interests: [],
  preference_text: null,
  travel_scope: 'auto',
  start_date: null,
  daily_start_time: null,
  daily_end_time: null,
  transport_mode: 'auto',
})

const MAX_SINGLE_CITY_DAYS = 3

function sameList(left: string[], right: string[]) {
  return left.length === right.length && left.every((value, index) => value === right[index])
}

function isPlanCompatible(candidate: TravelPlanResponse, conversation: Conversation) {
  const current = conversation.requirement
  const saved = candidate.requirement
  if (current.days === null || saved.days !== current.days) return false
  if (saved.province !== current.province) return false
  if (!sameList(saved.cities, current.cities)) return false
  if (saved.province_preference_confirmed !== current.province_preference_confirmed) return false
  if (saved.city_preference_confirmed !== current.city_preference_confirmed) return false
  if (!sameList(saved.interests, current.interests)) return false
  if (saved.preference_text !== current.preference_text) return false
  if (saved.start_date !== current.start_date) return false
  if (!current.daily_start_time || !current.daily_end_time) return false
  if (saved.daily_start_time !== current.daily_start_time) return false
  if (saved.daily_end_time !== current.daily_end_time) return false
  if (saved.travel_scope !== current.travel_scope) return false
  if (!Array.isArray(candidate.plan?.city_stays)) return false
  // A cached result from the old single-city branch must not survive a
  // 4～7-day requirement, even when the requirement fields themselves match.
  if (current.days > MAX_SINGLE_CITY_DAYS && candidate.plan.city_stays.length < 2) return false
  return true
}

function suggestionsFor(next: RequirementDraft, mode: ConversationMode = 'planning'): string[] {
  if (mode === 'general') {
    return ['你能做什么？', '介绍一个目的地']
  }
  if (!next.province && !next.cities.length && !next.province_preference_confirmed) {
    return ['北京', '上海', '四川省', '云南省', '浙江省', '你来推荐一个省份']
  }
  if (next.days === null) {
    if (next.travel_scope === 'multi_city') {
      return ['4天', '5天', '6天', '7天']
    }
    return next.travel_scope === 'single_city' || next.cities.length === 1
      ? ['1天', '2天', '3天']
      : ['1天', '2天', '3天', '4天', '5天', '6天', '7天']
  }
  if (next.travel_scope === 'auto') {
    return next.days !== null && next.days <= MAX_SINGLE_CITY_DAYS
      ? ['一个城市深度游', '增加到4天以上游多个城市']
      : ['同省多城市']
  }
  if (next.travel_scope === 'multi_city' && next.days <= MAX_SINGLE_CITY_DAYS) {
    return ['4天', '5天', '6天', '7天']
  }
  if (!next.cities.length && !next.city_preference_confirmed) {
    return ['没有指定城市，你来推荐']
  }
  if (!next.interests.length && !next.preference_text) {
    return ['自然风光', '历史文化', '古城', '摄影', '美食', '休闲慢游']
  }
  if (!next.start_date) return ['明天出发', '后天出发', '下周一出发']
  if (!next.daily_start_time || !next.daily_end_time) {
    return ['每天08:00到21:00', '每天09:00到18:00', '每天10:00到20:00']
  }
  return ['确认生成计划']
}

function normalizeMessages(items: ChatMessage[]): ChatMessage[] {
  return items.map((message) => {
    if (message.role !== 'assistant') return message
    return {
      ...message,
      content: message.content
        .replaceAll('查看并确认右侧旅行需求卡', '回复“确认生成计划”')
        .replaceAll('查看并确认旅行需求', '确认生成计划')
        .replace(/(?:查看|看看|看)右侧旅行需求卡(?:并确认)?(?:一下)?/g, '回复“确认生成计划”')
        .replaceAll('出发时间还没确定也没关系，之后补上即可。', '出发日期还需要确定。')
        .replaceAll('出发时间还没确定也没关系，之后补上即可', '出发日期还需要确定')
        .replaceAll('景点之间怎么移动？步行、公共交通，还是自驾？', '景点间路线将由地图工具根据实际位置自动推荐。')
        .replaceAll('景点之间你打算怎么走？步行、公共交通，还是自驾？', '景点间路线将由地图工具根据实际位置自动推荐。')
        .replaceAll('你打算怎么在景点之间移动？步行、公共交通，还是自驾？', '景点间路线将由地图工具根据实际位置自动推荐。')
        .replaceAll('景点之间准备怎么出行？请选择公共交通、自驾或步行。', '景点间路线将由地图工具根据实际位置自动推荐。')
        .replaceAll('景点之间准备怎么移动？请选择景点间公共交通、景点间自驾或景点间步行。到达目的地的飞机、高铁不用填写。', '景点间路线将由地图工具根据实际位置自动推荐。'),
    }
  })
}

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
  const destinationChoice = ref<DestinationChoice | null>(null)
  const conversationLoading = ref(false)
  const conversationCreating = ref(false)
  const conversationDeleting = ref<string | null>(null)
  const planningElapsedSeconds = ref(0)
  const planningRequestActive = ref(false)
  const planningEventCursor = ref(0)
  const planningJobId = ref<string | null>(null)
  let planningStartedAt: number | null = null
  let planningTimer: number | null = null

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
    stopPlanningTimer(true)
    setActiveConversation('planning', conversation.id)
    loadPlanningState(conversation.id)
    requirement.value = conversation.requirement
    messages.value = normalizeMessages(conversation.messages)
    suggestions.value = suggestionsFor(conversation.requirement, 'planning')
    stage.value = 'chat'
    steps.value = []
    plan.value = null
    destinationChoice.value = null
    error.value = ''
  }

  function applyGeneralConversation(conversation: GeneralConversation) {
    stopPlanningTimer(true)
    setActiveConversation('general', conversation.id)
    resetPlanningRuntime()
    requirement.value = emptyRequirement()
    messages.value = normalizeMessages(conversation.messages)
    suggestions.value = suggestionsFor(requirement.value, 'general')
    stage.value = 'chat'
    steps.value = []
    plan.value = null
    destinationChoice.value = null
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
            applyConversation(conversation)
            await restorePlan(conversation)
            if (conversation.status === 'planning') await resumePlanning()
            else stage.value = 'chat'
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
          applyConversation(conversation)
          await restorePlan(conversation)
          if (conversation.status === 'planning') await resumePlanning()
          else stage.value = 'chat'
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

  async function restorePlan(conversation: Conversation) {
    plan.value = null
    stage.value = 'chat'
    const cached = sessionStorage.getItem(`travel_plan_${conversation.id}`)
    if (cached) {
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
          applyConversation(conversation)
          await restorePlan(conversation)
          if (conversation.status === 'planning') await resumePlanning()
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
        destinationChoice.value = null
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
    sending.value = true
    error.value = ''
    if (mode === 'planning' && conversationId.value && (plan.value || stage.value === 'plan')) {
      plan.value = null
      stage.value = 'chat'
      sessionStorage.removeItem(`travel_plan_${conversationId.value}`)
    }
    messages.value.push({ role: 'user', content: clean })
    messages.value.push({ role: 'assistant', content: '', streaming: true })
    const assistantIndex = messages.value.length - 1
    let renderChain = Promise.resolve()
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
          if (mode === 'planning' && event === 'requirement_updated') requirement.value = data
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
        applyConversation(conversation)
        await restorePlan(conversation)
        if (conversation.status === 'planning') await resumePlanning()
        else if (view === 'chat') stage.value = 'chat'
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
      await streamEvents(
        `/conversations/${conversationId.value}/plan`,
        undefined,
        (event, data, sequence) => handlePlanningEvent(event, data, sequence),
        {
          after: planningEventCursor.value,
          jobId: planningJobId.value,
          onOpen: ({ jobId }) => {
            if (jobId && jobId !== planningJobId.value) {
              planningJobId.value = jobId
              planningEventCursor.value = 0
              persistPlanningState()
            }
          },
        },
      )
    } catch (caught) {
      await handlePlanningConnectionError(caught)
    } finally {
      stopPlanningTimer()
      planningRequestActive.value = false
      // The backend marks the conversation completed before emitting
      // plan_completed. Refresh after the stream so the sidebar moves the
      // record into the travel-plan history immediately.
      await refreshConversations()
    }
  }

  async function resumePlanning() {
    if (planningRequestActive.value || !conversationId.value) return
    planningRequestActive.value = true
    stage.value = 'planning'
    error.value = ''
    steps.value = []
    plan.value = null
    destinationChoice.value = null
    setConversationStatus('planning')
    startPlanningTimer()
    try {
      await streamGetEvents(
        `/conversations/${conversationId.value}/plan/events`,
        planningEventCursor.value,
        (event, data, sequence) => handlePlanningEvent(event, data, sequence),
        {
          jobId: planningJobId.value,
          onOpen: ({ jobId }) => {
            if (jobId && jobId !== planningJobId.value) {
              planningJobId.value = jobId
              planningEventCursor.value = 0
              persistPlanningState()
            }
          },
        },
      )
    } catch (caught) {
      await handlePlanningConnectionError(caught)
    } finally {
      stopPlanningTimer()
      planningRequestActive.value = false
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
    if (event === 'destination_choice_required') {
      destinationChoice.value = data
      setConversationStatus('confirmed')
    }
    if (event === 'planning_error') {
      error.value = data.message
      setConversationStatus('confirmed')
      failRunningSteps()
    }
  }

  async function handlePlanningConnectionError(caught: unknown) {
    try {
      const current = await conversationApi.get(conversationId.value)
      if (current.status === 'completed') {
        applyConversation(current)
        await restorePlan(current)
        return
      }
      if (current.status === 'planning') {
        error.value = '连接暂时中断，规划任务仍在后台运行；点击“重新尝试”可恢复进度。'
        setConversationStatus('planning')
        return
      }
    } catch {
      // Fall through to the local error when the status cannot be refreshed.
    }
    error.value = readableError(caught)
    setConversationStatus('confirmed')
    failRunningSteps()
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
    conversationMode,
    suggestions,
    stage,
    sending,
    initializing,
    error,
    steps,
    plan,
    destinationChoice,
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
    openConversation,
    deleteConversation,
    showPlan,
    confirmAndPlan,
    retryPlanning,
    resumePlanning,
    chooseScope,
    backToChat,
  }
})
