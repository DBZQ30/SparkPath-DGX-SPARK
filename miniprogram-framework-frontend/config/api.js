//config/api.js
const {
  API_BASE_URL: _API_BASE_URL,
  WARNING_API_BASE: _WARNING_API_BASE,
  PLAN_API_BASE: _PLAN_API_BASE,
} = require('./instance')

// 统一去掉 BASE 结尾的斜杠：instance 里的 API_BASE_URL 形如 ".../dgx-agentapi/"，
// 直接 `${BASE}/api/x` 会拼出 ".../dgx-agentapi//api/x"（双斜杠）。上游 nginx 目前
// 会规整掉、能跑通，但双斜杠在部分网关会被当成不同路径或触发重定向，属隐患。
// 去尾斜杠后每个接口仍是各自 BASE 的派生，validate.js 的 startsWith 派生校验不受影响。
const stripTrailingSlash = (url) => (url || '').replace(/\/+$/, '')
const BASE_URL = stripTrailingSlash(_API_BASE_URL)
const WARNING_BASE = stripTrailingSlash(_WARNING_API_BASE)
const PLAN_BASE = stripTrailingSlash(_PLAN_API_BASE)

module.exports = {
  LOGIN_URL: `${BASE_URL}/api/miniapp/login`,
  // 设备算力指标（GPU / 统一内存 / 吞吐）——接口仍在部署（agent4som/gpu_metrics.py）。
  // 轨迹页当前**只展示总耗时**，不再显示算力三项；此处保留 URL 供需要时快速接回。
  PLAN_METRICS_URL: PLAN_BASE ? `${PLAN_BASE}/api/metrics/gpu` : '',
  // 培养方案智能解读（004 设计）：agent4som HTTP 服务（PLAN_API_BASE 空 = 未启用）
  PLAN_MAJORS_URL: PLAN_BASE ? `${PLAN_BASE}/api/plan/majors` : '',
  PLAN_OVERVIEW_URL: PLAN_BASE ? `${PLAN_BASE}/api/plan/overview` : '',
  PLAN_SEMESTER_URL: PLAN_BASE ? `${PLAN_BASE}/api/plan/semester-map` : '',
  PLAN_COURSES_URL: PLAN_BASE ? `${PLAN_BASE}/api/plan/courses` : '',
  PLAN_PREREQ_URL: PLAN_BASE ? `${PLAN_BASE}/api/plan/prereq` : '',
  PLAN_PREREQ_IMAGE_URL: PLAN_BASE ? `${PLAN_BASE}/api/plan/prereq-image` : '',
  PLAN_STATUS_URL: PLAN_BASE ? `${PLAN_BASE}/api/plan/status` : '',
  PLAN_UPLOAD_URL: PLAN_BASE ? `${PLAN_BASE}/api/plan/upload` : '',
  PLAN_RETRY_URL: PLAN_BASE ? `${PLAN_BASE}/api/plan/retry` : '',
  PLAN_PREREQ_VERIFY_URL: PLAN_BASE ? `${PLAN_BASE}/api/plan/prereq/verify` : '',
  // 多路径个性化学业规划（010 设计）：路线图 / 对比 / 专业选择 / 转专业
  PLAN_ROUTE_OPTIONS_URL: PLAN_BASE ? `${PLAN_BASE}/api/plan/route/options` : '',
  PLAN_ROUTE_URL: PLAN_BASE ? `${PLAN_BASE}/api/plan/route` : '',
  PLAN_ROUTE_COMPARE_URL: PLAN_BASE ? `${PLAN_BASE}/api/plan/route/compare` : '',
  PLAN_SELECT_SIMULATE_URL: PLAN_BASE ? `${PLAN_BASE}/api/plan/select/simulate` : '',
  PLAN_SIMULATE_TRANSFER_URL: PLAN_BASE ? `${PLAN_BASE}/api/plan/simulate/transfer` : '',
  // 文件中心（007 设计）：公共教学文件统一上传与适用性管理（管理员）
  PLAN_DOC_CENTER_FILES_URL: PLAN_BASE ? `${PLAN_BASE}/api/doc-center/files` : '',
  PLAN_DOC_CENTER_UPLOAD_URL: PLAN_BASE ? `${PLAN_BASE}/api/doc-center/upload` : '',
  PLAN_DOC_CENTER_STATUS_URL: PLAN_BASE ? `${PLAN_BASE}/api/doc-center/status` : '',
  PLAN_DOC_CENTER_FILE_URL: PLAN_BASE ? `${PLAN_BASE}/api/doc-center/file` : '',
  PLAN_DOC_CENTER_RETRY_URL: PLAN_BASE ? `${PLAN_BASE}/api/doc-center/retry` : '',
  // 学业预警（007 设计）：agent4som HTTP 服务（WARNING_API_BASE 空 = 未启用）
  WARNING_UPLOAD_URL: WARNING_BASE ? `${WARNING_BASE}/api/warning/upload` : '',
  WARNING_STATUS_URL: WARNING_BASE ? `${WARNING_BASE}/api/warning/status` : '',
  WARNING_DELETE_URL: WARNING_BASE ? `${WARNING_BASE}/api/warning/delete` : '',
  WARNING_CHECK_URL: WARNING_BASE ? `${WARNING_BASE}/api/warning/selection-check` : '',
  WARNING_CHECK_RUN_URL: WARNING_BASE ? `${WARNING_BASE}/api/warning/selection-check/run` : '',
  WARNING_GRADE_URL: WARNING_BASE ? `${WARNING_BASE}/api/warning/grade` : '',
  // 年级注册表（唯一事实来源）：所有功能页的年级选项都读它
  WARNING_GRADES_URL: WARNING_BASE ? `${WARNING_BASE}/api/warning/grades` : '',
  // v6.3：确认成绩覆盖学期（POST；PUT 学期沿用 WARNING_GRADE_URL，仅 method 不同）
  WARNING_GRADE_CONFIRM_URL: WARNING_BASE ? `${WARNING_BASE}/api/warning/grade/confirm` : '',
  WARNING_EXPORT_URL: WARNING_BASE ? `${WARNING_BASE}/api/warning/selection-check/export` : '',
  // v7：双类型豁免（POST/GET/DELETE 复用 URL，method 区分）+ 学生详情（打开弹层按需拉）
  WARNING_WAIVER_URL: WARNING_BASE ? `${WARNING_BASE}/api/warning/waiver` : '',
  WARNING_STUDENT_URL: WARNING_BASE ? `${WARNING_BASE}/api/warning/selection-check/student` : '',
  CHAT_URL: `${BASE_URL}/api/chat`,
  HISTORY_URL: `${BASE_URL}/api/miniapp/history`,
  // 聊天等待期的实时进度（只读当前 run 的活动缓冲；见 adapter._run_progress）
  RUN_PROGRESS_URL: `${BASE_URL}/api/miniapp/run-progress`,
  // 全屏轨迹页运行中轮询的实时事件（见 adapter._run_trace）
  RUN_TRACE_URL: `${BASE_URL}/api/miniapp/run-trace`,
  UPLOAD_URL: `${BASE_URL}/api/uploads`,
  MESSAGES_URL: `${BASE_URL}/api/messages`,

  METHODS_IDENTITY: `${BASE_URL}/api/methods/identity`,
  METHODS_APPLY_AUTH: `${BASE_URL}/api/methods/apply-auth`,
  METHODS_UNREAD: `${BASE_URL}/api/methods/unread`,
  METHODS_TEACHER_AUTHS: `${BASE_URL}/api/methods/teacher-auths`,
  METHODS_TEACHER_AUTHS_APPROVE: `${BASE_URL}/api/methods/teacher-auths/approve`,
  METHODS_TEACHER_AUTHS_REJECT: `${BASE_URL}/api/methods/teacher-auths/reject`,
  METHODS_ADMIN_AUTHS: `${BASE_URL}/api/methods/admin-auths`,
  METHODS_ADMIN_AUTHS_APPROVE: `${BASE_URL}/api/methods/admin-auths/approve`,
  METHODS_ADMIN_AUTHS_REJECT: `${BASE_URL}/api/methods/admin-auths/reject`,
  METHODS_CHANGE_ROLE: `${BASE_URL}/api/methods/change-role`,
  METHODS_PROFILE: `${BASE_URL}/api/methods/profile`,
  METHODS_ADMISSION_PROFILE: `${BASE_URL}/api/methods/admission-profile`,
  METHODS_PHONE_BIND: `${BASE_URL}/api/methods/phone-bind`,
  METHODS_PHONE_WHITELIST: `${BASE_URL}/api/methods/phone-whitelist`,
  // 011：学生/教师/管理员分开导入（角色由入口固定）
  METHODS_PHONE_WHITELIST_IMPORT_STUDENT: `${BASE_URL}/api/methods/phone-whitelist/import/student`,
  METHODS_PHONE_WHITELIST_IMPORT_TEACHER: `${BASE_URL}/api/methods/phone-whitelist/import/teacher`,
  METHODS_PHONE_WHITELIST_IMPORT_ADMIN: `${BASE_URL}/api/methods/phone-whitelist/import/admin`,
  // 011：学籍档案（学生只读；管理员列表/编辑/学号绑定）
  METHODS_STUDENT_ARCHIVE: `${BASE_URL}/api/methods/student-archive`,
  METHODS_STUDENT_ARCHIVE_LINK: `${BASE_URL}/api/methods/student-archive/link`,
  METHODS_JXTZ_SYNC: `${BASE_URL}/api/methods/jxtz-sync`,
  METHODS_KNOWLEDGE: `${BASE_URL}/api/methods/knowledge`,
  METHODS_KNOWLEDGE_BATCH: `${BASE_URL}/api/methods/knowledge/batch`,
  METHODS_KNOWLEDGE_ORPHANS: `${BASE_URL}/api/methods/knowledge/orphans`,
  // v1.5：原文只读预览（GET scope/filename）与删除操作历史（GET limit，仅 admin/owner）
  METHODS_KNOWLEDGE_CONTENT: `${BASE_URL}/api/methods/knowledge/content`,
  METHODS_KNOWLEDGE_AUDIT: `${BASE_URL}/api/methods/knowledge/audit`,
  METHODS_CERTIFIED_USERS: `${BASE_URL}/api/methods/certified-users`,
  METHODS_CERTIFIED_USERS_CHANGE_ROLE: `${BASE_URL}/api/methods/certified-users/change-role`,
}
