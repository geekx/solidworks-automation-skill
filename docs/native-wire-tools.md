# 原生圆截面路径与机械导入（pilot）

这些工具使用本机 SOLIDWORKS PIA，不执行调用方代码。每次操作都要求保存的目标文档完整路径和宿主 MCP 选定的 PID；通过宿主操作锁串行化。不运行新的 SOLIDWORKS 实例。原生子进程优先使用 PID 命名的 ROT 条目；对于只注册标准活动对象的 2026 实例，回退后仍必须核对相同 PID，拒绝连接其他实例。

- `solidworks_inspect_native_geometry`：读取实体名称、体积、包围盒和组件变换。
- `solidworks_build_round_sweeps`：在空的已保存零件中建立圆截面原生扫描。路径是 2 点直线或 3–6 点 Bezier；JSON 数值经验证后转换为方程，不接受任意表达式。可建立独立矩形拉伸。先 `dry_run=true`。要求毫米单位，检查路径实测长度、矩形包围盒和重建。不会自动保存。
- `solidworks_import_step_part`：导入到新的多实体 SLDPRT；恢复全局导入选项，拒绝覆盖，返回每个实体供审查。原始目标文档不保存。
- `solidworks_replace_component_preserving_transform`：只支持唯一的固定实例；保持变换和其他组件引用，不保存。
- `solidworks_exclude_reviewed_bodies`：按已审查的精确实体名称建立 DeleteBody 特征，保留原始导入历史，不保存。

## 规格

```json
{"sweeps":[{"name":"Wire1","control_points_mm":[[0,0,0],[0,3,0],[3,5,0],[5,5,0]],"diameter_mm":0.9,"color":[1,0,0]}],"boxes":[]}
```

失败可能留下未保存的部分特征，必须审查，不能盲目重试。不会覆盖或删除既有特征。模型仅提供几何，不证明线束工艺、压接适配、绝缘性能、拉脱力或原生 SOLIDWORKS Routing 许可证。

结果中的 `save_requested` 表示本次操作是否请求保存。检查、预演、扫描、替换和实体排除不会返回表示保存失败的 `saved=false`；STEP 导入成功保存后返回 `save_requested=true, saved=true`。真正的保存失败仍返回执行错误，人工复核要求保留。

MCP 与原生子进程使用 UTF-8 标准输入传递 JSON，请求上限为 1 MiB；64 条高精度路径和中文路径不会占用 Windows 命令行长度。短请求的 `-InputBase64` 调用保留兼容。

验证：输入拒绝测试不启动 CAD；真实 SOLIDWORKS 版本和操作证据应单独记录。当前能力为 pilot，发布前需几何与视觉复核。
## 实机验证

原作者记录（SOLIDWORKS 2026 SP4.1 / Python 3.10）：已验证 38 个独立实体的扫描和拉伸、STEP 多实体导入、指定实体排除、固定组件替换及保存后重开。路径长度、盒体范围、组件变换已回读；导出实体通过辅助 B-Rep 检查。输入测试 8 项通过；安装集成的目标文档/进程回归 42 项及 23 个子测试通过。未测试其他 SOLIDWORKS 版本。能力仍为 pilot。

维护者复核（2026-10-17，SOLIDWORKS 2026 SP01.1，Revision 34.1.1 / Python 3.12）：通过真实 MCP 注册工具、PowerShell 和本机 PIA 完成直线/Bezier 扫描、矩形拉伸、STEP 多实体导入、实体排除、固定组件替换及保存重开。直线长度 10 mm，Bezier 实测 17.216903638 mm，理论采样 17.216903179 mm；盒体体积 500 mm³。已确认未请求保存的操作没有改写磁盘文件，导入的三个全局选项均恢复。测试仅关闭自身创建的文档和进程。

安装测试依赖并确认没有用户 CAD 实例后，可手动运行实机回归。第二个参数必须是不存在的输出目录；脚本会启动自己的 2026 实例、创建独立测试模型并输出 JSON 报告和 BMP 预览。该脚本不属于自动化 `pytest` 收集，不会在普通 CI 中启动 CAD。

```powershell
python tests/manual_native_wire_2026.py "E:\path\solidworks-automation-skill" "E:\cad-tests\native-wire-new-run"
```

实体 RGB 随审查结果返回；创建后立即回读。AP203 导出不包含颜色，预览可以使用已核验的实体 RGB，但必须记录颜色来源，不能宣称 AP203 包含外观。

实机实体颜色回读存在 8 位量化（例如输入 0.70 回读 178/255），因此颜色验收允许一个 8 位步长；这不放宽几何尺寸检查。
