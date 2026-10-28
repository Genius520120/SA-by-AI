# 装配体与运动配合参考

## 官方资料入口

- AddMate5: `https://help.solidworks.com/2023/english/api/sldworksapi/solidworks.interop.sldworks~solidworks.interop.sldworks.iassemblydoc~addmate5.html`
- GetModelDoc2: `https://help.solidworks.com/2019/english/api/sldworksapi/solidworks.interop.sldworks~solidworks.interop.sldworks.icomponent2~getmodeldoc2.html`
- GetCorresponding: `https://help.solidworks.com/2019/English/api/sldworksapi/solidworks.interop.sldworks~solidworks.interop.sldworks.icomponent2~getcorresponding.html`
- IsCylinder / CylinderParams: `https://help.solidworks.com/2021/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.ISurface~IsCylinder.html`、`https://help.solidworks.com/2024/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.ISurface~CylinderParams.html`
- Gear Mates: `https://help.solidworks.com/2024/English/solidworks/sldworks/t_Gear_Mates_SWassy.htm`
- Hinge Mates: `https://help.solidworks.com/2023/english/SolidWorks/sldworks/t_hinge_mates.htm`
- Mate Controller: `https://help.solidworks.com/2024/english/Solidworks/sldworks/c_assembly_mate_controller.htm`

## 添加组件

优先复用 `scripts/sw_assembly.py` 的 `add_component()`。SW2024 中文版 + pywin32 下，
`AddComponent4()` 可能无异常但返回 `None`；当前封装会优先走 `AddComponent5()` 的
8 参数签名，失败后再回退 `AddComponent4()`。若两者都返回空，封装会自动把零件
静默打开到 SolidWorks 会话、重新激活装配体，再重试 `AddComponent5()`。

```python
from sw_assembly import add_component

component = add_component(asm_model, r"E:\parts\base.SLDPRT", x=0, y=0, z=0)
```

底层稳定调用：

```python
component = asm_model.AddComponent5(
    CompPath, 0, "", False, ConfigName, X, Y, Z
)
```

`AddComponent4` / `AddComponent5` 失败时，先检查零件文件是否真实存在、装配体是否为活动文档、零件是否已保存到磁盘；必要时先打开零件再切回装配体。真实回归验证：

```powershell
py -3.13 tests\solidworks_add_component_regression.py --output-dir C:\CADAutomationWorkbench\solidworks_add_component_regression
```

## 运动型装配工作流

1. 先生成并保存每个零件，尽量关闭不再编辑的零件文档，避免 SolidWorks 打开的文档过多。
2. 新建装配体，添加底座、轴承座、轴、齿轮、上盖等组件。
3. 对所有需要读特征或面的组件先调用 `resolve_component(component)`；轻化/压缩组件会让 `GetModelDoc2()` 返回 `None`。
4. 用 `IComponent2.GetCorresponding()` 将零件内部基准面、圆柱面或草图实体映射到装配体上下文，再用 `Select2()` 选择。
5. 固定件可以用三基准面重合或距离 Mate 锁死；旋转件不要用三基准面完全锁死。
6. 轴/孔用同心 Mate，`lock_rotation=False`，保留绕轴旋转自由度。
7. 齿轮联动用 Gear Mate；选择齿轮轴线、孔圆柱面、圆柱面或轴实体，按齿数传入比例。
8. 上盖铰链可用同心 Mate 加一个平面重合/距离 Mate 表达一条旋转自由度；若需要 Motion 分析，优先在 GUI 中用官方 Hinge Mate 或用更高阶 MateFeatureData API 生成。
9. 创建后遍历 MateGroup 子特征，确认真实 Mate 写入特征树；预览动画只说明脚本能驱动，不等同于可在软件里拖动。
10. 最后运行 `sw_review.py` 导出多视角预览图，并在 SolidWorks 中手动拖动一个自由件验证运动。

## 在线工程经验摘要

