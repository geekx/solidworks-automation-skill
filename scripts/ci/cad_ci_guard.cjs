/** @file 真机 Runner 分配前的只读授权核验；缺配置、未知或 API 错误均阻断。 */
const fs = require('node:fs');
const lib = require('./governance_attestation.cjs');
const { TRUSTED_KEYS: DEFAULT_TRUST_ROOT } = require('./governance_trust_root.cjs');

const TRUSTED_REPOSITORY = 'wzyn20051216/solidworks-automation-skill';
const TRUSTED_REPOSITORY_ID = '1184045765';
const ENVIRONMENT = 'cad-validation';
/** @brief 允许合并前必须通过的既有状态检查；改名需同步 ruleset。 */
const REQUIRED_CHECK = lib.REQUIRED_CHECK;
/** @brief 离线签名证明的固定路径；CI 只读该文件，不承担任何写权限。 */
const ATTESTATION_PATH = 'docs/architecture/evidence/governance-ruleset-attestation.json';
/** @brief 仅当 NODE_ENV=test 时才接受路径注入；生产验证不得被环境变量改写证明来源。 */
const TEST_ENV_NAME = 'test';

/**
 * @brief 对 GitHub 实际状态做纯策略判断；供在线读取和离线故障注入共用。
 * @note 本项目为单人核心维护 + 开放社区贡献：审批数可以为 0，但进入 main 必须经 Pull Request、
 *       必须通过必需状态检查，且任何 bypass 都必须为空。
 *       `bypass_actors` 在只读凭据下不可读（GitHub 仅在调用者对 ruleset 有写权限时返回），
 *       因此该字段的证据来自维护者离线采集并签名的证明；本函数只做只读比对与验签。
 *       信任根本身也可能被"公钥 + 日志 + 证明"整体替换，故再用仓库变量锚点做外部比对。
 */
