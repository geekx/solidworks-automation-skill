# P0-2.1：夹爪机械保持、公差及静态全行程验收

本阶段在 P0-2 上新增独立 Golden `parallel_gripper_retention_travel`。原 P0-1、P0-2 用例及其历史证据不改写。
工作分支 `test/p0-real-gripper-retention-travel-2026`，堆叠 Base 为 `e5c242175c0a1d7e0fa4fabc90839f6d9fbc74ac`，
Draft PR 目标为 `test/p0-real-assembly-gripper-2026`。不合并 #39/#42，不修改 main、生产 Review、Verification、
Execution Core 或 GitHub 安全设置。Core 仍只验证终态适配。

## 原结构审计及最小机械改动

P0-2 有双导杆同心配合和两个独立 Z 平移，但 Ø8 导杆、Ø8.1 滑块孔、Ø8.02 支撑孔仅为名义几何，
没有公差带；支撑/底座和手指/滑块仅有 Mate，未建紧固接口；无限行程会越出导杆。

P0-2.1 保留双导杆、两滑块、独立手指、两端支撑的拓扑，新增以下功能实体和接口：

| 部件 | 原生数量 | 改动及功能（mm） |
| --- | --- | --- |
| 底座 | 1 | 60×12×116，Y=-38..-26、Z=-58..58；集成 52×36×8 中央挡块，Y=-26..10、Z=-4..4 |
| 支撑 | 2 | 60×46×12，Y=-38..8；Z=-70..-58 与 58..70，内端面同时为外侧行程止挡 |
| 导杆 | 2 | 建模直径 7.9905，长度 139.8，Z=-69.9..69.9；两根轴 X=±18、Y=0 |
| 滑块 | 2 | 52×20×18；保留双导向孔，补两个沿 Y 的 Ø3.2 连接孔 |
| 手指 | 2 | 12×30×18，Y=10..40；补与对应滑块同轴的两个连接孔 |
| 端部挡板 | 2 | 60×46×4；Z=-74..-70 与 70..74，封住导杆轴向出口 |
| 拉铆杆 | 2 | Ø4×148 杆段，Z=-74..74；两端 Ø8×3 成形头，贯穿端板/支撑/底座的真实接口 |
| 手指铆钉 | 4 | Ø3×50 杆段，Y=-10..40；两端 Ø5.5×3 成形头；同侧头部净距 0.5，每个手指用两根不同轴铆钉保持 |

共八种原生 SLDPRT、十七个实例。铆钉是装配后成形状态的真实双头实体，配有贯穿孔和头部贴合面，
不是螺栓、装饰性几何或已认证标准件。装配顺序需先穿入杆/铆钉再成形末端；成形工艺、可达性、夹紧预载、
头部尺寸和强度必须由工程师审核。本阶段不证明铆接工艺或承载能力，不宣称可直接制造。

导杆与挡板名义轴向间隙各 0.1，总轴向浮动 0.2；止挡面使整个滑块在端点仍位于导杆有效长度内。
轴向叠加公差尚未放行，因此 0.2 是设计间隙，不是制造后保证值。

## 孔与定位明细

下表全部为原生切除，不以标记点代替。位置来自统一 Golden 规格，不允许运行配置更改。

| 零件 / 接口 | 数量 | 轴向、规格及定位基准 |
| --- | --- | --- |
| 底座集成挡块 / 导向 | 2 | 沿 Z，建模 Ø8.0075；X=±18、Y=0，贯穿挡块 8；底座局部 Z=54..62 |
| 支撑 / 导杆 | 每件 2 | 沿 Z，建模 Ø8.0075；X=±18、Y=0，贯穿 12 |
| 滑块 / 导杆 | 每件 2 | 沿 Z，建模 Ø8.0075；X=±18、Y=0，贯穿 18 |
| 底座、支撑、端板 / 拉铆杆 | 每件 2 | 沿 Z，Ø4.1；X=±22、Y=-32，分别贯穿 116 / 12 / 4 |
| 底座 / 外部安装 | 4 | 沿 Y，Ø4.5；X=±12、世界 Z=±40（底座局部 Z=18/98），贯穿底座 12 |
| 滑块及手指 / 铆钉 | 每件 2 | 沿 Y，Ø3.2；X=±3、零件局部 Z=9，贯穿滑块 20 / 手指 30 |

