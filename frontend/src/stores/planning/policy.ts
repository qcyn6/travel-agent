import type { Conversation, ConversationMode, RequirementDraft, TravelPlanResponse } from '../../types'

export const emptyRequirement = (): RequirementDraft => ({
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

export function isPlanCompatible(candidate: TravelPlanResponse, conversation: Conversation) {
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

export function suggestionsFor(next: RequirementDraft, mode: ConversationMode = 'planning'): string[] {
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
  if (!next.interests.length) {
    return ['自然风光', '历史文化', '古城', '摄影', '美食', '休闲慢游']
  }
  if (!next.preference_text || ['没有额外体验偏好', '没有额外偏好', '无额外偏好', '无特殊偏好'].includes(next.preference_text)) {
    return [
      '节奏轻松，不赶时间',
      '喜欢拍照和沉浸体验',
      '优先游览代表性景点',
    ]
  }
  if (!next.start_date) return ['明天出发', '后天出发', '下周一出发']
  if (!next.daily_start_time || !next.daily_end_time) {
    return ['每天08:00到21:00', '每天09:00到18:00', '每天10:00到20:00']
  }
  return ['确认生成计划']
}
