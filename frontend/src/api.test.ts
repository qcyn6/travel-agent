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
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, { status: 200 })))
    const received: Array<[string, unknown]> = []

    await streamEvents('/conversations/demo/messages', { message: '云南' }, (event, data) => {
      received.push([event, data])
    })

    expect(received).toEqual([
      ['message_delta', { text: '云' }],
      ['message_done', { requirement_ready: true }],
    ])
  })

  it('reconnects to a replay endpoint with an event cursor', async () => {
    const encoder = new TextEncoder()
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('event: planning_progress\ndata: {"step":"destination"}\n\n'))
        controller.close()
      },
    })
    const fetchMock = vi.fn().mockResolvedValue(new Response(body, { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const received: Array<[string, unknown]> = []

    await streamGetEvents('/conversations/demo/plan/events', 7, (event, data) => {
      received.push([event, data])
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/conversations/demo/plan/events?after=7',
      expect.objectContaining({ method: 'GET' }),
    )
    expect(received).toEqual([['planning_progress', { step: 'destination' }]])
  })
})
