// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'

import { streamEvents } from './api'


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
})
