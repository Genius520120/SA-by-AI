# 常见问题排查

## 连接问题

### 无法连接到 SolidWorks

```
错误: pywintypes.com_error: (-2147221005, '无效的类字符串', None, None)
```

**原因**: SolidWorks 未安装或 COM 未注册
**解决**: 确认 SolidWorks 已安装，尝试指定版本号连接：
```python
sw = win32com.client.Dispatch("SldWorks.Application.32")  # SW 2024
```

### GetActiveObject 失败

```
错误: pywintypes.com_error: (-2147221021, '操作不可用', None, None)
```

**原因**: SolidWorks 未运行
**解决**: 先手动启动 SolidWorks，或使用 `Dispatch` 启动新实例

### Python 32/64 位不匹配

**原因**: Python 位数必须与 SolidWorks 一致（通常为 64 位）
**解决**: 安装 64 位 Python

## 类型错误

### COM 伪可调用属性导致“找不到成员”

场景：遍历特征树或读取模型摘要时，`FirstFeature`、`GetNextFeature`、`GetTitle` 等成员在 pywin32 中显示 `callable=True`，但调用后报：

```text
(-2147352573, '找不到成员。', None, None)
```

原因：SolidWorks 某些 COM 成员在动态派发下既可能表现为属性，也可能表现为方法；`callable(member)` 不能作为唯一判断。

稳定写法：优先使用 `sw_connect.get_com_member()` 或 `sw_assembly.safe_get_com_member()`。

```python
from sw_connect import get_com_member

feature = get_com_member(model, "FirstFeature")
while feature:
    print(get_com_member(feature, "Name"), get_com_member(feature, "GetTypeName2"))
    feature = get_com_member(feature, "GetNextFeature")
```

### Motion Study 成员“找不到成员”

场景：`model.Extension.GetMotionStudyManager()` 或 `motion_mgr.CreateMotionStudy()` 报：

```text
(-2147352573, '找不到成员。', None, None)
```

常见原因：

1. Motion Study 的强类型接口位于 `swmotionstudy.tlb`，不是主 `sldworks` 类型库。
2. pywin32 动态派发下，`GetMotionStudyManager`、`CreateMotionStudy`、`Activate`、`Calculate`、`Play` 可能表现为属性，不是方法。

稳定写法：优先使用 `sw_motion.py`。

```python
from sw_motion import create_motion_study, motion_member

study = create_motion_study(asm, name="Motion_60RPM", duration=4.0)
calculated = motion_member(study, "Calculate")
```

如果必须底层调用，先加载类型库：

```python
from sw_motion import ensure_motion_type_library

ensure_motion_type_library(raise_on_error=True)
motion_mgr = asm.Extension.GetMotionStudyManager
study = motion_mgr.CreateMotionStudy
```

### SelectByID2 类型不匹配

```
错误: TypeError: Objects of type 'NoneType' can not be converted to a COM VARIANT
```

**解决**: 对 Callout 参数使用显式 VARIANT：
```python
from win32com.client import VARIANT
import pythoncom
callout = VARIANT(pythoncom.VT_DISPATCH, None)
model.Extension.SelectByID2("Front Plane", "PLANE", 0, 0, 0, False, 0, callout, 0)
```

### by-ref 参数错误

**解决**: 使用 VARIANT 包装输出参数：
```python
errors = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
warnings = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
```

### Pack and Go 获取对象时报缺少参数

场景：`model.Extension.GetPackAndGo()` 或强类型 `IModelDocExtension.GetPackAndGo()` 报缺少必要参数，但官方 API 和 Interop 反射都显示它是零参数返回 `IPackAndGo`。

原因：本机 SW2024/2026 + pywin32 运行时对象会把 `[out, retval] IPackAndGo**` 编组错位。实测官方零参数动态调用和显式 `VT_BYREF | VT_DISPATCH` 都可能拿不到返回指针。

稳定写法：优先使用 `sw_delivery.pack_and_go()`。封装会先尝试 pywin32 官方零参数调用；若 pywin32 抛出签名错误，再使用 `comtypes` 早绑定调用原生 `IModelDocExtension.GetPackAndGo()` / `SavePackAndGo()`。

```python
from sw_delivery import pack_and_go

report = pack_and_go(assembly, r"E:\\delivery\\pack_and_go", flatten=True)
assert report["success"], report
```

### Pack and Go 只输出顶层装配体

场景：`IModelDoc2.GetDependencies2()` 能返回装配体引用的 `.SLDPRT`，但 `IPackAndGo.GetDocumentNames()` 只有顶层 `.SLDASM`，保存后交付包也只有装配文件。

处理：不要把保存前枚举直接当作最终产物，也不要用 `AddExternalDocuments` 补装配体原生零件。`sw_delivery.pack_and_go()` 会先执行原生 `SavePackAndGo()`，再按本轮实际落盘文件审计依赖；SW2026 SP01.1 的基础两零件装配已连续三次输出完整的 1 个装配体和 2 个零件。若实际落盘仍缺依赖，默认策略生成明确标记的 `pilot` 暂存包，严格策略返回 `status=blocked` 和 `SW_PACK_AND_GO_DEPENDENCY_ENUMERATION_INCOMPLETE`；两者都必须保留缺失证据并人工复核。

### STL 导出了当前装配体而不是目标零件

场景：`IModelDocExtension.SaveAs()` 返回成功，但目标零件名的 STL 不存在，目录中反而出现
`零件名 - 装配组件名.STL` 等多个文件。

原因：SW2024 的 STL 导出可能读取 `ActiveDoc`，只持有目标零件 `IModelDoc2` 并不足以保证
导出对象正确。

处理：使用 `sw_export.batch_export_formats()`。它会在每个格式导出前调用 `ActivateDoc3()`，
并严格核对活动文档路径；路径不一致时停止，不接受 API 的表面成功结果。

## 操作失败

### run_review 只截到一个零件或子装配

场景：装配体里组件数量、可见状态和颜色回读都正常，但 `run_review()` 导出的图只看到一个轮毂/一个小零件，其他几何只剩阴影或完全不可见。

常见原因：

1. SolidWorks `SaveBMP()` 使用当前活动图形窗口，目标 `model` 不是活动文档。
2. 批量打开零件/子装配后，活动文档停留在最后一个组件上。
3. 规则审查只判断预览非空白，无法发现“截错文档”的视觉错误。

稳定写法：使用新版 `sw_review.save_preview()` / `run_review()`；它会在截图前按 `GetTitle()` 激活目标文档。若手写截图流程，必须先 `ActivateDoc3()`，再 `ClearSelection2(True)`、`ViewZoomtofit2()`、`SaveBMP()`。

```python
errors = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
sw.ActivateDoc3(model.GetTitle(), False, 0, errors)
model.ClearSelection2(True)
model.ViewZoomtofit2()
model.SaveBMP(r"E:\review\assembly.bmp", 1600, 1000)
```

最终仍要目视检查四视图；`evaluation.status=pass` 不能替代几何判断。

### STEP 分件装配后全部叠在原点