- 官方 Mechanical Mates 文档强调：Gear Mate 只强制两个组件绕所选轴按比例相对转动；Hinge Mate 的效果等价于同心配合加重合配合，并可限制角度。
- GoEngineer 的 Mate 教程把 Mate 理解为零部件之间的几何约束；机械配合用于齿轮、齿条、螺旋、万向节等运动关系，不等同于接触仿真：`https://www.goengineer.com/blog/introduction-to-solidworks-mates`
- CodeStack 的轻化组件示例说明 `GetModelDoc2()` 对轻化/压缩组件会返回空，可通过解析或静默打开引用模型兜底：`https://www.codestack.net/solidworks-api/document/assembly/components/lightweight-get-model-doc/`
- CAD Booster 的 GetCorresponding 文章强调 part context 与 assembly context 不能混用；能拿对象引用时优先用 `GetCorresponding()` + `Select2()`，少依赖坐标点击：`https://cadbooster.com/entities-and-getcorresponding-in-the-solidworks-api/`
- 轻化组件导致 `GetModelDoc2()` 返回空是高频坑；做 Mate 前先解析组件，不要把 `None` 当作“零件没有特征”。

## AddMate5 参数

SolidWorks 2015+ 的 `AddMate5` 是 15 参数签名，Python COM 下 by-ref 错误码必须用 `VARIANT` 包装：

```python
errors = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
mate = asm_model.AddMate5(
    mate_type,          # swMateType_e
    align,              # swMateAlign_e，常用 0=Aligned
    flip,               # bool
    distance,           # 距离配合值，单位米
    distance_upper,     # 距离上限；无限制时等于 distance
    distance_lower,     # 距离下限；无限制时等于 distance
    gear_num,           # Gear Mate 分子
    gear_den,           # Gear Mate 分母
    angle,              # 角度，单位弧度
    angle_upper,        # 角度上限
    angle_lower,        # 角度下限
    for_positioning_only,
    lock_rotation,      # 同心配合是否锁定旋转
    width_mate_option,  # Width Mate 选项
    errors,
)
```

优先复用 `scripts/sw_assembly.py`：

```python
from sw_assembly import (
    add_concentric_mate_by_cylinders,
    add_gear_mate_by_cylinders,
    collect_mate_feature_summary,
)

add_concentric_mate_by_cylinders(
    asm,
    shaft_comp,
    bearing_comp,
    radius_a=(0.004, 0.006),
    radius_b=(0.004, 0.006),
    name="shaft_bearing_concentric",
    lock_rotation=False,
)

add_gear_mate_by_cylinders(
    asm,
    small_gear_comp,
    large_gear_comp,
    teeth_a=18,
    teeth_b=26,
    radius_a=(0.004, 0.008),
    radius_b=(0.004, 0.008),
    name="small_large_gear_ratio_18_26",
)

print(collect_mate_feature_summary(asm))
```

## 配合类型枚举

本机 SW 2024 `SolidWorks.Interop.swconst.dll` 验证过的常用值：

| 值 | 名称 | 说明 |
|---:|---|---|
| 0 | `swMateCOINCIDENT` | 重合 |
| 1 | `swMateCONCENTRIC` | 同心 |
| 2 | `swMatePERPENDICULAR` | 垂直 |
| 3 | `swMatePARALLEL` | 平行 |
| 4 | `swMateTANGENT` | 相切 |
| 5 | `swMateDISTANCE` | 距离 |
| 6 | `swMateANGLE` | 角度 |
| 9 | `swMateCAMFOLLOWER` | 凸轮从动件（**原生存在**，2026-10-28 查证官方 `swMateType_e`；此前"没有 Cam Mate"的说法是错的） |
| 10 | `swMateGEAR` | 齿轮 |
| 11 | `swMateWIDTH` | 宽度 |
| 16 | `swMateLOCK` | 锁定 |
| 20 | `swMateCOORDINATE` | 坐标配合 |
| 22 | `swMateHINGE` | 铰链 |

