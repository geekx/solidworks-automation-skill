/** @brief 验证便携版等待器的瞬态连接重试与启动失败诊断，不运行桌面程序。 */
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { waitUntil, firstPage } = require("../ai_team/release-portable-e2e.cjs");

test("连接尚未就绪时可以重试，返回实际就绪值", async () => {
  let calls = 0;
  const value = await waitUntil(() => {
    if (++calls === 1) throw new Error("ECONNREFUSED");
    return "ready";
  }, "fixture", 1_000);
  assert.equal(value, "ready");
  assert.equal(calls, 2);
});

test("进程提前退出时立即失败，不等待端口超时", async () => {
  await assert.rejects(waitUntil(() => assert.fail("已退出时不应继续检查端口"), "fixture", 1_000,
    () => "process exited with code 7"), /process exited with code 7/);
});

test("超时保留最后连接错误，不能当作通过", async () => {
  await assert.rejects(waitUntil(() => { throw new Error("ECONNREFUSED"); }, "fixture", 10),
    /timeout: fixture; last error: ECONNREFUSED/);
});

/** @brief 复现真实缺陷：CDP 已连通但 page target 尚未注册时不得抛 TypeError。 */
test("尚无 page target 时返回 null 而不抛异常", () => {
  assert.equal(firstPage({ contexts: () => [] }), null);
  assert.equal(firstPage({ contexts: () => [{ pages: () => [] }] }), null);
});

test("跳过空 context，选中首个真实 page", () => {
  const target = { name: "workbench" };
  const browser = {
    contexts: () => [{ pages: () => [] }, { pages: () => [target] }, { pages: () => [{ name: "second" }] }],
  };
  assert.equal(firstPage(browser), target);
});

test("page 延后注册时 waitUntil 会等到它出现", async () => {
  const target = { name: "workbench" };
  let contexts = [];
  setTimeout(() => { contexts = [{ pages: () => [target] }]; }, 60);
  const page = await waitUntil(() => firstPage({ contexts: () => contexts }), "portable Tauri page", 2_000);
  assert.equal(page, target);
});