场景：把一个彩色 STEP/装配拆成多个 STEP，再逐个导入 SolidWorks 原生分件并新建总装；组件数量正确，但预览中只看到一个轮子或一团重叠阴影。

原因：部分 CAD 导出器在“单独导出某个实体”时会保留实体原始坐标；而 SolidWorks `AddComponent5(path, ..., x, y, z)` 可能按组件包围盒中心放置，传 `(0,0,0)` 会把所有分件居中叠放。

稳定写法：

1. 导出分件 manifest 时记录每个实体导出前的全局包围盒中心。
2. 添加组件时传入该中心坐标（米），而不是统一 `(0,0,0)`。
3. 添加后读取组件 `Transform2.ArrayData[9:12]` 和 `GetBox()`，确认装配包围盒接近原始整机尺寸。
4. 对子装配 `.SLDASM` 递归检查内部组件，不要只看顶层组件数。

```python
center_m = [value / 1000.0 for value in bbox_center_mm]
component = add_component(asm, native_path, x=center_m[0], y=center_m[1], z=center_m[2])
```

### GetModelDoc2 返回 None

场景：装配体中组件明明存在，但 `component.GetModelDoc2()` 返回 `None`，后续无法读取基准面、实体面或特征。

常见原因：

1. 组件处于压缩或轻化状态。
2. 组件引用文件没有加载到内存。
3. 刚添加组件后装配体尚未解析。

稳定写法：先解析组件，再读取模型；优先用 `sw_assembly.get_component_model()`。

```python
from sw_assembly import get_component_model, resolve_component

resolve_component(component)  # 默认 swComponentFullyResolved=2，失败时回退 swComponentResolved=3
part = get_component_model(component)
```

### AddComponent4 返回 None

场景：SW2024 中文版 + pywin32 下，`asm.AddComponent4(path, "", x, y, z)` 无异常但持续返回 `None`，导致装配体无法添加零件。

稳定写法：优先使用 `sw_assembly.add_component()`。当前封装先检查文件路径，然后调用 `AddComponent5(path, 0, "", False, config, x, y, z)`，失败后回退 `AddComponent4()`；若仍为空，会自动静默打开零件、重新激活装配体，再重试 `AddComponent5()`。

```python
from sw_assembly import add_component, get_components

component = add_component(asm, r"E:\parts\probe_block.SLDPRT", x=0, y=0, z=0)
if component is None:
    raise RuntimeError("添加组件失败")
print(get_components(asm))
```

真实 SolidWorks 回归验证：

```powershell
py -3.13 tests\solidworks_add_component_regression.py --output-dir C:\CADAutomationWorkbench\solidworks_add_component_regression
```

通过标准：`component_count >= 1`，`component_name` 非空，`.SLDASM` 保存成功，`review_evaluation.status` 为 `pass` 或至少 `review_checks.expected_outputs_exist=true`。

### Mate 创建后特征树没有真实配合

场景：脚本报告“成功”，但 SolidWorks 里没有 Gear Mate / Concentric Mate，拖动也不会联动。

排查：

1. `AddMate5()` 是否返回 `None`。
2. by-ref `ErrorStatus` 是否为成功码；`swAddMateError_NoError=1`，部分版本也会在成功时返回 0。
3. 创建前选择集是否正好有 2 个对象。
4. 选择对象是否为装配体上下文实体，而不是零件文档内实体。
5. Mate 是否写入 `MateGroup` 子特征。

稳定写法：

```python
from sw_assembly import add_mate5_checked, collect_mate_feature_summary

mate = add_mate5_checked(asm, 1, lock_rotation=False, name="shaft_concentric")
print(collect_mate_feature_summary(asm))
```

### AddMate5 返回 Mate 但 error_status=1

场景：`AddMate5()` 返回了非空 Mate，SolidWorks 特征树里也有配合，但脚本仍报失败：

```text
AddMate5 失败: type=1, error_status=1
```

原因：`swAddMateError_e` 中 `1=swAddMateError_NoError`，不是错误。不要只把 `0` 当成功。

验证命令：

```powershell
Add-Type -Path 'E:\Solidworks\SOLIDWORKS\SolidWorks.Interop.swconst.dll'
[int][SolidWorks.Interop.swconst.swAddMateError_e]::swAddMateError_NoError
```

稳定写法：使用新版 `sw_assembly.add_mate5_checked()`，它接受 `0` 和 `1` 作为成功码，并仍会检查 Mate 是否为空。

### AddMate5 返回 error_status=5（过定义，"同一对零件只允许一个配合"）

场景：给同一对零件先建一个配合成功，再建第二个配合（比如同心 + 距离/重合）时，
`AddMate5` 被拒：

```text
AddMate5 失败: type=1, error_status=5
```

定性（2026-10-28 查证官方 API 文档）：**5 = `swAddMateError_OverDefinedAssembly`（过定义）**，
不是"冗余"也不是未知错误。

本机实测的等价关系——很多"组合配合"其实第一个就已经把自由度锁完了：

| 看似两个配合 | 实际效果 |
|---|---|
| 圆柱-圆柱 `distance=0` | **等价于同心**（再叠加同心 = 过定义） |
| 面-面 `distance=0` | **等价于重合**（锁 3 个自由度，再叠加 = 过定义） |

结论：**同一对零件只允许一个配合**。要完全定位一个回转件（轴/销/滚子），
本机可行组合是"同心（锁径向）+ `FixComponent()`（轴向交完全定义）"，
而不是同心 + 距离。面-面重合用 `AddMate5(type=COINCIDENT)` 在本机直接被拒，
要用"贴合"时改传 `SW_MATE_DISTANCE, distance=0.0`。

排查顺序：先数该对零件已有配合数 → 换算自由度 → 再决定加不加第二个配合。

### 选择集被清空

场景：第一个面选择成功，执行解析组件或切换文档后，第二个面选择成功但 `AddMate5()` 失败。

原因：`SetSuppression2()`、激活文档、部分重建操作会清空或改变选择集。

稳定顺序：

1. 先解析所有参与 Mate 的组件。
2. 再用 `GetCorresponding()` 映射到装配体上下文。
3. 再清空选择集并连续选择两个实体。
4. 立即调用 `AddMate5()`。

```python
from sw_assembly import select_entities_for_mate

select_entities_for_mate(asm, entity_a, entity_b, mark=1)
```

### GetCorresponding 返回 None

场景：零件内基准面或面存在，但 `component.GetCorresponding(face_or_feature)` 返回 `None`。

常见原因：

1. 传入对象不是该组件引用文档里的对象。
2. 组件未解析。
3. 使用了错误组件实例；同一零件插入多次时必须用目标实例调用 `GetCorresponding()`。
4. 选择的是临时几何或已失效对象。

稳定写法：从 `get_component_model(component)` 返回的模型中查找特征/面，再由同一个 `component` 映射。

### 同心 Mate 后零件不能转

原因通常不是“没有铰链”，而是旋转自由度被锁死：

1. `lock_rotation=True` 或 GUI 中勾选了 Lock Rotation。
2. 旋转件又被三个基准面重合约束完全固定。
3. 组件本身被固定。
4. 轴向定位 Mate 太多，导致过定义。

