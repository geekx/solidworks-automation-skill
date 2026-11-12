# P0-2：双指平行夹爪原生装配 Golden

本用例只验证结构、原生装配与交付一致性。没有电机、丝杠、同步带、联动驱动、载荷能力或制造放行结论。
P0-1.2 的安装板验收范围、生产 Review 和 Execution Core 安全门禁均不扩大。

## 分支与执行边界

- 工作分支：`test/p0-real-assembly-gripper-2026`。
- 堆叠 Base：`818087a366efdd830d85b37d0705dd2d873464eb`，来自最新 P0-1.2 开发分支。
- Draft PR 目标为 `test/p0-real-golden-mounting-bracket-2026`；不合并 #39，不修改 main 或 GitHub 设置。
- COM 写操作串行；原始工程 DAG 保存在每轮 engineering_plan.json，排除 Motion、制造工程图和驱动传递。
- Core 仅执行 CAD 和安全清理后的终态适配，`core_scope=terminal_adaptation_only`。

## 统一规格（mm）

规格唯一来源为 golden-workflows.yaml 的 parallel_gripper_assembly，不接受运行时改变尺寸、容差、版本或审批要求。

| 零件文件 | 数量 | 原生几何与定位 |
| --- | --- | --- |
| base.SLDPRT | 1 | 60×12×140；局部截面中心 (0,-32)，底面 z=0；实例原点 z=-70 |
| rod.SLDPRT | 2 | Ø8×140 圆柱，轴线沿 Z；实例 X=-18/+18、Y=0、Z=-70 |
| support.SLDPRT | 2 | 60×34×12，截面中心 (0,-9)；两个 Ø8.02 贯穿孔，X=-18/+18、Y=0；实例 Z=-70/+58 |
| slider.SLDPRT | 2 | 52×20×18，截面中心 (0,0)；两个 Ø8.1 贯穿孔，X=-18/+18、Y=0；实例 Z=-43/+25 |
| finger.SLDPRT | 2 | 12×30×18，截面中心 (0,25)；分别随对应滑块，原点 Z=-43/+25 |

五种零件均为参数化凸台；支撑与滑块另有孔草图和贯穿切除。草图完全定义、可编辑深度参数、材料库条目
6061-T6 (SS)、密度及 B-Rep 回读均参与验收。这里的材料和间隙只是结构 Golden 名义参数，
不替代材料认证、导杆硬度/耐磨要求、轴承选型、公差链或紧固件设计。

两导杆中心距 36；初始夹持间距沿 Z 为 50。支撑底面与底座 y=-26 贴合，手指与滑块在 y=10 接合。
可制造性仍需补充安装/夹紧紧固件、行程限位、轴向保持、润滑、驱动和载荷设计。

## 原生配合与自由度

底座固定，其它组件不使用 Fixed 属性代替 Mate：

- 两个支撑各 3 个基准面 Mate；右端轴向距离为 128，其余对应基准重合。
- 两根导杆各 1 个同心 Mate（对应左支撑孔）、1 个轴向基准重合 Mate 和 1 个角向平行 Mate。
- 两个滑块各与两根导杆建立同心 Mate；两条不同轴的同心约束共同限制转动，只保留沿 Z 的轴向移动。
- 每个手指与对应滑块建立 3 个基准面重合 Mate。

共 22 个真实 Mate。初始变换只用于插入定位；验收回读 MateGroup、Mate2 类型、抑制/健康、MateEntity2
关联组件 ID 和实体参数，并回读组件变换、固定状态、约束状态和实例唯一 ID。

GetConstrainedStatus 只能证明整体约束状态，不能单独证明自由度数。目标自由度通过实际双导杆同心 Mate 的
两条关联轴线、手指的刚性平面 Mate、静态件完全约束状态，以及原生 SetTransformAndSolve2
六轴探针联合证明。两个滑块各执行 Z 正负移动 2 mm、X/Y 平移和 X/Y/Z 旋转请求，共 14 项；
只允许 Z 平移且手指跟随，另一组及静态件不得变化，其它五个方向必须被约束，逐次恢复。
被约束方向的请求可被原生求解器投影为已验证范围内的 Z 滑动，但横向偏移和旋转仍必须为零。
每次请求都用 SAFEARRAY(double) 创建 MathTransform 并回读全部 16 项，不把无效 ArrayData 写入当作请求成功。
测试运动不会退回直接 Transform2 赋值；无法读取或证明时验收失败，不以视觉代替证明。

