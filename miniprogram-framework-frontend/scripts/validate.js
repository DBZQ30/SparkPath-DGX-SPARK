// scripts/validate.js —— 架构核验脚本（ARCHITECTURE.md §13 手工核验表的自动化版）
// 用法: node scripts/validate.js    （npm test 已指向本脚本）
// 前置: config/instance.js 已生成（先执行 npm run switch；未生成时脚本会提示）
//
// 核验内容（与 ARCHITECTURE.md 第 13 节对照）：
//   1. 实例清单（1 个）与每个实例的构建可执行性（dry-run，含必填字段/页面存在性校验）
//   2. config/api.js 接口完整性（全部由 API_BASE_URL 派生）与数量
//   3. UI_TEXT 合并正确性（实例覆盖与默认值）
//   4. _default = 教务
//   5. 共享代码零硬编码：'招生老师' 0 处；实例专属命名（mba_admission/mba_username）0 处；
//      共享代码无 INSTANCE_ID 分支
//   6. 模块加载链：Node 模拟 require 全部共享页面 + app.js（stub wx/App/Page/getApp）
//   7. 构建产物不入库（.gitignore 含 app.json / project.config.json / config/instance.js）
//   8. SHARED_PAGES 声明的页面文件全部存在
//   9. 存储键按 INSTANCE_ID 派生（utils/instance-keys.js）
'use strict'

const fs = require('fs')
const path = require('path')
const cp = require('child_process')
const Module = require('module')

const ROOT = path.resolve(__dirname, '..')
let failures = 0

function report(name, ok, extra) {
  console.log(`  ${ok ? '✓' : '✗'} ${name}${extra ? '  —— ' + extra : ''}`)
  if (!ok) failures++
}

function scan(dir, exts, excludeRel) {
  const hits = []
  const walk = (d) => {
    let entries
    try { entries = fs.readdirSync(d, { withFileTypes: true }) } catch { return }
    for (const e of entries) {
      const full = path.join(d, e.name)
      const rel = path.relative(ROOT, full)
      // Windows 分隔符为反斜杠，统一为 POSIX 格式比较（排除列表用正斜杠，跨平台一致）
      const relPosix = rel.replace(/\\/g, '/')
      if (excludeRel && excludeRel.some((x) => relPosix.startsWith(x))) continue
      if (e.isDirectory()) walk(full)
      else if (exts.some((x) => e.name.endsWith(x))) hits.push(rel)
    }
  }
  walk(dir)
  return hits
}

function grep(files, pattern) {
  return files.filter((rel) => {
    const content = fs.readFileSync(path.join(ROOT, rel), 'utf8')
    return pattern.test(content)
  })
}

// ── 0. 前置 ─────────────────────────────
const INSTANCE_FILE = path.join(ROOT, 'config', 'instance.js')
if (!fs.existsSync(INSTANCE_FILE)) {
  console.error('[validate] ✗ config/instance.js 不存在。请先执行 npm run switch 生成构建产物，再运行本脚本。')
  process.exit(1)
}
console.log('[validate] 架构核验（npm test）\n')

// ── 1. 实例清单 + 全实例 dry-run ────────
const { listInstances } = require('./build')
const ids = listInstances()
report('实例清单（config/instances/ 共 1 个实例）', ids.length === 1, ids.join(', '))

for (const id of ids) {
  let ok = true
  let msg = ''
  try {
    cp.execSync(`node scripts/build.js ${id} --dry-run`, { cwd: ROOT, stdio: 'pipe' })
  } catch (e) {
    ok = false
    msg = String(e.stderr || e.stdout || '').split('\n').find((l) => l.includes('[build] ✗')) || '构建失败'
  }
  report(`实例 ${id} 构建通过（必填字段/页面存在性）`, ok, msg)
}

// ── 2. api.js 接口完整性 ────────────────
const api = require('../config/api')
const { API_BASE_URL, WARNING_API_BASE, PLAN_API_BASE } = require('../config/instance')
// 学业预警（007 设计）：WARNING_* 接口由独立的 WARNING_API_BASE 派生（agent4som 服务）；
// 培养方案解读（004 设计）：PLAN_* 接口由独立的 PLAN_API_BASE 派生（同服务不同路由）；
// 两个 BASE 为空时对应接口为 ''（功能未启用，仍须派生自空前缀）。
const badUrls = Object.entries(api).filter(([k, v]) => {
  if (typeof v !== 'string') return true
  if (k.startsWith('WARNING_')) return !v.startsWith(WARNING_API_BASE)
  if (k.startsWith('PLAN_')) return !v.startsWith(PLAN_API_BASE)
  return !v.startsWith(API_BASE_URL)
}).map(([k]) => k)
report('api.js 接口全部由 API_BASE_URL（或 WARNING_API_BASE / PLAN_API_BASE）派生', badUrls.length === 0, badUrls.length ? `异常: ${badUrls.join(', ')}` : `${Object.keys(api).length} 个接口`)
if (Object.keys(api).length !== 66) console.log(`        （提示：当前接口数 ${Object.keys(api).length}，与文档记录的 66 不一致，请同步更新文档）`)

