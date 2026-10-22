"""
SolidWorks 装配体操作工具。

本模块优先封装装配体中最容易踩坑的 COM 流程：组件解析、装配体上下文实体映射、
真实 Mate 创建、运动型装配验证。SolidWorks API 使用米作为长度单位。
"""
import math
import os
from pathlib import Path

try:
    from .sw_preflight import import_com_dependencies
    from .sw_connect import get_com_member, open_document, safe_get_com_member
except ImportError:
    from sw_preflight import import_com_dependencies
    from sw_connect import get_com_member, open_document, safe_get_com_member

pythoncom, _win32com, VARIANT = import_com_dependencies()


SW_MATE_COINCIDENT = 0
SW_MATE_CONCENTRIC = 1
SW_MATE_PARALLEL = 3
SW_MATE_DISTANCE = 5
SW_MATE_GEAR = 10
SW_ADD_MATE_ERROR_UNKNOWN = 0
SW_ADD_MATE_ERROR_NO_ERROR = 1
SW_COMPONENT_SUPPRESSED = 0
SW_COMPONENT_LIGHTWEIGHT = 1
SW_COMPONENT_FULLY_RESOLVED = 2
SW_COMPONENT_RESOLVED = 3
SW_SOLID_BODY = 0
PLANE_NAME_ALIASES = {
    "Front Plane": ["Front Plane", "前视基准面"],
    "Top Plane": ["Top Plane", "上视基准面"],
    "Right Plane": ["Right Plane", "右视基准面"],
    "前视基准面": ["前视基准面", "Front Plane"],
    "上视基准面": ["上视基准面", "Top Plane"],
    "右视基准面": ["右视基准面", "Right Plane"],
}


def _empty_callout():
    """创建兼容 SelectByID2 的空 Callout 参数。"""
    return VARIANT(pythoncom.VT_DISPATCH, None)


def _select_by_id(extension, entity_name, entity_type, append=False, mark=0):
    """
    统一封装 SelectByID2，避免 None 在部分 SolidWorks 版本中触发类型不匹配。

    参数:
        extension: ModelDocExtension 对象
        entity_name: 实体名称
        entity_type: 实体类型字符串
        append: 是否追加选择
        mark: 选择标记

    返回:
        bool
    """
    return extension.SelectByID2(
        entity_name, entity_type, 0, 0, 0, append, mark, _empty_callout(), 0
    )


def _as_alias_list(aliases):
    """将字符串或序列统一成非空名称列表。"""
    if isinstance(aliases, str):
        return [aliases]
    names = [str(item) for item in aliases if str(item)]
    if not names:
        raise ValueError("aliases 不能为空")
    return names


def _active_solidworks_app():
    """获取当前运行中的 SolidWorks 应用对象。"""
    try:
        return _win32com.GetActiveObject("SldWorks.Application")
    except Exception:
        return None


def _activate_model_document(sw, model):
    """激活给定文档，兼容 ActivateDoc3 的 by-ref error 参数。"""
    if sw is None or model is None:
        return False
    title = safe_get_com_member(model, "GetTitle")
    if not title:
        return False
    errors = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    try:
        return sw.ActivateDoc3(title, False, 0, errors) is not None
    except Exception:
        try:
            sw.ActivateDoc2(title, False, errors)
            return True
        except Exception:
            return False


def _add_component5(asm_model, component_path, config_name, x, y, z):
    """调用 AddComponent5 的 SW2024 兼容签名。"""
    add_component5 = getattr(asm_model, "AddComponent5", None)
    if not add_component5:
        return None
    return add_component5(
        component_path,
        0,              # swAddComponentConfigOptions_e: 使用指定/默认配置
        "",
        False,
        config_name or "",
        float(x),
        float(y),
        float(z),
    )


def _add_component4(asm_model, component_path, config_name, x, y, z):
    """调用 AddComponent4。"""
    return asm_model.AddComponent4(
        component_path,
        config_name or "",
        float(x),
        float(y),
        float(z),
    )