GetRemainingDOFs 网页说明不完整，12 个 out 参数已在本机原厂 sldworks.tlb / Interop 中核对，
原始签名另存离线证据。首轮实测证明其返回值不是自由度数，状态也不能直接套用 swDofStatus_e；
因此只保留原始 SDK 诊断和一致性检查，不把它写成直接自由度计数。精确运动约束结论以上述原生 Mate
和六轴探针为依据，仍需人工工程复核，也不宣称其它版本支持该私有接口语义。

## 装配、干涉与交付验收

每轮创建新目录和新实例，先拒绝已有或状态未知的 SolidWorks 进程。逐件保存、关闭重开，之后创建装配，
保存并关闭装配及本轮加载的引用，再从磁盘重开 SLDASM。检查所有引用位于本轮 parts 目录且哈希一致。

干涉复用 sw_measure.inspect_interference：通过 InterferenceDetectionManager.GetInterferences 实际计算，
读取数量、体积与组件；Done 仅在读取后释放。关闭重合面干涉，但不忽略隐藏体；不以包围盒代替原生干涉。
每轮还在自有装配中制造真实滑块/手指穿透和抑制滑块 Mate，必须检出，恢复后再保存正常交付。

STEP 在真实 SolidWorks 中重新导入，逐体匹配九个实体的体积、世界质心、圆柱面孔径/轴线和面积，
处理导入后拆分的圆柱接缝。BOM CSV 复用 sw_delivery，并与实际组件引用、数量和磁盘内容交叉检查。
零件原生自定义属性同时保存编号、材料及从规格派生的尺寸/孔位/功能说明，BOM 必须回读一致；不向 CSV
填充无关文字，也不关闭通用 Review 的文件大小规则。
原始 Review 使用 sw_review，不修改其人工复核标记。装配体不支持的零件孔测量字段不能宣称已由通用
Review 完成，而由本用例的独立零件与装配 B-Rep 规则承担。

完整产物哈希、Artifact Ledger、原始 Review、快照和唯一 Core trace 在套件结束时再次回读。
只有全部通过且唯一阻塞为人工复核时才允许 acceptance_status=passed / task_status=blocked /
manual_review_required=true。默认 completed 模式仍严格返回非零。

## 运行命令

在仓库根目录执行，输出目录必须不存在：

```powershell
$env:PYTHONUTF8 = '1'
python -m pytest -q -rs
python tests/evals/runner.py --output-dir output/p0-2-eval-unique
python tests/solidworks_golden_workflow.py --case parallel_gripper_assembly --output output/p0-2-real-unique --runs 5 --expected-terminal blocked
```

--runtime-config 沿用模板/材料库/有界超时，另允许本用例专用 assembly_template（.asmdot）；相对路径相对
配置文件解析。缺省模板复用原有发现。安装板不接受 assembly_template，不能以配置扩大其能力范围。

离线故障注入是合成数据；不得计入真机结果。开发失败、未知证据及异常清理均保留。远程 CAD CI 的
P0-1.2 安全配置阻塞保持原状，本阶段没有变更 Runner、Environment、权限或工作流触发机制。

## API 核查依据

