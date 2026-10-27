# 零件建模 API 参考

## 草图操作

### 基准面选择

```python
# 英文版
model.Extension.SelectByID2("Front Plane", "PLANE", 0, 0, 0, False, 0, None, 0)
# 中文版
model.Extension.SelectByID2("前视基准面", "PLANE", 0, 0, 0, False, 0, None, 0)
```

基准面对应关系：
| 英文 | 中文 | 法线方向 |
|---|---|---|
| Front Plane | 前视基准面 | Z 轴 |
| Top Plane | 上视基准面 | Y 轴 |
| Right Plane | 右视基准面 | X 轴 |

**草图局部坐标 → 模型坐标的映射（实测，SolidWorks 2024 + pywin32 动态派发）：**

| 基准面 | 草图 u → 模型 | 草图 v → 模型 | 法线（拉伸方向） |
|---|---|---|---|
| Front Plane | X | Y | +Z |
| Top Plane | X | Z | **±Y（见下）** |
| Right Plane | **−Z** | Y | ±X |

两个最容易踩的坑：

1. **Right Plane 的 u 是负向**。想在 (y=0, z=170) 打一个沿 X 的横孔，草图圆心要写
   `sketch_circle(m, mm(-170), 0, ...)`——写 `(0, mm(170))` 圆会跑到 y=170 米之外，
   切除/拉伸都返回 None，而且报错完全看不出位置错了。
2. **Top Plane 的 v 是 Z**（不是 Y）：`sketch_corner_rectangle(m, mm(x1), mm(z1), mm(x2), mm(z2))`
   画出来的是 XZ 剖面，沿 Y 拉伸——这正是箱体类零件"剖分面"建模的正确姿势。

**拉伸/切除方向语义（实测）：**

| 调用 | direction=True | direction=False |
|---|---|---|
| `extrude_boss(depth)` | 向 **−法线** | 向 +法线 |
| `extrude_offset_start(depth, start_offset)` | 从 start_offset 向 **−法线** | 从 start_offset 向 +法线 |
| `extrude_cut(depth)` | 向 −法线 | 向 +法线 |

经验法则：

- **想要"从 z=a 到 z=b (b>a) 的圆柱"**：`extrude_offset_start(depth=b−a, start_offset=b, direction=True)`。
  即 **start_offset 传高端值，depth 传长度**。
- **cut 的方向语义与 boss 相反的直觉坑**：材料在 −Z 时 boss 用 `direction=True`，
  但 cut 要用 **`direction=False`** 才切得到；材料在 +Z 时 cut 用 `direction=True`。
  `flip=True` 实测反而两个方向都失败，不要依赖它。
- **拿不准就先探针**：建一段圆柱打印 `GetPartBox`，确认方向后再写正式代码——
  两个项目的调试时间主要都耗在这里。
- **`GetPartBox` 必须在 `SaveAs3` 之前调用**：保存后文档句柄失效，再取包围盒报
  `<unknown>.GetPartBox`。`save()` 类辅助函数请先取 box 再存盘。

### 草图几何体方法速查

| 方法 | 参数 | 说明 |
|---|---|---|
| `CreateLine(x1,y1,z1, x2,y2,z2)` | 起点终点坐标（米） | 直线 |
| `CreateCircleByRadius(cx,cy,cz, r)` | 圆心+半径 | 圆 |
| `CreateCenterRectangle(cx,cy,cz, rx,ry,rz)` | 中心+角点 | 中心矩形 |
| `CreateCornerRectangle(x1,y1,z1, x2,y2,z2)` | 对角点 | 角点矩形 |
| `CreateArc(cx,cy,cz, x1,y1,z1, x2,y2,z2, dir)` | 圆心+起终点+方向 | 圆弧 |
| `CreatePolygon(cx,cy,cz, sx,sy,sz, sides, inscr)` | 中心+顶点+边数 | 正多边形 |
| `CreateEllipse(cx,cy,cz, mx,my,mz, r)` | 中心+长轴端点+短轴半径 | 椭圆 |
| `CreateSpline2(pointArray, closed)` | 点数组+是否闭合 | 样条曲线 |
| `CreateSketchSlot(type, w, w, ...)` | 类型+宽度+端点 | 槽口 |

### 草图约束

```python
# 选中实体后添加约束
model.SketchAddConstraints("sgHORIZONTAL")
```