def add_component(asm_model, part_path, x=0, y=0, z=0, config_name="", sw=None):
    """
    向装配体添加零部件

    参数:
        asm_model: IAssemblyDoc (装配体文档)
        part_path: 零件/子装配体文件路径
        x, y, z: 放置位置（米）
        config_name: 配置名称，空字符串使用默认配置
        sw: 可选 SolidWorks 应用对象；用于 AddComponent 失败后自动打开零件并切回装配体。

    返回:
        IComponent2 对象
    """
    component_path = os.path.abspath(os.path.expandvars(os.path.expanduser(str(part_path))))
    if not os.path.exists(component_path):
        raise FileNotFoundError(f"组件文件不存在: {component_path}")

    component = None
    errors = []

    try:
        # SW2024 中文版 + pywin32 下 AddComponent4 可能返回 None；
        # AddComponent5 的 8 参数签名更稳定。
        component = _add_component5(asm_model, component_path, config_name, x, y, z)
    except Exception as exc:
        errors.append(f"AddComponent5: {exc}")

    if component is None:
        try:
            component = _add_component4(asm_model, component_path, config_name, x, y, z)
        except Exception as exc:
            errors.append(f"AddComponent4: {exc}")

    if component is None:
        sw = sw or _active_solidworks_app()
        if sw is not None:
            try:
                opened = open_document(sw, component_path, read_only=False, silent=True)
                if opened is None:
                    errors.append("OpenDoc6: 返回 None")
                elif _activate_model_document(sw, asm_model):
                    component = _add_component5(
                        asm_model,
                        component_path,
                        config_name,
                        x,
                        y,
                        z,
                    )
                    if component is None:
                        errors.append("open_then_AddComponent5: 返回 None")
                else:
                    errors.append("ActivateDoc: 无法重新激活装配体")
            except Exception as exc:
                errors.append(f"open_then_AddComponent5: {exc}")
        else:
            errors.append("GetActiveObject(SldWorks.Application): 返回 None")

    if component:
        print(f"已添加组件: {component_path}")
    else:
        detail = "；".join(errors) if errors else "AddComponent5/AddComponent4 均返回 None"
        print(f"添加组件失败: {component_path} ({detail})")
    return component


def resolve_component(component, state=SW_COMPONENT_FULLY_RESOLVED):
    """
    @brief 将组件解析为 Resolved/FullyResolved 状态。
    @param component IComponent2 对象。
    @param state swComponentSuppressionState_e，默认 2=swComponentFullyResolved。
    @return SetSuppression2 的返回值；若主状态失败会尝试 3=swComponentResolved。
    """
    if component is None:
        raise ValueError("component 不能为空")
    try:
        return component.SetSuppression2(state)
    except Exception:
        if state != SW_COMPONENT_RESOLVED:
            return component.SetSuppression2(SW_COMPONENT_RESOLVED)
        raise


def get_component_model(component, resolve=True, raise_on_error=True):
    """
    @brief 获取组件引用的零件/子装配文档。
    @param component IComponent2 对象。
    @param resolve 是否先解析组件；轻化或压缩组件常导致 GetModelDoc2 返回 None。
    @param raise_on_error 失败时是否抛异常。
    @return IModelDoc2；失败且 raise_on_error=False 时返回 None。
    """
    if component is None:
        if raise_on_error:
            raise ValueError("component 不能为空")
        return None
    if resolve:
        resolve_component(component)
    model = safe_get_com_member(component, "GetModelDoc2")
    if model is None and raise_on_error:
        name = safe_get_com_member(component, "Name2")
        raise RuntimeError(f"组件未解析或未加载，无法读取零件文档: {name}")
    return model


def get_component_feature(component, aliases, resolve=True):
    """
    @brief 按一组候选名称查找组件内部特征。
    @param component IComponent2 对象。
    @param aliases 特征候选名，例如 ["前视基准面", "Front Plane"]。
    @param resolve 是否先解析组件。
    @return IFeature 对象。
    """
    model = get_component_model(component, resolve=resolve)
    names = []
    for alias in _as_alias_list(aliases):
        for candidate in PLANE_NAME_ALIASES.get(alias, [alias]):
            if candidate not in names:
                names.append(candidate)
    for name in names:
        feature = safe_get_com_member(model, "FeatureByName", name)
        if feature:
            return feature
    comp_name = safe_get_com_member(component, "Name2")
    raise RuntimeError(f"组件 {comp_name} 缺少特征: {names}")


def get_assembly_entity(component, feature_or_face):
    """
    @brief 将零件文档内对象映射到当前组件实例的装配体上下文。
    @param component IComponent2 对象。
    @param feature_or_face 零件内的 IFeature、IFace2、ISketchSegment 等对象。
    @return 装配体上下文对象，可用于 Select2 后创建 Mate。
    """
    entity = component.GetCorresponding(feature_or_face)
    if entity is None:
        comp_name = safe_get_com_member(component, "Name2")
        raise RuntimeError(f"无法映射装配体上下文实体: {comp_name}")
    return entity


def get_component_feature_entity(component, aliases, resolve=True):
    """
    @brief 查找组件内部特征并映射为装配体上下文实体。
    @param component IComponent2 对象。
    @param aliases 特征候选名。
    @param resolve 是否先解析组件。
    @return 装配体上下文实体。
    """
    feature = get_component_feature(component, aliases, resolve=resolve)
    return get_assembly_entity(component, feature)