- [AddMate5 参数](https://help.solidworks.com/2025/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IAssemblyDoc~AddMate5.html)
- [组件约束状态](https://help.solidworks.com/2022/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IComponent2~GetConstrainedStatus.html)
- [自由度状态枚举](https://help.solidworks.com/2026/English/api/swconst/SolidWorks.Interop.swconst~SolidWorks.Interop.swconst.swDofStatus_e.html)
- [原生干涉计算](https://help.solidworks.com/2026/English/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IInterferenceDetectionMgr~GetInterferences.html)
- [Body2 质量属性及世界坐标转换](https://help.solidworks.com/2024/english/api/sldworksapi/solidworks.interop.sldworks~solidworks.interop.sldworks.ibody2~getmassproperties.html)

以下记录实际执行结果；验收契约与历史诊断、正式五轮分别统计。

### 开发阶段发现并保留的问题

1. 首次离线定向测试 26 failed / 33 passed：合成夹具预填 trace 文本，与 Core JSONL 混合。
   修正夹具为仅由真实 Core 适配生成 trace，定向和完整回归随后通过；未启动 CAD。
2. 首轮真实诊断（pilot-01，PID 46796）五种原生零件均通过，但装配 Z 初始变换未实际生效，
   滑块重叠，原生干涉检测返回 4 项（最大 16864.920954 mm³）。任务及验收均 failed，退出码 1，
   SLDASM、STEP、BOM、预览及完整证据均保留，清理通过。
3. 同轮暴露 GetRemainingDOFs 返回值/状态不可按原假设解释，以及空 BOM 属性导致 CSV 只有 765 字节、
   通用 Review 出现 WARN。修复方向分别为原生六轴探针与明确 SDK 诊断范围，以及保存真实编号、材料和
   尺寸/孔位/功能属性；没有修改生产 Review，没有把 WARN 改写为 PASS。
4. 装配诊断 probe-02 的 MathUtility 带参方法被 pywin32 绑定为属性，触发成员不存在；改为显式
   _FlagAsMethod 后，无文档的真实 API 最小探针回读了正确 16 项变换，清理通过。初版辅助诊断脚本未把
   失败映射到非零退出码，随后修复；该脚本不属于 Golden 入口，诊断失败从未计入验收通过。
5. probe-03（PID 40144）原生 Review PASS/100、零干涉，但右支撑位于错误的负向距离，且单一同心
   Mate 即使回读 LockRotation=true，原生旋转探针仍能使滑块转动，验收按预期失败。增加第二导杆同心
   Mate、固定导杆的显式角向 Mate，并修正距离方向；不把相应检查删除。原始失败 SLDASM/STEP 和预览保留。

后续装配 API 诊断可复用上述真实零件文件，但必须标记 native_assembly_diagnostic_only，
不属于从零建模的独立 Golden 轮次，也不计入连续五轮成功率。

## 2026-10-26 本机实测结果

正式五轮均使用冻结源码 `b2d0bc4bdc4067f9656b8b41321bbd2efd783d31`，真实 SolidWorks 2026 SP01.1
（revision 34.1.1）、Windows 11、Python 3.12.10。之后的收尾提交只增加实际证据索引、文档和修正
GetRemainingDOFs 注释，不改变执行或验收逻辑。交付 Commit 见堆叠 Draft PR 的 HEAD。

先完成离线全量回归，再运行正式真机套件；每轮从零创建五种零件、独立进程、输出目录和 trace：

| 轮次 | PID | 零件文件 / 组件实例 / Mate | SLDASM 重开 / STEP 回读 / BOM | 原生干涉 | 验收 / 任务 / 清理 |
| --- | --- | --- | --- | --- | --- |
| run-01 | 51052 | 5 / 9 / 22 | PASS / PASS / 9 | 0 | passed / blocked / pass |
| run-02 | 6576 | 5 / 9 / 22 | PASS / PASS / 9 | 0 | passed / blocked / pass |
| run-03 | 48788 | 5 / 9 / 22 | PASS / PASS / 9 | 0 | passed / blocked / pass |
| run-04 | 35240 | 5 / 9 / 22 | PASS / PASS / 9 | 0 | passed / blocked / pass |
| run-05 | 5276 | 5 / 9 / 22 | PASS / PASS / 9 | 0 | passed / blocked / pass |

- 几何及装配成功率 5/5，预期人工门禁正确率 5/5，Test Acceptance=5、Task Completion=0、False Completion=0。
- 五轮原始 Review 均 PASS/100、无 issues，manual_review_required=true；Core 实际终态均 blocked，
  verification=PASS、recovery=user_action_required。唯一阻塞是 EXPECTED_MANUAL_REVIEW_REQUIRED，命令退出码 0。
- 以同一批原始证据重算默认 completed 模式会拒绝验收，未额外执行或伪造 CAD；结果单独记录在离线控制文件。
- 每轮 22 个健康且未抑制的原生 Mate：13 个重合、6 个同心、2 个平行、1 个距离；完整名称、类型、关联实体参数
  及组件唯一 ID 见 built.json / reopened.json。固定件和运动件约束状态、两个独立 Z 移动方向均通过上述 14 项探针。
- 正常装配原生干涉数量和总干涉体积均为 0。每轮实际穿透控制均检出 2 项，体积分别为
  16864.920954 和 6480.0 mm³；抑制滑块 Mate 也被真实回读检出，恢复后正常快照重新通过。
- 每轮原生及 STEP 回读均为 9 个实体，总体积约 208099.355043 mm³；逐体质心、圆柱面孔径、轴线与面积通过。
  五件原生零件的特征、全约束草图、尺寸、孔位和材料均通过保存重开验证。
- BOM 五行，数量为 1/2/2/2/2，共 9；CSV 1606 字节，编号、材料、尺寸/孔位/功能属性与组件一致。
- 每轮四视角真实预览均已目视复核，结构一致。正投影可有正常遮挡，需结合等轴测及其它视角；没有制造工程图放行结论。
- 清理成功率 5/5，每轮文档数归零、自有进程退出；套件后再次检查 SolidWorks 进程为空，本次 Python 入口也无残留。

离线全量结果为 **999 passed / 1 skipped，118.09 s**。跳过项为既有桌面内嵌 Skill 未生成的发布包检查，
未作为通过统计。11 个工作流共 **40 个 deterministic mock 场景**，预期结果准确率 100%、逃逸假完成率 0；
**26 项 CI 策略合成快照测试**通过。这些离线结果均不属于真实 CAD 五轮。

## Evidence 与复核入口

机器可读索引：[p0-2-real-gripper-2026-10-26.json](evidence/p0-2-real-gripper-2026-10-26.json)。
索引含各轮 PID/创建时间、trace ID、材料/模板路径及哈希、Mate 合同、体积、Review、清理及失败修复记录。
每轮独立目录有原生零件、SLDASM、STEP、BOM、四视角、原始 Review、日志、快照、Core trace、Artifact Ledger
和 SHA-256 清单。逐轮哈希条目共 145 项；原始失败也保留，未用后续通过结果替换。

本机证据路径：

```text
<worktree-p0-real-assembly-2026>/output/p0-2-real-five-01/summary.json
<worktree-p0-real-assembly-2026>/output/p0-2-real-five-01/run-01..05/report.json
<worktree-p0-real-assembly-2026>/output/p0-2-offline/pytest-full-final.log
<worktree-p0-real-assembly-2026>/output/p0-2-offline/eval-final/eval_report.json
<worktree-p0-real-assembly-2026>/output/p0-2-evidence-20261026.zip
<worktree-p0-real-assembly-2026>/output/p0-2-evidence-sha256.json
<worktree-p0-real-assembly-2026>/output/p0-2-independent-seal-verification.json
```

> 该批证据产生于 P0-2 阶段的独立 worktree（`p0-real-assembly-2026`），路径随 worktree 所在磁盘位置变化，
> 故以占位符表示。证据包未入库，只在维护者本机保留。

ZIP 为 **11743432 字节 / 258 项**，包含正式五轮、开发 pilot、三个装配诊断、离线日志、SDK 签名、运行配置、
诊断/归档复核脚本和证据索引。SHA-256：

```text
0cdd1d7287ca6fd0438ba555a547d155ff5f22bd62bb0ee1161bd66d8e2ca1ac
```

归档后独立验证 ZIP CRC、压缩包 SHA-256、257 项清单中的磁盘字节与 ZIP 字节一致性，并再次调用逐轮
Evidence/Core/Ledger/交付语义检查；全部通过。首次离线失败的完整控制台未预先落盘，索引明确该日志限制，
其后所有完整回归及真实失败日志均保留。输出目录受 .gitignore 保护，完整大文件归档留在本机，未上传远程附件。

## 本阶段完整变更清单

```text
golden-workflows.yaml                                     新增统一夹爪规格
scripts/sw_assembly.py                                    严格原生变换、Mate 和 SDK 诊断回读
tests/golden_workflow_support.py                          注册 SLDASM 用例及专用模板配置
tests/golden_workflow_harness.py                          共用双状态、证据/终态/隔离与失败停止壳
tests/solidworks_golden_parallel_gripper_2026.py            真实建模、装配、故障控制和交付回读
tests/test_solidworks_golden_parallel_gripper_2026.py       36 项离线故障及安全回归
tests/test_eval_runner.py                                 按 Golden 定义推导工作流和场景数
references/assembly.md                                    修正原生干涉 API 用法
docs/architecture/real-golden-parallel-gripper-acceptance.md 验收规格、运行和本机实测报告
docs/architecture/evidence/p0-2-real-gripper-2026-10-26.json  实际证据索引
```

## 未验证范围及 P0-2.1

业务任务仍为 BLOCKED，未启用自动审批。Core 仅终态适配，不声称逐步调度了真实建模。
GetRemainingDOFs 的私有直接计数语义仍未验证；两独立移动方向的结论仅限本规格的原生 Mate 和有限六轴探针，
不等于全行程、限位、接触或其它版本认证。COM 单次调用永久挂起恢复也未验证。

初始诊断发现旧 apply_component_transform_x 在本机有 ArrayData 空写入风险；本阶段新增严格 helper 并让
夹爪用例逐项回读，未全局重写旧函数，其它旧调用链未做本阶段真机复测，不扩大原有验证范围。

建议 P0-2.1 优先补齐导杆夹紧/轴向保持、支撑/底座和手指紧固接口、轴承及公差工程图，再定义限位和全行程
原生碰撞回归。电机、丝杠、同步带、同步驱动、载荷/刚度/磨损及 Motion Study 需另立规格与验证，当前全部未完成。

远程 CAD CI **BLOCKED / 未执行**，继承 P0-1.2 的 Runner 隔离、GitHub 审计凭据与审批安全配置前置条件。
本阶段没有修改生产 Review、Core、capabilities、Windows CAD 工作流、Runner、Environment 或仓库权限。
保留堆叠 Draft PR；不建议在人工工程复核及前置安全条件关闭前直接合并 main。