常用约束类型：`sgHORIZONTAL`, `sgVERTICAL`, `sgCOLINEAR`, `sgPARALLEL`, `sgPERPENDICULAR`, `sgTANGENT`, `sgCONCENTRIC`, `sgEQUAL`, `sgSYMMETRIC`, `sgMIDPOINT`, `sgCOINCIDENT`。`sgFIXED` 只用于明确固定参考的场景，不得批量使用以伪造参数化草图的完全定义。

坐标只定义初始几何位置，不等同于驱动尺寸和几何关系。圆/矩形基础草图可在编辑态调用 `sw_sketch_quality.fully_define_sketch(model)`；该方法用实际原点补充尺寸与关系，并回读约束状态、尺寸数量和 Fix 数量。复杂草图优先人工定义设计参数，再调用 `inspect_model_sketches(model)` 检查；没有尺寸、悬空或证据不足均需要复核。

### 原生阵列

`sw_part.linear_pattern()` 委托 `sw_pattern`，选择精确母特征及与方向向量平行的真实直边，并使用 20 参数 `FeatureLinearPattern4`。方向向量无量纲，间距仍以米传入；选择失败立即停止。圆周阵列使用明确的轴名称/实体，不能把 `InsertAxis2` 当成 FeatureManager 成员。SolidWorks 2026 真机已验证保存重开后改变厚度、孔数与孔中心位置保持不变。

### 草图选择稳定策略

优先使用 `sw_part.sketch()` 上下文或 `end_sketch()` 返回值，不要手写 `SelectByID2("Sketch1", "SKETCH", ...)` 作为唯一选择方式。SolidWorks 2024 中文版反馈过 SKETCH 名称选择持续返回 `False` 的情况。

推荐：

```python
with sketch(model, "Front Plane") as sketch_name:
    sketch_circle(model, 0, 0, mm(25))
feature = extrude_boss(model, sketch_name, mm(50))
```

手动流程：

```python
start_sketch(model, "Front Plane")
sketch_circle(model, 0, 0, mm(25))
sketch_ref = end_sketch(model)
feature = extrude_boss(model, sketch_ref, mm(50))
```

内部规则：`extrude_boss()` / `extrude_cut()` 会按“活动草图引用 -> 创建时缓存引用 -> FeatureByName -> SelectByID2("SKETCH")”顺序选择草图；不再把残留选择集当作草图已选中的证据。

## 特征操作

### FeatureExtrusion3 参数详解

```python
feature_mgr.FeatureExtrusion3(
    Sd,           # bool: 单方向（True）或双方向（False）
    Flip,         # bool: 翻转切割方向
    Dir,          # bool: 翻转挤出方向
    T1,           # int: 终止条件1 (见下表)
    T2,           # int: 终止条件2（双方向时使用）
    D1,           # float: 深度1（米）
    D2,           # float: 深度2
    Dchk1,        # bool: 拔模1
    Dchk2,        # bool: 拔模2
    Ddir1,        # bool: 拔模方向1向外
    Ddir2,        # bool: 拔模方向2向外
    Dang1,        # float: 拔模角度1（弧度）
    Dang2,        # float: 拔模角度2
    OffsetReverse1, # bool: 偏移反转1
    OffsetReverse2, # bool: 偏移反转2
    TranslateSurface, # bool: 平移曲面
    Merge,        # bool: 合并结果
    UseFeatScope, # bool: 自动选择实体范围
    UseAutoSelect, # bool: 自动选择
    T0,           # bool:
    StartOffset   # float: 起始偏移
)
```

终止条件枚举：
| 值 | 名称 | 说明 |
|---|---|---|
| 0 | swEndCondBlind | 给定深度 |
| 1 | swEndCondThroughAll | 完全贯穿 |
| 2 | swEndCondThroughAllBoth | 两侧贯穿 |
| 5 | swEndCondUpToSurface | 成形到一曲面 |
| 6 | swEndCondMidPlane | 两侧对称 |
| 7 | swEndCondUpToBody | 成形到实体 |

### FeatureCut4 参数详解

与 FeatureExtrusion3 类似，但用于切除操作。额外参数：
- `NormalCut`: 法向切除
- `FlipSide`: 翻转切除侧

### FeatureRevolve2 参数