def find_largest_cylinder_face(component, min_radius=0.0, max_radius=None, resolve=True):
    """
    @brief 查找组件中指定半径范围内面积最大的圆柱面。
    @param component IComponent2 对象。
    @param min_radius 最小半径，单位米。
    @param max_radius 最大半径，单位米；None 表示不限制。
    @param resolve 是否先解析组件。
    @return 装配体上下文中的 IFace2 圆柱面。
    """
    part = get_component_model(component, resolve=resolve)
    max_radius = float("inf") if max_radius is None else float(max_radius)
    best_face = None
    best_area = -1.0
    bodies = safe_get_com_member(part, "GetBodies2", SW_SOLID_BODY, False) or []
    for body in bodies:
        faces = safe_get_com_member(body, "GetFaces") or []
        for face in faces:
            surface = safe_get_com_member(face, "GetSurface")
            if not surface:
                continue
            try:
                if not safe_get_com_member(surface, "IsCylinder"):
                    continue
                params = safe_get_com_member(surface, "CylinderParams")
                radius = float(params[6])
                if radius < min_radius or radius > max_radius:
                    continue
                area = float(safe_get_com_member(face, "GetArea"))
                if area > best_area:
                    best_area = area
                    best_face = face
            except Exception:
                continue

    if best_face is None:
        comp_name = safe_get_com_member(component, "Name2")
        raise RuntimeError(
            f"未找到圆柱面: {comp_name}, radius=[{min_radius}, {max_radius}]"
        )
    return get_assembly_entity(component, best_face)


def select_entities_for_mate(model, entity1, entity2, mark=1):
    """
    @brief 选择两个装配体上下文实体并校验选择集数量。
    @param model IModelDoc2/IAssemblyDoc 对象。
    @param entity1 第一个 Mate 实体。
    @param entity2 第二个 Mate 实体。
    @param mark SolidWorks 选择标记，普通 Mate 通常为 1。
    @return True。
    """
    model.ClearSelection2(True)
    if not entity1.Select2(False, mark):
        raise RuntimeError("选择第一个 Mate 实体失败")
    if not entity2.Select2(True, mark):
        raise RuntimeError("选择第二个 Mate 实体失败")
    selected_count = model.SelectionManager.GetSelectedObjectCount2(-1)
    if selected_count != 2:
        model.ClearSelection2(True)
        raise RuntimeError(f"Mate 选择数量错误: selected={selected_count}, expected=2")
    return True


def add_mate5_checked(
    asm_model,
    mate_type,
    align=0,
    flip=False,
    distance=0.0,
    distance_upper=None,
    distance_lower=None,
    gear_num=0.0,
    gear_den=0.0,
    angle=0.0,
    angle_upper=None,
    angle_lower=None,
    for_positioning_only=False,
    lock_rotation=False,
    width_mate_option=0,
    name=None,
    clear_selection=True,
):
    """
    @brief 用 SolidWorks 2015+ 的 15 参数 AddMate5 创建 Mate，并检查错误码。
    @param asm_model IAssemblyDoc/IModelDoc2 装配体对象。
    @param mate_type swMateType_e 枚举值。
    @param gear_num 齿轮比分子，仅 Gear Mate 使用。
    @param gear_den 齿轮比分母，仅 Gear Mate 使用。
    @param lock_rotation 同心 Mate 是否锁定旋转；运动模型默认应为 False。
    @param name 可选 Mate 名称。
    @return Mate2 对象。
    """
    distance_upper = distance if distance_upper is None else distance_upper
    distance_lower = distance if distance_lower is None else distance_lower
    angle_upper = angle if angle_upper is None else angle_upper
    angle_lower = angle if angle_lower is None else angle_lower
    error_status = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    mate = asm_model.AddMate5(
        int(mate_type),
        int(align),
        bool(flip),
        float(distance),
        float(distance_upper),
        float(distance_lower),
        float(gear_num),
        float(gear_den),
        float(angle),
        float(angle_upper),
        float(angle_lower),
        bool(for_positioning_only),
        bool(lock_rotation),
        int(width_mate_option),
        error_status,
    )
    # swAddMateError_e 中 1=swAddMateError_NoError；0=Unknown 在部分版本也会伴随非空 Mate。
    if mate is None or int(error_status.value) not in (
        SW_ADD_MATE_ERROR_UNKNOWN,
        SW_ADD_MATE_ERROR_NO_ERROR,
    ):
        raise RuntimeError(
            f"AddMate5 失败: type={mate_type}, error_status={error_status.value}"
        )
    if name:
        try:
            mate.Name = name
        except Exception:
            pass
    if clear_selection:
        asm_model.ClearSelection2(True)
    return mate


