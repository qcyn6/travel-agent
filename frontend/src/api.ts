import type {
  Conversation,
  ConversationCreated,
  ConversationSummary,
  RequirementDraft,
  SavedTravelPlan,
  TravelScope,
} from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1'

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
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
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
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: body ? JSON.stringify(body) : undefined,
  })
  return consumeEventStream(response, onEvent, options.onOpen)
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
    headers: { Accept: 'text/event-stream' },
  })
  return consumeEventStream(response, onEvent, options.onOpen)
}
