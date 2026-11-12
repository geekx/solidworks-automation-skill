/**
 * @file 离线采集 Ruleset 完整配置并签发证明。
 *
 * 使用场景：维护者在本机以管理员授权运行一次，产出可被 CI 只读校验的证明文件。
 * 私钥只从本机路径或环境变量读取，**永不写入仓库、日志或证明文件**。
 *
 * 为什么必须离线：GitHub 仅在调用者对 ruleset 有写权限时返回 `bypass_actors`，
 * CI 的只读审计凭据读不到该字段。把写权限放进 CI 等于放弃最小权限，故改为离线采集 + 签名。
 * 一次性管理员读取只在本机进行，不默认存为可被 Actions 读取的 Repository Secret。
 *
 * 合规门槛：默认只在远端状态真正合规时签发；非合规时**不写文件**并以非零退出。
 * 仅当显式传 `--allow-noncompliant`（诊断留证）时，才写出标记 `compliant: false` 的记录，
 * 该记录永远无法通过准入校验。
 *
 * 用法：
 *   node scripts/ci/capture_governance_attestation.cjs [--ttl-days 30] [--out <path>]
 *        [--key <path>] [--print-only] [--allow-noncompliant] [--verify]
 * 环境变量：
 *   GH_TOKEN                 管理员授权（必需；仅用于读取，不写入任何输出）
 *   GOVERNANCE_SIGNING_KEY   私钥 PEM 内容（可选，优先于 --key）
 */
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const lib = require('./governance_attestation.cjs');

const TRUSTED_REPOSITORY = 'wzyn20051216/solidworks-automation-skill';
const TRUSTED_REPOSITORY_ID = '1184045765';
const RULESET_NAME = 'protect-main';
const DEFAULT_OUT = path.join('docs', 'architecture', 'evidence', 'governance-ruleset-attestation.json');
const DEFAULT_KEY = path.join(os.homedir(), '.cad-governance', 'ed25519-private.pem');
const DAY_MS = 24 * 60 * 60 * 1000;

const USAGE = '用法: node scripts/ci/capture_governance_attestation.cjs [--ttl-days N] [--out PATH]'
  + ' [--key PATH] [--print-only] [--allow-noncompliant] [--verify]';

function parseArgs(argv) {
  const options = { ttlDays: lib.MAX_TTL_DAYS, out: DEFAULT_OUT, key: DEFAULT_KEY,
    printOnly: false, allowNoncompliant: false, verify: false };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--ttl-days') options.ttlDays = Number(argv[++index]);
    else if (arg === '--out') options.out = argv[++index];
    else if (arg === '--key') options.key = argv[++index];
    else if (arg === '--print-only') options.printOnly = true;
    else if (arg === '--allow-noncompliant') options.allowNoncompliant = true;
    else if (arg === '--verify') options.verify = true;
    else if (arg === '--help' || arg === '-h') {
      console.log(USAGE);
      process.exit(0);
    } else throw new Error(`未知参数: ${arg}`);
  }
  if (!Number.isInteger(options.ttlDays) || options.ttlDays < 1 || options.ttlDays > lib.MAX_TTL_DAYS) {
    throw new Error(`--ttl-days 必须是 1..${lib.MAX_TTL_DAYS} 的整数`);
  }
  return options;
}

/** @brief 私钥来源优先级：环境变量 > 本机文件。任何情况下都不写入输出。 */
function loadPrivateKey({ env = process.env, keyPath = DEFAULT_KEY } = {}) {
  if (env.GOVERNANCE_SIGNING_KEY && env.GOVERNANCE_SIGNING_KEY.trim()) return env.GOVERNANCE_SIGNING_KEY;
  if (!fs.existsSync(keyPath)) throw new Error(`私钥不存在: ${keyPath}（私钥必须留在本机，不得入库）`);
  return fs.readFileSync(keyPath, 'utf8');
}

function keyIdOfPrivateKey(privateKeyPem) {
  const publicPem = crypto.createPublicKey(crypto.createPrivateKey(privateKeyPem)).export({ type: 'spki', format: 'pem' });
  return lib.keyIdOf(publicPem);
}