def add_concentric_mate_by_cylinders(
    asm_model,
    component_a,
    component_b,
    radius_a=None,
    radius_b=None,
    name=None,
    lock_rotation=False,
):
    """
    @brief 通过两个圆柱面创建同心 Mate，默认保留旋转自由度。
    @param asm_model IAssemblyDoc/IModelDoc2 装配体对象。
    @param component_a 第一个组件。
    @param component_b 第二个组件。
    @param radius_a 第一个组件圆柱半径范围 (min, max)，单位米；None 表示任意。
    @param radius_b 第二个组件圆柱半径范围 (min, max)，单位米；None 表示任意。
    @param name 可选 Mate 名称。
    @param lock_rotation 是否锁定同心旋转；运动装配应保持 False。
    @return Mate2 对象。
    """
    radius_a = radius_a or (0.0, None)
    radius_b = radius_b or (0.0, None)
    face_a = find_largest_cylinder_face(component_a, radius_a[0], radius_a[1])
    face_b = find_largest_cylinder_face(component_b, radius_b[0], radius_b[1])
    select_entities_for_mate(asm_model, face_a, face_b, mark=1)
    return add_mate5_checked(
        asm_model,
        SW_MATE_CONCENTRIC,
        lock_rotation=lock_rotation,
        name=name,
    )


def add_gear_mate_by_cylinders(
    asm_model,
    component_a,
    component_b,
    teeth_a,
    teeth_b,
    radius_a=None,
    radius_b=None,
    name=None,
):
    """
    @brief 通过两个齿轮轴/孔圆柱面创建真实 Gear Mate。
    @param asm_model IAssemblyDoc/IModelDoc2 装配体对象。
    @param component_a 第一个齿轮组件。
    @param component_b 第二个齿轮组件。
    @param teeth_a 第一个齿轮齿数或等效节圆比。
    @param teeth_b 第二个齿轮齿数或等效节圆比。
    @param radius_a 第一个齿轮轴/孔圆柱半径范围，单位米。
    @param radius_b 第二个齿轮轴/孔圆柱半径范围，单位米。
    @param name 可选 Mate 名称。
    @return Mate2 对象。
    """
    if float(teeth_a) == 0 or float(teeth_b) == 0:
        raise ValueError("Gear Mate 的齿数/传动比不能为 0")
    radius_a = radius_a or (0.0, None)
    radius_b = radius_b or (0.0, None)
    face_a = find_largest_cylinder_face(component_a, radius_a[0], radius_a[1])
    face_b = find_largest_cylinder_face(component_b, radius_b[0], radius_b[1])
    select_entities_for_mate(asm_model, face_a, face_b, mark=1)
    return add_mate5_checked(
        asm_model,
        SW_MATE_GEAR,
        gear_num=float(teeth_a),
        gear_den=float(teeth_b),
        name=name,
    )


def add_revolute_joint_by_cylinders(
    asm_model,
    component_a,
    component_b,
    radius_a=None,
    radius_b=None,
    name=None,
):
    """
    @brief 创建可转动铰接轴的核心同心 Mate。

    官方 Hinge Mate 等效于同心配合加轴向定位配合；自动化时先用本函数保留
    一条旋转自由度，再用平面重合/距离 Mate 约束轴向窜动。
    """
    return add_concentric_mate_by_cylinders(
        asm_model,
        component_a,
        component_b,
        radius_a=radius_a,
        radius_b=radius_b,
        name=name,
        lock_rotation=False,
    )


_AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}


def _variant_double_array(values):
    """
    @brief 把 16 个 double 包成 VARIANT(VT_ARRAY|VT_R8)——ArrayData 唯一可靠的写入形式。
    @note 直接赋 Python 元组时 COM 会丢弃旋转块（实测：旋转保持单位阵，平移槽被写入错位值），
          本模块所有 ArrayData 写入必须经过这里。
    """
    pythoncom, _win32com, variant = import_com_dependencies()
    return variant(pythoncom.VT_ARRAY | pythoncom.VT_R8, [float(v) for v in values])


def _rotation_matrix_axis(axis, angle_rad):
    """@brief 返回行主序 3x3 旋转矩阵的 9 个元素（右手定则，p' = R·p）。"""
    index = _AXIS_INDEX.get(str(axis).upper())
    if index is None:
        raise ValueError(f"不支持的旋转轴: {axis!r}，仅支持 X/Y/Z")
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    rotation = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    a, b = [i for i in range(3) if i != index]
    rotation[a][a] = c
    rotation[a][b] = -s
    rotation[b][a] = s
    rotation[b][b] = c
    return [v for row in rotation for v in row]


def build_transform_data_axis(axis="Z", angle_rad=0.0, translation=(0.0, 0.0, 0.0)):
    """
    @brief 生成绕任意主轴旋转并平移的 MathTransform 数组。
    @param axis 旋转轴，"X"/"Y"/"Z"（不区分大小写）。
    @param angle_rad 旋转角，弧度，右手定则。
    @param translation 平移量 (tx, ty, tz)，单位米。
    @return 16 元组：前 9 个为行主序 3x3 旋转矩阵，第 10-12 为平移，第 13 为缩放 1。
    @note 返回值必须经 VARIANT(VT_ARRAY|VT_R8) 包装后再赋给 ArrayData；直接赋元组会丢失旋转。
    """
    tx, ty, tz = (float(v) for v in translation)
    return tuple(_rotation_matrix_axis(axis, angle_rad) + [tx, ty, tz, 1.0, 0.0, 0.0, 0.0])


