// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { conversationApi, generalConversationApi, streamEvents } from '../api'
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
  vi.clearAllMocks()
  vi.mocked(conversationApi.list).mockResolvedValue([])
  vi.mocked(generalConversationApi.list).mockResolvedValue([])
})

describe('planning store conversation lifecycle', () => {
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

  it('restores an ordinary-mode tool card after switching conversations', async () => {
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
        { role: 'assistant', content: '福州天气', id: 2, sequence_number: 2 },
      ],
    })
    await store.openConversation('general-1', 'general')

    expect(store.messages[1].card).toEqual(card)
  })
})