本机实测补充：Gear Mate(10) 经 API 创建**两次挂起**（200s/900s 超时），批量自动化绕开；
凸轮从动件(9) 尚未实测，做凸轮机构真约束可优先尝试。

组件压缩状态常用值：

| 值 | 名称 | 说明 |
|---:|---|---|
| 0 | `swComponentSuppressed` | 压缩 |
| 1 | `swComponentLightweight` | 轻化 |
| 2 | `swComponentFullyResolved` | 完全解析 |
| 3 | `swComponentResolved` | 解析 |
| 4 | `swComponentFullyLightweight` | 完全轻化 |

## 选择与装配体上下文

稳定顺序：

```python
from sw_assembly import (
    get_component_feature,
    get_assembly_entity,
    find_component_by_name,
    resolve_component,
    select_entities_for_mate,
)

component_a = find_component_by_name(asm, "shaft")
component_b = find_component_by_name(asm, "bearing")
resolve_component(component_a)
resolve_component(component_b)

feature_a = get_component_feature(component_a, ["前视基准面", "Front Plane"])
feature_b = get_component_feature(component_b, ["前视基准面", "Front Plane"])
entity_a = get_assembly_entity(component_a, feature_a)
entity_b = get_assembly_entity(component_b, feature_b)

select_entities_for_mate(asm, entity_a, entity_b, mark=1)
```

注意事项：

- 先解析组件，再选择实体；`SetSuppression2()` 可能清空选择集。
- 第一个实体用 `Select2(False, 1)`，第二个实体用 `Select2(True, 1)` 追加。
- 创建 Mate 前检查 `SelectionManager.GetSelectedObjectCount2(-1) == 2`。
- Gear Mate、普通 Mate 选择标记通常用 `1`；Width Mate 用 `16`，Cam-Follower 用 `8`。

## 圆柱面识别

轴、孔、齿轮轴线常通过最大圆柱面识别：

```python
from sw_assembly import find_largest_cylinder_face

face = find_largest_cylinder_face(
    gear_component,
    min_radius=0.004,
    max_radius=0.008,
)
```

底层逻辑：

```python
from sw_connect import safe_get_com_member

bodies = part.GetBodies2(0, False)
for body in bodies:
    for face in body.GetFaces():
        surface = face.GetSurface()
        # 必须走 safe_get_com_member：动态派发下 getattr(surface, "IsCylinder")
        # 会抛 com_error「找不到成员」，异常一旦被吞掉就是"0 个圆柱面"的静默空结果。
        if safe_get_com_member(surface, "IsCylinder"):
            params = surface.CylinderParams   # 这个用 getattr 正常
            radius = params[6]  # 单位米
```

同一坑也适用于 `IsPlane`、`IsCone`、`IsTorus` 等所有 `Is*` 曲面类型判定；而 `CylinderParams` / `PlaneParams` / `ConeParams` 这些参数属性用 `getattr` 取是正常的。规律：**`Is*` 走 `safe_get_com_member`，`*Params` 走普通属性**。

实测（SolidWorks 2024，pywin32 动态派发）：对同一零件的 6 个面逐个调用，`getattr(surface, "IsPlane")` 6/6 抛 `com_error(-2147352573, '找不到成员')`；换成 `safe_get_com_member` 后正常返回 True/False。

## 按包围盒匹配配合面

外部数据（STEP 装配、Fusion/URDF 导出）通常会给出"哪个面参与配合"，最可靠的做法是**按面的包围盒做唯一命中**，不要按半径/面积猜。

直接用封装好的函数：

```python
from sw_assembly import find_face_by_box

# target_box = [xmin, ymin, zmin, xmax, ymax, zmax]，单位米，零件坐标系
f1 = find_face_by_box(comp1, box_from_data_one)
f2 = find_face_by_box(comp2, box_from_data_two)
if f1 and f2:
    asm.ClearSelection2(True)
    f1.Select2(False, 1)
    f2.Select2(True, 1)
    mate = asm.AddMate5(SW_MATE_CONCENTRIC, 0, False, 0, 0, 0, 0, 0, 0, 0, 0, False, False, 0, err)
```