def build_transform_data_x(tx, ty, tz, angle_rad=0.0):
    """
    @brief 生成绕 X 轴旋转并平移的 SolidWorks MathTransform 数组。
    @note 保留旧签名；等价于 build_transform_data_axis("X", angle_rad, (tx, ty, tz))。
    """
    return build_transform_data_axis("X", angle_rad, (tx, ty, tz))


def _assert_rotation(object_with_arraydata, expected, label):
    """
    @brief 回读 ArrayData 校验旋转块是否真的落地，不一致直接抛错。
    @note 这条断言是本模块的核心防线：旧实现无论写入是否成功都 return True，
          导致"返回成功但位姿没变"的静默故障。
    """
    try:
        actual = [float(v) for v in object_with_arraydata.ArrayData][:9]
    except Exception as exc:
        raise RuntimeError(f"{label}: 无法回读 Transform2.ArrayData: {exc}") from exc
    worst = max(abs(a - b) for a, b in zip(actual, expected))
    if worst > 1e-6:
        raise RuntimeError(
            f"{label}: 旋转未生效（最大偏差 {worst:.6g}）。期望 {['%.6f' % v for v in expected]}，"
            f"实际 {['%.6f' % v for v in actual]}。"
            "常见原因：ArrayData 用普通元组赋值被 COM 丢弃，必须用 VARIANT(VT_ARRAY|VT_R8)。"
        )


def build_transform_data_matrix(rotation, translation=(0.0, 0.0, 0.0)):
    """
    @brief 由任意行主序 3x3 旋转矩阵生成 MathTransform 数组。
    @param rotation 9 个元素，行主序（p' = R·p）。
    @param translation 平移量 (tx, ty, tz)，单位米。
    @return 16 元组，需经 VARIANT(VT_ARRAY|VT_R8) 包装后赋给 ArrayData。
    @note 绕单轴表达不了的位姿（例如"先绕 X 转 90° 再绕 Z 转 110°"）用这个入口。
    """
    values = [float(v) for v in rotation]
    if len(values) != 9:
        raise ValueError("rotation 必须包含 9 个元素（行主序 3x3）")
    tx, ty, tz = (float(v) for v in translation)
    return tuple(values + [tx, ty, tz, 1.0, 0.0, 0.0, 0.0])