// ── 3. UI_TEXT 合并正确性（经 build.js loadInstance，单一事实来源）──
for (const [id, expect] of [['jwc', '教师']]) {
  let ok = false
  let msg = ''
  try {
    const out = cp.execSync(
      `node -e "const {loadInstance}=require('./scripts/build');const c=loadInstance('${id}');console.log(c.UI_TEXT.ROLE_TEACHER)"`,
      { cwd: ROOT, encoding: 'utf8' }
    )
    ok = out.trim() === expect
    msg = ok ? `ROLE_TEACHER=${out.trim()}` : `期望 ${expect}，实际 ${out.trim()}`
  } catch (e) { msg = 'loadInstance 失败' }
  report(`UI_TEXT 合并（${id}）`, ok, msg)
}

// ── 4. _default = 教务 ──────────────────
const def = require('../config/instances/_default')
report('_default 指向教务实例', def.INSTANCE_ID === 'jwc-assistant', `INSTANCE_ID=${def.INSTANCE_ID}`)

// ── 5. 共享代码零硬编码（仅运行时共享代码；scripts/ 是开发工具，默认文案可合法存在）──
const RUNTIME_SRC = [
  ...scan(path.join(ROOT, 'pages'), ['.js', '.wxml'], ['pages/_ext']),
  ...scan(path.join(ROOT, 'components'), ['.js', '.wxml']),
  ...scan(path.join(ROOT, 'utils'), ['.js'], ['utils/instance-keys.js']),
  'app.js',
]
const hardcodedTeacher = grep(RUNTIME_SRC, /招生老师/)
report('共享代码无"招生老师"硬编码', hardcodedTeacher.length === 0, hardcodedTeacher.join(', '))

const hardcodedInstance = grep(RUNTIME_SRC, /mba_admission|mba_username/)
report('共享代码无实例专属命名（mba_admission/mba_username）', hardcodedInstance.length === 0,
  hardcodedInstance.length ? hardcodedInstance.join(', ') : '0 处')

const branchHits = grep(scan(path.join(ROOT, 'pages'), ['.js']), /if\s*\([^)]*INSTANCE_ID|INSTANCE_ID\s*===|switch\s*\([^)]*INSTANCE_ID/)
report('共享页面无 INSTANCE_ID 分支', branchHits.length === 0, branchHits.join(', '))

// ── 6. 模块加载链（stub 小程序运行时）───
let loadOk = true
let loadErr = ''
try {
  global.App = () => {}
  global.Page = () => {}
  global.getApp = () => ({ globalData: {} })
  global.wx = new Proxy({}, { get: () => (...a) => ({}), set: () => true })
  const origResolve = Module._resolveFilename
  Module._resolveFilename = function (request, parent, isMain, options) {
    if (!request.startsWith('.') && (request.startsWith('config/') || request.startsWith('utils/'))) {
      return origResolve.call(this, path.join(ROOT, request), parent, isMain, options)
    }
    return origResolve.call(this, request, parent, isMain, options)
  }
  const pageFiles = scan(path.join(ROOT, 'pages'), ['.js'], ['pages/_ext'])
  for (const f of pageFiles) require(path.join(ROOT, f))
  require(path.join(ROOT, 'app.js'))
  console.log(`  ✓ 模块加载链（${pageFiles.length} 个共享页面 + app.js + config/api.js + utils/instance-keys.js）全部通过`)
} catch (e) {
  loadOk = false
  loadErr = e.message
  console.log(`  ✗ 模块加载链失败 —— ${loadErr}`)
}
if (!loadOk) failures++

// ── 7. 构建产物不入库 ──────────────────
const gi = fs.readFileSync(path.join(ROOT, '.gitignore'), 'utf8')
report('.gitignore 排除 app.json / project.config.json / config/instance.js',
  gi.includes('app.json') && gi.includes('project.config.json') && gi.includes('config/instance.js'))

// ── 8. SHARED_PAGES 页面文件齐全 ───────
const buildSrc = fs.readFileSync(path.join(ROOT, 'scripts', 'build.js'), 'utf8')
const m = buildSrc.match(/const SHARED_PAGES = \[([\s\S]*?)\]/)
const sharedPages = m ? (m[1].match(/'pages\/[^']+'/g) || []).map((s) => s.replace(/'/g, '')) : []
const missingPages = sharedPages.filter((p) => !fs.existsSync(path.join(ROOT, `${p}.js`)))
report(`SHARED_PAGES 页面文件齐全（${sharedPages.length} 个）`, missingPages.length === 0,
  missingPages.length ? `缺失: ${missingPages.join(', ')}` : '')

// ── 9. 存储键按实例派生 ─────────────────
const { PREFIX, KEYS } = require('../utils/instance-keys')
const { INSTANCE_ID } = require('../config/instance')
report('存储键前缀由 INSTANCE_ID 派生', PREFIX === INSTANCE_ID,
  `PREFIX=${PREFIX}，示例键=${KEYS.SESSION_TOKEN_KEY}`)

// ── 汇总 ────────────────────────────────
console.log(`\n[validate] ${failures === 0 ? '全部通过 ✓' : `${failures} 项未通过 ✗`}`)
process.exit(failures === 0 ? 0 : 1)
