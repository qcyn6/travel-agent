import type {
  Conversation,
  ConversationCreated,
  ConversationSummary,
  AttractionReplacementOption,
  AttractionReplacementPreview,
  AttractionReplacementRequest,
  GeneralConversation,
  RequirementDraft,
  RouteChatCard,
  SavedTravelPlan,
  TravelScope,
} from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1'
const ACCESS_TOKEN_KEY = 'travel_access_token'

export function getAccessToken() {
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}

export function setAccessToken(token: string) {
  localStorage.setItem(ACCESS_TOKEN_KEY, token)
}

export function clearAccessToken() {
  localStorage.removeItem(ACCESS_TOKEN_KEY)
}

export interface EventStreamMeta {
  jobId: string | null
}

export interface EventStreamOptions {
  after?: number
  jobId?: string | null
  onOpen?: (meta: EventStreamMeta) => void
}

type EventHandler = (event: string, data: any, sequence?: number) => void

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message)
  }
}

async function decodeError(response: Response): Promise<never> {
  let message = `请求失败（${response.status}）`
  try {
    const body = await response.json()
    message = body.detail || message
  } catch {
    // Keep the readable fallback.
  }
  throw new ApiError(message, response.status)
}

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getAccessToken()
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  })
  if (!response.ok) return decodeError(response)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const conversationApi = {
  create: () => jsonRequest<ConversationCreated>('/conversations', { method: 'POST' }),
  list: (limit = 30) => jsonRequest<ConversationSummary[]>(`/conversations?limit=${limit}`),
  get: (id: string) => jsonRequest<Conversation>(`/conversations/${id}`),
  remove: (id: string) => jsonRequest<void>(`/conversations/${id}`, { method: 'DELETE' }),
  listPlans: (id: string) => jsonRequest<SavedTravelPlan[]>(`/conversations/${id}/plans`),
  replacementOptions: (id: string, day: number, currentPoiId: string) =>
    jsonRequest<AttractionReplacementOption[]>(
      `/conversations/${id}/plans/replacement-options?day=${day}&current_poi_id=${encodeURIComponent(currentPoiId)}`,
    ),
  previewAttractionReplacement: (id: string, request: AttractionReplacementRequest) =>
    jsonRequest<AttractionReplacementPreview>(
      `/conversations/${id}/plans/attraction-replacement/preview`,
      { method: 'POST', body: JSON.stringify(request) },
    ),
  replaceAttraction: (id: string, request: AttractionReplacementRequest) =>
    jsonRequest<AttractionReplacementPreview>(
      `/conversations/${id}/plans/attraction-replacement`,
      { method: 'POST', body: JSON.stringify(request) },
    ),
  updateRequirement: (id: string, draft: RequirementDraft | { travel_scope: TravelScope }) =>
    jsonRequest<Conversation>(`/conversations/${id}/requirement`, {
      method: 'PATCH',
      body: JSON.stringify(draft),
    }),
  confirm: (id: string) =>
    jsonRequest(`/conversations/${id}/requirement/confirm`, { method: 'POST' }),
}

async function consumeEventStream(
  response: Response,
  onEvent: EventHandler,
  onOpen?: (meta: EventStreamMeta) => void,
): Promise<void> {
  if (!response.ok) return decodeError(response)
  if (!response.body) throw new ApiError('浏览器无法读取流式响应', 0)
  onOpen?.({ jobId: response.headers.get('X-Planning-Job-Id') })

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const blocks = buffer.split(/\r?\n\r?\n/)
    buffer = blocks.pop() || ''
    for (const block of blocks) {
      let event = 'message'
      let eventId: number | undefined
      const dataLines: string[] = []
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        if (line.startsWith('id:')) {
          const parsed = Number.parseInt(line.slice(3).trim(), 10)
          if (Number.isFinite(parsed) && parsed >= 0) eventId = parsed
        }
        if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
      }
      if (dataLines.length) onEvent(event, JSON.parse(dataLines.join('\n')), eventId)
    }
    if (done) break
  }
}

