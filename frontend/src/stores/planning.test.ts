// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { ApiError, conversationApi, generalConversationApi, streamEvents, streamGetEvents } from '../api'
import { usePlanningStore } from './planning'

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {
    status = 0
  },
  conversationApi: {
    create: vi.fn(),
    list: vi.fn(),
    get: vi.fn(),
    remove: vi.fn(),
    listPlans: vi.fn(),
    planningState: vi.fn(),
    updateRequirement: vi.fn(),
    confirm: vi.fn(),
  },
  generalConversationApi: {
    create: vi.fn(),
    list: vi.fn(),
    get: vi.fn(),
    remove: vi.fn(),
  },
  streamEvents: vi.fn(),
  streamGetEvents: vi.fn(),
}))

const requirement = {
  province: null,
  cities: [],
  province_preference_confirmed: false,
  city_preference_confirmed: false,
  days: null,
  interests: [],
  preference_text: null,
  travel_scope: 'auto' as const,
  start_date: null,
  daily_start_time: null,
  daily_end_time: null,
  transport_mode: 'auto' as const,
}

function planningConversation(id: string) {
  return {
    id,
    title: '新的旅行计划',
    status: 'draft' as const,
    mode: 'planning' as const,
    requirement,
    requirement_ready: false,
    requirement_confirmed: false,
    messages: [],
  }
}

function generalConversation(id: string) {
  return {
    id,
    title: '新的对话',
    status: 'active' as const,
    messages: [],
    created_at: '2026-09-17T00:00:00.000Z',
    updated_at: '2026-09-17T00:00:00.000Z',
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  sessionStorage.clear()
  vi.resetAllMocks()
  vi.mocked(conversationApi.list).mockResolvedValue([])
  vi.mocked(conversationApi.planningState).mockResolvedValue(null)
  vi.mocked(generalConversationApi.list).mockResolvedValue([])
})

describe('planning dialogue Agent boundaries', () => {
  it('refreshes expired confirmation requirements and offers a new date', async () => {
    const store = usePlanningStore()
    store.conversationMode = 'planning'
    store.conversationId = 'expired-chat'
    const expired = { ...requirement, cities: ['杭州市'], days: 2, interests: ['自然'],
      preference_text: '节奏轻松', city_preference_confirmed: true, travel_scope: 'single_city' as const,
      daily_start_time: '09:00', daily_end_time: '18:00', start_date: '2026-10-19' }
    store.requirement = expired
    store.suggestions = ['确认生成计划']
    const failure = new ApiError('出发日期必须从明天开始', 422)
    failure.status = 422
    vi.mocked(conversationApi.confirm).mockRejectedValue(failure)
    vi.mocked(conversationApi.get).mockResolvedValue({ ...planningConversation('expired-chat'),
      requirement: { ...expired, start_date: null } })
    await expect(store.confirmAndPlan()).rejects.toThrow('出发日期必须从明天开始')
    expect(store.requirement.start_date).toBeNull()
    expect(store.suggestions).toContain('明天出发')
    expect(store.suggestions).not.toContain('确认生成计划')
    expect(store.error).toBe('出发日期必须从明天开始')
    expect(streamEvents).not.toHaveBeenCalled()
  })
  it('submits the displayed requirement snapshot when confirming', async () => {
    const store = usePlanningStore()
    store.conversationMode = 'planning'
    store.conversationId = 'snapshot-chat'
    store.requirement = { ...requirement, cities: ['杭州市'], days: 2 }
    vi.mocked(conversationApi.confirm).mockRejectedValue(new Error('确认卡片已过期'))
    await expect(store.confirmAndPlan()).rejects.toThrow('过期')
    expect(conversationApi.confirm).toHaveBeenCalledWith('snapshot-chat', store.requirement)
    expect(streamEvents).not.toHaveBeenCalled()
  })

  it.each(['empty', 'disconnect', 'error'] as const)('reports %s planning response instead of leaving an empty bubble', async (kind) => {
    const store = usePlanningStore()
    store.conversationMode = 'planning'
    store.conversationId = 'agent-chat'
    store.suggestions = ['确认生成计划']
    vi.mocked(streamEvents).mockImplementation(async (_path, _body, onEvent) => {
      expect(store.suggestions).toEqual([])
      if (kind === 'empty') onEvent('message_done', { requirement_ready: false })
      if (kind === 'error') onEvent('error', { message: '需求已在其他页面更新' })
    })
    await store.sendMessage('改成苏州')
    expect(store.error).not.toBe('')
    expect(store.sending).toBe(false)
    expect(store.messages).toEqual([{ role: 'user', content: '改成苏州' }])
    expect(store.suggestions).toEqual([])
  })
})

