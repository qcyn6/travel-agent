import type {
  Conversation,
  ConversationCreated,
  ConversationSummary,
  RequirementDraft,
  SavedTravelPlan,
  TravelScope,
} from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1'

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

export async function streamEvents(
  path: string,
  body: object | undefined,
  onEvent: (event: string, data: any) => void,
): Promise<void> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) return decodeError(response)
  if (!response.body) throw new ApiError('浏览器无法读取流式响应', 0)

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
      const dataLines: string[] = []
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
      }
      if (dataLines.length) onEvent(event, JSON.parse(dataLines.join('\n')))
    }
    if (done) break
  }
}