做运动装配时，同心 Mate 默认应使用：

```python
add_concentric_mate_by_cylinders(..., lock_rotation=False)
```

上盖铰链可用同心 Mate 保留旋转，再用一个平面重合/距离 Mate 限制轴向窜动。

### 特征创建失败（返回 None）

常见原因：
1. **未选择正确实体** - 检查 SelectByID2 的实体名称和类型
2. **草图未完全约束** - 添加足够的约束和尺寸
3. **草图有开环** - 拉伸需要闭合轮廓
4. **单位错误** - API 使用米，不是毫米

### extrude_boss 的 direction=True 是负方向（2026-10-28 三基准面复核）

实测（三个基准面一致，圆 u=50 r=5、拉伸 10，读包围盒中心）：

| 基准面 | direction=True | direction=False |
|---|---|---|
| Front Plane | z = **−5** | z = +5 |
| Right Plane | x = **−5** | x = +5 |
| Top Plane | y = **−5** | y = +5 |

结论：**`direction=True` 沿负向（−Z/−X/−Y），`direction=False` 沿正向**。
旧文档"direction 表示沿法向"的描述会误导——按它写会把零件建到负坐标区，
后续切除（草图在正区、实体在负区）全部返回 None，且报错完全看不出方向错。
稳定习惯：**建模统一用 `direction=False`**（零件落在正坐标区）。

### extrude_cut 只切草图平面一侧，且深度会被截断

实测（200 宽箱体，Right Plane 圆 φ10，depth=500）：

- 只切掉 **3,927 mm³** = π×5²×**50**（单侧 50mm 深）
- 单侧 100 深应为 7,854、贯穿 200 深应为 15,708

两个结论：

1. **只切一侧**：贯穿必须正反各切一次（两次都要新建草图）。
2. **深度被截断**：传 500mm 实际只切 50mm。需要切多深就分段切，或改用
   `extrude_cut(depth=实体厚度+ε)` 并两侧各一次。

```python
def cut_circle(m, plane, cx, cy, dia, depth=200.0):
    for d in (True, False):
        with sketch(m, plane) as s:
            sketch_circle(m, cx, cy, mm(dia / 2.0))
        try:
            extrude_cut(m, s, mm(depth), direction=d)
        except Exception:
            pass
```

### Top Plane 上的切除完全不可用（本机）

实测（φ64.8×45 活塞上打 φ16 横孔，Top Plane 草图）：

- `direction=True` → 抛异常；`direction=False` → 抛异常
- 换深度 20/30/32/80/100 全部失败

规避：**沿 Y 方向的孔不要在 Top Plane 上切**。若几何允许（如活塞销孔应平行曲轴），
改在 Right Plane 上切（沿 X）；否则只能用"分段凸台拼合"绕开。

### SelectByID2("SKETCH") 持续返回 False
场景：SolidWorks 2024 中文版中，`SelectByID2("草图1", "SKETCH", ...)` 和 `SelectByID2("Sketch1", "SKETCH", ...)` 都返回 `False`，随后 `FeatureExtrusion3()` 返回 `None`。

根因：部分版本/语言环境下，草图名称选择不稳定；旧版 `sw_part._ensure_sketch_selected()` 还曾经把“当前选择集数量 > 0”当成成功条件，导致前几个零件靠残留选择集偶然成功，选择集被清空后全部失败。

稳定写法：

1. 优先使用 `with sketch(model, "...") as sketch_name:`，由 `sw_part.py` 在创建草图时缓存真实草图对象引用。
2. 不要在特征创建前依赖残留选择集；切换文档、重建、解析组件和失败的 API 调用都可能清空选择集。
3. 选择草图时优先用对象级 `Select2()` 选择草图 Feature / Sketch；最后才回退 `SelectByID2("SKETCH")`。
4. 若必须手写底层流程，`end_sketch(model)` 的返回值可直接传给 `extrude_boss()` / `extrude_cut()`。

```python
from sw_part import end_sketch, extrude_boss, sketch_circle, start_sketch

start_sketch(model, "Front Plane")
sketch_circle(model, 0, 0, 0.025)
sketch_ref = end_sketch(model)
feature = extrude_boss(model, sketch_ref, 0.05)
if feature is None:
    raise RuntimeError("拉伸失败：检查草图闭合、方向和对象选择")
```

真实 SolidWorks 回归验证：

```powershell
py -3.13 tests\solidworks_sketch_selection_regression.py --output-dir C:\CADAutomationWorkbench\solidworks_skill_regression
```

通过标准：脚本在两次 `ClearSelection2(True)` 后都报告 `selection_count=0`，随后仍能生成 `凸台-拉伸1` 和 `切除-拉伸1`，保存 `.SLDPRT`、导出 `.step`，并且 `review_evaluation.status` 为 `pass`。

### FeatureCut4 长参数不稳定

场景：手写 `FeatureCut4(...)` 后特征返回 `None`，但同一草图在 GUI 或封装函数中可切除。

原因：`FeatureCut4` 参数多，终止条件、翻转方向、`FlipSide`、`NormalCut`、选择集状态任一不匹配都会静默失败。不要凭记忆拼长参数。

稳定写法：

1. 优先使用 `sw_part.extrude_cut()`。
2. 如果必须底层调用，先用最小圆柱/矩形测试各种方向和终止条件。
3. 每次切除后检查返回特征对象；失败时不要继续保存交付文件。

```python
from sw_part import extrude_cut

feature = extrude_cut(model, sketch_name, 0, direction=True, flip=False)
if feature is None:
    feature = extrude_cut(model, sketch_name, 0, direction=True, flip=True)
if feature is None:
    raise RuntimeError("切除失败，需检查草图、方向或终止条件")
```

### 中心基准面切槽导致轴被剖开

场景：轴类零件上要做长圆槽/键槽，脚本在 `Front Plane` 上画槽并盲切，结果模型出现沿轴向的大平面切口，看起来像把圆柱剖掉一半。

原因：`Front Plane` 常穿过轴线，不是圆柱外表面。直接从中心面切槽会产生剖切效果，而不是表面凹槽。

稳定写法：先创建切向偏置参考面，再在该平面上画槽并按槽深盲切；见 `references/part-modeling.md` 的“圆柱表面长圆槽”。

### 槽特征树成功但预览不可见

场景：特征树中已有 `Cut_*_Slot`，`run_review()` 也通过，但等轴测或主视图看不到槽。

排查：

1. 槽是否切到了背侧；尝试翻转 `flip` 或导出另一侧视图。
2. 槽是否从中心面切除但被当前着色遮挡；查看俯视/右视。
3. 特征是否只切掉了不可见的内部体积；用剖视或修改切向参考面确认。
4. 预览图是否显示了被选中的参考面，导致遮挡槽轮廓。

处理：不要只依赖 `feature is not None`。导出 `isometric/front/top/right`，目视确认关键几何确实出现。

### 基准面选择失败

**原因**: 中英文名称不同
**解决**:
```python
# 尝试英文名称
success = model.Extension.SelectByID2("Front Plane", "PLANE", 0, 0, 0, False, 0, None, 0)
if not success:
    # 尝试中文名称
    model.Extension.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, None, 0)
```

