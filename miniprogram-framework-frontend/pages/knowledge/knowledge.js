// pages/knowledge —— 知识库管理（设计 §5）
// 2026-09-09 新建：按角色切换范围（个人 / 教师 / 公共）→ 列出已入库文件；
// 删除前一律先取后端 dry_run 预览（不写库）再二次确认，确认文案强制「删除后不可撤回」；
// 来源筛选与孤儿扫描只对 admin/owner 显示（教务通知只存在于公共库）。
const {
  METHODS_IDENTITY,
  METHODS_KNOWLEDGE,
  METHODS_KNOWLEDGE_BATCH,
  METHODS_KNOWLEDGE_ORPHANS,
  METHODS_KNOWLEDGE_AUDIT,
} = require('../../config/api')
const { read } = require('../../utils/instance-keys')

const SOURCE_LABELS = { file: '文件入库', jxtz: '教务通知' }
const ERROR_TEXT = {
  file_not_found: '文件已不存在',
  partial_delete: '只删除了部分内容，请重试',
  chroma_unavailable: '服务暂时不可用，请稍后重试',
  too_many_files: '一次最多删除 50 个文件',
}

Page({
  data: {
    role: 'guest',
    openid: '',
    scopes: [],           // [{key, label, scope}]
    activeScope: '',
    activeScopeLabel: '',
    isAdmin: false,       // 来源筛选 / 孤儿扫描的可见性
    sourceFilter: 'all',  // all | file | jxtz（本地过滤）
    sortOrder: 'asc',     // asc | desc（服务端 ORDER BY filename）
    keyword: '',
    items: [],            // 接口返回的原始列表
    view: [],             // 过滤 + 装饰后的展示列表
    selected: {},         // filename -> true（批量选择）
    selectedCount: 0,
    allSelected: false,
    loading: true,
    loadFailed: false,
    noAccess: false,
    orphans: [],          // 孤儿扫描候选
    orphanScanning: false,
    orphanListed: false,
    batchResult: null,    // {deleted, failed, failures:[{filename, reason}]}
    quota: null,          // {used, limit}（仅个人库返回；admin/owner 为 null）
    mode: 'files',        // files | audit（操作历史，仅 admin/owner）
    auditEvents: [],
    auditLoading: false,
    auditFailed: false,
  },

  onLoad() {
    const app = getApp()
    let token = read('SESSION_TOKEN_KEY') || ''
    if (!token && app && app.globalData) token = app.globalData.sessionToken || ''
    this._token = token
    if (!token) { this.setData({ loading: false, loadFailed: false }); return }
    this._loadIdentity()
  },

  // 角色与个人范围来自 /api/methods/identity（与功能页同源）
  _loadIdentity() {
    wx.request({
      url: METHODS_IDENTITY, method: 'GET', timeout: 15000,
      header: { 'Authorization': `Bearer ${this._token}` },
      success: (res) => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          this.setData({ loading: false, loadFailed: true }); return
        }
        const d = res.data || {}
        const role = d.role || 'guest'
        const openid = d.openid || ''
        const isStaff = role === 'teacher' || role === 'admin' || role === 'owner'
        const isAdmin = role === 'admin' || role === 'owner'
        const scopes = []
        if (role !== 'guest' && openid) {
          scopes.push({ key: 'personal', label: '个人', scope: `users/${openid}` })
        }
        if (isStaff) scopes.push({ key: 'teachers', label: '教师', scope: 'teachers' })
        if (isAdmin) scopes.push({ key: 'global', label: '公共', scope: 'global' })
        if (!scopes.length) {
          this.setData({ role, openid, isAdmin, noAccess: true, loading: false }); return
        }
        // 管理员/所有者默认进「公共」：教务通知等公共内容都在 global，
        // 默认落在个人库会让公共内容看起来"没同步"。
        const preferGlobal = isAdmin && scopes.some((s) => s.scope === 'global')
        const active = preferGlobal ? scopes.find((s) => s.scope === 'global') : scopes[0]
        this.setData({
          role, openid, isAdmin, scopes,
          activeScope: active.scope,
          activeScopeLabel: active.label,
        })
        this._loadList()
      },
      fail: () => this.setData({ loading: false, loadFailed: true }),
    })
  },

  _loadList() {
    const scope = this.data.activeScope
    if (!scope) return
    this._loadedOnce = true
    this.setData({ loading: true, loadFailed: false, selected: {}, selectedCount: 0,
                   allSelected: false, orphans: [], orphanListed: false })
    wx.request({
      url: METHODS_KNOWLEDGE, method: 'GET', timeout: 30000,
      header: { 'Authorization': `Bearer ${this._token}` },
      data: { scope, order: this.data.sortOrder },
      success: (res) => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          this.setData({ loading: false, loadFailed: true }); return
        }
        const d = res.data || {}
        const items = (d.files || []).map((it) => {
          const uploaders = (it.uploaders || []).map((u) => u.user_id)
          const times = (it.uploaders || []).map((u) => u.ingested_at).filter(Boolean).sort()
          const when = times.length ? times[times.length - 1] : ''   // 最新一条入库时间
          return {
            ...it,
            uploaders,
            sourceLabel: SOURCE_LABELS[it.source] || SOURCE_LABELS.file,
            // status 仅个人库返回（§4.7.1）：empty = 没有内容，可删；缺失则不打标签
            statusLabel: it.status === 'empty' ? '未完成' : '',
            statusHint: it.status === 'empty' ? '该文件没有内容，可以删除' : '',
            sub: [scope === `users/${this.data.openid}` ? '' : uploaders.join('、'), when]
              .filter(Boolean).join(' · '),
          }
        })
        this.setData({ loading: false, loadFailed: false, items, quota: d.quota || null })
        this._applyFilter()
      },
      fail: () => this.setData({ loading: false, loadFailed: true }),
    })
  },

  // 从预览页返回时刷新列表（预览页 404 提示「已为你刷新」依赖这里）
  onShow() {
    if (this._loadedOnce && this.data.mode === 'files') this._loadList()
  },

  // 点文件名 → 只读预览页（§4.7.2）
  tapPreview(e) {
    const { filename } = e.currentTarget.dataset
    wx.navigateTo({
      url: `/pages/knowledge-preview/knowledge-preview?scope=${encodeURIComponent(this.data.activeScope)}`
        + `&filename=${encodeURIComponent(filename)}`,
    })
  },

  // —— 操作历史（仅 admin/owner，§4.7.3）——
  tapAudit() {
    if (!this.data.isAdmin) return
    this.setData({ mode: 'audit' })
    this._loadAudit()
  },

  tapBackToFiles() { this.setData({ mode: 'files' }) },

  _loadAudit() {
    this.setData({ auditLoading: true, auditFailed: false })
    wx.request({
      url: METHODS_KNOWLEDGE_AUDIT, method: 'GET', timeout: 30000,
      header: { 'Authorization': `Bearer ${this._token}` },
      success: (res) => {
        const d = res.data || {}
        if (res.statusCode < 200 || res.statusCode >= 300) {
          this.setData({ auditLoading: false, auditFailed: true }); return
        }
        this.setData({
          auditLoading: false, auditFailed: false,
          auditEvents: (d.events || []).map((it) => ({ ...it, node_count: it.node_count || 0 })),
        })
      },
      fail: () => this.setData({ auditLoading: false, auditFailed: true }),
    })
  },

  // 本地过滤：来源（仅 admin/owner 可选）+ 显示名搜索
  _applyFilter() {
    const kw = (this.data.keyword || '').trim().toLowerCase()
    const sf = this.data.sourceFilter
    const selected = this.data.selected || {}
    const view = (this.data.items || []).filter((it) => {
      if (sf !== 'all' && it.source !== sf) return false
      if (kw && !(it.display_name || '').toLowerCase().includes(kw)) return false
      return true
    }).map((it) => ({ ...it, checked: !!selected[it.filename] }))
    const selectedCount = view.filter((it) => it.checked).length
    this.setData({ view, selectedCount, allSelected: view.length > 0 && selectedCount === view.length })
  },

  onSearch(e) { this.setData({ keyword: e.detail.value }); this._applyFilter() },
  clearSearch() { this.setData({ keyword: '' }); this._applyFilter() },

  switchScope(e) {
    const { scope, label } = e.currentTarget.dataset
    if (scope === this.data.activeScope) return
    // 切换范围时重置来源筛选：否则在个人库选了「文件入库」再切到公共库，
    // 会把公共库的教务通知全部筛掉，看起来像"没同步"。
    this.setData({ activeScope: scope, activeScopeLabel: label,
                   sourceFilter: 'all', batchResult: null })
    this._loadList()
  },

  setSourceFilter(e) {
    this.setData({ sourceFilter: e.currentTarget.dataset.v })
    this._applyFilter()
  },

  // 服务端按 filename 排序：正序 / 倒序
  setSortOrder(e) {
    const order = e.currentTarget.dataset.order
    if (order !== 'asc' && order !== 'desc') return
    if (order === this.data.sortOrder) return
    this.setData({ sortOrder: order })
    this._loadList()
  },

  reload() { this._loadList() },

  // —— 批量选择 ——
  toggleSelect(e) {
    const { filename } = e.currentTarget.dataset
    const selected = { ...this.data.selected }
    if (selected[filename]) delete selected[filename]
    else selected[filename] = true
    this.setData({ selected })
    this._applyFilter()
  },

  toggleSelectAll() {
    const selected = {}
    if (!this.data.allSelected) this.data.view.forEach((it) => { selected[it.filename] = true })
    this.setData({ selected })
    this._applyFilter()
  },

  // —— 单条删除：dry_run 预览 → 二次确认 → 真删 ——
  removeItem(e) {
    const { filename } = e.currentTarget.dataset
    const scope = this.data.activeScope
    wx.request({
      url: METHODS_KNOWLEDGE, method: 'DELETE', timeout: 15000,
      header: { 'Authorization': `Bearer ${this._token}`, 'content-type': 'application/json' },
      data: { scope, filename, dry_run: true },
      success: (res) => {
        const d = res.data || {}
        if (res.statusCode < 200 || res.statusCode >= 300) {
          wx.showToast({ title: ERROR_TEXT[d.error] || d.error || '操作失败', icon: 'none' }); return
        }
        wx.showModal({
          title: '删除文件',
          content: this._deleteMessage(d.preview || {}),
          confirmText: '删除', confirmColor: '#e64340',
          success: (m) => { if (m.confirm) this._doDelete(scope, filename) },
        })
      },
      fail: () => wx.showToast({ title: '操作失败', icon: 'none' }),
    })
  },

  // 确认弹窗文案（§5）：常规 / 0 片段 / 多上传者 / 自动抓取，可叠加
  _deleteMessage(p) {
    const name = p.display_name || p.filename || ''
    const lines = []
    if (p.node_count === 0) {
      lines.push(`《${name}》未占用知识片段，删除仅清理记录。若该内容曾以其它文件名入库，需单独删除。删除后不可撤回。`)
    } else {
      lines.push(`将删除《${name}》的 ${p.node_count} 个知识片段，删除后不可撤回。`)
    }
    const rows = p.metadata_rows || []
    if (rows.length > 1) {
      lines.push(`该文件由 ${rows.map((r) => r.user_id).join('、')} 上传，删除将同时移除两人的记录。`)
    }
    if (p.source === 'jxtz') {
      lines.push('该文件由系统从教务处网站自动抓取，删除后不会自动恢复。')
    }
    return lines.join('\n')
  },

  _doDelete(scope, filename) {
    wx.showLoading({ title: '删除中...', mask: true })
    wx.request({
      url: METHODS_KNOWLEDGE, method: 'DELETE', timeout: 30000,
      header: { 'Authorization': `Bearer ${this._token}`, 'content-type': 'application/json' },
      data: { scope, filename },
      success: (res) => {
        wx.hideLoading()
        const d = res.data || {}
        if (res.statusCode >= 200 && res.statusCode < 300) {
          wx.showToast({ title: '已删除', icon: 'success' })
        } else if (res.statusCode === 404) {
          wx.showToast({ title: '文件已不存在，已为你刷新', icon: 'none' })
        } else {
          wx.showToast({ title: ERROR_TEXT[d.error] || d.error || '删除失败', icon: 'none' })
        }
        this._loadList()
      },
      fail: () => { wx.hideLoading(); wx.showToast({ title: '删除失败', icon: 'none' }) },
    })
  },

  // —— 批量删除：一次 dry_run 汇总 → 二次确认 → 逐条执行 ——
  tapBatchDelete() {
    const filenames = this.data.view.filter((it) => it.checked).map((it) => it.filename)
    if (!filenames.length) return
    this._batchDryRun(filenames)
  },

  _batchDryRun(filenames) {
    const scope = this.data.activeScope
    wx.showLoading({ title: '校验中...', mask: true })
    wx.request({
      url: METHODS_KNOWLEDGE_BATCH, method: 'DELETE', timeout: 30000,
      header: { 'Authorization': `Bearer ${this._token}`, 'content-type': 'application/json' },
      data: { scope, filenames, dry_run: true },
      success: (res) => {
        wx.hideLoading()
        const d = res.data || {}
        if (res.statusCode < 200 || res.statusCode >= 300) {
          wx.showToast({ title: ERROR_TEXT[d.error] || d.error || '操作失败', icon: 'none' }); return
        }
        const by = d.by_source || {}
        const content = `将删除 ${(d.items || []).length} 个文件、共 ${d.total_nodes || 0} 个知识片段`
          + `（教务通知 ${by.jxtz || 0} 个 / 文件 ${by.file || 0} 个），删除后不可撤回。`
        wx.showModal({
          title: '批量删除', content, confirmText: '删除', confirmColor: '#e64340',
          success: (m) => { if (m.confirm) this._doBatchDelete(filenames) },
        })
      },
      fail: () => { wx.hideLoading(); wx.showToast({ title: '操作失败', icon: 'none' }) },
    })
  },

  _doBatchDelete(filenames) {
    const scope = this.data.activeScope
    wx.showLoading({ title: '删除中...', mask: true })
    wx.request({
      url: METHODS_KNOWLEDGE_BATCH, method: 'DELETE', timeout: 60000,
      header: { 'Authorization': `Bearer ${this._token}`, 'content-type': 'application/json' },
      data: { scope, filenames },
      success: (res) => {
        wx.hideLoading()
        const d = res.data || {}
        if (res.statusCode < 200 || res.statusCode >= 300) {
          wx.showToast({ title: ERROR_TEXT[d.error] || d.error || '删除失败', icon: 'none' }); return
        }
        const failures = (d.results || []).filter((r) => !r.ok).map((r) => ({
          filename: r.filename,
          reason: ERROR_TEXT[r.error] || r.error || '删除失败',
        }))
        this.setData({
          batchResult: { deleted: d.deleted || 0, failed: d.failed || 0, failures },
        })
        wx.showToast({
          title: d.failed ? `已删除 ${d.deleted} 个，${d.failed} 个失败` : `已删除 ${d.deleted} 个`,
          icon: d.failed ? 'none' : 'success',
        })
        this._loadList()
      },
      fail: () => { wx.hideLoading(); wx.showToast({ title: '删除失败', icon: 'none' }) },
    })
  },

  closeBatchResult() { this.setData({ batchResult: null }) },

  // —— 孤儿扫描（仅 admin/owner）：只列候选，删除仍走批量确认 ——
  tapScanOrphans() {
    if (this.data.orphanScanning) return
    const scope = this.data.activeScope
    this.setData({ orphanScanning: true })
    wx.request({
      url: METHODS_KNOWLEDGE_ORPHANS, method: 'GET', timeout: 60000,
      header: { 'Authorization': `Bearer ${this._token}` },
      data: { scope },
      success: (res) => {
        this.setData({ orphanScanning: false })
        const d = res.data || {}
        if (res.statusCode < 200 || res.statusCode >= 300) {
          // 503 时明确提示服务不可用，不展示空列表（否则会被误读成「没有孤儿」）
          wx.showToast({ title: ERROR_TEXT[d.error] || '知识库服务暂时不可用，请稍后重试', icon: 'none' })
          return
        }
        const orphans = (d.orphans || []).map((it) => ({
          filename: it.filename,
          display_name: it.display_name || it.filename,
          sub: (it.uploaders || []).map((u) => u.user_id).join('、'),
          checked: false,
        }))
        if (!orphans.length) {
          this.setData({ orphans: [], orphanListed: false })
          wx.showToast({ title: '未发现可清理的记录', icon: 'none' })
          return
        }
        this.setData({ orphans, orphanListed: true })
      },
      fail: () => {
        this.setData({ orphanScanning: false })
        wx.showToast({ title: '知识库服务暂时不可用，请稍后重试', icon: 'none' })
      },
    })
  },

  toggleOrphan(e) {
    const { filename } = e.currentTarget.dataset
    const orphans = (this.data.orphans || []).map((it) => (
      it.filename === filename ? { ...it, checked: !it.checked } : it))
    this.setData({ orphans })
  },

  tapDeleteOrphans() {
    const filenames = (this.data.orphans || []).filter((it) => it.checked).map((it) => it.filename)
    if (!filenames.length) { wx.showToast({ title: '请先勾选要清理的记录', icon: 'none' }); return }
    this._batchDryRun(filenames)
  },
})
