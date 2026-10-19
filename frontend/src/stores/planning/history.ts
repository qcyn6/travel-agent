import { generalConversationApi } from '../../api'
import type { ChatMessage, RouteChatCard } from '../../types'

export function normalizePlanningReply(text: string): string {
  return text.replace(/\\r\\n|\\n|\\r/g, '\n')
}

export function normalizeMessages(items: ChatMessage[], planning = false): ChatMessage[] {
  return items.map((message) => {
    if (message.role !== 'assistant') return message
    return {
      ...message,
      content: (planning ? normalizePlanningReply(message.content) : message.content)
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

export function migrateLegacyGeneralRouteCards(conversationId: string, items: ChatMessage[]): ChatMessage[] {
  const cacheKey = `travel_general_cards_${conversationId}`
  let cached: unknown
  try {
    cached = JSON.parse(sessionStorage.getItem(cacheKey) || '[]')
  } catch {
    sessionStorage.removeItem(cacheKey)
    return items
  }
  if (!Array.isArray(cached)) {
    sessionStorage.removeItem(cacheKey)
    return items
  }

  const restored = [...items]
  const migrations: Promise<void>[] = []
  for (const entry of cached) {
    const route = entry?.card?.type === 'route' ? entry.card.route : null
    if (
      !Number.isInteger(entry?.index)
      || entry.index < 0
      || !route
      || !['walking', 'transit', 'driving'].includes(route.mode)
    ) continue
    const message = restored[entry.index]
    if (message?.role !== 'assistant') continue
    if (message.card?.type === 'collection') {
      const cards = [...message.card.cards]
      const routeCandidates = cards.flatMap((item, index) => item.type === 'route' ? [{ item, index }] : [])
      // A collection may contain several routes. Never replace its first
      // route merely because a legacy cache entry is also a route card.
      const matched = route.origin_poi_id && route.destination_poi_id
        ? routeCandidates.find(({ item }) => (
          item.route.origin_poi_id === route.origin_poi_id
          && item.route.destination_poi_id === route.destination_poi_id
        ))
        : undefined
      const routeIndex = matched?.index ?? (routeCandidates.length === 1 ? routeCandidates[0].index : -1)
      if (routeIndex < 0) continue
      cards[routeIndex] = entry.card as RouteChatCard
      restored[entry.index] = { ...message, card: { type: 'collection', cards } }
    } else if (message.card?.type === 'route') {
      restored[entry.index] = { ...message, card: entry.card as RouteChatCard }
    } else {
      continue
    }
    const sequenceNumber = message.sequence_number ?? entry.index + 1
    migrations.push(
      generalConversationApi
        .updateMessageCard(conversationId, sequenceNumber, entry.card as RouteChatCard)
        .then(() => undefined),
    )
  }

  if (!migrations.length) {
    sessionStorage.removeItem(cacheKey)
  } else {
    void Promise.all(migrations).then(
      () => sessionStorage.removeItem(cacheKey),
      () => undefined,
    )
  }
  return restored
}
