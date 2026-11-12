/**
 * @file 证明签名信任根（公钥固定 + 只追加轮换日志）。
 *
 * 信任边界（必须如实披露，不得表述为独立验证）：
 * 本项目为单人维护，公钥与轮换日志都存放在仓库内。任何能合并到 main 的人都能同时替换
 * 公钥与证明，因此这是**防篡改留痕**，不是**独立可信根**。它的作用是让密钥轮换与信任根
 * 变更必须体现为一次可见的代码变更（diff + 历史），而不是静默生效。
 *
 * 轮换流程：
 *   1. 本机生成新密钥对，私钥留在 `%USERPROFILE%\\.cad-governance\\`，永不入库；
 *   2. 在 KEYS 中**新增**条目（不得删除旧条目），并 append 到 ROTATION_LOG；
 *   3. 需要废止旧 key 时，必须设置 `revoked_at`。缺失字段不被视为已撤销：退役但未撤销会让
 *      `trust_root_rotation_log` 检查失败并全员阻断；
 *   4. 同步更新仓库变量 `GOVERNANCE_TRUST_ROOT_ANCHOR`（用 governance_attestation.cjs 的
 *      `trustRootAnchor()` 计算），否则 `trust_root_anchor` 检查失败；
 *   5. 重新采集证明（旧证明在新配置下会因摘要与署名不符而失效）。
 * 删除 KEYS 中仍被 ROTATION_LOG 引用的条目会让 `trust_root_intact` 失败，从而全员阻断。
 *
 * 锚点边界：仓库变量在仓库文件之外，故"同时改公钥 + 改日志 + 重签证明"这一整组操作会与锚点
 * 不符而被拒。但拥有仓库管理员权限的人可以连变量一起改，所以锚点只是提高成本、留下痕迹，
 * 仍**不是**独立可信根。
 */

/** @brief 已固定公钥。每个条目一经加入即保留，只允许追加或标记 revoked_at。 */
const KEYS = Object.freeze({
  'ed25519:c6c66c9d63df52056b8b2c326e43b9224846ebf66f9e7e6b58d43fe915f353bf': Object.freeze({
    pem: '-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAnhmTePXrFKeJsNnl16tCGALG7bwnpQw2eISnkPBdJTc=\n-----END PUBLIC KEY-----\n',
    not_before: '2026-10-27T00:00:00Z',
    revoked_at: null,
    note: 'P0-2.1 初始治理证明签名密钥（ed25519），私钥仅存于维护者本机',
  }),
});

/** @brief 只追加的轮换日志；用于检测信任根被静默删除。 */
const ROTATION_LOG = Object.freeze([
  Object.freeze({ key_id: 'ed25519:c6c66c9d63df52056b8b2c326e43b9224846ebf66f9e7e6b58d43fe915f353bf', added_at: '2026-10-27T00:00:00Z', reason: '初始固定' }),
]);

module.exports = { TRUSTED_KEYS: { keys: KEYS, log: ROTATION_LOG.map(entry => entry.key_id) }, ROTATION_LOG };
