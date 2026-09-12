export type TravelScope = 'single_city' | 'multi_city' | 'auto'
export type AppStage = 'chat' | 'planning' | 'plan'

export interface RequirementDraft {
  province: string | null
  cities: string[]
  days: number | null
  interests: string[]
  preference_text: string | null
  travel_scope: TravelScope
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
  city_day: number
  attractions: string[]
  description: string
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
