// utils/trace-demo.js —— 录播演示数据（比赛演示 / 录屏）
//
// 事件契约见 utils/trace.js。`t` 为相对毫秒；接上真实事件流后，同一结构由
// tui_gateway 的 reasoning.available / skill.activate / tool.start / tool.complete 驱动。
'use strict'

const QUERY = '帮我检查 2023 级这个学期的选课情况'

// 「思考」增量（模拟 reasoning 流式）
const THINK_CHUNKS = [
  '这是一个', '选课检查', '请求，属于', '学业预警', '领域。',
  '需要先确认', '年级与学期', '，再调用', '选课合理性检查', '。',
  '命中技能 ', 'academic-warning', ' —— 它要求先跑 ', 'precheck',
  ' 校验数据齐备性，再跑 ', 'check', ' 出名单。',
]
// 「思考执行」增量（读回工具结果后的再推理）
const REFLECT_CHUNKS = [
  '检查已完成：在籍 ', '128', ' 人，', '12 人存在选课缺口',
  '（3 红 / 9 黄）。', '整理为可读摘要，', '并生成可下载报告。',
]

// 把文本块摊成按时间递增的事件
function stream(chunks, startT, stepMs) {
  return chunks.map((text, i) => ({ t: startT + i * stepMs, type: 'reasoning', text }))
}

const EVENTS = [].concat(
  [{ t: 0, type: 'user' }],
  stream(THINK_CHUNKS, 420, 120),
  [{ t: 2500, type: 'skill', name: 'academic-warning', description: '选课合理性检查 / 学业预警' }],
  [{ t: 2760, type: 'tool.start', tool: 'bash', title: 'precheck · 数据齐备性校验',
     cmd: 'python -m academicwarning.cli precheck --grade 2023级' }],
  [{ t: 3120, type: 'tool.done', title: 'precheck · 数据齐备性校验', duration_s: 0.4 }],
  [{ t: 3240, type: 'files', files: ['学籍名单.xlsx', '选课结果.xlsx', '成绩单.docx', '培养方案.docx'] }],
  [{ t: 3380, type: 'tool.start', tool: 'bash', title: 'check · 选课合理性计算',
     cmd: 'python -m academicwarning.cli check --grade 2023级 --sem 3-1' }],
  [{ t: 5820, type: 'tool.done', title: 'check · 选课合理性计算', duration_s: 2.4,
     result: '{ in_school: 128, gaps: 12, red: 3, yellow: 9 }' }],
  stream(REFLECT_CHUNKS, 6260, 230),
  [{ t: 8600, type: 'tool.start', tool: 'bash', title: 'export · 生成预警报告',
     cmd: 'python -m academicwarning.cli export --grade 2023级' }],
  [{ t: 8900, type: 'tool.done', title: 'export · 生成预警报告', duration_s: 0.3 }],
  [{ t: 9200, type: 'result',
     answer: '2023 级 · 2025-2026-1 选课检查完成：在籍 128 人，其中 12 人存在选课缺口（3 人缺修必修课·红级，9 人专业选修学分不足·黄级）；另有 4 人本学期无选课记录（疑似休学/交流），已单独列出待核对。检查名单与可下载报告已生成。',
     artifacts: [
       { label: '选课检查名单', value: '12 人' },
       { label: '预警报告 xlsx', value: '1 份' },
       { label: '未选课核对', value: '4 人' },
     ],
     metrics: { tools: 3, skills: 1, tokens: 1284, seconds: 12.5 } }],
  [{ t: 12480, type: 'end' }],
)

// 算力采样（DGX Spark）：接实时后由 nvidia-smi + 推理端指标驱动
const TELEMETRY = [
  { t: 0, gpu: 8, vram: 41, tps: 0 },
  { t: 400, gpu: 58, vram: 41, tps: 13.1 },
  { t: 1200, gpu: 86, vram: 41, tps: 19.4 },
  { t: 2500, gpu: 92, vram: 41, tps: 18.4 },
  { t: 3380, gpu: 96, vram: 41, tps: 11.7 },
  { t: 5820, gpu: 88, vram: 41, tps: 16.9 },
  { t: 7000, gpu: 72, vram: 41, tps: 21.7 },
  { t: 9000, gpu: 34, vram: 41, tps: 8.2 },
  { t: 12480, gpu: 14, vram: 41, tps: 0 },
]

const DEMO_TRACE = {
  id: 'demo-academic-warning',
  title: '选课检查 · 2023 级',
  query: QUERY,
  model: 'qwen3-coder · 128k',
  durationMs: 12480,
  events: EVENTS,
  telemetry: TELEMETRY,
}

module.exports = { DEMO_TRACE }