```python
feature_mgr.FeatureRevolve2(
    SingleDir,     # bool: 单方向
    IsSolid,       # bool: 实体旋转（True）
    IsThin,        # bool: 薄壁旋转
    IsCut,         # bool: 切除旋转
    ReverseDir,    # bool: 反转方向
    BothDirectionUpToSameEntity, # bool
    Dir1Type,      # int: 终止条件（0=Blind）
    Dir2Type,      # int
    Dir1Angle,     # float: 旋转角度（弧度）
    Dir2Angle,     # float
    OffsetReverse1, # bool
    OffsetReverse2, # bool
    OffsetDistance1, # float
    OffsetDistance2, # float
    ThinType,      # int: 薄壁类型
    ThinThickness1, # float
    ThinThickness2, # float
    Merge,         # bool
    UseFeatScope,  # bool
    UseAutoSelect  # bool
)
```

### 阶梯轴建模推荐流程

轴类零件优先用“半剖轮廓 + 中心线 + 旋转凸台”一次生成主体，不要逐段拉伸圆柱再合并。这样特征树更短，轴肩位置和总长也更容易由参数表控制。

推荐做法：

1. 用毫米参数表描述轴段：`start_mm`、`end_mm`、`diameter_mm`。
2. 在 `Front Plane` 上画中心线作为旋转轴。
3. 按轴段半径生成一条闭合半剖轮廓。
4. 调用 `FeatureRevolve2(..., 2*pi, ...)` 后立即检查返回特征不是 `None`。
5. 将公差、形位公差、粗糙度作为工程要求记录到属性或交付说明，不要直接扰动名义实体尺寸。

### 螺旋螺纹（扫掠配方与已验证边界）

梯形/三角螺纹用 `sweep_profile_helix`（3D 样条路径 + 引导线，见 `sw_part.py`）。
**实测边界（SolidWorks 2024）**：

| 截面 | 结果 |
|---|---|
| 窄梯形（径向 3mm × 轴向 2~3.4mm，Tr32×6 牙型） | **求解器拒绝**，换相位/采样数/引导线顺序均无效 |
| 圆截面 R1.6 @ r14.5，引导线 (12.9, 16.1)，points_per_turn=90 | **稳定通过**，高度 12mm 与 72mm（12 圈）都成功 |

可用配方（螺杆 Tr32×6，芯轴 φ26，螺牙外缘 32.2≈大径 32）：

```python
from sw_part import sketch, sketch_circle, sweep_profile_helix

with sketch(m, "Front Plane") as s:
    sketch_circle(m, mm(14.5), 0, mm(1.6))          # 截面形心在 r=14.5
f = sweep_profile_helix(m, s, mm(14.5), mm(6), mm(110),   # 螺距 6，长 110
                        (mm(12.9), mm(16.1)), hand="right",
                        points_per_turn=90)
```

要点：

- **引导线半径取截面外缘**，与截面实际占据的半径范围一致；给与路径同半径的引导线是退化配置。
- 梯形牙型建不出来时，退而求其次用圆截面（外缘对齐名义大径），并在交付说明里声明"牙型简化"。
- 螺母内螺纹不再另建螺旋切除：内孔给光孔（直径 = 螺杆大径 + 0.4mm）即可保证装配不干涉，
  啮合外观由螺杆螺牙表达；此简化必须在交付报告里声明。
- 已知限制（本函数说明第 4 条）仍适用：**只扫掠一次（相位 0）**，不要碰 288°/297.3° 附近的相位。

最小模式：

```python
segments = [
    {"start": 0, "end": 43, "diameter": 56},
    {"start": 43, "end": 113, "diameter": 65},
]

with sketch(model, "Front Plane") as sketch_name:
    model.SketchManager.CreateCenterLine(mm(-5), 0, 0, mm(120), 0, 0)
    points = [(0, 0), (0, 28), (43, 28), (43, 32.5), (113, 32.5), (113, 0), (0, 0)]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        model.SketchManager.CreateLine(mm(x1), mm(y1), 0, mm(x2), mm(y2), 0)

model.ClearSelection2(True)
model.Extension.SelectByID2(sketch_name, "SKETCH", 0, 0, 0, False, 0, create_empty_dispatch_variant(), 0)
feature = model.FeatureManager.FeatureRevolve2(
    True, True, False, False, False, False,
    0, 0, 2 * math.pi, 0,
    False, False, 0, 0, 0, 0, 0,
    True, True, True,
)
if feature is None:
    raise RuntimeError("阶梯轴旋转主体创建失败")
```