export async function streamEvents(
  path: string,
  body: object | undefined,
  onEvent: EventHandler,
  options: EventStreamOptions = {},
): Promise<void> {
  const separator = path.includes('?') ? '&' : '?'
  const query = options.after === undefined
    ? ''
    : `${separator}after=${Math.max(0, options.after)}${options.jobId ? `&job_id=${encodeURIComponent(options.jobId)}` : ''}`
  const response = await fetch(`${API_BASE}${path}${query}`, {
    // Keep the cursor and job identity together. The server resets the
    // cursor automatically when a retry created a new job.
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(getAccessToken() ? { Authorization: `Bearer ${getAccessToken()}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  })
  return consumeEventStream(response, onEvent, options.onOpen)
}

export const generalConversationApi = {
  create: () => jsonRequest<GeneralConversation>('/general-conversations', { method: 'POST' }),
  list: (limit = 30) => jsonRequest<ConversationSummary[]>(`/general-conversations?limit=${limit}`),
  get: (id: string) => jsonRequest<GeneralConversation>(`/general-conversations/${id}`),
  updateMessageCard: (id: string, sequenceNumber: number, card: RouteChatCard) =>
    jsonRequest<{ content: string }>(`/general-conversations/${id}/messages/${sequenceNumber}/card`, {
      method: 'PATCH',
      body: JSON.stringify({ card }),
    }),
  remove: (id: string) => jsonRequest<void>(`/general-conversations/${id}`, { method: 'DELETE' }),
}

export interface AuthUser {
  id: string
  username: string
  display_name: string
  role: 'user' | 'admin'
  is_active: boolean
  created_at: string | null
}

interface AuthTokenResponse {
  access_token: string
  token_type: string
  user: AuthUser
}

export const authApi = {
  login: async (username: string, password: string) => {
    const result = await jsonRequest<AuthTokenResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    setAccessToken(result.access_token)
    return result.user
  },
  register: async (username: string, password: string, displayName?: string) => {
    const result = await jsonRequest<AuthTokenResponse>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ username, password, display_name: displayName || undefined }),
    })
    setAccessToken(result.access_token)
    return result.user
  },
  me: () => jsonRequest<AuthUser>('/auth/me'),
  logout: () => clearAccessToken(),
}

export interface AdminUser {
  id: string
  username: string
  display_name: string
  role: string
  is_active: boolean
  created_at: string | null
}

export interface AdminConversationSummary {
  id: string
  title: string
  mode: 'general' | 'planning'
  status: string
  user_id: string | null
  user_username: string | null
  updated_at: string | null
}

export interface AdminGuideSummary {
  filename: string
  title: string
  size: number
  updated_at: string | null
}

export interface AdminGuide {
  filename: string
  title: string
  content: string
}

export interface AdminGuideIndexStatus {
  source_files: number
  source_documents: number
  indexed_documents: number
  stale_documents: number
  in_sync: boolean
}

export const adminApi = {
  users: (limit = 100) => jsonRequest<AdminUser[]>(`/admin/users?limit=${limit}`),
  userConversations: (id: string, limit = 100) =>
    jsonRequest<AdminConversationSummary[]>(`/admin/users/${encodeURIComponent(id)}/conversations?limit=${limit}`),
  conversation: (id: string, mode: 'general' | 'planning') =>
    jsonRequest<any>(`/admin/conversations/${encodeURIComponent(id)}?mode=${mode}`),
  guides: () => jsonRequest<AdminGuideSummary[]>('/admin/knowledge/guides'),
  guide: (filename: string) =>
    jsonRequest<AdminGuide>(`/admin/knowledge/guides/${encodeURIComponent(filename)}`),
  createGuide: (filename: string, content: string) =>
    jsonRequest<AdminGuide>('/admin/knowledge/guides', {
      method: 'POST',
      body: JSON.stringify({ filename, content }),
    }),
  updateGuide: (filename: string, content: string) =>
    jsonRequest<AdminGuide>(`/admin/knowledge/guides/${encodeURIComponent(filename)}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    }),
  deleteGuide: (filename: string) =>
    jsonRequest<void>(`/admin/knowledge/guides/${encodeURIComponent(filename)}`, { method: 'DELETE' }),
  guideIndexStatus: () => jsonRequest<AdminGuideIndexStatus>('/admin/knowledge/guides/index-status'),
  rebuildGuideIndex: () => jsonRequest<{ indexed_count: number }>('/admin/knowledge/guides/index', { method: 'POST' }),
  attractions: (query = '') =>
    jsonRequest<any[]>(`/admin/knowledge/attractions?limit=100${query ? `&query=${encodeURIComponent(query)}` : ''}`),
  updateAttraction: (poiId: string, description: string) =>
    jsonRequest<any>(`/admin/knowledge/attractions/${encodeURIComponent(poiId)}`, {
      method: 'PATCH',
      body: JSON.stringify({ description }),
    }),
  generatePlan: (id: string, onEvent: EventHandler) =>
    streamEvents('/admin/conversations/' + encodeURIComponent(id) + '/plan', undefined, onEvent),
}

export async function streamGetEvents(
  path: string,
  after: number,
  onEvent: EventHandler,
  options: Omit<EventStreamOptions, 'after'> = {},
): Promise<void> {
  const separator = path.includes('?') ? '&' : '?'
  const jobQuery = options.jobId ? `&job_id=${encodeURIComponent(options.jobId)}` : ''
  const response = await fetch(`${API_BASE}${path}${separator}after=${Math.max(0, after)}${jobQuery}`, {
    method: 'GET',
    headers: {
      Accept: 'text/event-stream',
      ...(getAccessToken() ? { Authorization: `Bearer ${getAccessToken()}` } : {}),
    },
  })
  return consumeEventStream(response, onEvent, options.onOpen)
}
