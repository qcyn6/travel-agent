// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'

import { adminApi, conversationApi, streamEvents, streamGetEvents } from './api'

describe('readable API errors', () => {
  afterEach(() => vi.unstubAllGlobals())

  it.each([
    [{ detail: [{ loc: ['body', 'start_date'], msg: 'Value error, 出发日期必须从明天开始' }] }, '出发日期必须从明天开始'],
    [{ detail: [{ msg: '参数缺失' }, { msg: '参数缺失' }, { msg: '时间不合法' }] }, '参数缺失；时间不合法'],
    [{ detail: { message: '需求已变更' } }, '需求已变更'],
    [{ detail: { unexpected: 'value' } }, '请求失败（422）'],
    [{ detail: '请重新确认需求' }, '请重新确认需求'],
  ])('formats server error %j as human text', async (body, message) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body), {
      status: 422, headers: { 'Content-Type': 'application/json' },
    })))
    await expect(conversationApi.get('test')).rejects.toThrow(message)
  })
})


describe('streamEvents', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('cancels and releases the reader when event decoding fails', async () => {
    const cancel = vi.fn()
    const body = new ReadableStream({
      start(controller) { controller.enqueue(new TextEncoder().encode('data: not-json\n\n')) },
      cancel,
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, { status: 200 })))
    await expect(streamEvents('/test', undefined, () => {})).rejects.toBeInstanceOf(SyntaxError)
    expect(cancel).toHaveBeenCalledTimes(1)
    expect(body.locked).toBe(false)
  })

  it('releases a normally completed response reader', async () => {
    const body = new ReadableStream({ start(controller) { controller.close() } })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, { status: 200 })))
    await streamEvents('/test', undefined, () => {})
    expect(body.locked).toBe(false)
  })

  it.each(['12oops', '-1', '9007199254740993', ''])('does not accept invalid event ID %s as a cursor', async (id) => {
    const body = `id: ${id}\nevent: planning_progress\ndata: {}\n\n`
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, { status: 200 })))
    const callback = vi.fn()
    await streamEvents('/test', undefined, callback)
    expect(callback).toHaveBeenCalledWith('planning_progress', {}, undefined)
  })

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

describe('guide revision requests', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sends the loaded version when saving a guide', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await adminApi.updateGuide('旅行指南.md', '# 新正文', 'a'.repeat(64))
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/admin/knowledge/guides/${encodeURIComponent('旅行指南.md')}`,
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ content: '# 新正文', expected_revision: 'a'.repeat(64) }) }),
    )
  })

  it('sends the loaded version when deleting a guide', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)
    await adminApi.deleteGuide('guide.md', 'b'.repeat(64))
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/admin/knowledge/guides/guide.md?expected_revision=${'b'.repeat(64)}`,
      expect.objectContaining({ method: 'DELETE' }),
    )
  })
})
