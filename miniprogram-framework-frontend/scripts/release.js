// scripts/release.js —— 用 miniprogram-ci 上传代码包到指定小程序
// 用法:
//   单个发布: node scripts/release.js <instanceId> [--version 1.0.0] [--desc "发布说明"] [--robot 1]
//   批量发布: node scripts/release.js --all [--version 1.0.0] [--desc "..."] [--robot 1]
// 前置条件:
//   1. 已安装依赖: npm install
//   2. 上传密钥: keys/<AppID>.key（微信公众平台 → 开发管理 → 开发设置 → 小程序代码上传密钥，
//      每个小程序一把，用 AppID 命名；keys/ 目录已 gitignore，不会入库）
'use strict'

const fs = require('fs')
const path = require('path')
const cp = require('child_process')

const { buildInstance, listInstances } = require('./build')

const ROOT = path.resolve(__dirname, '..')
const KEYS_DIR = path.join(ROOT, 'keys')

function parseArgs(argv) {
  const args = { all: false, version: null, desc: null, robot: 1, id: null }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--all') args.all = true
    else if (a === '--version') args.version = argv[++i]
    else if (a === '--desc') args.desc = argv[++i]
    else if (a === '--robot') args.robot = Number(argv[++i]) || 1
    else if (!a.startsWith('--') && !args.id) args.id = a
  }
  return args
}

// 发布留痕：成功后追加 CHANGELOG.md 并打本地 tag（<实例>@<版本>），保证可追溯
// CI 环境（GitLab）下不打 tag（tag 由流水线/平台管理），仅记录 CHANGELOG
function recordRelease(id, cfg, version, desc, success) {
  if (!success) return
  const ts = new Date().toISOString().slice(0, 10)
  let commitHash = ''
  try { commitHash = cp.execSync('git rev-parse --short HEAD', { cwd: ROOT, encoding: 'utf8' }).trim() } catch (_) {}
  const descSafe = String(desc || '').replace(/\|/g, '\\|').replace(/\n/g, ' ')

  // 1) CHANGELOG.md（幂等：同实例同版本不重复记录）
  const changelog = path.join(ROOT, 'CHANGELOG.md')
  if (!fs.existsSync(changelog)) {
    fs.writeFileSync(changelog, '# CHANGELOG\n\n| 日期 | 实例 | 版本 | Commit | 说明 |\n|---|---|---|---|---|\n')
  }
  const content = fs.readFileSync(changelog, 'utf8')
  const line = `| ${ts} | ${id} | ${version} | \`${commitHash || '-'}\` | ${descSafe} |\n`
  if (!content.includes(`| ${ts} | ${id} | ${version} |`)) {
    fs.appendFileSync(changelog, line)
    console.log(`[release] ✓ 已记录 CHANGELOG.md（请提交该文件）`)
  }

  // 2) 本地 tag（CI 环境跳过；tag 名只保留安全字符）
  if (!process.env.CI) {
    const tag = `${id}@${String(version).replace(/[^\w.-]/g, '_')}`
    try {
      cp.execSync(`git tag "${tag}"`, { cwd: ROOT })
      console.log(`[release] ✓ 已打本地 tag: ${tag}（git push --tags 推送后可用于追溯）`)
    } catch (e) {
      console.warn(`[release] ⚠ 打 tag 失败（可能已存在）: ${e.message.split('\n')[0]}`)
    }
  }
}

async function uploadOne(id, { version, desc, robot }) {
  const cfg = buildInstance(id) // 发布前先按该实例组装

  const keyFile = path.join(KEYS_DIR, `${cfg.APPID}.key`)
  if (!fs.existsSync(keyFile)) {
    console.error(`[release] ✗ ${cfg.ASSISTANT_TITLE} (${cfg.APPID}): 缺少上传密钥`)
    console.error(`         请将微信公众平台下载的密钥保存为 ${path.relative(ROOT, keyFile)}（keys/ 已 gitignore）`)
    return false
  }

  let ci
  try {
    ci = require('miniprogram-ci')
  } catch (err) {
    console.error('[release] ✗ 未安装 miniprogram-ci，请先执行 npm install')
    return false
  }

  const project = new ci.Project({
    appid: cfg.APPID,
    type: 'miniProgram',
    projectPath: ROOT,
    privateKeyPath: keyFile,
    ignores: ['node_modules/**/*', 'keys/**/*', 'scripts/**/*'],
  })

  const v = version || '1.0.0'
  console.log(`[release] 上传 ${cfg.ASSISTANT_TITLE} (${cfg.APPID}) v${v} ...`)
  try {
    await ci.upload({
      project,
      version: v,
      desc: desc || `发布 ${cfg.ASSISTANT_TITLE}`,
      robot,
      setting: { es6: true, minify: true },
      onProgressUpdate: (p) => {
        if (p.status === 'uploading') console.log(`[release]   上传进度: ${p.data}%`)
      },
    })
    console.log(`[release] ✓ ${cfg.ASSISTANT_TITLE} (${cfg.APPID}) 上传成功`)
    recordRelease(id, cfg, v, desc, true)
    return true
  } catch (err) {
    console.error(`[release] ✗ ${cfg.ASSISTANT_TITLE} 上传失败: ${err.message}`)
    return false
  }
}

async function main() {
  const { all, id, version, desc, robot } = parseArgs(process.argv.slice(2))
  const ids = all ? listInstances() : [id || '_default']

  // 警告：工作区有未提交修改时，发布会把这些改动一并打进代码包
  try {
    const dirty = cp.execSync('git status --porcelain', { cwd: ROOT, encoding: 'utf8' }).trim()
    if (dirty) {
      console.warn(`[release] ⚠ 工作区有未提交的修改，发布将包含以下改动（如需纯净发布，请先提交或切换到干净分支）:`)
      console.warn(dirty.split('\n').slice(0, 10).map((l) => `  ${l}`).join('\n'))
    }
  } catch (_) { /* 非 git 环境（如 CI 打包目录），跳过检查 */ }

  if (ids.length === 0) {
    console.error('[release] ✗ 未找到任何实例配置（config/instances/ 为空？）')
    process.exit(1)
  }

  fs.mkdirSync(KEYS_DIR, { recursive: true })
  console.log(`[release] 待发布 ${ids.length} 个实例: ${ids.join(', ')}`)

  let ok = 0
  for (const i of ids) {
    if (await uploadOne(i, { version, desc, robot })) ok++
  }
  console.log(`\n[release] 完成: ${ok}/${ids.length} 成功`)
  process.exit(ok === ids.length ? 0 : 1)
}

if (require.main === module) main()

module.exports = { uploadOne, recordRelease, parseArgs }