底层逻辑（自己写时的要点）：

```python
def box_of(face):
    v = face.GetBox            # 属性，不是方法
    return [float(x) for x in (v() if callable(v) else v)]

def match_face_by_box(comp, target_box, tol=1e-4):
    part = comp.GetModelDoc2
    hits = []
    for body in (part.GetBodies2(0, False) or []):
        for face in (body.GetFaces() or []):
            b = box_of(face)
            if max(abs(b[i] - target_box[i]) for i in range(6)) < tol:
                hits.append(face)
    return comp.GetCorresponding(hits[0]) if len(hits) == 1 else None
```

要点：

- **`IFace2.GetBox` 返回的是零件坐标系**，和数据源里 entity 的 `bounding_box` 同一套坐标，可以直接比。
- **必须要求唯一命中**（`len(hits) == 1`）。对称件上常有多个包围盒相同的面，命中不唯一就说明这个判据在这台件上失效，应报出来而不是随便挑一个。
- **实测对比**：按"半径范围内面积最大的圆柱面"猜 → 配合求解后组件漂移 **15.6 mm**；改成按包围盒匹配 → 唯一命中、误差 **0.0000 mm**。
- 匹配完的面要 `comp.GetCorresponding(face)` 转成装配体上下文的实体，才能传给 `AddMate5`。
- **每个 `AddMate5` 之后面指针全部失效**（求解器会移动组件），必须重新取面，不能跨 Mate 复用缓存。

## 同轴配合保留轴向自由度

同心/同轴 Mate 只锁径向两个自由度，**轴向是自由的**。装配体一旦求解，SolidWorks 会把零件沿轴推到一个它自己认为合理的位置。

实测：把组件按原始位姿精确摆好、再加同心 Mate，求解后某台装配体的包围盒从 0 偏差变成 **95.21 mm** 偏差——零件被轴向推走。

所以：

- **判据要用"径向偏差"，不是"组件平移总差"**。平移里包含正常的轴向滑动，用总差判会把成功案例误判成失败。
  ```python
  delta = [now[i] - expected[i] for i in range(3)]
  axial = sum(delta[i] * axis[i] for i in range(3))
  radial = math.sqrt(max(0.0, sum(v * v for v in delta) - axial * axial))
  ok = radial < 1e-4          # 径向≈0 即配合成立，轴向非 0 是允许的
  ```
- **要真定位就必须补轴向约束**：再给一个距离 Mate（距离 = 数据源里的 offset），或者干脆不加 Mate、直接按位姿摆放 + 全选 Fix（见 `references/review.md` 的验收门槛）。
- 别指望 `align` / `flip` / `lock_rotation` 参数能救方向问题：两圆柱轴向相反时 SolidWorks 会强制翻转 180°，这几个参数都无效。

## 可以在软件里操作吗

可以，但前提是装配体里有真实 Mate 且没有被过约束：

- 在 SolidWorks 打开 `.SLDASM`。
- 使用 `移动零部件` / `Move Component` 拖动轴、齿轮或上盖。
- 如果 Gear Mate 正确，拖动一个齿轮绕轴旋转时，另一个齿轮会按比例反向/同向联动。
- 如果拖不动，优先检查：零件是否固定、同心 Mate 是否勾了 Lock Rotation、是否额外用平面 Mate 把旋转件锁死、Mate 是否报错或过定义。

脚本生成的 GIF/预览视频只能证明“可以被驱动到多个位置”；真正可交互要靠 SolidWorks Mate 求解器和自由度。

## 干涉与运动边界

Gear Mate 不负责防止齿轮实体穿透；它只建立旋转比例关系。需要几何安全时：

- 用 `InterferenceDetection` 或 GUI 中的干涉检查验证。
- 用 Limit Angle / Limit Distance 限制行程。
- 用 Mate Controller 保存几个关键位置并生成 Motion Study 动画。