### 圆柱表面长圆槽

图纸中的键槽、长圆槽、局部沉槽如果位于圆柱外表面，不要直接在中心基准面上切除。中心面切除容易把轴“剖开”，预览看起来像大平面开口，而不是表面凹槽。

稳定做法：

1. 按槽所在圆柱半径创建平行于 `Front Plane` 的偏置参考面，偏置量约等于目标圆柱半径。
2. 在该切向参考面上用 `sketch_slot()` 画长圆槽。
3. 用盲切深度表达槽深；先尝试一个方向，失败再翻转 `flip`。
4. 创建成功后隐藏临时参考面，避免预览图被橙色基准面干扰。

```python
plane = model.FeatureManager.InsertRefPlane(8, mm(radius_mm), 0, 0, 0, 0)
plane.Name = "Plane_Keyway_Tangent"

model.Extension.SelectByID2(plane.Name, "PLANE", 0, 0, 0, False, 0, create_empty_dispatch_variant(), 0)
model.SketchManager.InsertSketch(True)
sketch_name = model.SketchManager.ActiveSketch.Name
sketch_slot(model, mm(x1), 0, mm(x2), 0, mm(slot_width_mm / 2))
model.SketchManager.InsertSketch(True)

feature = extrude_cut(model, sketch_name, mm(depth_mm), direction=True, flip=True)
if feature is None:
    feature = extrude_cut(model, sketch_name, mm(depth_mm), direction=True, flip=False)
if feature is None:
    raise RuntimeError("圆柱表面长圆槽创建失败")
```

注意：`CreateSketchSlot` 参数中 `radius` 是槽宽相关参数，实际表现需用局部测试验证。对制造精确槽宽要求高时，导出后用测量或工程图复核。

### 圆角/倒角

```python
# 圆角 FeatureFillet 参数
feature_mgr.FeatureFillet(
    Options,   # int: 195 = 常用默认值
    R1,        # float: 半径（米）
    R2,        # float: 第二半径
    R3,        # float: 第三半径
    Rarray1,   # variant: 半径数组1
    Rarray2,   # variant: 半径数组2
    Rarray3    # variant: 半径数组3
)

# 倒角 InsertFeatureChamfer
feature_mgr.InsertFeatureChamfer(
    Options,     # int: 4 = 角度距离
    ChamferType, # int: 1 = 等距离
    Width,       # float: 距离（米）
    Angle,       # float: 角度（弧度）
    OtherDist,   # float: 另一距离
    VertexDist1, # float
    VertexDist2, # float
    VertexDist3  # float
)
```

### 圆角/倒角当前可靠边界

圆角和倒角的核心难点不是 API 参数本身，而是**稳定选择目标边线/面**。不同 SolidWorks 版本、界面语言、特征顺序和草图轮廓都会改变自动命名的 `Edge`/`Face`，因此不要把“随机坐标 SelectByID2 选边 + FeatureFillet”当成高可靠流程。

推荐策略：

1. 基准 demo 和无人值守批量脚本不要把圆角/倒角作为成功标准。
2. 需要圆角时，优先在建模阶段用更稳定的草图轮廓近似，例如圆弧、槽口、圆角矩形草图。
3. 必须做特征圆角/倒角时，先通过 body/face/edge 枚举按几何条件过滤目标边，再选择实体对象调用特征，不要依赖临时的 `Edge1` 名称。
4. 圆角失败应降级为保留直边模型，并在审查报告或最终说明中标注“外观圆角未应用”，不要让模型生成流程整体失败。

## SelectByID2 实体类型

| 类型字符串 | 说明 |
|---|---|
| `"PLANE"` | 基准面 |
| `"FACE"` | 面 |
| `"EDGE"` | 边线 |
| `"VERTEX"` | 顶点 |
| `"SKETCH"` | 草图 |
| `"BODYFEATURE"` | 实体特征 |
| `"COMPONENT"` | 组件 |
| `"AXIS"` | 轴线 |
| `"SKETCHSEGMENT"` | 草图线段 |
| `"SKETCHPOINT"` | 草图点 |
| `"EXTSKETCHSEGMENT"` | 外部草图线段 |
| `"REFERENCECURVES"` | 参考曲线 |
