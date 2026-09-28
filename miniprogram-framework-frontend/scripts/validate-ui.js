// scripts/validate-ui.js —— 界面与样式规范核验（UI-DESIGN-SYSTEM.md 的自动化版）
// 用法: node scripts/validate-ui.js    （npm test 已连带执行本脚本）
// 前置: app.json 已生成（先执行 npm run switch；未生成时脚本会提示）
//
// 核验内容（与 docs/UI-DESIGN-SYSTEM.md 对照）：
//   1. WXSS 括号配平
//   2. WXML 事件处理函数在对应 .js 中存在（防"误删 bindtap"）
//   3. WXML 数据绑定变量在 .js 中存在（排除 wx:for 局部变量）
//   4. 图标类 .ic-* 在 app.wxss 中有定义（含 JS 下发的动态图标）
//   5. 禁用旧色值回流（硬错误）；非调色板色值仅提示（警告）
//   6. 禁止 box-shadow（卡片用发丝边框，见规范 §6.1）
//   7. 禁止 emoji（允许 ✓ ✕ 等文字符号，见规范 §6.4）
//   8. 每个页面都有 navigationBarTitleText（导航栏承载页面名，见规范 §6.2）
'use strict'

const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..')
let failures = 0
let warnings = 0

function report(name, ok, extra) {
  console.log(`  ${ok ? '✓' : '✗'} ${name}${extra ? '  —— ' + extra : ''}`)
  if (!ok) failures++
}
function warn(msg) {
  console.log(`  ⚠ ${msg}`)
  warnings++
}
function read(rel) {
  return fs.readFileSync(path.join(ROOT, rel), 'utf8')
}

// ── 0. 前置 ─────────────────────────────
const APP_JSON = path.join(ROOT, 'app.json')
if (!fs.existsSync(APP_JSON)) {
  console.error('[validate-ui] ✗ app.json 不存在。请先执行 npm run switch 生成构建产物，再运行本脚本。')
  process.exit(1)
}
console.log('[validate-ui] 界面与样式核验（npm test 连带执行）\n')

const pages = JSON.parse(fs.readFileSync(APP_JSON, 'utf8')).pages
const wxmlFiles = pages.map((p) => `${p}.wxml`)
const wxssFiles = ['app.wxss', ...pages.map((p) => `${p}.wxss`)]

// 去注释后再扫描（避免注释里的色值/emoji 误报）
function stripComments(s, kind) {
  if (kind === 'wxml') return s.replace(/<!--[\s\S]*?-->/g, '')
  return s.replace(/\/\*[\s\S]*?\*\//g, '')
}

// ── 1. WXSS 括号配平 ────────────────────
{
  const bad = []
  for (const f of wxssFiles) {
    const s = stripComments(read(f), 'wxss')
    const open = (s.match(/\{/g) || []).length
    const close = (s.match(/\}/g) || []).length
    if (open !== close) bad.push(`${f}(open=${open} close=${close})`)
  }
  report(`WXSS 括号配平（${wxssFiles.length} 个文件）`, bad.length === 0, bad.join(', '))
}

// ── 2 & 3. WXML 事件处理函数 / 数据绑定 ──
{
  const missing = []
  for (const p of pages) {
    const wxml = stripComments(read(`${p}.wxml`), 'wxml')
    const js = read(`${p}.js`)

    // 局部作用域变量：wx:for 默认 item/index + 显式 wx:for-item/index + 字面量/关键字
    const local = new Set([
      'item', 'index', 'true', 'false', 'null', 'undefined',
      'new', 'typeof', 'in', 'of', 'instanceof', 'void', 'this',
    ])
    for (const m of wxml.matchAll(/wx:for-(?:item|index)="([^"]+)"/g)) local.add(m[1])

    // 2) 事件处理函数
    const handlers = new Set()
    for (const m of wxml.matchAll(/(?:bind|catch)[a-z]+="([^"]+)"/g)) {
      if (m[1]) handlers.add(m[1])
    }
    for (const h of handlers) {
      if (!new RegExp('(^|[\\s{,])(async\\s+)?' + h + '\\s*\\(', 'm').test(js)) {
        missing.push(`${p}.wxml → ${h}()`)
      }
    }

    // 3) 数据绑定：只取「根标识符」（排除 .property、函数调用名、字符串字面量）
    const used = new Set()
    for (const m of wxml.matchAll(/\{\{([\s\S]*?)\}\}/g)) {
      const expr = m[1].replace(/'[^']*'/g, "''").replace(/"[^"]*"/g, '""')
      for (const mm of expr.matchAll(/(?<![.\w$])([a-zA-Z_$][\w$]*)/g)) {
        const v = mm[1]
        if (expr[mm.index + v.length] === '(') continue // 函数调用名
        used.add(v)
      }
    }
    for (const v of used) {
      if (local.has(v)) continue
      const inData = new RegExp('(^|[\\s{,])' + v + '\\s*:').test(js)
      if (!inData) missing.push(`${p}.wxml → {{${v}}}`)
    }
  }
  report('WXML 事件处理函数与数据绑定均有定义', missing.length === 0,
    missing.length ? missing.slice(0, 8).join('; ') + (missing.length > 8 ? ` 等 ${missing.length} 处` : '') : `${pages.length} 个页面`)
}

