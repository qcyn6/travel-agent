// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { generalConversationApi } from '../../api'
import type { ChatMessage, RouteChatCard } from '../../types'
import { migrateLegacyGeneralRouteCards, normalizeMessages } from './history'
import { emptyRequirement, suggestionsFor } from './policy'

vi.mock('../../api', () => ({ generalConversationApi: { updateMessageCard: vi.fn() } }))

const route = (origin: string, destination: string, mode: 'walking' | 'driving' = 'walking'): RouteChatCard => ({
  type: 'route',
  route: {
    origin_poi_id: origin, origin_name: origin,
    destination_poi_id: destination, destination_name: destination,
    distance_m: 1000, duration_min: 15, mode, source: 'amap', estimated: false,
  },
})

beforeEach(() => {
  sessionStorage.clear()
  vi.resetAllMocks()
  vi.mocked(generalConversationApi.updateMessageCard).mockResolvedValue({ content: '' })
})

describe('planning helpers', () => {
  it('creates independent requirement arrays', () => {
    const first = emptyRequirement()
    first.cities.push('杭州市')
    expect(emptyRequirement().cities).toEqual([])
  })

  it('keeps mode-specific suggestions separate', () => {
    expect(suggestionsFor(emptyRequirement(), 'general')).toEqual(['你能做什么？', '介绍一个目的地'])
    expect(suggestionsFor({ ...emptyRequirement(), cities: ['杭州市'], travel_scope: 'single_city' }))
      .toEqual(['1天', '2天', '3天'])
  })

  it('normalizes assistant history without rewriting user messages or mutating input', () => {
    const items: ChatMessage[] = [
      { role: 'user', content: '查看并确认旅行需求' },
      { role: 'assistant', content: '查看并确认旅行需求' },
    ]
    const result = normalizeMessages(items)
    expect(result[0].content).toBe(items[0].content)
    expect(result[1].content).toBe('确认生成计划')
    expect(items[1].content).toBe('查看并确认旅行需求')
  })

  it('matches a cached route by endpoints instead of overwriting the first route', async () => {
    const first = route('A', 'B')
    const replacement = route('B', 'C', 'driving')
    const items: ChatMessage[] = [{
      role: 'assistant', content: '', sequence_number: 9,
      card: { type: 'collection', cards: [first, route('B', 'C')] },
    }]
    sessionStorage.setItem('travel_general_cards_test', JSON.stringify([{ index: 0, card: replacement }]))
    const result = migrateLegacyGeneralRouteCards('test', items)
    expect(result[0].card).toEqual({ type: 'collection', cards: [first, replacement] })
    expect(items[0].card).toEqual({ type: 'collection', cards: [first, route('B', 'C')] })
    expect(generalConversationApi.updateMessageCard).toHaveBeenCalledWith('test', 9, replacement)
    await vi.waitFor(() => expect(sessionStorage.getItem('travel_general_cards_test')).toBeNull())
  })

  it('does not guess which route to overwrite in an ambiguous collection', () => {
    const items: ChatMessage[] = [{ role: 'assistant', content: '', card: {
      type: 'collection', cards: [route('A', 'B'), route('B', 'C')],
    } }]
    sessionStorage.setItem('travel_general_cards_test', JSON.stringify([{ index: 0, card: route('X', 'Y') }]))
    expect(migrateLegacyGeneralRouteCards('test', items)).toEqual(items)
    expect(generalConversationApi.updateMessageCard).not.toHaveBeenCalled()
  })

  it('retains cached data if persistence fails', async () => {
    vi.mocked(generalConversationApi.updateMessageCard).mockRejectedValue(new Error('offline'))
    const cached = JSON.stringify([{ index: 0, card: route('A', 'B', 'driving') }])
    sessionStorage.setItem('travel_general_cards_test', cached)
    migrateLegacyGeneralRouteCards('test', [{ role: 'assistant', content: '', card: route('A', 'B') }])
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(sessionStorage.getItem('travel_general_cards_test')).toBe(cached)
  })
})
