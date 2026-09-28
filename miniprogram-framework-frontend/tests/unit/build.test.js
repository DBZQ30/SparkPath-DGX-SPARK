// tests/unit/build.test.js —— scripts/build.js 多实例组装引擎
// 只测无副作用的路径：loadInstance 的默认值合并、listInstances 的实例枚举过滤、
// buildInstance --dry-run 不落盘。真实写入路径由 `npm run switch` + validate.js 覆盖。
'use strict'

const fs = require('fs')
const path = require('path')

const { buildInstance, listInstances, loadInstance } = require('../../scripts/build')

describe('listInstances', () => {
  // config/instances/ 下：_default.js（排除）、jwc.js（真实实例）、jwc.local.js.example
  // （不以 .js 结尾外的扩展名过滤规则是不以 '.local' 结尾的 .js）
  test('枚举真实实例，排除 _default 与 *.local', () => {
    const ids = listInstances()
    expect(ids).toContain('jwc')
    expect(ids).not.toContain('_default')
    expect(ids.every((id) => !id.endsWith('.local'))).toBe(true)
  })
})

describe('loadInstance', () => {
  test('必填字段齐全（缺字段会在构建期 fail）', () => {
    const cfg = loadInstance('jwc')
    for (const field of ['APPID', 'INSTANCE_ID', 'ASSISTANT_TITLE', 'WELCOME_TEXT',
      'CHAT_PLACEHOLDER', 'API_BASE_URL']) {
      expect(cfg[field]).toBeTruthy()
    }
  })

  test('FEATURES 与默认开关合并（实例字段优先，缺省补默认）', () => {
    const cfg = loadInstance('jwc')
    // jwc.js 没写 phoneAuthMode → 吃默认 'manual'
    expect(cfg.FEATURES.phoneAuthMode).toBe('manual')
    // 默认 badge=true，实例若显式 false 则否——只断言合并后是布尔
    expect(typeof cfg.FEATURES.badge).toBe('boolean')
  })

  test('UI_TEXT 与默认文案合并', () => {
    const cfg = loadInstance('jwc')
    for (const field of ['ROLE_TEACHER', 'APPLY_TEACHER_DESC', 'PENDING_TEACHER_AUTH',
      'AUTH_TEACHER_LABEL', 'ALREADY_TEACHER']) {
      expect(typeof cfg.UI_TEXT[field]).toBe('string')
      expect(cfg.UI_TEXT[field].length).toBeGreaterThan(0)
    }
  })

  test('TAB_BAR 缺省时兜底默认三栏', () => {
    const cfg = loadInstance('jwc')
    expect(cfg.TAB_BAR.list.length).toBeGreaterThan(0)
    for (const tab of cfg.TAB_BAR.list) {
      expect(tab.pagePath).toBeTruthy()
      expect(tab.text).toBeTruthy()
    }
  })
})

describe('buildInstance dry-run', () => {
  const OUT_INSTANCE_FILE = path.join(__dirname, '..', '..', 'config', 'instance.js')

  test('dry-run 不写任何构建产物', () => {
    const existed = fs.existsSync(OUT_INSTANCE_FILE)
    const cfg = buildInstance('jwc', { dryRun: true })
    expect(cfg.INSTANCE_ID).toBeTruthy()
    // dry-run 之后 config/instance.js 的存在状态不变（未凭空生成）
    expect(fs.existsSync(OUT_INSTANCE_FILE)).toBe(existed)
  })
})