### 保存/导出失败

检查错误码：
```python
errors = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
warnings = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
success = model.Extension.SaveAs(path, 0, 1, None, errors, warnings)
print(f"错误码: {errors.value}, 警告码: {warnings.value}")
# 查看 references/export.md 中的错误码对照表
```

### STEP/IGES 用 OpenDoc6 崩溃或弹模板对话框

场景：`OpenDoc6()` 打开 `.step/.stp/.igs/.iges` 时 SolidWorks 退出、RPC 断开；或
`LoadFile4(path, "", importData, errors)` 弹出“新建 SOLIDWORKS 文件”模板对话框并阻塞。

原因：非 DXF/DWG、非 Pro/E 外来文件应通过 `LoadFile4` 导入为新的 SolidWorks 文档，
并将 `ArgString` 设为 `"r"`。空字符串在 SW2024 环境中可能触发模板选择 UI，不适合自动化。

稳定写法：优先使用 `sw_connect.open_document()` / `SolidWorksSession.open()`；封装会对
STEP/STP/IGES/IGS 自动调用 `LoadFile4(file_path, "r", sw.GetImportFileData(file_path), errors)`。
导入前还会调用 `ensure_default_templates()`，设置默认零件/装配/工程图模板，并打开
`swAlwaysUseDefaultTemplates=111`，避免 SolidWorks 弹出“新建 SOLIDWORKS 文件”模板窗口。

```python
from sw_session import SolidWorksSession

session = SolidWorksSession(version=2024, visible=True)
model = session.open(r"E:\parts\body.step", silent=True, raise_on_error=True)
session.save(model, r"E:\parts\body.SLDPRT")
```

真实 SolidWorks 回归验证：

```powershell
py -3.13 tests\solidworks_loadfile4_import_regression.py --output-dir C:\CADAutomationWorkbench\solidworks_loadfile4_import_regression
```

通过标准：`status=pass`，`body_count >= 1`，导入后的 `.SLDPRT` 保存成功，并生成四视图审查报告。

注意：默认模板路径的字符串偏好枚举为 `swDefaultTemplatePart=8`、
`swDefaultTemplateAssembly=9`、`swDefaultTemplateDrawing=10`。不要误用文件位置枚举或旧值，
否则 `NewDocument()` / `LoadFile4()` 仍可能弹模板对话框。

若完整大型 STEP 反复触发 `pywintypes.com_error: (-2147023170, '远程过程调用失败。')`，
不要继续盲目重试同一 SolidWorks 进程；先保存日志，重启 SolidWorks 后改用更小分件导入、
Parasolid 中间格式，或让用户手工确认模板弹窗。`ensure_default_templates()` 只能降低弹窗概率，
不能保证所有 SW2024 本地模板配置都静默导入。

### 模态弹窗把自动化卡死（兜底方案：看门狗）

即使模板偏好都配好了，SolidWorks 在自动化过程中仍可能弹「新建 SOLIDWORKS 文件」「输入诊断」
「重建模型错误」这类**模态对话框**。一旦弹出，COM 调用会被阻塞，脚本无限等待，必须人工点确定。

兜底做法：`scripts/sw_dialog_guard.py` 起一个后台线程，枚举顶层窗口，命中就点掉。

```python
from sw_dialog_guard import DialogGuard, ensure_templates

ensure_templates(sw)                 # 先把三种默认模板写进偏好，降低弹窗概率
with DialogGuard(interval=0.4):      # 再挂看门狗兜底
    ...                              # 你的 COM 操作
```

实现要点（踩过的坑）：

- 只认 `#32770` 类窗口 + 标题关键字（中英文都覆盖），别乱点其他窗口。
- **必须找到对话框里的按钮发 `BM_CLICK`**。给对话框本身发 `WM_COMMAND(IDOK)` 跨进程**无效**，
  实测会一直重复命中、对话框纹丝不动。
- 「输入诊断 / 重建错误」类发 `WM_CLOSE`，不要按确定（按了会走一遍诊断流程）。
- 默认模板偏好的枚举是 `swDefaultTemplatePart=8`、`Assembly=9`、`Drawing=10`。
  用 145/146/147 去写会返回 False（`SetUserPreferenceStringValue` 静默失败）。

实测：人为弹出同名对话框，**0.6 s 内自动点掉**；批量跑 152 件装配体时，实战命中并点掉了
两次「新建 SOLIDWORKS 文件」弹窗，全程无人值守。

**注意区分两种"卡住"**：弹窗卡住可以用看门狗救；但如果窗口枚举里**根本没有对话框**、
进程内存也不再增长，那是 SolidWorks 在导入/求解里挂死了，看门狗无能为力——只能靠超时 +
重启进程。大装配遇到这种情况，优先怀疑文档堆积（见 `references/assembly.md` 的内存管理配方）。


### SaveAs3 返回 1 或文件不落盘

场景：`doc.SaveAs3(path, 0, 0)` 返回 `1`，不抛异常，但目标文件**根本没生成**。

两个必查点：

1. **路径必须是绝对路径。** 传相对路径（如 `"out/part.sldprt"`）会静默失败。
   ```python
   doc.SaveAs3(os.path.abspath(out_path), 0, 0)      # 对
   doc.SaveAs3("out/part.sldprt", 0, 0)              # 错，返回 1 不落盘
   ```
2. **装配体打开时，导入零件的 SaveAs 会失败。** 从 STEP 批量导零件时，如果先 `new_document("assembly")` 再导零件，
   `SaveAs3` 会返回 1。正确顺序是**先把所有零件导入并保存成 SLDPRT，再建装配体**。

```python
# 对：先导零件
for step in step_files:
    doc = open_document(sw, step)
    doc.SaveAs3(os.path.abspath(sld_path), 0, 0)
    sw.CloseDoc(doc.GetTitle)
asm = new_document(sw, "assembly")       # 再建装配体
```

**不要用返回值判断成功，要用文件系统判断**：

```python
rc = doc.SaveAs3(os.path.abspath(path), 0, 0)
if not os.path.exists(path):
    raise RuntimeError(f"SaveAs3 返回 {rc}，文件未落盘: {path}")
```

同理，`SetTransformAndSolve2()` 返回 True 也不代表旋转写进去了（见本节 ArrayData 条目），
`AddMate5` 返回非 None 也不代表配合是对的。**一律以回读为准**：文件存在性、`ArrayData` 数值、
`GetBox()` 包围盒、`IsFixed` 状态。

### SaveAs3 之后文档句柄失效，GetPartBox 报 <unknown>

场景：`SaveAs3(...)` 之后调用 `model.GetPartBox(True)` 抛
`AttributeError: <unknown>.GetPartBox`（动态派发下成员"消失"）。

原因：保存动作会使既有文档引用失效。

**规矩：所有回读（包围盒、质量、材质名）都必须在 `SaveAs3` 之前完成**：

```python
b = box(model)                       # 先回读
model.SaveAs3(path, 0, 0)            # 再保存
app.CloseDoc(get_com_member(model, "GetTitle"))
```

### 打开装配体报"所参照的文档具有不匹配的内部 ID"

