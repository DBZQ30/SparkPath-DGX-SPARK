// utils/trace.js —— 执行轨迹：事件模型、摘要与格式化（共享工具）
//
// 轨迹事件契约（与 tui_gateway 的事件流一致，t 为相对毫秒）：
//   { t, type: 'user' }                                   提问
//   { t, type: 'reasoning', text }                        思考增量（流式）
//   { t, type: 'skill', name, description? }              命中 Skill（skill.activate）
//   { t, type: 'tool.start', tool, title, cmd? }          工具开始
//   { t, type: 'tool.done',  title, duration_s, result? } 工具完成
//   { t, type: 'files', files: [name] }                   数据文件流入
//   { t, type: 'result', answer, artifacts, metrics }     结果
//   { t, type: 'end' }                                    结束
//   trace.telemetry: [{ t, gpu, vram, tps }]              算力采样（保留在契约里，轨迹页当前不展示）
'use strict'

const { PREFIX } = require('./instance-keys')

// 最近一次执行轨迹的本地缓存键（按实例派生，保证各实例互不污染）
const TRACE_KEY = `${PREFIX}_last_trace`

// 「执行过程」折叠条的本地索引：答案归一化前缀 → {key,id,summary}。
// 刷新/重编后消息由后端历史重建，而历史里没有 trace —— 靠它把折叠条接回去。
// 只记录最近一次（全量 trace 也只缓存最近一次，见 chatbot._attachTrace）。
const TRACE_INDEX_KEY = `${PREFIX}_trace_index`

// 归一化答案文本，作为匹配键（去标签/实体、压空白、截断到 120 字）
function answerKey(text) {
  return String(text || '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 120)
}

// 六拍步骤定义（与 pages/trace 的展示一一对应）
const STEP_DEFS = [
  { key: 'ask', icon: '💬', title: '用户提问' },
  { key: 'think', icon: '💭', title: '思考' },
  { key: 'skill', icon: '🧩', title: '命中 Skill' },
  { key: 'flow', icon: '🔧', title: '开始计算 / 搜索' },
  { key: 'reflect', icon: '💭', title: '思考执行' },
  { key: 'result', icon: '📊', title: '得到结果' },
]

// 毫秒 → "12.5s"
function formatDuration(ms) {
  return `${(Math.max(0, Number(ms) || 0) / 1000).toFixed(1)}s`
}

// 毫秒 → "00:12.4"（计时条）
function formatClock(ms) {
  const total = Math.max(0, Number(ms) || 0)
  const m = Math.floor(total / 60000)
  const s = Math.floor((total % 60000) / 1000)
  const d = Math.floor((total % 1000) / 100)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${d}`
}

// 轨迹摘要（对话页折叠条用）：步数 / 耗时 / 命中 skill / 分拍明细
function summarize(trace) {
  const events = trace && Array.isArray(trace.events) ? trace.events : []
  if (!events.length) return null
  const totalMs = Number(trace.durationMs) || Number(events[events.length - 1].t) || 0
  const skills = events.filter((e) => e.type === 'skill')
  const tools = events.filter((e) => e.type === 'tool.start')
  // 一次 run 可能命中多个 skill：按名字去重、保持出现顺序，供列表展示
  const skillNames = []
  for (const s of skills) {
    const name = s.name || ''
    if (name && !skillNames.includes(name)) skillNames.push(name)
  }
  const skillName = skillNames[0] || ''

  // 分拍聚合（think / reflect 同为 reasoning，按此前是否已跑过工具区分）
  const groups = {}
  const order = []
  let hasToolRun = false
  for (const e of events) {
    const key = stepOf(e, hasToolRun)
    if (e.type === 'tool.done') hasToolRun = true
    if (!key || key === 'ask') continue
    let g = groups[key]
    if (!g) { g = groups[key] = { key, first: e.t, last: e.t }; order.push(key) }
    g.last = e.t
  }
  const titleOf = {
    think: '思考',
    skill: skillNames.length
      ? `命中 Skill · ${skillNames.length > 1 ? `${skillNames.length} 个` : skillNames[0]}`
      : '命中 Skill',
    flow: '开始计算 / 搜索',
    reflect: '思考执行',
    result: '得到结果',
  }
  const steps = order.map((k) => {
    const span = groups[k].last - groups[k].first
    return { key: k, title: titleOf[k] || k, meta: span >= 300 ? formatDuration(span) : '' }
  })

  const firstToolDone = events.find((e) => e.type === 'tool.done')
  const reflect = firstToolDone
    ? events.some((e) => e.type === 'reasoning' && e.t > firstToolDone.t)
    : false

  return {
    id: trace.id || '',
    title: trace.title || '执行过程',
    totalMs,
    durationText: formatDuration(totalMs),
    toolCount: tools.length,
    skillCount: skillNames.length,
    skillName,
    skillNames,
    stepCount: steps.length,
    steps,
    hasAsk: events.some((e) => e.type === 'user'),
    hasReflect: reflect,
  }
}

// 事件归属到哪一拍（think 与 reflect 同为 reasoning，按此前是否已跑过工具区分）
function stepOf(ev, hasToolRun) {
  switch (ev.type) {
    case 'user': return 'ask'
    case 'reasoning': return hasToolRun ? 'reflect' : 'think'
    case 'skill': return 'skill'
    case 'tool.start':
    case 'tool.done':
    case 'files': return 'flow'
    case 'result':
    case 'end': return 'result'
    default: return ''
  }
}

module.exports = {
  TRACE_KEY,
  TRACE_INDEX_KEY,
  answerKey,
  STEP_DEFS,
  summarize,
  stepOf,
  formatDuration,
  formatClock,
}
