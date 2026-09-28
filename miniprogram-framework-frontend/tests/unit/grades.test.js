// tests/unit/grades.test.js —— 年级注册表读取（统一事实来源）
'use strict'

const { FALLBACK_GRADES, fetchGrades } = require('../../utils/grades')

describe('utils/grades', () => {
  test('兜底年级为 2023–2026（4 个）', () => {
    expect(FALLBACK_GRADES).toEqual(['2023级', '2024级', '2025级', '2026级'])
  })

  test('注册表不可用/请求失败 → 回退默认年级', (done) => {
    // jest setup 的 wx.request 桩会触发 fail 回调；夹具 WARNING_API_BASE 非空。
    fetchGrades((grades) => {
      expect(grades).toEqual(FALLBACK_GRADES)
      done()
    })
  })

  test('返回的年级列表是副本，改动不污染 FALLBACK_GRADES', (done) => {
    fetchGrades((grades) => {
      grades.push('2099级')
      expect(FALLBACK_GRADES).not.toContain('2099级')
      done()
    })
  })
})