症状：打开 `.SLDASM` 弹出 SolidWorks 对话框
「所参照的文档具有不匹配的内部 ID —— 您正打开的文档参考 xxx.SLDPRT，但内部 ID 不匹配」。

原因：**用"新建零件再覆盖文件"的方式改了被装配体引用的零件**。
SolidWorks 在装配体里记录的是零件文档的**内部 ID**（不是文件名）；
新建的文档即使存成同名文件，内部 ID 也不同 → 引用失效。

正确做法（按优先级）：

1. **在原文档内改几何**：打开零件 → 加/改特征 → 保存。文档对象不变、内部 ID 不变，引用始终有效。
   ```python
   doc = open_document(sw, part_path)
   with sketch(doc, "Front Plane") as s:
       sketch_circle(doc, 0, 0, mm(20))
   f = extrude_offset_start(doc, s, mm(15), mm(75), direction=True, merge=True)
   doc.SaveAs3(part_path, 0, 0)      # 同一文档 → ID 不变
   ```
2. **做几何变更实验时用副本**：把 `parts/` 与装配体整目录复制到临时目录再改副本，
   交付文件完全不受影响，也不会污染引用。
3. **如果确实覆盖了**：只能重建装配体（重新 AddComponent 生成新引用），
   或在 SW 界面用「替换件 / Replace Components」把引用指到新文件。

预防：

- 交付脚本里凡是"重建零件"的函数，**不要原地覆盖已被引用的文件**；
  要么在原文档内改，要么写到新目录并同步重建装配体。
- 改完零件后**重建装配体**再交付，并回读 `GetPathName()` 校验引用全部存在。
- 弹窗本身是模态的：可用 `sw_dialog_guard.DialogGuard` 兜底，但它只能点掉、
  不能替你选对选项——所以根子上要避免制造这个局面。

### 键槽/沉孔与孔壁相切时切除返回 None

场景：在孔壁上切键槽，矩形内缘与孔圆柱面**正好相切**（y 从孔半径开始），
`FeatureCut4` 返回 None；把内缘深入孔内 0.5mm 立即成功。

原因：相切面让求解器出现零厚度边界，与"轮廓被吞并"同类。

```python
# 键槽：矩形内缘伸入孔内 0.5mm，避免相切
sketch_corner_rectangle(m, mm(-4), mm(bore_r - 0.5), mm(4), mm(bore_r + 3.3))
```

同理适用于沉孔台阶、肋板与圆柱面相交等所有"轮廓与既有面相切"的场景：
**让轮廓越过相切位置，宁可多切一点重叠材料。**

### 工程图导出 PDF：GetExportFileData 路径类型不匹配，直接 SaveAs3 即可

场景：按官方推荐 `app.GetExportFileData(1)` + `data.SetSheets(0, names)` +
`Extension.SaveAs3(pdf, 0, 1, data, err, warn)` 导出 PDF，动态派发下报
`类型不匹配 (-2147352571)`——`names` 的 SAFEARRAY 编组很难凑对。

稳定写法（SW 自带 PDF 滤镜，一张纸一张 PDF）：

```python
dwg.SaveAs3(os.path.join(out_dir, name + ".pdf"), 0, 0)
if not os.path.exists(pdf_path):
    raise RuntimeError("PDF 未落盘")
```

另注意：GB 图纸格式（.slddrt）在
`C:\ProgramData\SolidWorks\SOLIDWORKS 2024\lang\Chinese-Simplified\sheetformat\a3 - gb.slddrt`，
**不在** `templates\*.drwdot`（那里是图纸模板，`select_drawing_template` 要求 `.slddrt`）。

### OBJ/STL 用 OpenDoc6 打不开或 LoadFile4 类型不匹配

场景：公开网格参考模型、汽车外观模型或测试立方体 `.obj/.stl` 用 `OpenDoc6(path, swDocPART, ...)`
打开失败，错误码为 `2097152`（`swFileRequiresRepairError`）；改用
`LoadFile4(path, "r", None, errors)` 又报 `com_error(-2147352571, '类型不匹配。')`。

原因：在部分 SolidWorks 2024 COM 环境中，OBJ/STL 不能按普通零件文档路径打开；
`LoadFile4()` 第三个参数也不能传 Python `None`。这不一定表示网格文件坏，即使一个
684 字节的简单 STL 立方体也可能复现同样问题。

稳定写法：优先使用 `scripts/sw_import_mesh_reference.py`，或手写以下最小导入：

```python
from sw_connect import connect_solidworks, create_empty_dispatch_variant, save_document
from win32com.client import VARIANT
import pythoncom

sw, _ = connect_solidworks(visible=True)
sw.SetUserPreferenceIntegerValue(208, 0)   # swImportStlVrmlModelType_Graphics
sw.SetUserPreferenceIntegerValue(210, 2)   # swMETER
sw.SetUserPreferenceToggle(303, True)      # swImportStlVrmlTextureInformation
sw.SetUserPreferenceToggle(679, True)      # swVrmlStlImportAsPSMesh

errors = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
model = sw.LoadFile4(
    r"E:\work\reference_scaled_m.obj",
    "r",
    create_empty_dispatch_variant(),
    errors,
)
assert model is not None, errors.value
save_document(model, r"E:\work\reference.SLDPRT")
```

注意：STEP/STP/IGES/IGS 与 OBJ/STL 的导入数据参数不同。中性 CAD 文件继续使用
`sw.GetImportFileData(path)`；OBJ/STL 网格参考模型使用空 Dispatch 变体。不要把两条路线混用。

### SaveAs 错误码 1 或覆盖失败

场景：`session.save(model, path)` 或 `model.Extension.SaveAs(...)` 返回失败，错误码为 `1`，目标文件明明存在且路径正确。

常见原因：同名文档已经在 SolidWorks 会话中打开，或目标文件被当前 SolidWorks 进程占用，导致覆盖失败。

稳定写法：

```python
session = SolidWorksSession()
session.sw.CloseDoc("Drawing_Stepped_Shaft.SLDPRT")
model = session.new_part()
```

注意：只关闭本次脚本明确要覆盖的输出文档，不要盲目 `CloseAllDocuments()`，除非用户确认可以清理整个会话。

### 临时 SLDPRT 删除失败

场景：PowerShell `Remove-Item` 报：

```text
The process cannot access the file ... because it is being used by another process.
```

原因：调试零件仍在 SolidWorks 中打开。

处理顺序：

1. 用 `sw.CloseDoc("<文件名>.SLDPRT")` 关闭对应文档。
2. 再用 `Remove-Item -LiteralPath <path> -Force` 清理临时文件。
3. 删除前校验路径只匹配预期临时文件，例如 `debug_*.SLDPRT`。

### 参考面或草图污染预览图

场景：`run_review()` 导出的 BMP 出现橙色大参考面、草图或选择高亮，影响判断实体几何。

稳定写法：

```python
for plane_name in created_plane_names:
    model.ClearSelection2(True)
    selected = model.Extension.SelectByID2(
        plane_name, "PLANE", 0, 0, 0, False, 0,
        create_empty_dispatch_variant(), 0,
    )
    if selected:
        model.BlankRefGeom()

model.BlankSketch()
model.ClearSelection2(True)
model.ForceRebuild3(False)
```

