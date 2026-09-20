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
}

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
  weather: WeatherResult | null
  schedule: DaySchedule | null
  description: string
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
  mode: 'walking' | 'transit' | 'driving'
  // Legacy values are accepted when rendering historical saved plans.
  source: 'amap_mcp' | 'amap' | 'haversine' | 'unavailable'
  estimated: boolean
}

export interface AttractionStop {
  poi_id: string
  name: string
  city: string
  category: string | null
  description: string | null
  longitude: number | null
  latitude: number | null
  visit_duration_minutes: number
  start_time: string | null
  end_time: string | null
}

export interface TravelPlanResponse {
  requirement: RequirementDraft
  plan: {
    province: string | null
    city_stays: CityStay[]
    city_plans: unknown[]
    itinerary: TravelDay[]
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
  type: 'preference' | 'schedule_density' | 'route' | 'weather'
  severity: 'low' | 'medium' | 'high'
  day: number | null
  message: string
  suggestion: string | null
}

export interface EvaluationResult {
  passed: boolean
  issues: EvaluationIssue[]
}

export interface PlanningStep {
  step: string
  label: string
  status: 'running' | 'completed' | 'failed'
}

export interface DestinationChoice {
  requirement: RequirementDraft
  options: {
    city_stays: CityStay[]
    alternative_city_stays: CityStay[] | null
    requires_choice: boolean
  }
}