function evaluatePolicy(snapshot) {
  const { context = {}, branch = {}, rules = [], rulesets = [], permissions = {}, environment = {}, runners = [],
    attestation = null, trustRoot = DEFAULT_TRUST_ROOT, trustRootAnchor = null,
    attestationSource = null, now = new Date() } = snapshot;
  const reviewers = environment?.protection_rules?.find(r => r.type === 'required_reviewers');
  const branchPolicy = environment?.deployment_branch_policy;
  const roles = [context.actor, context.triggering_actor].filter(Boolean);
  const activeRulesets = (Array.isArray(rulesets) ? rulesets : [])
    .filter(r => r.enforcement === 'active' && (!r.target || r.target === 'branch'));
  const statusChecks = rules.find(r => r.type === 'required_status_checks');
  const requiredContexts = (statusChecks?.parameters?.required_status_checks || []).map(c => c?.context);
  // 无独立审查人属单人维护的固有边界，不是配置错误：记录为风险，不阻断准入。
  const independentReview = Boolean(reviewers?.prevent_self_review === true && reviewers?.reviewers?.length > 0);
  // bypass 在该快照里通常不可见；仅作诊断信息与纵深检查，绝不据此推断"为空"。
  const bypassVisible = activeRulesets.length > 0 && activeRulesets.every(r => Array.isArray(r.bypass_actors));

  // 信任锚点：仓库变量注入的期望值必须存在、格式合法且与实际信任根一致。
  const expectedAnchor = typeof trustRootAnchor === 'string' ? trustRootAnchor.trim() : '';
  const actualAnchor = lib.trustRootAnchor(trustRoot);
  const anchorFormatOk = /^sha256:[a-f0-9]{64}$/.test(expectedAnchor);
  const anchorFailures = [];
  if (expectedAnchor.length === 0) anchorFailures.push('trust_root_anchor_missing');
  else if (!anchorFormatOk) anchorFailures.push('trust_root_anchor_invalid_format');
  else if (actualAnchor === null) anchorFailures.push('trust_root_anchor_uncomputable');
  else if (actualAnchor !== expectedAnchor) anchorFailures.push('trust_root_anchor_mismatch');

  // 证明绑定到具体 ruleset：优先按证明中的 id 找线上对应项，找不到才算身份不符。
  const attestedId = attestation?.ruleset?.id;
  const liveRuleset = activeRulesets.find(r => r.id === attestedId) || activeRulesets[0] || null;
  const verification = lib.verifyAttestation({
    attestation, trustRoot, live: liveRuleset, now, requiredCheck: REQUIRED_CHECK,
    repository: { id: context.repository_id, full_name: context.repository },
  });
  const attested = verification.checks;

  const checks = {
    trusted_repository: context.repository === TRUSTED_REPOSITORY && context.repository_id === TRUSTED_REPOSITORY_ID,
    manual_branch_only: context.event_name === 'workflow_dispatch' && context.ref_type === 'branch'
      && context.ref?.startsWith('refs/heads/') && context.ref_protected === true,
    // ref_protected 只保证"所选分支受保护"，不保证它就是 main；显式断言，避免未来新增受保护分支后
    // 用该分支的代码执行本凭据链路。
    protected_ref_is_main: context.ref === lib.GOVERNED_REF,
    protected_immutable_source: branch.protected === true && /^[a-f0-9]{40}$/.test(context.sha || '') && branch.commit?.sha === context.sha,
    ruleset_active: activeRulesets.length > 0,
    pull_request_required: rules.some(r => r.type === 'pull_request'),
    required_status_checks_present: requiredContexts.includes(REQUIRED_CHECK),
    trust_root_integrity: attested.trust_root_intact,
    trust_root_rotation_log: attested.rotation_log_consistent,
    trust_root_anchor: anchorFailures.length === 0,
    attestation_present: Boolean(attestation),
    attestation_schema: attested.schema_supported,
    attestation_declaration: attested.declaration_valid,
    attestation_signature: attested.signature_valid,
    attestation_repository: attested.repository_matches,
    attestation_ruleset_identity: attested.ruleset_identity_matches,
    attestation_main_scope: attested.main_scope_matches,
    attestation_fresh: attested.not_expired,
    attestation_policy_digest: attested.policy_digest_matches,
    attestation_snapshot_digest: attested.snapshot_digest_matches,
    attestation_updated_at: attested.updated_at_matches,
    attestation_compliant: attested.compliant,
    no_bypass_actors: attested.bypass_empty,
    // 纵深检查：若凭据本就能看见 bypass（如管理员场景），线上非空即阻断；
    // 看不见时不据此推断为空，由签名证明承担该结论。
    live_bypass_consistent: !bypassVisible || activeRulesets.every(r => (r.bypass_actors || []).length === 0),
    authorized_actor_and_rerunner: roles.length === 2 && roles.every(actor => ['admin', 'maintain'].includes(permissions[actor])),
    protected_environment_exists: environment?.name === ENVIRONMENT && Number.isInteger(environment?.id),
    protected_deployment_branches: Boolean(branchPolicy?.protected_branches === true && branchPolicy?.custom_branch_policies === false),
    idle_windows_cad_runner: runners.some(r => r.status === 'online' && r.busy === false
      && ['self-hosted', 'windows', 'cad'].every(label => r.labels?.some(v => v.name?.toLowerCase() === label))),
  };
  const governanceRisks = [];
  if (!independentReview) governanceRisks.push('no_independent_human_reviewer');
  if (reviewers && reviewers.prevent_self_review !== true) governanceRisks.push('environment_allows_self_review');
  if (!bypassVisible) governanceRisks.push('bypass_list_not_readable');
  // CODEOWNERS 文件已存在，但 require_code_owner_review 未启用：文件本身不构成门禁。
  governanceRisks.push('code_owner_review_not_enforced');
  // PR 仍可同时修改 guard、工作流与测试；在没有可信基线工作流的前提下，这条链未闭环。
  governanceRisks.push('trusted_baseline_workflow_not_established');
  // 这三项是架构固有限制，不随配置改善，必须长期披露。
  governanceRisks.push('attestation_self_issued_by_maintainer');
  governanceRisks.push('trust_root_stored_in_repository');
  if (attestationSource?.test_override) governanceRisks.push('attestation_path_overridden_by_test_env');
  const blocking = Object.keys(checks).filter(key => !checks[key]);
  return { status: blocking.length === 0 ? 'authorized' : 'blocked', checks, blocking_reasons: blocking,
    attestation_reasons: [...new Set([...anchorFailures, ...verification.reasons])],
    governance: { mode: 'single_maintainer', independent_human_review: independentReview, independent_audit: false,
      required_check: REQUIRED_CHECK, bypass_requirement: 'empty', bypass_proof: 'offline_signed_attestation',
      bypass_visible: bypassVisible,
      // 该列表来自签名证明；线上只读凭据看不到此字段，故此处绝不使用"字段缺失"推导空数组。
      bypass_actors: verification.facts.bypass_actors,
      trust_root_anchor: { expected: expectedAnchor || null, actual: actualAnchor, matched: checks.trust_root_anchor },
      attestation_source: attestationSource,
      attestation: { present: Boolean(attestation), verified: verification.ok, reasons: verification.reasons, ...verification.facts },
      trust_root_keys: Object.keys(trustRoot?.keys || {}), remaining_risks: governanceRisks,
      note: '单人维护：准入由 Pull Request + 必需状态检查 + 离线签名证明 + 仓库变量信任锚点把关；'
        + '未提供独立审批、独立审计或双人复核能力' },
    // Environment 管理员绕过及跨工作流 Runner 隔离不由这份 REST 快照证明。
    scope: 'workflow_admission_only', safety_acceptance_complete: false,
    manual_conditions: ['禁止管理员绕过 Environment 审批', 'Runner 只允许可信工作流访问并与日常桌面隔离',
      '维护者不得把本报告当作独立审批已通过的证据',
      '证明由维护者自行签发且信任根在仓库内，不得表述为独立审计',
      '治理代码、工作流与信任根的变更仍需要维护者人工确认，当前无强制 code owner 审查',
      'PR 仍可同时修改验证代码与工作流；可信基线工作流尚未建立'] };
}

/**
 * @brief 读取离线证明。
 * @note GOVERNANCE_ATTESTATION_PATH 只在 NODE_ENV=test 时生效；生产运行忽略该变量，
 *       避免通过环境注入把验证指向任意文件。缺失与解析失败都必须阻断，不能静默降级。
 */