def apply_component_transform_matrix(component, rotation, translation=None,
                                     preserve_translation=True, verify=True):
    """
    @brief 用任意 3x3 旋转矩阵设置组件位姿，写入后回读校验。
    @param component IComponent2 对象。
    @param rotation 9 个元素，行主序。
    @param translation 平移量；None 时按 preserve_translation 决定。
    @param preserve_translation True 时沿用组件现有平移。
    @param verify True 时回读 ArrayData 校验旋转块，未生效抛 RuntimeError。
    @return True 表示已写入并通过校验。
    """
    transform = component.Transform2
    if transform is None:
        raise RuntimeError("组件没有可用 Transform2")
    current = [float(v) for v in transform.ArrayData]
    tail = current[9:16] if len(current) >= 16 else [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
    if translation is None:
        translation = tail[0:3] if preserve_translation else (0.0, 0.0, 0.0)
    rotation = [float(v) for v in rotation]
    transform.ArrayData = _variant_double_array(
        rotation + [float(v) for v in translation] + list(tail[3:7])
    )
    if verify:
        _assert_rotation(transform, rotation, "ArrayData 写入")
    applied = False
    try:
        applied = bool(component.SetTransformAndSolve2(transform))
    except Exception:
        applied = False
    if not applied:
        try:
            component.Transform2 = transform
            applied = True
        except Exception as exc:
            raise RuntimeError(f"组件变换写入失败: {exc}") from exc
    if verify:
        _assert_rotation(component.Transform2, rotation, "组件位姿应用")
    return True


def _normalize_layout(layout):
    """
    校验并归一化装配布局。

    参数:
        layout: 元素为 dict，需含 "path"；可选 "axis"(默认 Z)、"angle_deg"(默认 0)、
                "translation"(默认 None=保留现有平移)、"group"(默认 root)
    返回:
        {"groups": {组名: [条目]}, "warnings": [...]}
    """
    if not layout:
        raise ValueError("layout 不能为空")
    warnings, groups = [], {}
    for idx, item in enumerate(layout):
        if not isinstance(item, dict) or not item.get("path"):
            raise ValueError(f"layout[{idx}] 缺少 path")
        axis = str(item.get("axis", "Z")).upper()
        if axis not in _AXIS_INDEX:
            raise ValueError(f"layout[{idx}] axis 不支持: {item.get('axis')}")
        entry = {
            "path": str(item["path"]),
            "axis": axis,
            "angle_deg": float(item.get("angle_deg", 0.0)),
            "translation": item.get("translation"),
            "group": str(item.get("group", "root")),
            "mark": int(item.get("mark", idx)),
        }
        trans = entry["translation"]
        if trans is not None:
            trans = tuple(float(v) for v in trans)
            if len(trans) != 3:
                raise ValueError(f"layout[{idx}].translation 必须是 3 元组")
            entry["translation"] = trans
        groups.setdefault(entry["group"], []).append(entry)
    return {"groups": groups, "warnings": warnings}


def verify_layout(model, layout, tol_translation_mm=0.05, tol_angle_deg=0.01):
    """
    校验装配体布局与规格一致（同轴 + 轴向基准）。

    逐组件比对 Transform2 与规格条目：
      - 平移 XY ≈ 期望（径向同轴）
      - 平移 Z ≈ 期望（轴向基准；全部零件必须从公共端面起算）
      - 旋转块 ≈ 期望矩阵
    不一致即记入 problems —— 这是为了拦截 AddComponent5 的**包围盒居中默认放置**
    （平移 z = −L/2），它会让每层以中点对称、导体从护套两端穿透。

    参数:
        model: 已重建的装配体
        layout: 与 rebuild_assembly 相同的布局
    返回:
        {"ok": bool, "problems": [...], "checked": n}
    """
    norm = _normalize_layout(layout)
    expected = {}
    for gname, entries in norm["groups"].items():
        if gname != "root" and len(norm["groups"]) > 1:
            expected[gname] = {"path": gname + ".sldasm", "axis": "Z",
                               "angle_deg": 0.0, "translation": (0.0, 0.0, 0.0)}
        for e in entries:
            base = Path(e["path"]).stem
            expected[base] = {**e, "path": e["path"]}

    problems, checked = [], 0
    for c in (model.GetComponents(True) or []):
        name2 = str(get_com_member(c, "Name2", default=""))
        base = name2.split("-")[0]
        e = expected.get(base)
        if e is None:
            continue
        checked += 1
        arr = [float(v) for v in c.Transform2.ArrayData]
        exp_t = e["translation"] if e["translation"] is not None else (0.0, 0.0, 0.0)
        dxy = math.hypot(arr[9] - exp_t[0], arr[10] - exp_t[1]) * 1000.0
        dz = abs(arr[11] - exp_t[2]) * 1000.0
        if dxy > tol_translation_mm or dz > tol_translation_mm:
            problems.append(
                f"{name2}: 平移 ({arr[9]:.4f},{arr[10]:.4f},{arr[11]:.4f}) m ≠ 期望 "
                f"({exp_t[0]:.4f},{exp_t[1]:.4f},{exp_t[2]:.4f}) m —— "
                "AddComponent5 默认包围盒居中未被显式变换覆盖")
        R = _rotation_matrix_axis(e["axis"], math.radians(e["angle_deg"]))
        if not all(abs(float(a) - float(b)) <= tol_angle_deg * math.pi / 180.0
                   for a, b in zip(arr[:9], R)):
            problems.append(f"{name2}: 旋转块与期望不符")
    return {"ok": not problems, "problems": problems, "checked": checked}


def rebuild_assembly(session, layout, out_path, sw=None, *, plain_sub: str = "sub"):
    """
    按布局一次性重建装配体，并对每个组件施加指定位姿。

    为什么需要它：**已保存装配体的组件位姿改不动**，手动重建既慢又容易把各层枢轴弄乱
    （`AddComponent5` 会给每个零件做包围盒居中，长度不同枢轴就不同）。

    参数:
        session: SolidWorksSession
        layout: 见 `_normalize_layout`
        out_path: 最终装配体保存路径
        sw: 可选，SolidWorks 应用对象（默认取 session.sw）
        plain_sub: 单组装配时子装配文件名前缀
    返回:
        最终装配体对象
    """
    sw = sw or getattr(session, "sw", None)
    norm = _normalize_layout(layout)
    groups = norm["groups"]
    for warning in norm["warnings"]:
        print("[rebuild_assembly] " + warning, flush=True)

    out_path = str(out_path)
    root = Path(out_path)
    root.parent.mkdir(parents=True, exist_ok=True)

    def _place(model, entry):
        comp = add_component(model, entry["path"], 0.0, 0.0, 0.0, "", sw)
        if comp is None:
            raise RuntimeError(f"添加组件失败: {entry['path']}")
        # 无条件施加显式变换：AddComponent5 默认按包围盒居中放置（平移 z = −L/2），
        # 信任默认会让各层以中点对称、导体穿透护套。即使角度为 0 也必须归零平移。
        apply_component_transform_matrix(
            comp,
            _rotation_matrix_axis(entry["axis"], math.radians(entry["angle_deg"])),
            translation=entry["translation"] if entry["translation"] is not None
            else (0.0, 0.0, 0.0),
            preserve_translation=False,
        )
        return comp

    top_sources = []
    multi = len(groups) > 1
    if multi:
        for gname, entries in groups.items():
            if gname == "root":
                top_sources.extend(entries)
                continue
            sub = session.new_assembly()
            for entry in entries:
                _place(sub, entry)
            sub.ForceRebuild3(False)
            sub_path = root.parent / f"{gname}.sldasm"
            session.save(sub, str(sub_path))
            session.close_owned_documents()
            top_sources.append({"path": str(sub_path), "axis": "Z", "angle_deg": 0.0,
                                "translation": None, "group": "root"})
        top_model = session.new_assembly()
        for entry in top_sources:
            _place(top_model, entry)
    else:
        top_model = session.new_assembly()
        for entry in next(iter(groups.values())):
            _place(top_model, entry)

    top_model.ForceRebuild3(False)
    verification = verify_layout(top_model, layout)
    if not verification["ok"]:
        for problem in verification["problems"]:
            print("[rebuild_assembly] 布局校验失败: " + problem, flush=True)
        raise RuntimeError(
            f"装配布局校验失败 {len(verification['problems'])} 项"
            "（组件位姿与规格不一致，多为包围盒居中默认放置未覆盖）")
    session.save(top_model, out_path)
    return top_model


def apply_component_transform_axis(component, axis="Z", angle_rad=0.0,
                                   translation=None, preserve_translation=True,
                                   verify=True):
    """
    @brief 绕任意主轴旋转组件并触发装配求解，写入后回读校验。
    @param component IComponent2 对象。
    @param axis 旋转轴，"X"/"Y"/"Z"。
    @param angle_rad 旋转角，弧度，右手定则。
    @param translation 平移量 (tx, ty, tz)，单位米；None 时按 preserve_translation 决定。
    @param preserve_translation True 时沿用组件现有平移。AddComponent5 会把包围盒中心放到原点，
           直接写 0 会让组件跳位。
    @param verify True 时回读 ArrayData 校验旋转块，未生效抛 RuntimeError。
    @return True 表示已写入并通过校验。
    """
    return apply_component_transform_matrix(
        component, _rotation_matrix_axis(axis, angle_rad), translation,
        preserve_translation=preserve_translation, verify=verify,
    )


def apply_component_transform_x(component, tx, ty, tz, angle_rad=0.0):
    """
    @brief 绕 X 轴旋转并平移组件（旧签名，保留兼容）。
    @note 内部已改为 VARIANT 写入 + 回读校验。需要 Z/Y 轴或保留原平移请用
          apply_component_transform_axis。
    """
    return apply_component_transform_axis(
        component, axis="X", angle_rad=angle_rad, translation=(tx, ty, tz),
        preserve_translation=False, verify=True,
    )


def iter_feature_tree(model, include_subfeatures=True):
    """
    @brief 遍历模型特征树，兼容 FirstFeature/GetNextFeature 伪可调用属性。
    @param model IModelDoc2 对象。
    @param include_subfeatures 是否包含子特征。
    @return 生成器，产出 (feature, depth)。
    """
    def walk_subfeatures(parent, depth):
        sub = safe_get_com_member(parent, "GetFirstSubFeature")
        while sub:
            yield sub, depth
            if include_subfeatures:
                yield from walk_subfeatures(sub, depth + 1)
            sub = safe_get_com_member(sub, "GetNextSubFeature")

    feature = safe_get_com_member(model, "FirstFeature")
    while feature:
        yield feature, 0
        if include_subfeatures:
            yield from walk_subfeatures(feature, 1)
        feature = safe_get_com_member(feature, "GetNextFeature")


def collect_mate_feature_summary(model):
    """
    @brief 收集 MateGroup 及其子 Mate 特征摘要。
    @param model IModelDoc2 装配体对象。
    @return 包含 name、type、depth 的列表，用于验证真实机械配合是否写入特征树。
    """
    result = []
    for feature, depth in iter_feature_tree(model, include_subfeatures=True):
        name = safe_get_com_member(feature, "Name")
        type_name = safe_get_com_member(feature, "GetTypeName2")
        if (
            type_name == "MateGroup"
            or str(type_name).startswith("Mate")
            or "mate" in str(name).lower()
            or "配合" in str(name)
        ):
            result.append({
                "name": name,
                "type": type_name,
                "depth": depth,
            })
    return result


def add_mate_coincident(asm_model, entity1_name, entity1_type, entity2_name, entity2_type):
    """
    添加重合配合

    参数:
        entity1_name/entity2_name: 实体名称（面、边、点等）
        entity1_type/entity2_type: 实体类型字符串（"FACE", "PLANE", "EDGE", "VERTEX" 等）
    """
    asm_model.ClearSelection2(True)
    _select_by_id(asm_model.Extension, entity1_name, entity1_type, mark=1)
    _select_by_id(asm_model.Extension, entity2_name, entity2_type, append=True, mark=1)
    return add_mate5_checked(asm_model, SW_MATE_COINCIDENT)


def add_mate_concentric(asm_model, face1_name, face2_name):
    """添加同心配合"""
    asm_model.ClearSelection2(True)
    _select_by_id(asm_model.Extension, face1_name, "FACE", mark=1)
    _select_by_id(asm_model.Extension, face2_name, "FACE", append=True, mark=1)
    return add_mate5_checked(asm_model, SW_MATE_CONCENTRIC, lock_rotation=False)


def add_mate_distance(asm_model, entity1_name, entity1_type, entity2_name, entity2_type, distance):
    """
    添加距离配合

    参数:
        distance: 配合距离（米）
    """
    asm_model.ClearSelection2(True)
    _select_by_id(asm_model.Extension, entity1_name, entity1_type, mark=1)
    _select_by_id(asm_model.Extension, entity2_name, entity2_type, append=True, mark=1)
    return add_mate5_checked(asm_model, SW_MATE_DISTANCE, distance=distance)


def add_mate_parallel(asm_model, face1_name, face2_name):
    """添加平行配合"""
    asm_model.ClearSelection2(True)
    _select_by_id(asm_model.Extension, face1_name, "FACE", mark=1)
    _select_by_id(asm_model.Extension, face2_name, "FACE", append=True, mark=1)
    return add_mate5_checked(asm_model, SW_MATE_PARALLEL)


def get_components(asm_model, top_level_only=True):
    """
    获取装配体中的所有组件

    参数:
        top_level_only: True=仅顶层组件, False=包含子装配体中的组件

    返回:
        组件信息列表
    """
    components = safe_get_com_member(asm_model, "GetComponents", top_level_only)
    result = []
    if components:
        for comp in components:
            result.append({
                "name": safe_get_com_member(comp, "Name2"),
                "path": safe_get_com_member(comp, "GetPathName"),
                "suppressed": safe_get_com_member(comp, "IsSuppressed"),
                "visible": safe_get_com_member(comp, "Visible"),
            })
    return result


def find_component_by_name(asm_model, keyword, top_level_only=True, case_sensitive=False):
    """
    @brief 按组件名关键字查找 IComponent2 对象。
    @param asm_model 装配体 IModelDoc2/IAssemblyDoc。
    @param keyword 组件名关键字，例如 "impeller" 或 "叶轮"。
    @param top_level_only True=仅顶层组件。
    @param case_sensitive 是否区分大小写。
    @return 第一个匹配的 IComponent2 对象。
    """
    if not keyword:
        raise ValueError("keyword 不能为空")
    components = safe_get_com_member(asm_model, "GetComponents", top_level_only) or []
    needle = str(keyword) if case_sensitive else str(keyword).lower()
    for component in components:
        name = str(safe_get_com_member(component, "Name2"))
        haystack = name if case_sensitive else name.lower()
        if needle in haystack:
            return component
    raise RuntimeError(f"未找到组件: {keyword}")


def suppress_component(asm_model, component_name):
    """压缩（隐藏）组件"""
    _select_by_id(asm_model.Extension, component_name, "COMPONENT")
    asm_model.EditSuppress()


def unsuppress_component(asm_model, component_name):
    """解压缩（显示）组件"""
    _select_by_id(asm_model.Extension, component_name, "COMPONENT")
    asm_model.EditUnsuppress()


def replace_component(asm_model, old_component_name, new_part_path):
    """
    替换装配体中的组件

    参数:
        old_component_name: 旧组件名称
        new_part_path: 新零件文件路径
    """
    _select_by_id(asm_model.Extension, old_component_name, "COMPONENT")
    return asm_model.ReplaceComponents2(new_part_path, "", False, 0, True)


def get_interference_detection(asm_model):
    """运行干涉检查并返回可审计报告。"""
    try:
        from .sw_measure import inspect_interference
    except ImportError:
        from sw_measure import inspect_interference
    result = inspect_interference(asm_model)
    # 保留旧入口的 SI 体积字段；计算和失败处理共享同一实现。
    for item in result["items"]:
        value = item.get("volume_mm3")
        item["volume"] = value / 1e9 if value is not None else None
    if result["errors"]:
        result["error"] = "; ".join(result["errors"])
    return result
