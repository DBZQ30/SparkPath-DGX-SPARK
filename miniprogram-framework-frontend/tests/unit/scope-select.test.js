// tests/unit/scope-select.test.js —— 适用性勾选归一化（「全部/不限」与具体项互斥）
'use strict'

const { toggleScope } = require('../../utils/scope-select')

const GRADES = ['2023级', '2024级', '2025级', '2026级']
const ALL = '全部'

describe('toggleScope（年级：全部 与具体年级互斥 + 全选收敛）', () => {
  test('点「全部」未选中 → 只选全部', () => {
    expect(toggleScope([], ALL, GRADES, ALL)).toEqual([ALL])
  })

  test('点「全部」已选中 → 清空', () => {
    expect(toggleScope([ALL], ALL, GRADES, ALL)).toEqual([])
  })

  test('「全部」已选时点具体年级 → 收敛为该年级', () => {
    expect(toggleScope([ALL], '2024级', GRADES, ALL)).toEqual(['2024级'])
  })

  test('具体年级逐个累加', () => {
    expect(toggleScope(['2023级'], '2024级', GRADES, ALL)).toEqual(['2023级', '2024级'])
  })

  test('点已选具体年级 → 取消', () => {
    expect(toggleScope(['2023级', '2024级', '2025级'], '2023级', GRADES, ALL))
      .toEqual(['2024级', '2025级'])
  })

  test('四个具体年级全选 → 自动收敛为「全部」', () => {
    expect(toggleScope(['2023级', '2024级', '2025级'], '2026级', GRADES, ALL)).toEqual([ALL])
  })

  test('不修改入参', () => {
    const cur = ['2023级']
    toggleScope(cur, '2024级', GRADES, ALL)
    expect(cur).toEqual(['2023级'])
  })
})

describe('toggleScope（专业：不限 与具体专业互斥，不收敛）', () => {
  const MAJORS = ['工商管理', '工业工程']

  test('「不限」已选时点具体专业 → 收敛为该专业', () => {
    expect(toggleScope([''], '工商管理', MAJORS, '', { autoAll: false })).toEqual(['工商管理'])
  })

  test('点「不限」未选中 → 只选不限；已选 → 清空', () => {
    expect(toggleScope(['工商管理'], '', MAJORS, '', { autoAll: false })).toEqual([''])
    expect(toggleScope([''], '', MAJORS, '', { autoAll: false })).toEqual([])
  })

  test('具体专业全选不收敛为「不限」（不限语义更广）', () => {
    expect(toggleScope(['工商管理'], '工业工程', MAJORS, '', { autoAll: false }))
      .toEqual(['工商管理', '工业工程'])
  })
})