除导向孔/轴配合外，上述安装孔及铆钉尺寸公差为**工程提案、待确认**。外部安装配对设备及其紧固件未建模，
不能宣称已完成整机安装。铆杆穿过底座与支撑的功能连接、双头保持与手指铆钉接触均需原生几何回读。

## 公差依据及适用边界

建议基本尺寸 Ø8 H7/g6。原厂资料：

- [MISUMI 线性轴 g6 推荐表](https://sg.misumi-ec.com/pdf/fa/2013/p226.pdf)：Ø8/10 的 g6 为 -0.005/-0.014 mm。
- [MISUMI H7 轴配件原厂表](https://jp.misumi-ec.com/vona2/detail/110300223240/?HissuCode=ADKR8-10)：Ø8 H7 为 0/+0.015 mm。
- [MISUMI 配合技术资料](https://pt.misumi-ec.com/pdf/fa/2014/P1_2287-2288_F80_EN.pdf)：孔轴公差带与配合分类。

由此得到轴 7.986..7.995、孔 8.000..8.015，直径间隙最小 0.005、最大 0.029 mm。CAD 采用公差带中值
7.9905 / 8.0075，名义建模直径间隙 0.017 mm，便于把间隙与有意端面接触分开；不把中值写成基本标称尺寸。
实际配合尺寸仍需图纸与检测。没有在本阶段声称满足完整 ISO/GB 制造标准。

双导杆孔位置度、平行度、同轴度、粗糙度、润滑/磨损/热膨胀及轴向叠加公差仍待确认；单独的 H7/g6
不能证明双导杆不会卡滞。导杆及铆件的库材料提案为 AISI 1020，其余为
6061-T6 (SS)；这些是 CAD 属性，不是热处理、硬度、材料证书或铆接性能认证。

## 行程与真实计算契约

- 左滑块原点 Z 最小/中间/最大为 -58/-40/-22；右滑块为 4/22/40；每侧独立行程 36。
- 最外侧端点贴合支撑内端面；最内侧端点贴合底座集成中央挡块。没有把必要接触删除或人为留缝以获得零干涉。
- 48 个原生 Mate（含两条真实距离上下限）；左侧相对左端板为 16..52，右侧相对底座为 62..98。
  读取限位 FeatureData 的 IsAdvancedMate、Distance、MinimumDistance、MaximumDistance。
- 对两组三位置执行 3×3 共九工况；每次发送真实编组并回读，再通过原生 Mate 当前距离及 ModifyDefinition
  持久化该静态工况，重建后检查全部组件、Mate、引用、当前距离和上下限。不是直接写矩阵模拟配合。
- 每工况用 InterferenceDetectionManager.GetInterferences 实际计算一次体积干涉，再以 TreatCoincidenceAsInterference=True
  单独计算有意接触，读取后才 Done。缺失、未知、接口异常或未计算均失败；不使用包围盒作为碰撞结论。
- 静态连接头部接触和端点止挡接触均必须真实检出；体积穿透单独判定，不把所有非零“接触条目”误作穿透。
- 保留四次不提交 Mate 当前值的变换请求，必须回读到有效位姿，不能以请求成功冒充行程到达。
- 另增加四次真实越限扩界/穿透注入：每侧分别越出上下界 2 mm，必须同时检出相应支撑/中央挡块的原生体积干涉和正常规则拒绝，逐项恢复。
  独立执行限位 Mate 抑制及滑块/中央挡块穿透控制，必须检出并恢复。正常文件只在恢复后保存。
- 保存关闭原生装配和引用后重开，回读十七个实例的身份/位置、Mate/上下限/引用、BOM；STEP 在 SW 中重新导入逐体验证。

九静态组合覆盖用户规定的端点和中位组合；不等于连续扫掠碰撞求解或 Motion Study，不宣称同步运动。
无电机、丝杠、同步带、动力传递或载荷结论。

真机已发现，未提交原生限位配合的当前距离时，变换请求虽可返回成功，重建仍回到起始距离；直接拖动、
从动尺寸、SetTransformAndSolve3 或仅换用 CreateMate 均不足以证明持久化运动。故本用例验证的是以真实
Mate 参数控制的静态工况，不宣称新装配已验证自由拖动或原生接触动力学，亦不扩大 P0-2 的自由度认证范围。
修改 Distance 还可能使 SolidWorks 自动扩界，因此正常入口仅允许不可配置的合法工况，回读上下限必须保持一致；
扩界及实际越限穿透作为必须被拒绝的故障注入。不能声称 CAD 求解器本身会自动模拟机械止挡。

## 运行与双状态

在当前仓库根目录执行，输出目录必须不存在：

```powershell
$env:PYTHONUTF8 = '1'
python -m pytest -q -rs
python tests/evals/runner.py --output-dir output/p0-2-1-eval-unique
node --test tests/test_cad_ci_guard.cjs
python tests/solidworks_golden_workflow.py --case parallel_gripper_retention_travel --output output/p0-2-1-real-unique --runs 5 --expected-terminal blocked --runtime-config config/runtime.example.json
```

受控 runtime 配置只允许现有模板、材料库路径和有界超时。`config/runtime.example.json` 是入库的可移植模板
（路径为 `null` 时自动探测）；正式验收当时使用的是本机 `output/p0-2-1-runtime.json`（含本机绝对路径，未入库）。
执行前环境诊断、已有进程拒绝、每轮独立目录/UUID trace、
Session 所有权、PID 与创建时间核验、清理失败即停止后续实例、全文件 SHA-256、Artifact Ledger 和 Core trace 均沿用 P0-2。
仅所有规则通过且唯一阻塞是原始 Review 的人工复核时 acceptance_status=passed、task_status=blocked、
manual_review_required=true。默认 completed 模式仍严格失败。远程 CAD CI 继承前置安全配置 BLOCKED，本阶段不更改或宣称验证。

## API 核查

- [FeatureExtrusion3 的起点偏移](https://help.solidworks.com/2024/english/api/sldworksapi/solidworks.interop.sldworks~solidworks.interop.sldworks.ifeaturemanager~featureextrusion3.html)：
  T0/StartOffset/FlipStartOffset；本机 SW2026 原厂枚举 swStartOffset=3。
- [FeatureCut4 的双向切除](https://help.solidworks.com/2024/english/api/sldworksapi/solidworks.interop.sldworks~solidworks.interop.sldworks.ifeaturemanager~featurecut4.html)：
  Sd=False、两方向各用原生 ThroughAll，避免定深孔冒充贯穿。
- [距离 Mate FeatureData](https://help.solidworks.com/2025/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IDistanceMateFeatureData_members.html)：
  IsAdvancedMate、MinimumDistance、MaximumDistance 原始回读。
- [原生距离配合创建与 ModifyDefinition 示例](https://help.solidworks.com/2022/english/api/sldworksapi/Create_Limit_Distance_Mate_Example_CSharp.htm)：
  当前距离持久化时显式回读并验证 Advanced、Distance 和上下限，不能只相信 ModifyDefinition 返回值。
- [原生有意接触开关](https://help.solidworks.com/2025/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IInterferenceDetectionMgr~TreatCoincidenceAsInterference.html)：
  开关仅当前管理器有效，每次明确设置，不假设保存后继承。

## 实际执行记录

本节在真实执行后记录结果；上述为验收合同，不是已通过声明。所有失败保留，离线合成与真机统计分开。

1. 首次真机套件 `p0-2-1-real-five-01/run-01`（源码 4aeccde、PID 41276）在导杆材料处失败，退出 1，未启动后续轮次。
   底座和支撑保存重开通过；导杆几何通过但材料为空、密度 1000。全部日志/快照与已生成 CAD 保留，清理通过。
   该轮导杆验证失败发生在保存前，因此没有伪称存在失败导杆原生文件；后续增加失败原生副本保留。
2. 独立真实材料 API 诊断不计 Golden 轮次。1045、1100-O 及 Alloy Steel 名称的应用未成功，可能保留先前材料；
   6061-T6 和 AISI 1020 正确回读。只凭 XML 中有名称不能算材料应用成功。改用可回读 AISI 1020，并按库值检查
   7900 kg/m³；铝件为 2700 kg/m³。材料适用性仍为工程提案，不能把该替代宣称为已认证等效材料。
3. 实施复核同时将同侧铆钉头径由 6 改为 5.5，避免名义相切；头部尺寸及成形工具可达性仍需人工审核。
4. 第二个真机套件 `p0-2-1-real-five-02/run-01`（源码 bebfe83、PID 5436）八种零件通过，但九工况的滑块
   重建后均回中位，铆钉因反向平面距离落到滑块另一侧。原生穿透为 0、Review PASS，位姿/接触/STEP 规则
   仍按预期判 failed；完整 CAD/STEP/BOM/预览、日志和哈希保留，清理通过，没有继续第二轮。
5. 独立装配诊断复制 SLDASM，仅只读历史零件，并在结束后核对全部历史输入哈希；从动尺寸、CreateMate、
   SetTransformAndSolve3 及隔离 Mate 都保留原始结果。第一项拖动诊断的退出码 0 仅表示 API 执行完毕，
   未校验重建后运动；第六项仅校验合法位置、未校验扩界，亦退出 0。两者都不属于 Golden，不计验收通过。
   随后诊断加入严格位姿/范围退出码检查；扩界诊断明确失败。正式 Golden 入口始终保持严格规则与非零失败退出。
6. 修正铆钉反向法向的距离符号，并增加对应故障回归。合法九工况改为原生 Mate 当前值控制，原有变换请求
   检查保留，另加四次真实机械越限干涉故障控制；没有删除位置、接触、Mate、上下限或清理检查。
7. 装配诊断 `assembly-probe-08` 复用八个已真实验证零件的副本，完整通过新建装配、九工况、四个真实越限穿透、
   抑制/中央穿透、恢复、保存重开、STEP、Review 和清理；退出 0，历史输入哈希未改变。四视角已目视复核。
   每个越限 2 mm 的原生穿透数量为 1，外侧体积 1670.560902、内侧体积 1878.560902 mm³。
   该诊断没有从零重建零件，因此不计入正式五轮；正常九工况的穿透全部 0，原生零体积接触条目为 18–22，分类见下文。
8. 冻结源码 `359959610a996b42066fc98e4bf313ddb0485aae` 的正式套件 `p0-2-1-real-five-03` 前四轮完整通过；
   第五轮在创建左滑块限位 Mate 前发生 COM 断连，未完成装配及清理接口回读，套件按原始事实退出 1。
   用户于 2026-10-27 确认该次断连由手动退出 SolidWorks 引起。保留人工中断的失败/未知记录，不补记通过，
   不把这次事件称为已证明的 CAD 崩溃或建模缺陷。事后无残留进程也不替代当轮文档清理验证。
   随后增加组件插入后、限位创建前及全部 Mate 创建后的三个独立 SLDASM 检查点与逐 Mate 进度记录，
   同时补充断连不能通过验收的故障测试。检查点不是恢复、成功或清理证明；没有新增异常自动重试或放宽门禁。
9. 另发现 `p0-2-1-real-five-04/run-01` 已有部分原始证据，含八个零件及九工况报告，但没有终态报告、清理证明或套件汇总。
   该次未封口执行的具体中断原因未确认，不计通过。2026-10-27 一次复用该目录的启动被原入口拒绝，未启动 CAD；
   外层 Tee 命令覆盖了旧的外围控制台日志，这是实施操作缺陷，该日志原内容无法恢复。目录内原始 `run.log`、CAD 和快照未修改。
   新生成的 `five-04-exitcode.txt` 只表示本次重复目录拒绝，不能用来推断旧执行终态。
   后续启动在进入 Tee 之前同时检查目录、控制台日志和退出码文件不存在，并改用 `five-05`。

## 2026-10-27 冻结源码五轮结果

运行源码为 `df4d4c9a4143602d5af7077a7be7b19cee4850c2`，SolidWorks 2026 SP01.1 / revision 34.1.1，
Python 3.12.10 / Windows 11。五轮均从零生成八种零件，在新实例及独立目录内完成全部流程。
不是复制已完成 CAD、确定性 Mock、OCCT 或复用辅助装配诊断。实际命令如下，重跑必须更换尚不存在的输出目录：

```powershell
python -u tests/solidworks_golden_workflow.py --case parallel_gripper_retention_travel --output output/p0-2-1-real-five-05 --runs 5 --expected-terminal blocked --runtime-config output/p0-2-1-runtime.json
```

> 上面是**当时实际执行**的命令，其中 `output/p0-2-1-runtime.json` 是本机配置（含本机绝对路径，被 `.gitignore` 排除，未入库）。
> 外部复现请改用入库模板 `config/runtime.example.json`（自动探测），并更换一个尚不存在的 `--output` 目录。

| 轮次 | 独立 PID | 原生几何、装配及交付 | Test Acceptance | Task Status | 自有文档 / 主进程清理 |
| --- | --- | --- | --- | --- | --- |
| 01 | 35168 | PASS | passed | blocked | PASS / 0 残留 |
| 02 | 25560 | PASS | passed | blocked | PASS / 0 残留 |
| 03 | 35628 | PASS | passed | blocked | PASS / 0 残留 |
| 04 | 39064 | PASS | passed | blocked | PASS / 0 残留 |
| 05 | 16088 | PASS | passed | blocked | PASS / 0 残留 |

每轮均八种 SLDPRT、十七组件、四十八 Mate：十四重合、六同心、二平行、二十六距离，其中两条为真实限位。
组件 ID/名称/路径在保存关闭重开后保持一致；每个实体及 STEP 回读均匹配体积、质心、孔轴圆柱尺寸/位置/面积。
原生与 STEP 总实体体积约 **244414.832633 mm³**，BOM 八行、数量合计十七。五轮四视角共二十张均已目视复核。
每轮原始 Review 为 PASS / 100，`manual_review_required=true`；Core 实际终态及 trace 均为 blocked。

正常组合在每一轮均实际计算并通过，表内为体积穿透数量（每格独立复测五次）：

| 左滑块 Z / 右滑块 Z（mm） | 4 | 22 | 40 |
| --- | --- | --- | --- |
| -58 | 0，5/5 | 0，5/5 | 0，5/5 |
| -40 | 0，5/5 | 0，5/5 | 0，5/5 |
| -22 | 0，5/5 | 0，5/5 | 0，5/5 |

四十五个正常工况原生体积穿透均为 0，体积均为 0 mm³。零体积接触条目按上述行序为
`21, 19, 20, 20, 18, 19, 22, 20, 21`。十八项名义连接接触与端点止挡接触分别保留；
内侧极限还存在手指与中央挡块上缘的**共边接触**，不能把它称为制造间隙或已批准的承载接触，倒角和公差仍待审。

从实际变换、已保存零件 D1 及已核对的止挡 B-Rep 平面派生：各工况两根导杆对每个滑块的覆盖均为十八，
止挡间隙不小于零，指间开口为八至八十，导杆轴向端隙各约 0.1 mm。
原生圆柱面回读轴径 7.9905、底座/支撑/滑块孔径 8.0075，名义直径间隙 0.017 mm。
这些是名义几何派生值，碰撞结论仍来自单独执行的原生 Interference Detection；不是包围盒碰撞或制造公差合格证明。

| 每轮真实故障控制 | 原生穿透条目 | 穿透体积（mm³） | 五轮检出及恢复 |
| --- | --- | --- | --- |
| 左侧越外限 -60 | 1，滑块 / 左支撑 | 1670.560902 | 5/5 |
| 左侧越内限 -20 | 1，滑块 / 中央挡块 | 1878.560902 | 5/5 |
| 右侧越内限 2 | 1，滑块 / 中央挡块 | 1878.560902 | 5/5 |
| 右侧越外限 42 | 1，滑块 / 右支撑 | 1670.560902 | 5/5 |
| 左侧中央穿透 -3 | 3，原始组件/体积明细保存在报告 | 见原始报告 | 5/5 |
| 限位 Mate 抑制 | 正常验收器拒绝抑制状态 | 不以零干涉替代配合检查 | 5/5 |

套件退出码 **0**：真实几何/装配成功率 5/5，预期门禁正确率 5/5，Test Acceptance=5，Task Completion=0，
False Completion=0，清理成功率 5/5。同一批真实证据在严格 completed 模式下重判为 failed，不计新增 CAD 轮次。
保留历史累计九次完整通过（旧源码四次、新源码五次）、两次开发失败、一次人工中断和一次未封口执行；
两次开发失败、人工中断和未封口执行均没有补记通过，辅助诊断不计 Golden 成功轮次。

## 离线回归、证据与实施后审查

- 全量离线 **1034 passed / 1 skipped（128.19 s）**，新增用例三十五项测试包含限位缺失、错误行程、Mate 失效、
  错误间隙、失去止挡接触、全行程穿透、扩界、错误 BOM/STEP、Review WARN/FAIL、证据篡改、清理失败和 COM 断连。
  跳过项仍为桌面内嵌 Skill 发布包未生成检查。日志为 `output/p0-2-1-offline/pytest-full-05.log`。
- 四十三个 deterministic mock 场景（十二名义、三十一鲁棒）符合预期，逃逸假完成率 0；
  二十六项 CI 准入策略合成测试通过，均单列，不是真机或远程 Runner 验收。
- 五轮全部产物共 **190 项逐轮哈希**、Artifact Ledger 和实际 Core trace 重新回读一致。
  实施后审查确认生产门禁、CI 及原有安装板/夹爪用例的八个保护文件 Git blob 与 Base 相同；
  原用例保留并通过全量离线，本阶段真机复测针对新增用例。
- 结束后只读检查主实例、监测子进程和测试入口无残留；五轮文档清零。
  运行前已存在的 `sldworks_fs.exe`（PID 27760，09:19:25 创建）按 PID+创建时间核对保留，未终止任何未知或用户进程。
- [机器可读证据索引](evidence/p0-2-1-real-gripper-travel-2026-10-27.json) 包含各轮配置/资产哈希、trace ID、
  Mate 合同、实际位置、接触、覆盖、间隙、故障、清理以及历史失败和日志覆盖限制。

本机归档包含五轮、历史失败/未封口数据、诊断及离线记录，549 项，26,746,788 字节；未上传远程附件：

```text
output/p0-2-1-evidence-20261027.zip        （相对仓库根目录）
SHA-256: 16a5e33383dd326d033ef316d4610c450a42676dbaed1758a963f2d612e8f3b4
```

> 该 ZIP 未入库（26.7 MB，`.gitignore` 排除 `output/`），只在维护者本机保留。
> 外部复核者只能验证公开的清单与哈希，**不能独立重放原始证据**。

ZIP CRC、整包 SHA-256、全部清单文件磁盘/归档字节对比均通过；Python 与 PowerShell SHA-256 一致。
独立复核结果为 `output/p0-2-1-independent-seal-verification.json`，逐文件清单为
`output/p0-2-1-evidence-sha256.json`。整包自身及其 sidecar 不参与循环自哈希。

本阶段相对 P0-2 Base 的七个项目文件：

```text
golden-workflows.yaml
scripts/sw_part.py
tests/golden_workflow_support.py
tests/solidworks_golden_gripper_travel_2026.py
tests/test_solidworks_golden_gripper_travel_2026.py
docs/architecture/real-golden-gripper-retention-travel-acceptance.md
docs/architecture/evidence/p0-2-1-real-gripper-travel-2026-10-27.json
```

`sw_part.py` 仅增加默认行为兼容的凸台起点偏移及双向切除选项，未重复开发 COM 框架。
辅助诊断、检查点、进程审计和封存脚本随本机证据保留，不改生产架构。

## 尚未放行与后续工程审核

本机静态 Golden Test Acceptance 为 passed；业务 Task Status 仍为 blocked，制造放行及合并就绪性保持 **BLOCKED**。
[堆叠 Draft PR #43](https://github.com/wzyn20051216/solidworks-automation-skill/pull/43) 目标为 P0-2 分支，保持 Draft，不合并 main 或 #39/#42。

工程师仍需审核 H7/g6 在该材料与工况中的适用性、双导杆位置度/平行度、轴向公差链、磨损/润滑、
内侧手指共边接触和倒角，以及 116 深 Ø4.1 孔的加工路线、铆接成形顺序/可达性、夹紧与强度。
CAD 导杆姿态 Mate 不等于实体抗转或接头刚度认证；外部安装配对设备、材料证书及工程图制造放行未完成。
没有验证新装配自由拖动、连续扫掠、接触动力学、驱动/同步、载荷或 Motion Study，亦未扩大原 P0-2 能力范围。
Core 仅终态适配，逐步 CAD 调度及 COM 永久挂起/异常终止恢复未验证。
跨机器解压后的引用重定位未验证；历史外围控制台覆盖是无法恢复的证据限制。
远程 CAD CI 本阶段未运行或配置，前置安全配置没有在本阶段解除或验收；不可声称远程安全验收完成。