// ── 4. 图标类定义完整 ───────────────────
{
  const appWxss = read('app.wxss')
  const defined = new Set([...appWxss.matchAll(/\.(ic-[a-z]+)\s*\{/g)].map((m) => m[1]))
  const used = new Set()
  for (const f of wxmlFiles) {
    for (const m of stripComments(read(f), 'wxml').matchAll(/\b(ic-[a-z]+)\b/g)) used.add(m[1])
  }
  // JS 下发的动态图标（如 methods.js 的功能清单 icon 字段）
  for (const p of pages) {
    for (const m of read(`${p}.js`).matchAll(/icon:\s*'(ic-[a-z]+)'/g)) used.add(m[1])
  }
  const undef = [...used].filter((c) => !defined.has(c))
  report(`图标类均有定义（用到 ${used.size} / 定义 ${defined.size}）`, undef.length === 0, undef.join(', '))
}

// ── 5. 色值 ─────────────────────────────
// 禁用清单 = 改版前（98310586^）用过的色值 − 当前调色板；用于拦截旧色值回流
const FORBIDDEN = [
  '#047857', '#059669', '#07c160', '#0c5460', '#10b981', '#111827', '#155724', '#15b66d',
  '#1d4ed8', '#1e40af', '#1f2933', '#1f2937', '#202b37', '#2563eb', '#2e7d32', '#374151',
  '#4a5568', '#4b5563', '#4f46e5', '#667085', '#6a1b9a', '#6b7280', '#721c24', '#856404',
  '#8895a7', '#8a94a6', '#8db2ff', '#92400e', '#93b4f4', '#9aa3b2', '#9ca3af', '#a7f3d0',
  '#b45309', '#b91c1c', '#bfdbfe', '#c2410c', '#c53030', '#c62828', '#cbd5e1', '#d1d5db',
  '#d1ecf1', '#d4edda', '#d97706', '#dc2626', '#e53e3e', '#e5e7eb', '#e5e8ec', '#e5eeff',
  '#e65100', '#e67e22', '#e6f7ec', '#e8f4fd', '#e8f5e9', '#eaf1fe', '#ecfdf5', '#edf0f4',
  '#eef0f4', '#eef0f5', '#eef1f6', '#eef2ff', '#ef4444', '#eff6ff', '#f0f0f0', '#f0f1f3',
  '#f0f1f5', '#f0f2f5', '#f3e5f5', '#f3f4f6', '#f4f5f8', '#f4f6f8', '#f59e0b', '#f5b041',
  '#f5c97b', '#f5f6fa', '#f7f8fa', '#f8d7da', '#f8fafc', '#f9fafb', '#fce4ec', '#fde0b3',
  '#fde8e8', '#fecaca', '#fee2e2', '#fef2f2', '#fef3c7', '#fff3cd', '#fff3e0', '#fff7e6',
  '#fffbeb',
]
// 允许的调色板（含组件辅助色，见 UI-DESIGN-SYSTEM.md §2.1）
// 尾部为「演示模式」暗色观测台专用板（pages/trace，仅演示页使用）
const PALETTE = new Set([
  '#17324d', '#41556b', '#5c6b7a', '#ffffff', '#f3f5f8', '#e2e6ec', '#b3261e', '#fbedeb',
  '#1f7a55', '#e8f5ef', '#9a6400', '#fbf3e2',
  '#eaf0f6', '#b7c0cb', '#afc2d6', '#ebc7c3', '#e7d2a8', '#b6dcc9', '#c7ced6',
  '#c6d3e0', '#3e5c7a', '#6e8aa6', '#9fb4c8', '#fff',
  // 演示模式（暗色观测台）
  '#0a121c', '#0d1826', '#14202f', '#1b2a3b', '#243447',
  '#e6edf5', '#8b9bac', '#5c6e80', '#76b900', '#e2554a', '#3fbf87',
])
{
  const scanFiles = [...wxssFiles, ...wxmlFiles]
  const hits = []
  const unknown = new Set()
  for (const f of scanFiles) {
    const kind = f.endsWith('.wxml') ? 'wxml' : 'wxss'
    const s = stripComments(read(f), kind)
    for (const m of s.matchAll(/#[0-9a-fA-F]{3,8}\b/g)) {
      const hex = m[0].toLowerCase()
      if (FORBIDDEN.includes(hex)) hits.push(`${f} ${hex}`)
      else if (!PALETTE.has(hex) && !/^#[0-9a-f]{3,4}$/.test(hex)) unknown.add(hex)
    }
  }
  report('无旧色值回流（禁用清单 88 项）', hits.length === 0,
    hits.length ? hits.slice(0, 6).join('; ') + (hits.length > 6 ? ` 等 ${hits.length} 处` : '') : '')
  if (unknown.size) warn(`调色板外色值（请确认是否应并入令牌）：${[...unknown].join(', ')}`)
}

// ── 6. 禁止 box-shadow ──────────────────
{
  // 「演示模式」页（暗色观测台）有意用发光表达"激活"，是唯一豁免点；
  // 产品页（亮色台账）仍然一律禁止 box-shadow，卡片用发丝边框。
  const DEMO_EXEMPT = ['pages/trace/trace.wxss']
  const hits = wxssFiles.filter(
    (f) => !DEMO_EXEMPT.includes(f) && /box-shadow/.test(stripComments(read(f), 'wxss'))
  )
  report('无 box-shadow（卡片用发丝边框；演示页豁免）', hits.length === 0, hits.join(', '))
}

// ── 7. 禁止 emoji ───────────────────────
{
  const EMOJI = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}]/gu
  // 允许的文字符号（勾选 / 箭头 / 三角 / 引号等，见规范 §6.4）
  const ALLOWED = /[\u2713\u2714\u2715\u2716\u2717\u2718]/g
  const hits = []
  for (const f of [...wxmlFiles, ...wxssFiles]) {
    const kind = f.endsWith('.wxml') ? 'wxml' : 'wxss'
    const s = stripComments(read(f), kind).replace(ALLOWED, '')
    const found = [...new Set(s.match(EMOJI) || [])]
    if (found.length) hits.push(`${f} ${found.join('')}`)
  }
  report('无 emoji（允许 ✓ ✕ 等文字符号）', hits.length === 0, hits.join('; '))
}

// ── 8. 导航栏标题 ───────────────────────
{
  const missing = []
  for (const p of pages) {
    let title = ''
    try { title = JSON.parse(read(`${p}.json`)).navigationBarTitleText || '' } catch (e) { /* 交给 validate.js */ }
    if (!title.trim()) missing.push(p)
  }
  report('每个页面都有 navigationBarTitleText', missing.length === 0, missing.join(', '))
}

// ── 结果 ────────────────────────────────
console.log('')
if (failures) {
  console.log(`[validate-ui] ✗ 未通过：${failures} 项失败${warnings ? `，${warnings} 项警告` : ''}`)
  process.exit(1)
}
console.log(`[validate-ui] 全部通过 ✓${warnings ? `（${warnings} 项警告，不阻断）` : ''}`)
