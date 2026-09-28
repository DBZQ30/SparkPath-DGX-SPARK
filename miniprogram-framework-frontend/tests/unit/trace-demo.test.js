// tests/unit/trace-demo.test.js —— 录播演示数据结构不变性
// trace-demo 是手工维护的事件流，结构漂移（t 乱序、事件契约字段缺失、telemetry 采样
// 超出轨迹时长）会让演示页静默渲染异常。这里钉住其与 trace.js 契约的一致性。
'use strict'

const { DEMO_TRACE } = require('../../utils/trace-demo')
const { summarize } = require('../../utils/trace')

const KNOWN_TYPES = new Set([
  'user', 'reasoning', 'skill', 'tool.start', 'tool.done', 'files', 'result', 'end',
])

describe('DEMO_TRACE 结构', () => {
  test('基础字段齐全', () => {
    for (const field of ['id', 'title', 'query', 'model', 'durationMs', 'events', 'telemetry']) {
      expect(DEMO_TRACE[field]).toBeDefined()
    }
    expect(DEMO_TRACE.events.length).toBeGreaterThan(0)
  })

  test('事件类型全部在契约中，t 单调不减且不超过总时长', () => {
    let prev = -1
    for (const ev of DEMO_TRACE.events) {
      expect(KNOWN_TYPES.has(ev.type)).toBe(true)
      expect(ev.t).toBeGreaterThanOrEqual(prev)
      expect(ev.t).toBeLessThanOrEqual(DEMO_TRACE.durationMs)
      prev = ev.t
    }
  })

  test('各类型事件带必需字段', () => {
    for (const ev of DEMO_TRACE.events) {
      if (ev.type === 'reasoning') expect(ev.text).toBeTruthy()
      if (ev.type === 'skill') expect(ev.name).toBeTruthy()
      if (ev.type === 'tool.start') { expect(ev.tool).toBeTruthy(); expect(ev.title).toBeTruthy() }
      if (ev.type === 'tool.done') { expect(ev.title).toBeTruthy(); expect(ev.duration_s).toBeDefined() }
      if (ev.type === 'files') expect(Array.isArray(ev.files)).toBe(true)
      if (ev.type === 'result') { expect(ev.answer).toBeTruthy(); expect(ev.metrics).toBeDefined() }
    }
  })

  test('tool.start 与 tool.done 配对（按 title）', () => {
    const started = DEMO_TRACE.events.filter((e) => e.type === 'tool.start').map((e) => e.title)
    const done = DEMO_TRACE.events.filter((e) => e.type === 'tool.done').map((e) => e.title)
    expect(done).toEqual(started)
  })

  test('事件流完整：user 开头、result 与 end 收尾', () => {
    const types = DEMO_TRACE.events.map((e) => e.type)
    expect(types[0]).toBe('user')
    expect(types[types.length - 1]).toBe('end')
    expect(types).toContain('result')
  })

  test('telemetry 采样均在轨迹时间轴内', () => {
    for (const s of DEMO_TRACE.telemetry) {
      expect(s.t).toBeGreaterThanOrEqual(0)
      expect(s.t).toBeLessThanOrEqual(DEMO_TRACE.durationMs)
      expect(typeof s.gpu).toBe('number')
      expect(typeof s.tps).toBe('number')
    }
  })
})

describe('DEMO_TRACE 与 trace.js 摘要兼容', () => {
  test('summarize 可直接消费录播数据', () => {
    const s = summarize(DEMO_TRACE)
    expect(s).not.toBeNull()
    expect(s.toolCount).toBe(3)
    expect(s.skillCount).toBe(1)
    expect(s.skillName).toBe('academic-warning')
    expect(s.hasReflect).toBe(true)   // 第一支 tool.done（t=3120）之后仍有 reasoning
    expect(s.durationText).toBe('12.5s')
    // 分拍顺序：ask 聚合跳过后 think → skill → flow → reflect → result
    expect(s.steps.map((x) => x.key)).toEqual(['think', 'skill', 'flow', 'reflect', 'result'])
    // metrics 与事件流一致（result 事件的 metrics.tools = tool.start 计数）
    const result = DEMO_TRACE.events.find((e) => e.type === 'result')
    expect(result.metrics.tools).toBe(s.toolCount)
    expect(result.metrics.skills).toBe(s.skillCount)
  })
})