function readAttestation(env = process.env) {
  const testOverride = env.NODE_ENV === TEST_ENV_NAME && typeof env.GOVERNANCE_ATTESTATION_PATH === 'string'
    && env.GOVERNANCE_ATTESTATION_PATH.length > 0;
  const file = testOverride ? env.GOVERNANCE_ATTESTATION_PATH : ATTESTATION_PATH;
  const source = { path: file, test_override: testOverride, ignored_env_path: !testOverride && Boolean(env.GOVERNANCE_ATTESTATION_PATH) };
  if (!fs.existsSync(file)) return { attestation: null, source, error: `attestation file missing: ${file}` };
  try { return { attestation: JSON.parse(fs.readFileSync(file, 'utf8')), source, error: null }; }
  catch (error) { return { attestation: null, source, error: `attestation unreadable: ${error.message}` }; }
}

/** @brief 固定 GitHub API 域名，只读请求；凭据不写入报告。 */
async function readSnapshot(env = process.env) {
  const context = { repository: env.GITHUB_REPOSITORY, repository_id: env.GITHUB_REPOSITORY_ID, event_name: env.GITHUB_EVENT_NAME, ref: env.GITHUB_REF,
    ref_type: env.GITHUB_REF_TYPE, ref_protected: env.GITHUB_REF_PROTECTED === 'true', sha: env.GITHUB_SHA,
    actor: env.GITHUB_ACTOR, triggering_actor: env.GITHUB_TRIGGERING_ACTOR };
  const { attestation, source, error: attestationError } = readAttestation(env);
  const trustRootAnchor = env[lib.TRUST_ROOT_ANCHOR_ENV];
  if (context.repository !== TRUSTED_REPOSITORY || context.repository_id !== TRUSTED_REPOSITORY_ID || context.event_name !== 'workflow_dispatch'
      || context.ref_type !== 'branch' || !context.ref_protected) {
    return { context, attestation, attestationSource: source, trustRootAnchor }; // 不为不可信上下文发起权限请求。
  }
  if (!env.GH_TOKEN) throw new Error('BLOCKED: CAD_CI_AUDIT_TOKEN missing (selected repository, Administration/Actions/Metadata read only)');
  const get = async path => {
    const response = await fetch(`https://api.github.com${path}`, { headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`, Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28' } });
    if (!response.ok) throw new Error(`GitHub read failed: HTTP ${response.status} ${path}`);
    return response.json();
  };
  const base = `/repos/${TRUSTED_REPOSITORY}`;
  const branchName = encodeURIComponent(context.ref.slice('refs/heads/'.length));
  const [branch, rules, rulesetList, environment, runnerResponse] = await Promise.all([
    get(`${base}/branches/${branchName}`), get(`${base}/rules/branches/${branchName}`),
    get(`${base}/rulesets?per_page=100`), get(`${base}/environments/${ENVIRONMENT}`),
    get(`${base}/actions/runners?per_page=100`),
  ]);
  // 分页不完整则不假定已经检查了全部候选 Runner。
  if (runnerResponse.total_count > runnerResponse.runners.length) throw new Error('Runner snapshot requires pagination; blocked');
  // 逐条读取 ruleset 详情：rules / conditions / updated_at / bypass 是否可见都取决于凭据权限。
  const branchRulesets = (Array.isArray(rulesetList) ? rulesetList : [])
    .filter(item => item.target === 'branch' && item.enforcement === 'active');
  const rulesets = await Promise.all(branchRulesets.map(item => get(`${base}/rulesets/${item.id}`)));
  const permissions = {};
  for (const actor of new Set([context.actor, context.triggering_actor])) {
    const value = await get(`${base}/collaborators/${encodeURIComponent(actor)}/permission`);
    permissions[actor] = value.role_name || value.permission;
  }
  return { context, branch, rules, rulesets, environment, runners: runnerResponse.runners, permissions,
    attestation, attestationSource: source, attestationError, trustRootAnchor };
}

/** @brief CLI 仅在线读取，不提供可绕过核验的本地快照输入开关。 */
async function main() {
  let report;
  try { const snapshot = await readSnapshot(); report = { ...evaluatePolicy(snapshot), context: snapshot.context }; }
  catch (error) { report = { status: 'blocked', safety_acceptance_complete: false, error: error.message }; }
  fs.writeFileSync('cad-ci-admission.json', JSON.stringify(report, null, 2));
  if (process.env.GITHUB_OUTPUT) fs.appendFileSync(process.env.GITHUB_OUTPUT, `authorized=${report.status === 'authorized'}\n`);
  console.log(JSON.stringify(report, null, 2));
  return report.status === 'authorized' ? 0 : 1;
}
module.exports = { evaluatePolicy, readSnapshot, readAttestation, TRUSTED_REPOSITORY, TRUSTED_REPOSITORY_ID,
  ENVIRONMENT, ATTESTATION_PATH, REQUIRED_CHECK, TEST_ENV_NAME };
if (require.main === module) main().then(code => { process.exitCode = code; });