`BlankRefGeom()` 通常需要先选择要隐藏的参考几何；单独调用不一定隐藏脚本新建的基准面。

### 多色装配体几乎全部变绿

场景：脚本配置了红、蓝、银、黑等颜色，但装配体或 BMP 预览几乎只有绿色。

原因通常有两类，必须分别验证：

1. 把普通 Python `list` 直接传给 `MaterialPropertyValues`，导致 COM 数组编组错位。
2. 截图前仍有组件、面或边被选中，SolidWorks 绿色选择高亮覆盖真实外观。

稳定写法：

```python
from sw_appearance import set_component_appearance, verify_appearance

assert set_component_appearance(component, "signal_red")
assert verify_appearance(component, "signal_red")["ok"]

model.ClearSelection2(True)
model.GraphicsRedraw2()
```

不要自行把列表换成 tuple 后继续尝试；应统一使用 `material_variant()` 生成
`VT_ARRAY | VT_R8`。不要用 `bool(SetMaterialPropertyValues2(...))` 判断结果，
因为该方法返回 `void`。验证命令：

```powershell
python tests\solidworks_appearance_regression.py
```

### MathUtility.CreateTransform 不稳定

场景：尝试 `sw.GetMathUtility().CreateTransform(data)` 或 `math_utility.CreateTransform()` 时 COM 返回异常、空对象，或组件移动后不求解。

稳定写法：读取组件已有 `Transform2`，改 `ArrayData`，再调用 `SetTransformAndSolve2()`；失败时回退到 `component.Transform2 = transform`。

```python
from sw_assembly import apply_component_transform_axis
from sw_connect import deg

# 绕 Z 轴转 72°，并保留组件原有平移（AddComponent5 会把包围盒中心放到原点，写 0 会让组件跳位）
apply_component_transform_axis(component, axis="Z", angle_rad=deg(72), preserve_translation=True)

# 绕 X 轴 + 显式平移
apply_component_transform_axis(component, axis="X", angle_rad=deg(30), translation=(0.1, 0.0, 0.0))
```

**必读：`ArrayData` 只能用 `VARIANT(VT_ARRAY|VT_R8)` 赋值。** 直接赋 Python 元组时 COM 会丢弃旋转块——实测旋转部分仍是单位阵，只有平移槽被写入错位值，而 `SetTransformAndSolve2` 仍可能返回 True。`apply_component_transform_axis` 内部已封装 VARIANT 并回读校验，写入不生效会抛 `RuntimeError`，不会静默假成功。

**必读之二：`ArrayData[0:9]` 是列主序，不是行主序。** 这 9 个数是"局部 X/Y/Z 轴在装配体中的像"连续存放：

```
ArrayData = [Xx, Xy, Xz,  Yx, Yy, Yz,  Zx, Zy, Zz,  tx, ty, tz,  1, 0, 0, 0]
             └─ X 轴的像 ─┘ └─ Y 轴的像 ─┘ └─ Z 轴的像 ─┘
```

如果按常见的行主序 `p' = R·p` 直接把这 9 个数摊平写进去，得到的是**转置矩阵**，也就是反向旋转。

实测证据（同一个 18 件装配体，只改这一处）：

| 写法 | 整机包围盒偏差 |
|---|---|
| 行主序 `[Xx,Yx,Zx, Xy,Yy,Zy, Xz,Yz,Zz]` | **493.3 mm**（零件散架） |
| 列主序 `[Xx,Xy,Xz, Yx,Yy,Yz, Zx,Zy,Zz]` | **2.1 mm**（正确） |

**为什么以前没暴露**：对称旋转阵两种写法等价。例如 `diag(-1,-1,1)`（绕 Z 转 180°）、单位阵、以及只翻转一个轴的矩阵，行/列主序结果完全一样。只有非对称旋转（斜轴、复合角、外部数据源给的任意姿态）才会出错。

用法：

```python
from sw_assembly import build_transform_data_axes, build_transform_data_matrix

# 外部数据源直接给三个轴向量时，用这个入口，绕开顺序歧义（推荐）
data = build_transform_data_axes(x_axis, y_axis, z_axis, translation=(0, 0, 0))

# 手上是行主序 3x3 时，这个函数会自动转置成列主序
data = build_transform_data_matrix(row_major_9, translation=(0, 0, 0))

transform.ArrayData = VARIANT(VT_ARRAY | VT_R8, list(data))
```

回归测试：`tests/solidworks_transform_convention_regression.py`（7 项，覆盖绕 Z +90°、绕 X +45°、对称阵不变、转置关系、双入口一致）。


注意：直接改 Transform 适合生成演示帧或定位；如果目标是在 SolidWorks 里可拖动，仍要靠真实 Mate 保留自由度。

### SolidWorks 内部错误或 COM 变慢

场景：大批量生成零件/装配后，`AddComponent4`、保存、导出或特征操作随机失败。

常见原因：打开文档过多、图形窗口和特征树刷新堆积、同一进程长时间运行。

处理顺序：

1. 保存关键输出。
2. 调用 `sw.CloseAllDocuments(False)` 清理会话。
3. 分批重新打开必要零件并装配。
4. 仍不稳定时，让用户确认后重启 `SLDWORKS.exe`。
5. 脚本内记录失败组件和失败 API，不要只打印“完成”。

### FeatureFillet 报"非选择性的参数"

场景：`model.FeatureManager.FeatureFillet(195, radius, 0, 0, None, None, None)` 报

```text
com_error: (-2147352561, '非选择性的参数。', None, None)
```

原因：`IFeatureManager::FeatureFillet` **只接受 4 个参数**：`(Options, Radius, R1, R2)`。
传 7 个参数（末尾补三个 `None`）会在 SolidWorks 2024 和 2026 上都失败。旧文档和早期
封装里有这个错误写法，真机实测确认。

稳定写法：

```python
from sw_edge_select import select_edges
from sw_part import fillet
from sw_connect import mm

# 必须先建立非空的边线选择集
selection = select_edges(model, {"axis": "z", "convex_only": True})
if selection["status"] != "ok":
    raise RuntimeError(f"未选中边线: {selection['errors']}")
feature = fillet(model, mm(4))
```

`sw_part.fillet()` 会在选择集为空时抛 `ValueError` 而不是继续——选择集为空时
SolidWorks 会对"当前轮廓"执行圆角，产生难以排查的错误特征。

### 圆角/倒角选到错误的边

场景：脚本没报错，但圆角加到了不该加的边上；或者模型改了尺寸后再跑，圆角位置全变了。

原因：`SelectByID2("Edge1", "EDGE", x, y, z, ...)` 依赖实体的坐标魔法值，模型一改就失效，
而且失败是静默的（返回 `False` 但后续特征不报错）。

稳定写法：改用 `sw_edge_select.select_edges()` 按 B-Rep 几何条件选边。详见
[references/edge-selection.md](references/edge-selection.md)。

