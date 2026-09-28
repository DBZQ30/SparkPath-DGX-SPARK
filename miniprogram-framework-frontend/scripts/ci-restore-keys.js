// scripts/ci-restore-keys.js —— 在 CI 中从 GitLab CI 变量恢复小程序上传密钥
// 变量命名: KEY_<AppID>，值为密钥文件内容的 base64（密钥文件本身绝不入库）
// 配置入口: GitLab → Settings → CI/CD → Variables（务必勾选 Protected）
// 用法: node scripts/ci-restore-keys.js   （无 KEY_* 变量时静默跳过，本地开发不受影响）
'use strict'

const fs = require('fs')
const path = require('path')

const KEYS_DIR = path.resolve(__dirname, '..', 'keys')

let restored = 0
for (const [name, value] of Object.entries(process.env)) {
  if (!name.startsWith('KEY_')) continue
  const appid = name.slice(4)
  if (!/^wx[0-9a-fA-F]{16}$/.test(appid)) {
    console.warn(`[ci-keys] 跳过: ${name} 不是合法 AppID 命名（应为 KEY_wx...）`)
    continue
  }
  let content
  try {
    content = Buffer.from(value, 'base64').toString('utf8')
  } catch (_) {
    console.warn(`[ci-keys] 跳过: ${name} 不是合法 base64`)
    continue
  }
  if (!content.includes('-----BEGIN PRIVATE KEY-----')) {
    console.warn(`[ci-keys] 跳过: ${name} 解码后不是私钥文件内容（请确认放入了 .key 文件的 base64）`)
    continue
  }
  fs.mkdirSync(KEYS_DIR, { recursive: true })
  fs.writeFileSync(path.join(KEYS_DIR, `${appid}.key`), content)
  restored++
}

console.log(restored
  ? `[ci-keys] 已恢复 ${restored} 个上传密钥到 keys/（仅本 job 内存在，不入库）`
  : '[ci-keys] 未发现 KEY_* 变量，跳过密钥恢复')