```python
interference = asm.InterferenceDetection
interference.TreatSubAssembliesAsComponents = False
interference.TreatCoincidenceAsInterference = False
interference.Done()
count = interference.GetInterferenceCount()
```

## 大型装配体建议

- 超过 20 个零件时，分批生成零件，保存后关闭文档，再统一装配。
- 添加组件前确认所有路径存在，失败组件要记录清单。
- 自动化脚本中关闭不必要的图形刷新；最终恢复、重建、导出预览。
- 文档太多或 COM 报内部错误时，先 `CloseAllDocuments(False)`；仍不稳定再重启 SolidWorks 主进程。

### 每加完一个组件就释放零件文档（>40 件的必用配方）

`AddComponent5` 会把零件文档留在内存里。组件越多、常驻文档越多，到某个点就会崩。

实测（SolidWorks 2024）：46 件和 73 件的装配体都在**第 45 个组件**处抛
`com_error(-2147023174, 'RPC 服务器不可用')`，随后 SolidWorks 进程直接退出。
现象很像"件数上限"，其实和件数无关——把文档压住以后，**152 件 / 148 条配合的整机一次跑通**（298 s，与官方整机 STEP 偏差 1.17 mm）。

```python
from sw_assembly import add_component, release_part_document

for occ in occurrences:
    comp = add_component(asm, sld_path, sw=sw)
    apply_component_transform_axes(comp, ...)        # 先设好位姿
    release_part_document(sw, sld_path, keep_title=asm.GetTitle)   # 再关掉零件文档
```

要点：

- **顺序不能反**。先设位姿再关文档；先关文档会让后续 `comp.Transform2` 拿不到对象。
- `keep_title` 传装配体标题，防止把装配体本身关掉。
- 加了这一步之后，不需要"每 3–4 台重启一次 SolidWorks"，也不需要进程级隔离。
- 关掉文档不影响已放置的组件：位姿已写进组件，重开文档时按需加载。

## 约束状态判定

批量装配后要判断"是不是真的完全定义了"，别只看有没有报错：

```python
from sw_connect import get_com_member

comp.IsFixed                    # 属性，直接可读，True = 已固定
comp.GetConstrainedStatus()     # 3 = 完全定义/固定，2 = 欠定义
```

枚举含义用对照实测标定过（全新装配体）：

| 组件 | `IsFixed` | `GetConstrainedStatus` |
|---|---|---|
| 第一个组件（SolidWorks 默认固定） | True | **3** |
| 第二个组件（默认浮动） | False | **2** |

所以验收条件是：**全部组件 `IsFixed is True` 且 `GetConstrainedStatus() == 3`**。
注意 `IsFixed` 在 pywin32 动态派发下是**属性**，写成 `comp.IsFixed()` 会报 not callable。

### Fix（完全定义）不等于位姿保证——验收必须回读位姿

2026-10-28 受控实验（凸轮机构导向副，4 组件，3 同心 + Fix）：

- 把 3 个已 Fix 的零件用 API 写 `Transform2` 挪偏 (40, −25, 18) mm，`ForceRebuild3` 后
  **没有回位**，偏差恰为扰动量 50.49 mm。
- 结论：**API 直接写 `Transform2` 会压过"完全定义"**——位姿的最终保证来自
  "约束 + 求解"路径，不是固定标志本身。`IsFixed`/DoF=3 只能说明约束完备，
  不能证明当前位姿正确。
- 因此批量装配的验收**必须逐组件回读 `Transform2` 平移并与设计值比对**（径向偏差 ≈0），
  不能只看 `IsFixed` 或约束状态。

对应的正面结论（改几何跟随实验）：同心配合在零件几何变更后（导套 60→75mm，在
**临时副本**里改）重开装配，径向偏差 [0.0, 0.0]、轴线平行度 1.000——真约束会自动跟随，
这是纯坐标摆位做不到的。改几何实验务必用副本目录，见 troubleshooting"内部 ID 不匹配"条目。