```python
from sw_edge_select import select_edges

# 先预览选中了哪些边，以及每条被排除的边的原因
preview = select_edges(model, {"axis": "z", "convex_only": True})
print(preview["selected_count"], preview["selected"])
print(preview["rejected_sample"])
```

### IEdge.Select4 报"类型不匹配"

场景：`edge.Select4(True, None, False)` 报

```text
com_error: (-2147352571, '类型不匹配。', None, None)
```

原因：`Select4(Append, Callout)` 的第二个参数是 **Callout 对象**，必须是 Dispatch VARIANT，
不能是 Python 的 `None`。

稳定写法：优先用 `Select2(Append, Mark)`，它没有这个问题；必须用 Select4 时传
`create_empty_dispatch_variant()`。

```python
from sw_connect import create_empty_dispatch_variant

edge.Select2(True, 0)                                    # 首选
edge.Select4(True, create_empty_dispatch_variant())      # 回退
```

### 孔"创建成功"但模型上没有孔

场景：`create_through_hole()` 返回特征证据，但特征树里没有切除，或报
`SolidWorks 未创建特征`。

逐项排查：

1. **单位**。`sw_hole_features` 的 `center` / `diameter` / `depth` 全部期望**米**。
   传毫米（例如把 10 当成 10mm）会得到 1000 倍大的草图，切除完全落在实体之外而被
   静默拒绝。实测把 100x60x6mm 的板撑到 13300mm 的包围盒就是这个原因。
   ```python
   from sw_connect import mm
   create_through_hole(model, center=(mm(10), mm(10)), diameter=mm(6.6))
   ```
2. **孔位是否在实体范围内**。切除草图完全在实体外时 SolidWorks 不报错、只返回 `None`。
   用 `build_from_spec.validate_holes_inside_base()` 或自己核对边界。
3. **返回值取错键**。`HoleFeatureEvidence.to_dict()` 的键是 `feature_names`（元组），
   没有 `status` / `feature_name`。写成 `result.get("status")` 会把成功的孔判成失败。

### 特征返回值判空而不是判真

SolidWorks 大多数特征 API 在失败时返回 `None`。**不要**只写 `if feature:` 就继续保存，
也不要忽略返回值。每次创建特征后检查，失败就中止或降级：

```python
feature = extrude_boss(model, sketch_ref, mm(8))
if feature is None:
    raise RuntimeError("拉伸失败：检查草图是否闭合、方向是否正确、深度是否非零")
```

### 读取包围盒得到 1000 倍或 0.001 倍的尺寸

场景：`model.GetPartBox(True)` 的返回值被当成毫米或米用错。

原因：`GetPartBox` 有两个模式，单位不同：

| 调用 | 返回单位 |
|---|---|
| `GetPartBox(True)` | **米**（系统单位） |
| `GetPartBox(False)` | **毫米**（文档单位，受文档单位制影响） |

`sw_measure.collect_bounding_box()` 统一使用 `True` 并换算为毫米，直接用封装即可。

```python
from sw_measure import collect_bounding_box
box = collect_bounding_box(model)
print(box["size_mm"])   # [长, 宽, 高]，毫米
```

### 质量属性提示"未分配材料"

场景：`collect_mass_properties()` 返回的 `warnings` 里有"文档未分配材料"。

原因：SolidWorks 对未分配材料的文档按默认密度（1000 kg/m³）计算。数值算得出来，
但**不能直接用于工程判断**（电机选型、运输重量、成本估算都会偏）。

处理：在文档里分配材料，或显式传入密度覆盖：

```python
result = collect_mass_properties(model, density_kg_m3=7850.0)   # 45 钢
```

另外，`IMassProperty.Status` 在部分版本（实测 SW2024 SP5）不可用，返回 `status: null`
是正常的，不影响质量数值。

### MCP 调用长时间无响应

场景：调用任何 SolidWorks MCP 工具后一直不返回，后续工具调用也全部挂起。

原因：SolidWorks 弹出模态对话框（模板选择、许可证提示、文件覆盖确认）时不会响应
Automation 调用，而且不会超时。持锁的上一个操作因此卡死。

处理：

1. 调用 `solidworks_recover` 诊断——它会枚举 SolidWorks 窗口，报告检测到的模态对话框
   和当前持锁的操作。
2. 切到 SolidWorks 窗口**手动**关闭对话框。需要自动关闭时传
   `{"dismiss_dialogs": true}`，但注意 `WM_CLOSE` 可能丢弃未保存的更改，故默认关闭。
3. 窗口完全无响应时，用任务管理器结束 `SLDWORKS.exe`，再调用 `solidworks_connect`。

锁超时上限为 300 秒；超过后工具返回结构化错误而不是永久阻塞。

## 未封装 API 调用

当需要使用本 skill 尚未封装的 SolidWorks API 时：

1. 先查 SolidWorks 官方 API 文档或本地 SDK 文档，确认接口签名和枚举。
2. 用最小脚本验证接口，不要直接嵌入大任务。
3. 对 COM 返回值做 `None` / `False` 检查，并打印错误码/警告码。
4. 完成后把稳定写法补进 `scripts/`，把坑位补进本文件或对应 `references/*.md`。

沉淀模板：

```text
### <API 或错误现象>

场景:
错误/症状:
原因:
稳定写法:
  # 最小可运行示例
验证方式:
```

## 性能优化

### 大型装配体操作慢

```python
# 开启后台处理模式
model.FeatureManager.EnableFeatureTree = False
model.ActiveView.EnableGraphicsUpdate = False

# ... 执行操作 ...

# 恢复并刷新
model.FeatureManager.EnableFeatureTree = True
model.ActiveView.EnableGraphicsUpdate = True
model.GraphicsRedraw2()
```

### 批量操作优化

```python
# 禁用自动重建
sw.UserControl = False

# 执行批量操作...

# 手动重建
model.ForceRebuild3(False)   # ✅ 用这个
sw.UserControl = True
```

⚠️ 别写 `model.EditRebuild3()`：`EditRebuild3` 在本机动态派发下是 bool **属性**，
加括号调用会报 not callable。重建一律用 `ForceRebuild3(False)`。

## 版本号对照

| SolidWorks | 修订号 | ProgID |
|---|---|---|
| 2020 | 28 | SldWorks.Application.28 |
| 2021 | 29 | SldWorks.Application.29 |
| 2022 | 30 | SldWorks.Application.30 |
| 2023 | 31 | SldWorks.Application.31 |
| 2024 | 32 | SldWorks.Application.32 |
| 2025 | 33 | SldWorks.Application.33 |
| 2026 | 34 | SldWorks.Application.34 |

公式: `修订号 = (年份 - 2000) + 8`

## 许可证与加载项边界

SolidWorks 基础 COM 可连接，并不证明 Simulation、Routing、Motion 或模具加载项具备可用许可证。能力探测必须分别记录加载项注册、加载状态和最小真实操作结果；缺少合法授权时返回 `blocked` 与具体依赖，不把授权问题误报为普通脚本失败。

本项目不修补许可证、不绕过激活、不修改授权服务，也不为非授权安装伪造能力。此时仍可使用 OCP、CalculiX、ezdxf 等合法无头后端完成开放格式范围内的任务。

