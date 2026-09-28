// utils/scope-select.js —— 「全部 / 不限」通配项与具体项的勾选归一化
//
// 两个页面（入库文件与适用年级、文件中心适用性）都要处理同一套规则：
//   1) 通配项（全部 / 不限）与具体项互斥，不能同时选中；
//   2) 点通配项：未选中 → 只选通配；已选中 → 清空；
//   3) 通配已选中时点具体项 → 收敛为「仅该具体项」；
//   4) autoAll 为真（默认）时，具体项被全选 → 收敛为通配（覆盖面更广，含未来新增项）。
//
// 纯函数，无 wx 依赖，便于 jest 直测。

function toggleScope(current, toggled, universe, wildcard, options) {
  const list = (current || []).slice()
  const has = (v) => list.indexOf(v) >= 0
  if (toggled === wildcard) {
    return has(wildcard) ? [] : [wildcard]
  }
  if (has(wildcard)) {
    return [toggled]
  }
  const next = has(toggled)
    ? list.filter((v) => v !== toggled)
    : list.concat([toggled])
  const autoAll = !options || options.autoAll !== false
  if (autoAll && universe.length && universe.every((v) => next.indexOf(v) >= 0)) {
    return [wildcard]
  }
  return next
}

module.exports = { toggleScope }
