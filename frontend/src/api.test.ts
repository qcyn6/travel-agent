// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'

import { streamEvents, streamGetEvents } from './api'


describe('streamEvents', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('parses SSE event names and JSON data across chunks', async () => {
    const encoder = new TextEncoder()
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('event: message_delta\ndata: {"text":"云"}\n\n'))
        controller.enqueue(encoder.encode('event: message_done\ndata: {"requirement_ready":true}\n\n'))
        controller.close()
      },
    })
    const fetchMock = vi.fn().mockResolvedValue(new Response(body, { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const received: Array<[string, unknown]> = []

    await streamEvents('/conversations/demo/messages', { message: '云南', mode: 'planning' }, (event, data) => {
      received.push([event, data])
    })

    expect(received).toEqual([
      ['message_delta', { text: '云' }],
      ['message_done', { requirement_ready: true }],
    ])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/conversations/demo/messages',
      expect.objectContaining({ body: JSON.stringify({ message: '云南', mode: 'planning' }) }),
    )
  })

  it('reconnects to a replay endpoint with an event cursor', async () => {
    const encoder = new TextEncoder()
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('id: 8\nevent: planning_progress\ndata: {"step":"destination"}\n\n'))
        controller.close()
      },
    })
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(body, { status: 200, headers: { 'X-Planning-Job-Id': 'job-1' } }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const received: Array<[string, unknown, number | undefined]> = []
    let openedJobId: string | null = null

    await streamGetEvents(
      '/conversations/demo/plan/events',
      7,
      (event, data, sequence) => {
        received.push([event, data, sequence])
      },
      {
        jobId: 'job-1',
        onOpen: ({ jobId }) => {
          openedJobId = jobId
        },
      },
    )

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/conversations/demo/plan/events?after=7&job_id=job-1',
      expect.objectContaining({ method: 'GET' }),
    )
    expect(openedJobId).toBe('job-1')
    expect(received).toEqual([['planning_progress', { step: 'destination' }, 8]])
  })
})
