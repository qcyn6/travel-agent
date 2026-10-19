import { describe, expect, it } from 'vitest'
import { normalizeMessages, normalizePlanningReply } from './history'

describe('planning reply newline normalization', () => {
  it('decodes only newline escapes without corrupting Chinese text', () => {
    expect(normalizePlanningReply('浙江五天。\\n\\n想去哪里？\\r\\n请补充。'))
      .toBe('浙江五天。\n\n想去哪里？\n请补充。')
    expect(normalizePlanningReply('已有\n换行，保留\\t和\\u4e00'))
      .toBe('已有\n换行，保留\\t和\\u4e00')
  })

  it('repairs old planning assistant replies without rewriting users or ordinary chat', () => {
    const messages = [
      { role: 'assistant' as const, content: '浙江\\n城市' },
      { role: 'user' as const, content: '我输入\\n' },
    ]
    expect(normalizeMessages(messages, true).map(item => item.content)).toEqual(['浙江\n城市', '我输入\\n'])
    expect(normalizeMessages(messages)[0]?.content).toBe('浙江\\n城市')
    expect(messages[0]?.content).toBe('浙江\\n城市')
  })
})