async function githubGet(token, route) {
  const response = await fetch(`https://api.github.com${route}`, { headers: {
    Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' } });
  if (!response.ok) throw new Error(`GitHub 读取失败: HTTP ${response.status} ${route}`);
  return response.json();
}

/**
 * @brief 采集、判定合规、构造并签名。
 * @param token 管理员授权；仅用于读，不进入返回值
 * @param privateKeyPem 私钥 PEM
 * @param ttlDays 有效期天数（1..MAX_TTL_DAYS）
 * @param now 采集时间（测试可注入）
 * @param fetchJson API 读取实现（测试可注入）
 * @note 读不到 bypass_actors 时**拒绝生成**：此时任何"bypass 为空"的结论都是伪造断言。
 *       合规时返回的证明 `compliant=true`；不合规时仅在 `allowNoncompliant` 下返回，
 *       且 `compliant=false` 并带 `noncompliance_reasons`，调用方必须据此判定不可用于准入。
 */
async function capture({ token, privateKeyPem, ttlDays = lib.MAX_TTL_DAYS, now = new Date(),
  fetchJson = githubGet, allowNoncompliant = false } = {}) {
  if (!token) throw new Error('缺少 GH_TOKEN：需要具备 Administration 写权限的授权才能读取 bypass_actors');
  if (!privateKeyPem) throw new Error('缺少签名私钥');
  if (!Number.isInteger(ttlDays) || ttlDays < 1 || ttlDays > lib.MAX_TTL_DAYS) throw new Error(`ttlDays 必须是 1..${lib.MAX_TTL_DAYS}`);

  const repository = await fetchJson(token, `/repos/${TRUSTED_REPOSITORY}`);
  if (String(repository.id) !== TRUSTED_REPOSITORY_ID || repository.full_name !== TRUSTED_REPOSITORY) {
    throw new Error(`仓库身份不符: id=${repository.id} full_name=${repository.full_name}`);
  }
  const rulesetList = await fetchJson(token, `/repos/${TRUSTED_REPOSITORY}/rulesets?per_page=100`);
  const target = (Array.isArray(rulesetList) ? rulesetList : []).find(item => item.name === RULESET_NAME);
  if (!target) throw new Error(`未找到规则集 ${RULESET_NAME}`);
  const detail = await fetchJson(token, `/repos/${TRUSTED_REPOSITORY}/rulesets/${target.id}`);

  if (!Array.isArray(detail.bypass_actors)) {
    throw new Error('当前凭据看不到 bypass_actors（需对该 ruleset 有写权限）；拒绝生成可能失真的证明');
  }
  if (!Array.isArray(detail.rules)) throw new Error('规则集响应缺少 rules，拒绝生成证明');

  const refName = detail.conditions?.ref_name || {};
  const ruleset = {
    id: detail.id, name: detail.name, target: detail.target, enforcement: detail.enforcement,
    ref_name_include: [...(refName.include || [])], ref_name_exclude: [...(refName.exclude || [])],
    updated_at: detail.updated_at, created_at: detail.created_at,
    rules: detail.rules, bypass_actors: detail.bypass_actors,
  };
  const compliance = lib.evaluateCompliance(ruleset);
  const warnings = compliance.compliant ? [] : [`远端状态不合规：${compliance.reasons.join(', ')}`];

  // 非合规默认拒签：不产出任何文件，避免"已生成"被误读成"已通过"。
  if (!compliance.compliant && !allowNoncompliant) {
    const error = new Error(`远端规则集不合规，拒绝签发证明：${compliance.reasons.join(', ')}`
      + '（如需留存诊断证据，请显式使用 --allow-noncompliant，产物不可用于准入）');
    error.compliance = compliance;
    throw error;
  }
  const policyDigest = lib.policyDigest(ruleset);
  const snapshotDigest = lib.snapshotDigest(ruleset);
  if (policyDigest === null || snapshotDigest === null) throw new Error('无法计算规则集摘要，拒绝生成证明');

  const capturedAt = now instanceof Date ? now : new Date(now);
  const unsigned = {
    schema: lib.SCHEMA,
    repository: { id: String(repository.id), full_name: repository.full_name },
    ruleset,
    policy_digest_sha256: policyDigest,
    snapshot_digest_sha256: snapshotDigest,
    compliant: compliance.compliant,
    noncompliance_reasons: compliance.reasons,
    governance: {
      mode: 'single_maintainer', independent_audit: false, bypass_requirement: 'empty',
      trust_boundary: '证明由维护者以本机管理员授权采集并自行签名；信任根公钥存放于仓库内，'
        + '能合并到 main 的人可同时替换公钥与证明，故本证明仅提供防篡改留痕，不构成独立审计或独立审批。'
        + '仓库变量 GOVERNANCE_TRUST_ROOT_ANCHOR 提供仓库文件之外的第二道锚点，但不阻止仓库管理员一并修改。',
    },
    captured_at: capturedAt.toISOString(),
    expires_at: new Date(capturedAt.getTime() + ttlDays * DAY_MS).toISOString(),
    generator: { tool: 'capture_governance_attestation.cjs', node: process.version },
    signer: { key_id: keyIdOfPrivateKey(privateKeyPem), key_type: 'ed25519', signed_at: capturedAt.toISOString() },
  };
  return { attestation: { ...unsigned, signature: lib.signAttestation(unsigned, privateKeyPem) },
    warnings, ruleset, compliance, policyDigest, snapshotDigest };
}

/** @brief `--verify`：用只读快照与仓库内信任根复核刚采集的证明（不发写请求）。 */
async function verifyCaptured({ attestation, token, now = new Date(), fetchJson = githubGet }) {
  const live = await fetchJson(token, `/repos/${TRUSTED_REPOSITORY}/rulesets/${attestation.ruleset.id}`);
  const { TRUSTED_KEYS } = require('./governance_trust_root.cjs');
  return lib.verifyAttestation({ attestation, trustRoot: TRUSTED_KEYS, live, now,
    repository: { id: TRUSTED_REPOSITORY_ID, full_name: TRUSTED_REPOSITORY } });
}

/**
 * @brief 采集 + 落盘的唯一边界。
 * @note 落盘只发生在 `capture()` 成功之后；非合规默认拒签时会先抛错，因此**不会产生任何文件**。
 *       该顺序由测试直接断言，避免"已生成"被误读为"已通过"。
 */
async function runCapture(options, { env = process.env, fetchJson = githubGet } = {}) {
  const privateKeyPem = loadPrivateKey({ env, keyPath: options.key });
  const result = await capture({ token: env.GH_TOKEN, privateKeyPem, ttlDays: options.ttlDays,
    allowNoncompliant: options.allowNoncompliant, fetchJson });
  const serialized = `${JSON.stringify(result.attestation, null, 2)}\n`;
  if (options.printOnly) process.stdout.write(serialized);
  else {
    fs.mkdirSync(path.dirname(options.out), { recursive: true });
    fs.writeFileSync(options.out, serialized);
  }
  return { ...result, outPath: options.printOnly ? null : options.out };
}

async function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
    const { attestation, warnings, compliance, outPath } = await runCapture(options);
    if (outPath) console.log(`已写入证明: ${outPath}`);
    console.log(`ruleset           : ${attestation.ruleset.id} / ${attestation.ruleset.name}`);
    console.log(`updated_at        : ${attestation.ruleset.updated_at}`);
    console.log(`bypass_actors     : ${attestation.ruleset.bypass_actors.length}`);
    console.log(`compliant         : ${attestation.compliant}`);
    if (!compliance.compliant) console.log(`noncompliance     : ${compliance.reasons.join(', ')}`);
    console.log(`policy_digest     : ${attestation.policy_digest_sha256}`);
    console.log(`snapshot_digest   : ${attestation.snapshot_digest_sha256}`);
    console.log(`有效期            : ${attestation.captured_at} -> ${attestation.expires_at}`);
    console.log(`签名 key_id       : ${attestation.signer.key_id}`);
    for (const warning of warnings) console.warn(`警告: ${warning}`);
    if (options.verify) {
      const report = await verifyCaptured({ attestation, token: process.env.GH_TOKEN });
      console.log(`--verify          : ${report.ok ? '通过' : '未通过'} ${report.reasons.join(', ')}`);
      if (!report.ok) return 3;
    }
    return compliance.compliant ? 0 : 2;
  } catch (error) {
    console.error(`失败: ${error.message}`);
    return 1;
  }
}

module.exports = { capture, runCapture, verifyCaptured, parseArgs, loadPrivateKey, keyIdOfPrivateKey, USAGE,
  TRUSTED_REPOSITORY, TRUSTED_REPOSITORY_ID, RULESET_NAME, DEFAULT_OUT, DEFAULT_KEY };
if (require.main === module) main().then(code => { process.exitCode = code; });
