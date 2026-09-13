export type TravelScope = 'single_city' | 'multi_city' | 'auto'
export type TransportMode = 'auto' | 'walking' | 'transit' | 'driving'
export type AppStage = 'chat' | 'planning' | 'plan'

export interface RequirementDraft {
  province: string | null
  cities: string[]
  days: number | null
  interests: string[]
  preference_text: string | null
  travel_scope: TravelScope
  start_date: string | null
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
  description: string
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
  source: 'qweather' | 'none'
}

export interface RouteSegment {
  origin_poi_id: string
  origin_name: string
  destination_poi_id: string
  destination_name: string
  distance_m: number | null
  duration_min: number | null
  mode: 'walking' | 'transit' | 'driving'
  source: 'amap' | 'haversine' | 'unavailable'
  estimated: boolean
}

export interface AttractionStop {
  poi_id: string
  name: string
  city: string
  category: string | null
  longitude: number | null
  latitude: number | null
  environment: 'indoor' | 'outdoor' | 'mixed' | 'unknown'
  estimated_visit_minutes: number | null
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
  }
}

export interface PlanningStep {
  step: string
  label: string
  status: 'running' | 'completed'
}

export interface DestinationChoice {
  requirement: RequirementDraft
  options: {
    city_stays: CityStay[]
    alternative_city_stays: CityStay[] | null
    requires_choice: boolean
  }
}
