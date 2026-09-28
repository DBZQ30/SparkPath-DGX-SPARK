// tests/unit/trace.test.js —— utils/trace.js 纯逻辑单测
// 覆盖：格式化函数、answerKey 归一化、stepOf 事件分拍、summarize 摘要与多 skill 去重。
'use strict'

const {
  TRACE_KEY,
  TRACE_INDEX_KEY,
  answerKey,
  STEP_DEFS,
  summarize,
  stepOf,
  formatDuration,
  formatClock,
} = require('../../utils/trace')

describe('formatDuration', () => {
  test.each([
    [12500, '12.5s'],
    [100, '0.1s'],
    [0, '0.0s'],
    [-500, '0.0s'],          // 负数钳到 0
    ['abc', '0.0s'],         // 非数值容错
    [null, '0.0s'],
  ])('%p → %s', (ms, expected) => {
    expect(formatDuration(ms)).toBe(expected)
  })
})

describe('formatClock', () => {
  test.each([
    [12480, '00:12.4'],
    [0, '00:00.0'],
    [75000, '01:15.0'],
    [59940, '00:59.9'],
    [-1, '00:00.0'],
    ['x', '00:00.0'],
  ])('%p → %s', (ms, expected) => {
    expect(formatClock(ms)).toBe(expected)
  })
})

describe('answerKey', () => {
  test('null / undefined / 空串 → 空串', () => {
    expect(answerKey(null)).toBe('')
    expect(answerKey(undefined)).toBe('')
    expect(answerKey('')).toBe('')
  })

  test('去 HTML 标签与实体、压空白', () => {
    expect(answerKey('<p>你好&nbsp;世界</p>')).toBe('你好 世界')
    expect(answerKey('&amp;&lt;&gt;')).toBe('&<>')
    expect(answerKey('a\n\t b   c')).toBe('a b c')
  })

  test('截断到 120 字（先归一化再截断）', () => {
    const raw = 'x'.repeat(200)
    expect(answerKey(raw)).toHaveLength(120)
    // 标签在截断前已被压缩成空格，不会残留 '<'
    expect(answerKey(`<b>${raw}</b>`)).not.toContain('<')
  })

  test('非字符串输入走 String() 容错', () => {
    expect(answerKey(12345)).toBe('12345')
  })
})

describe('stepOf', () => {
  test.each([
    [{ type: 'user' }, false, 'ask'],
    [{ type: 'reasoning' }, false, 'think'],
    [{ type: 'reasoning' }, true, 'reflect'],   // 工具跑过后的 reasoning 归「思考执行」
    [{ type: 'skill' }, false, 'skill'],
    [{ type: 'tool.start' }, false, 'flow'],
    [{ type: 'tool.done' }, false, 'flow'],
    [{ type: 'files' }, false, 'flow'],
    [{ type: 'result' }, false, 'result'],
    [{ type: 'end' }, false, 'result'],
    [{ type: 'unknown-type' }, false, ''],      // 未知类型 → 空串（聚合时跳过）
  ])('%j hasToolRun=%p → %s', (ev, hasToolRun, expected) => {
    expect(stepOf(ev, hasToolRun)).toBe(expected)
  })
})

describe('STEP_DEFS', () => {
  test('六拍定义齐全且顺序稳定（与轨迹页展示一一对应）', () => {
    expect(STEP_DEFS.map((s) => s.key)).toEqual(
      ['ask', 'think', 'skill', 'flow', 'reflect', 'result'])
    for (const def of STEP_DEFS) {
      expect(def.icon).toBeTruthy()
      expect(def.title).toBeTruthy()
    }
  })
})

