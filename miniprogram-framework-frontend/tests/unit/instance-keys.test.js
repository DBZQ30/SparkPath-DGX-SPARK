// tests/unit/instance-keys.test.js —— 实例命名存储键派生
// 键名全部由 config/instance.js 的 INSTANCE_ID 派生（mock 夹具 = jest-instance），
// 保证多实例本地缓存互不污染。
'use strict'

const { PREFIX, KEYS, read } = require('../../utils/instance-keys')

describe('PREFIX 派生', () => {
  test('PREFIX 即 INSTANCE_ID', () => {
    expect(PREFIX).toBe('jest-instance')
  })
})

describe('KEYS', () => {
  test('所有键都以实例前缀开头（零硬编码）', () => {
    for (const value of Object.values(KEYS)) {
      expect(value.startsWith(`${PREFIX}_`)).toBe(true)
    }
  })

  test('键名清单稳定', () => {
    expect(Object.keys(KEYS).sort()).toEqual([
      'OPENID_KEY', 'SESSION_ID_KEY', 'SESSION_TOKEN_KEY', 'TEMP_USER_ID_KEY',
      'USERNAME_KEY', 'USER_ID_KEY',   // 注意 '_'(0x5F) > 'N'(0x4E)
    ])
    expect(KEYS.SESSION_TOKEN_KEY).toBe('jest-instance_session_token')
  })
})

describe('read', () => {
  test('从 wx 存储按键名取值（走构造好的键而非裸字符串）', () => {
    wx.setStorageSync(KEYS.USER_ID_KEY, 'u-001')
    expect(read('USER_ID_KEY')).toBe('u-001')
    // 写了 A 实例的键，取 B 前缀的键名应取不到 —— 这里用同一个前缀模拟隔离：
    // read 只认 KEYS 里的派生键
    expect(read('SESSION_ID_KEY')).toBe('')
  })

  test('未知键名 → 空串（KEYS 未登记，wx 未命中语义）', () => {
    expect(read('NOT_A_KEY')).toBe('')
  })

  test('删除后读到空串（wx.getStorageSync 未命中返回空串）', () => {
    wx.setStorageSync(KEYS.USERNAME_KEY, '张三')
    wx.removeStorageSync(KEYS.USERNAME_KEY)
    expect(read('USERNAME_KEY')).toBe('')
  })
})
