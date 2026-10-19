export type TravelScope = 'single_city' | 'multi_city' | 'auto'
export type TransportMode = 'auto' | 'walking' | 'transit' | 'driving'
export type ConversationMode = 'general' | 'planning'
export type AppStage = 'chat' | 'planning' | 'plan'

export interface RequirementDraft {
  province: string | null
  cities: string[]
  province_preference_confirmed: boolean
  city_preference_confirmed: boolean
  days: number | null
  interests: string[]
  preference_text: string | null
  travel_scope: TravelScope
  start_date: string | null
  daily_start_time: string | null
  daily_end_time: string | null
  transport_mode: TransportMode
}

export interface ChatMessage {
  id?: number
  sequence_number?: number
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
  progress?: string
  card?: ChatToolCard | null
}

export interface WeatherChatCard {
  type: 'weather'
  weather: WeatherResult | WeatherResult[]
}

export interface RouteChatCard {
  type: 'route'
  route: RouteSegment
}

export interface NearbyPlaceResult {
  poi_id: string
  name: string
  address: string | null
  category: string | null
  longitude: number
  latitude: number
  distance_m: number | null
}

export interface NearbySearchResult {
  center_name: string
  center_longitude: number
  center_latitude: number
  category: string
  radius_m: number
  places: NearbyPlaceResult[]
}

export interface NearbyChatCard {
  type: 'nearby'
  nearby: NearbySearchResult
}

export type SingleChatToolCard = WeatherChatCard | RouteChatCard | NearbyChatCard

export interface ChatToolCardCollection {
  type: 'collection'
  cards: SingleChatToolCard[]
}

export type ChatToolCard = SingleChatToolCard | ChatToolCardCollection

export interface Conversation {
  id: string
  title: string
  status: 'draft' | 'confirmed' | 'planning' | 'completed'
  mode: 'planning'
  requirement: RequirementDraft
  requirement_ready: boolean
  requirement_confirmed: boolean
  messages: ChatMessage[]
}

export interface ConversationCreated {
  id: string
  title: string
  status: 'draft'
  requirement: RequirementDraft
}

export interface ConversationSummary {
  id: string
  title: string
  status: Conversation['status'] | 'active'
  mode: ConversationMode
  updated_at: string
}

export interface GeneralConversation {
  id: string
  title: string
  status: 'active'
  messages: ChatMessage[]
  created_at: string
  updated_at: string
}

export interface CityStay {
  city: string
  days: number
}

export interface TravelDay {
  day: number
  city: string
  city_adcode: string
  city_day: number
  date: string | null
  attractions: AttractionStop[]
  routes: RouteSegment[]
  start_hotel?: HotelStay | null
  start_hotel_route?: RouteSegment | null
  hotel?: HotelStay | null
  hotel_route?: RouteSegment | null
  weather: WeatherResult | null
  schedule: DaySchedule | null
  description: string
}

export interface HotelStay {
  poi_id: string
  name: string
  city: string
  address?: string | null
  rating?: number | null
  longitude: number | null
  latitude: number | null
  image_url?: string | null
  source: 'amap' | 'amap_mcp' | 'unavailable'
}

export interface DaySchedule {
  start_time: string
  end_time: string
  visit_minutes: number
  route_minutes: number
  meal_minutes: number
  buffer_minutes: number
  total_minutes: number
  status: 'complete' | 'estimated' | 'incomplete'
}

export interface WeatherResult {
  city: string
  date: string | null
  condition: string | null
  temperature_min: number | null
  temperature_max: number | null
  precipitation_probability: number | null
  wind_level: string | null
  travel_advice?: string | null
  travel_advice_source?: 'ai' | 'rule_based' | null
  status: 'available' | 'skipped' | 'unavailable'
  source: 'amap_mcp' | 'qweather' | 'none'
}

export interface RouteSegment {
  origin_poi_id: string
  origin_name: string
  destination_poi_id: string
  destination_name: string
  distance_m: number | null
  duration_min: number | null
  origin_longitude?: number | null
  origin_latitude?: number | null
  destination_longitude?: number | null
  destination_latitude?: number | null
  origin_city?: string | null
  destination_city?: string | null
  mode: 'walking' | 'transit' | 'driving'
  // Legacy values are accepted when rendering historical saved plans.
  source: 'amap_mcp' | 'amap' | 'haversine' | 'unavailable'
  estimated: boolean
  polyline?: Array<[number, number]>
}

export interface AttractionStop {
  poi_id: string
  name: string
  city: string
  category: string | null
  description: string | null
  image_url?: string | null
  longitude: number | null
  latitude: number | null
  visit_duration_minutes: number
  opening_hours_weekly?: string | null
  opening_hours_today?: string | null
  opening_hours_status?: 'available' | 'unavailable' | 'not_checked'
  opening_hours_applied?: boolean
  start_time: string | null
  end_time: string | null
}

export interface ReplacementCandidate extends AttractionStop {
  preference_score: number
  tag_match_count?: number
  interest_tags?: string[]
  final_rank?: number
}

export interface AttractionReplacementOption {
  candidate: ReplacementCandidate
  distance_m: number | null
  distance_score: number
  priority_score: number
}

export interface AttractionReplacementRequest {
  day: number
  current_poi_id: string
  replacement_poi_id: string
}

export interface AttractionReplacementPreview {
  result: TravelPlanResponse
  warnings: string[]
}

export interface TravelPlanResponse {
  requirement: RequirementDraft
  plan: {
    province: string | null
    city_stays: CityStay[]
    city_plans: unknown[]
    itinerary: TravelDay[]
    replacement_candidates_by_city?: Record<string, ReplacementCandidate[]>
    manual_adjustment_warnings?: Record<string, string[]>
    partial_knowledge_cities?: string[]
    evaluation: EvaluationResult | null
    notices: DegradationNotice[]
  }
}

export interface SavedTravelPlan {
  id: number
  session_id: string
  version: number
  requirement: RequirementDraft
  plan: TravelPlanResponse['plan']
  evaluation: EvaluationResult | null
  created_at: string
}

export interface DegradationNotice {
  code: 'route_estimated' | 'route_unavailable' | 'weather_skipped' | 'weather_unavailable' | 'knowledge_partial' | 'schedule_incomplete'
  level: 'info' | 'warning'
  day: number | null
  message: string
}

export interface EvaluationIssue {
  type: 'preference' | 'schedule_density' | 'route' | 'weather' | 'schedule' | 'manual_adjustment' | 'review'
  severity: 'low' | 'medium' | 'high'
  day: number | null
  message: string
  suggestion: string | null
}

export interface EvaluationResult {
  passed: boolean
  issues: EvaluationIssue[]
  daily_metrics?: DayEvaluationMetric[]
}

export interface DayEvaluationMetric {
  day: number
  attraction_count: number
  preference_coverage?: number | null
  route_minutes: number
  route_segments: number
  estimated_route_ratio: number
}

export interface PlanningStep {
  step: string
  label: string
  status: 'running' | 'completed' | 'failed'
}