## COM 契约与几何构造陷阱（2026-10 实测）

本节记录一批"看名字找不到、按直觉写不对"的实测结论。每条给出**现象 / 原因 / 稳定写法**，
新增结论请直接追加到本节末尾。

### 物理材质 API 在 IPartDoc，不在 IModelDocExtension

- 现象：在 `model.Extension` 上找 `SetMaterialPropertyName2`，报"找不到成员"。
- 原因：该方法属于 `IPartDoc`（文档对象本身）。
- 稳定写法：

```python
model.SetMaterialPropertyName2("", "solidworks materials.sldmat", "铜")
```

### GetMaterialPropertyName2 必须传 byref BSTR

- 现象：`GetMaterialPropertyName2("", "solidworks materials.sldmat")` 报"类型不匹配"。
- 原因：第二个参数是 `[out]` 数据库名，不是输入。
- 稳定写法：

```python
from win32com.client import VARIANT
import pythoncom
name = model.GetMaterialPropertyName2("", VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, ""))
```

### 材质库里没有"聚乙烯"

- 现象：设 `"聚乙烯"` 不报错，但读回仍是上一个材质（静默失败）。
- 原因：中文材质库的有效名与直觉不同。
- 已验证可用：`铜`、`1060 合金`、`交联聚乙烯`、`普通碳钢`。
- 建议：设完必须读回比对，`back == name` 才算成功；半导电料库中没有，暂用交联聚乙烯近似。

### 设物理材质会覆盖外观颜色

- 现象：`SetMaterialPropertyName2` 之后，之前设的 `MaterialPropertyValues` 全部失效，模型变成材质库默认外观（常见为银白）。
- 原因：物理材质带默认外观，会覆盖已有外观。
- 稳定顺序：**先 `SetMaterialPropertyName2`（物理属性），再 `MaterialPropertyValues`（外观）**。

### 多轮廓草图无法拉伸（≥2 个闭合轮廓）

- 现象：一个草图里画 2 个同心圆（想做圆筒）或多个圆（想做线束），`FeatureExtrusion3` 返回 None。
- 原因：本环境的 `UseAutoSelect` 路径解析不了多轮廓草图。
- 稳定写法：
  - 圆筒 → 外圆凸台 + 内圆切除两步（见 `sw_part.extrude_tube`）。
  - 多根线/柱 → 每个轮廓单独建草图 + 单独拉伸（`merge=False` 保持独立实体）。

### 偏移起始拉伸只在单轮廓草图可用

- 现象：`FeatureExtrusion3` 传 `T0=3`（swStartOffset）+ `StartOffset` 时返回 None。
- 原因：不是偏移本身的问题，而是**同时用了多轮廓草图**。单轮廓下偏移完全正常（实测质心 z 精确命中）。
- 稳定写法：

```python
model.FeatureManager.FeatureExtrusion3(
    True, False, False, 0, 0, depth, 0.0,
    False, False, False, False, 0.0, 0.0,
    False, False, False, False, True, False, True,
    3, start_offset, False)   # T0=3 偏移起始
```

### FeatureCut4 的偏移起始参数调不通

- 现象：照 `FeatureExtrusion3` 的尾部参数给 `FeatureCut4` 传 `T0/StartOffset`，报"非选择性的参数"。
- 原因：两个 API 的参数个数与顺序不同。
- 绕过：需要"带偏移的孔/槽"时，改成"**先做实心体，末尾一刀通孔**"。例：波纹管 = 基管 + 逐道实心环（偏移拉伸）+ 最后一刀镗通内孔。

### 已保存装配体的组件位姿改不动

- 现象：打开已保存的 `.sldasm`，给 `component.Transform2` 写新矩阵，读回正确但**几何不动**；先 `UnfixComponent()` 也无效。
- 原因：组件变换只在**同一会话内刚 `AddComponent` 的组件**上生效。
- 绕过：改位姿必须重建装配体（用封装好的 `rebuild_assembly`），或改视图而不是改组件。

### 组件旋转的枢轴是自身包围盒中心

- 现象：多层圆筒一起绕轴旋转后，各层沿轴向散开、不再同轴。
- 原因：`AddComponent5` 会把包围盒中心放到指定点，不同长度的零件枢轴 z 不同；绕同一角度旋转就产生不同位移。
- 稳定写法：统一 `translation=(0, 0, 0)` + `preserve_translation=False`，让所有实体从同一端面起算。

### 圆周排布不要用"极坐标逐行"，用全局六方点阵

- 现象：按"每行角步长 = 直径 / 该行半径"排布圆形线束，线之间互相重叠。
- 原因：相邻行半径不同 → 角步长不同 → 行间切向错位量对不上，必然碰撞。
- 稳定写法：在 XY 平面铺**点距恰等于直径**的六方点阵（`dx=d, dy=d*√3/2, 奇数行偏移 d/2`），再按边界裁剪。这样任意最近邻距离恒等于直径，相切关系由构造保证。

### 扫掠在特定相位退化

- 现象：同一段扫掠，换个起始相位就建不出特征（实测 SW2024 在 288°/297.3° 必失败），微扰采样点数、引导线顺序、轮廓起点均无效。
- 原因：求解器在"窄截面 + 特定相位"下退化。
- 绕过：**只扫掠一次（相位 0），其余实例在装配体里旋转复用**；非圆截面必须配内/外缘两条螺旋引导线，否则截面会随路径扭转。

### 按草图名选截面会命中缓存

- 现象：循环建 N 根线、每根用 `_select_sketch_ref(model, sketch_ref)` 选截面，结果 N 根全部叠在同一位置。
- 原因：草图名缓存/反查每次命中第一个草图。
- 稳定写法：直接用**当次创建的实体对象**选，不要按名字反查：

```python
with sketch(model, "Front Plane") as prof:
    seg = sketch_circle(model, x, y, r)
# ...建路径...
seg.Select2(False, 1)   # mark=1 作为扫掠轮廓
```

### AddComponent5 默认按包围盒居中放置

- 现象：逐个 `AddComponent5(path, 0,0,0)` 加入的零件，装配后全部以**自身包围盒中点**落在装配原点
  （Transform2 平移 z = −L/2）。多层同轴件（电缆、轴系、管路）会因此每层以中点对称，
  导体从护套两端穿透出来——视觉上就是"线芯和护套重叠、不在一个中心点上"。
- 原因：`AddComponent5` 的默认放置是包围盒居中，不是"零件原点落在装配原点"。信任这个默认
  等于丢掉轴向基准。
- 稳定写法：
  1) **零件级约定**：所有零件几何必须从自身原点起建（拉伸体 z 质心 = 长度/2）。
     建完即校验：`assert_datum(model, length/2, name)`。
  2) **装配级约定**：每个组件都施加**显式变换**（即使角度为 0 也要把平移归零），不要信任默认。
     `rebuild_assembly` 已内置该行为，并在保存前调用 `verify_layout` 比对组件位姿与规格，
     不一致直接抛错。
- 验收：装配后逐组件 `Transform2` 平移 XY 应 ≈ 0（径向同轴）、Z 应 = 规格值（轴向级联）。