describe('planning store conversation lifecycle', () => {
  it.each(['initialize', 'mode', 'history'])('restores a running job through %s without starting another job', async (entry) => {
    sessionStorage.setItem('travel_active_conversation_mode', 'planning')
    sessionStorage.setItem('travel_conversation_id', 'saved-chat')
    sessionStorage.setItem('travel_planning_state_saved-chat', JSON.stringify({ jobId: 'old-job', cursor: 99 }))
    vi.mocked(conversationApi.get).mockResolvedValue({ ...planningConversation('saved-chat'), status: 'confirmed' })
    vi.mocked(conversationApi.planningState).mockResolvedValue({ job_id: 'running-job', status: 'running' })
    vi.mocked(streamGetEvents).mockImplementation(async (_path, _after, event) => {
      event('planning_progress', { step: 'destination', label: '目的地已确定', status: 'completed' }, 1)
      event('planning_error', { message: '模拟后续失败' }, 2)
    })
    const store = usePlanningStore()
    if (entry === 'initialize') await store.initialize()
    else if (entry === 'mode') await store.setConversationMode('planning')
    else await store.openConversation('saved-chat', 'planning')
    expect(streamGetEvents).toHaveBeenCalledWith('/conversations/saved-chat/plan/events', 0, expect.any(Function), expect.objectContaining({ jobId: 'running-job' }))
    expect(store.error).toBe('模拟后续失败')
    expect(store.steps).toHaveLength(1)
    expect(store.stage).toBe('planning')
    expect(streamEvents).not.toHaveBeenCalled()
    expect(conversationApi.create).not.toHaveBeenCalled()
  })

  it('rebuilds progress from the beginning after a full reload instead of using an orphaned cursor', async () => {
    sessionStorage.setItem('travel_active_conversation_mode', 'planning')
    sessionStorage.setItem('travel_conversation_id', 'saved-chat')
    sessionStorage.setItem('travel_planning_state_saved-chat', JSON.stringify({ jobId: 'job-1', cursor: 80 }))
    vi.mocked(conversationApi.get).mockResolvedValue({ ...planningConversation('saved-chat'), status: 'planning' })
    vi.mocked(conversationApi.planningState).mockResolvedValue({ job_id: 'job-1', status: 'running' })
    vi.mocked(streamGetEvents).mockImplementation(async (_path, after, event) => {
      expect(after).toBe(0)
      event('planning_progress', { step: 'retrieval', label: '已完成检索', status: 'completed' }, 1)
      event('planning_progress', { step: 'schedule', label: '正在排程', status: 'running' }, 2)
      event('planning_error', { message: '测试结束' }, 3)
    })
    const store = usePlanningStore()
    await store.initialize()
    expect(store.steps.map((step) => step.step)).toEqual(['retrieval', 'schedule'])
    expect(store.error).toBe('测试结束')
    expect(streamEvents).not.toHaveBeenCalled()
  })

  it('keeps conversation history when the state endpoint fails temporarily', async () => {
    sessionStorage.setItem('travel_active_conversation_mode', 'planning')
    sessionStorage.setItem('travel_conversation_id', 'saved-chat')
    vi.mocked(conversationApi.get).mockResolvedValue(planningConversation('saved-chat'))
    vi.mocked(conversationApi.planningState).mockRejectedValue(new Error('offline'))
    const store = usePlanningStore()
    await store.initialize()
    expect(store.conversationId).toBe('saved-chat')
    expect(store.error).toContain('暂时无法读取规划任务状态')
    expect(conversationApi.create).not.toHaveBeenCalled()
  })

  it('does not request execution or replay for a draft that has no job', async () => {
    sessionStorage.setItem('travel_active_conversation_mode', 'planning')
    sessionStorage.setItem('travel_conversation_id', 'saved-chat')
    vi.mocked(conversationApi.get).mockResolvedValue(planningConversation('saved-chat'))
    const store = usePlanningStore()
    await store.initialize()
    expect(store.stage).toBe('chat')
    expect(streamGetEvents).not.toHaveBeenCalled()
    expect(streamEvents).not.toHaveBeenCalled()
  })

  it('opens the latest completed plan on initialization instead of an older cache', async () => {
    const settled = {
      ...requirement, province: '浙江省', cities: ['杭州市'], days: 1,
      interests: ['自然'], preference_text: '节奏轻松', travel_scope: 'single_city' as const,
      start_date: '2027-01-01', daily_start_time: '09:00', daily_end_time: '18:00',
    }
    const body = {
      province: '浙江省', city_stays: [{ city: '杭州市', days: 1 }],
      city_plans: [{ description: '最新方案' }], itinerary: [], evaluation: null, notices: [],
    }
    sessionStorage.setItem('travel_active_conversation_mode', 'planning')
    sessionStorage.setItem('travel_conversation_id', 'saved-chat')
    sessionStorage.setItem('travel_plan_saved-chat', JSON.stringify({ requirement: settled,
      plan: { ...body, city_plans: [{ description: '旧缓存' }] } }))
    vi.mocked(conversationApi.get).mockResolvedValue({ ...planningConversation('saved-chat'), status: 'completed', requirement: settled })
    vi.mocked(conversationApi.planningState).mockResolvedValue({ job_id: 'finished-job', status: 'completed' })
    vi.mocked(conversationApi.listPlans).mockResolvedValue([{
      id: 2, session_id: 'saved-chat', version: 2, requirement: settled, plan: body,
      evaluation: null, created_at: '2027-01-01T00:00:00Z',
    }])
    const store = usePlanningStore()
    await store.initialize()
    expect(store.stage).toBe('plan')
    expect(store.plan?.plan.city_plans).toEqual([{ description: '最新方案' }])
    expect(streamEvents).not.toHaveBeenCalled()
    expect(streamGetEvents).not.toHaveBeenCalled()
  })

  it('checks backend status on an early EOF and retries by replay rather than starting another job', async () => {
    const store = usePlanningStore()
    store.conversationId = 'job-chat'
    vi.mocked(conversationApi.get).mockResolvedValue({ ...planningConversation('job-chat'), status: 'planning' })
    vi.mocked(streamEvents).mockResolvedValue(undefined)
    await store.retryPlanning()
    expect(store.error).toContain('仍在后台运行')
    expect(store.planningRequestActive).toBe(false)
    vi.mocked(streamGetEvents).mockImplementation(async (_path, _after, event) => {
      event('planning_error', { message: '排程失败' }, 3)
    })
    await store.retryPlanning()
    expect(streamEvents).toHaveBeenCalledTimes(1)
    expect(streamGetEvents).toHaveBeenCalledTimes(1)
    expect(store.error).toBe('排程失败')
    await store.retryPlanning()
    expect(streamEvents).toHaveBeenCalledTimes(2)
  })

  it.each(['same-job', 'new-job'])('preserves progress only when replay attaches to %s', async (nextJob) => {
    const store = usePlanningStore()
    store.conversationId = 'job-chat'
    vi.mocked(streamEvents).mockImplementation(async (_path, _body, event, options) => {
      options?.onOpen?.({ jobId: 'same-job' })
      event('planning_progress', { step: 'retrieval', label: '已检索', status: 'completed' }, 1)
      event('planning_error', { message: '测试失败' }, 2)
    })
    await store.retryPlanning()
    let finish!: () => void
    vi.mocked(streamGetEvents).mockImplementation((_path, _after, event, options) => {
      options?.onOpen?.({ jobId: nextJob })
      event('planning_error', { message: '测试失败' }, 3)
      return new Promise((resolve) => { finish = resolve })
    })
    const pending = store.resumePlanning()
    expect(store.steps).toHaveLength(nextJob === 'same-job' ? 1 : 0)
    finish()
    await pending
  })

  it('does not replace an already received terminal result with a later connection failure', async () => {
    const store = usePlanningStore()
    store.conversationId = 'job-chat'
    vi.mocked(streamEvents).mockImplementation(async (_path, _body, event) => {
      event('planning_error', { message: '明确的排程失败原因' }, 4)
      throw new Error('connection closed')
    })
    await store.retryPlanning()
    expect(store.error).toBe('明确的排程失败原因')
    expect(conversationApi.get).not.toHaveBeenCalled()
  })

  it('keeps recovery as replay when a completed plan cannot yet be loaded', async () => {
    const store = usePlanningStore()
    store.conversationId = 'job-chat'
    vi.mocked(streamEvents).mockResolvedValue(undefined)
    vi.mocked(streamGetEvents).mockResolvedValue(undefined)
    vi.mocked(conversationApi.get).mockResolvedValue({ ...planningConversation('job-chat'), status: 'completed' })
    vi.mocked(conversationApi.listPlans).mockRejectedValue(new Error('offline'))
    await store.retryPlanning()
    expect(store.error).toContain('暂时无法读取方案')
    await store.retryPlanning()
    expect(streamEvents).toHaveBeenCalledTimes(1)
    expect(streamGetEvents).toHaveBeenCalledTimes(1)
  })

  it('loads the server result rather than an older compatible cache after losing the terminal event', async () => {
    const settled = {
      ...requirement, province: '浙江省', cities: ['杭州市'], days: 1,
      interests: ['自然'], preference_text: '节奏轻松', travel_scope: 'single_city' as const,
      start_date: '2027-01-01', daily_start_time: '09:00', daily_end_time: '18:00',
    }
    const oldPlan = {
      province: '浙江省', city_stays: [{ city: '杭州市', days: 1 }],
      city_plans: [{ description: '旧方案' }], itinerary: [], evaluation: null, notices: [],
    }
    const newPlan = { ...oldPlan, city_plans: [{ description: '最新方案' }] }
    sessionStorage.setItem('travel_plan_job-chat', JSON.stringify({ requirement: settled, plan: oldPlan }))
    const store = usePlanningStore()
    store.conversationId = 'job-chat'
    vi.mocked(streamEvents).mockResolvedValue(undefined)
    vi.mocked(conversationApi.get).mockResolvedValue({
      ...planningConversation('job-chat'), status: 'completed', requirement: settled,
    })
    vi.mocked(conversationApi.listPlans).mockResolvedValue([{
      id: 2, session_id: 'job-chat', version: 2, requirement: settled,
      plan: newPlan, evaluation: null, created_at: '2027-01-01T00:00:00Z',
    }])
    await store.retryPlanning()
    expect(conversationApi.listPlans).toHaveBeenCalledWith('job-chat')
    expect(store.plan?.plan.city_plans).toEqual([{ description: '最新方案' }])
    expect(store.stage).toBe('plan')
    expect(store.error).toBe('')
  })

  it('does not start another job when both the stream and status lookup fail', async () => {
    const store = usePlanningStore()
    store.conversationId = 'job-chat'
    vi.mocked(streamEvents).mockRejectedValue(new Error('offline'))
    vi.mocked(conversationApi.get).mockRejectedValue(new Error('offline'))
    vi.mocked(streamGetEvents).mockResolvedValue(undefined)
    await store.retryPlanning()
    await store.retryPlanning()
    expect(streamEvents).toHaveBeenCalledTimes(1)
    expect(streamGetEvents).toHaveBeenCalledTimes(1)
    expect(store.planningRequestActive).toBe(false)
  })

  it.each(['start', 'resume'] as const)('shares job tracking and prevents duplicate %s requests', async (action) => {
    const store = usePlanningStore()
    store.conversationId = 'planning-job'
    let finish!: () => void
    const pending = new Promise<void>((resolve) => { finish = resolve })
    if (action === 'start') {
      vi.mocked(streamEvents).mockImplementation((_path, _body, onEvent, options) => {
        options?.onOpen?.({ jobId: 'job-1' })
        onEvent('planning_progress', { step: 'test', label: '测试', status: 'completed' }, 7)
        return pending
      })
    } else {
      vi.mocked(streamGetEvents).mockImplementation((_path, _after, onEvent, options) => {
        options?.onOpen?.({ jobId: 'job-1' })
        onEvent('planning_progress', { step: 'test', label: '测试', status: 'completed' }, 7)
        return pending
      })
    }
    const request = action === 'start' ? store.retryPlanning() : store.resumePlanning()
    expect(store.planningRequestActive).toBe(true)
    await store.resumePlanning()
    expect(action === 'start' ? streamEvents : streamGetEvents).toHaveBeenCalledTimes(1)
    expect(JSON.parse(sessionStorage.getItem('travel_planning_state_planning-job')!))
      .toEqual({ jobId: 'job-1', cursor: 7 })
    finish()
    await request
    expect(store.planningRequestActive).toBe(false)
    expect(conversationApi.list).toHaveBeenCalled()
  })

  it('creates a fresh target-mode conversation from the welcome mode selector', async () => {
    vi.mocked(conversationApi.create).mockResolvedValue(planningConversation('new-planning'))
    vi.mocked(conversationApi.get).mockResolvedValue(planningConversation('old-planning'))
    sessionStorage.setItem('travel_conversation_id', 'old-planning')

    const store = usePlanningStore()
    await store.setConversationMode('planning', { createNew: true })

    expect(conversationApi.create).toHaveBeenCalledTimes(1)
    expect(conversationApi.get).not.toHaveBeenCalled()
    expect(store.conversationId).toBe('new-planning')
    expect(store.conversationMode).toBe('planning')
    expect(store.messages).toEqual([])
  })

  it('keeps history recovery when mode switching is requested without createNew', async () => {
    vi.mocked(conversationApi.get).mockResolvedValue(planningConversation('old-planning'))
    sessionStorage.setItem('travel_conversation_id', 'old-planning')

    const store = usePlanningStore()
    await store.setConversationMode('planning')

    expect(conversationApi.get).toHaveBeenCalledWith('old-planning')
    expect(conversationApi.create).not.toHaveBeenCalled()
    expect(store.conversationId).toBe('old-planning')
  })

  it('does not send duplicate create requests while a new conversation is pending', async () => {
    let resolveCreate!: (value: ReturnType<typeof generalConversation>) => void
    vi.mocked(generalConversationApi.create).mockReturnValue(
      new Promise((resolve) => {
        resolveCreate = resolve
      }),
    )

    const store = usePlanningStore()
    const first = store.createConversation('general')
    const second = store.createConversation('general')

    expect(generalConversationApi.create).toHaveBeenCalledTimes(1)
    resolveCreate(generalConversation('new-general'))
    await Promise.all([first, second])
    expect(store.conversationId).toBe('new-general')
    expect(store.conversationCreating).toBe(false)
  })

  it('restores a persisted ordinary-mode tool card after switching conversations', async () => {
    const card = {
      type: 'weather' as const,
      weather: [{
        city: '福州市',
        date: '2026-09-23',
        condition: '多云',
        temperature_min: 25,
        temperature_max: 32,
        precipitation_probability: 0,
        wind_level: '2',
        status: 'available' as const,
        source: 'qweather' as const,
      }],
    }
    vi.mocked(generalConversationApi.create).mockResolvedValue(generalConversation('general-1'))
    vi.mocked(streamEvents).mockImplementation(async (_path, _body, onEvent) => {
      onEvent('tool_card', card)
      onEvent('message_delta', { text: '福州天气' })
      onEvent('message_done', { mode: 'general', status: 'completed' })
    })

    const store = usePlanningStore()
    await store.createConversation('general')
    await store.sendMessage('福州明天天气')
    expect(store.messages[1].card).toEqual(card)

    vi.mocked(generalConversationApi.get).mockImplementation(async (id) =>
      generalConversation(id),
    )
    await store.openConversation('general-2', 'general')
    vi.mocked(generalConversationApi.get).mockResolvedValue({
      ...generalConversation('general-1'),
      messages: [
        { role: 'user', content: '福州明天天气', id: 1, sequence_number: 1 },
        { role: 'assistant', content: '福州天气', id: 2, sequence_number: 2, card },
      ],
    })
    await store.openConversation('general-1', 'general')

    expect(store.messages[1].card).toEqual(card)
  })

  it.each(['empty', 'eof', 'failed', 'partial', 'success'])('handles ordinary reply outcome: %s', async (scenario) => {
    vi.mocked(generalConversationApi.create).mockResolvedValue(generalConversation('general-failure'))
    vi.mocked(streamEvents).mockImplementation(async (_path, _body, onEvent) => {
      if (scenario === 'partial' || scenario === 'success') onEvent('message_delta', { text: '回答' })
      if (scenario === 'failed') {
        onEvent('message_delta', { text: '本次回复生成失败，请重试。' })
        onEvent('message_error', { message: '本次回复生成失败，请重试。', retryable: true })
      }
      if (scenario !== 'eof' && scenario !== 'partial') onEvent('message_done', { mode: 'general' })
    })
    const store = usePlanningStore()
    await store.createConversation('general')
    await store.sendMessage('杭州有东湖吗')
    expect(store.sending).toBe(false)
    expect(store.messages.some((message) => message.streaming)).toBe(false)
    expect(store.error.length > 0).toBe(scenario !== 'success')
    if (scenario === 'empty' || scenario === 'eof') expect(store.messages).toHaveLength(1)
    if (scenario === 'partial' || scenario === 'success') expect(store.messages[1].content).toBe('回答')
    if (scenario === 'failed') expect(store.messages[1].content).toContain('请重试')
  })

  it('buffers cards until text rendering ends and exposes lightweight progress', async () => {
    vi.useFakeTimers()
    try {
      vi.mocked(generalConversationApi.create).mockResolvedValue(generalConversation('buffered'))
      const card = { type: 'collection' as const, cards: [] }
      vi.mocked(streamEvents).mockImplementation(async (_path, _body, onEvent) => {
        onEvent('message_progress', { message: '正在查询路线' })
        onEvent('tool_card', card)
        onEvent('message_delta', { text: '路线回答' })
        onEvent('message_done', { mode: 'general' })
      })
      const store = usePlanningStore()
      await store.createConversation('general')
      const pending = store.sendMessage('怎么走')
      await Promise.resolve()
      expect(store.messages[1].progress).toBe('正在查询路线')
      expect(store.messages[1].card).toBeUndefined()
      await vi.runAllTimersAsync()
      await pending
      expect(store.messages[1].content).toBe('路线回答')
      expect(store.messages[1].card).toEqual(card)
    } finally { vi.useRealTimers() }
  })

  it('cancels an ordinary request without showing a pending card or retrying', async () => {
    vi.mocked(generalConversationApi.create).mockResolvedValue(generalConversation('cancelled'))
    vi.mocked(streamEvents).mockImplementation((_path, _body, onEvent, options) => {
      onEvent('tool_card', { type: 'collection', cards: [] })
      return new Promise<void>((_resolve, reject) => {
        options?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      })
    })
    const store = usePlanningStore()
    await store.createConversation('general')
    const pending = store.sendMessage('查天气')
    store.cancelMessage()
    await pending
    expect(store.sending).toBe(false)
    expect(store.error).toBe('')
    expect(store.messages[1].content).toContain('已停止生成')
    expect(store.messages[1].card).toBeUndefined()
    expect(streamEvents).toHaveBeenCalledTimes(1)
  })
})