describe('summarize', () => {
  test('空轨迹 / 无事件 → null', () => {
    expect(summarize(null)).toBeNull()
    expect(summarize({})).toBeNull()
    expect(summarize({ events: [] })).toBeNull()
  })

  test('完整轨迹：分拍、去重、首尾耗时', () => {
    const trace = {
      id: 'run-1',
      title: '测试轨迹',
      durationMs: 12480,
      events: [
        { t: 0, type: 'user' },
        { t: 420, type: 'reasoning', text: '思考中' },
        { t: 2100, type: 'reasoning', text: '思考中2' },
        { t: 2500, type: 'skill', name: 'academic-warning' },
        { t: 2760, type: 'tool.start', tool: 'bash', title: 'precheck' },
        { t: 3120, type: 'tool.done', title: 'precheck', duration_s: 0.4 },
        { t: 6260, type: 'reasoning', text: '读回结果再推理' },   // tool.done 之后 → reflect
        { t: 9200, type: 'result', answer: '答案' },
        { t: 12480, type: 'end' },
      ],
    }
    const s = summarize(trace)
    expect(s.id).toBe('run-1')
    expect(s.title).toBe('测试轨迹')
    expect(s.totalMs).toBe(12480)
    expect(s.durationText).toBe('12.5s')
    expect(s.toolCount).toBe(1)
    expect(s.skillCount).toBe(1)
    expect(s.skillName).toBe('academic-warning')
    expect(s.skillNames).toEqual(['academic-warning'])
    // ask 被聚合跳过，分拍顺序：think → skill → flow → reflect → result
    expect(s.steps.map((x) => x.key)).toEqual(['think', 'skill', 'flow', 'reflect', 'result'])
    expect(s.hasAsk).toBe(true)
    expect(s.hasReflect).toBe(true)
    // think 拍跨度 420→2100 = 1680ms ≥ 300 → 带耗时；skill 拍单事件跨度 0 → 无 meta
    const byKey = Object.fromEntries(s.steps.map((x) => [x.key, x]))
    expect(byKey.think.meta).toBe('1.7s')
    expect(byKey.skill.meta).toBe('')
  })

  test('多 skill 去重并保持出现顺序', () => {
    const s = summarize({
      durationMs: 1000,
      events: [
        { t: 0, type: 'user' },
        { t: 10, type: 'skill', name: 'b-skill' },
        { t: 20, type: 'skill', name: 'a-skill' },
        { t: 30, type: 'skill', name: 'b-skill' },   // 重复
        { t: 40, type: 'skill', name: '' },          // 空名忽略
        { t: 50, type: 'result', answer: 'ok' },
      ],
    })
    expect(s.skillNames).toEqual(['b-skill', 'a-skill'])
    expect(s.skillCount).toBe(2)
    expect(s.skillName).toBe('b-skill')
    expect(s.steps.find((x) => x.key === 'skill').title).toBe('命中 Skill · 2 个')
  })

  test('skill 事件无名字时标题不带名字；无工具时 hasReflect=false', () => {
    const s = summarize({
      durationMs: 100,
      events: [
        { t: 0, type: 'user' },
        { t: 10, type: 'reasoning', text: '只思考' },
        { t: 20, type: 'skill', name: '' },        // 空名 skill 事件：拍存在但不计数
        { t: 90, type: 'result', answer: 'ok' },
      ],
    })
    expect(s.skillNames).toEqual([])
    expect(s.steps.find((x) => x.key === 'skill').title).toBe('命中 Skill')
    expect(s.hasReflect).toBe(false)
    expect(s.hasAsk).toBe(true)
  })

  test('durationMs 缺失时回退用最后事件时间', () => {
    const s = summarize({
      events: [
        { t: 0, type: 'user' },
        { t: 2500, type: 'result', answer: 'ok' },
      ],
    })
    expect(s.totalMs).toBe(2500)
  })

  test('标题缺失回退「执行过程」，id 缺失回退空串', () => {
    const s = summarize({
      events: [{ t: 0, type: 'user' }, { t: 100, type: 'result', answer: 'ok' }],
    })
    expect(s.id).toBe('')
    expect(s.title).toBe('执行过程')
  })
})

describe('存储键按实例派生', () => {
  // PREFIX 来自被 mock 的 tests/fixtures/instance.js（INSTANCE_ID=jest-instance）
  test('TRACE_KEY / TRACE_INDEX_KEY 带实例前缀', () => {
    expect(TRACE_KEY).toBe('jest-instance_last_trace')
    expect(TRACE_INDEX_KEY).toBe('jest-instance_trace_index')
  })
})
