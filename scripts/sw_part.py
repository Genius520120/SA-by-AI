"""
SolidWorks 零件建模工具
提供草图绘制和特征创建的常用函数
"""
import math
import weakref
from dataclasses import dataclass
from contextlib import contextmanager

try:
    from .sw_connect import get_com_member
    from .sw_preflight import import_com_dependencies
except ImportError:
    from sw_connect import get_com_member
    from sw_preflight import import_com_dependencies


def _callout_variant():
    """
    构造 Select4 需要的空 Callout（Dispatch VARIANT）。

    sw_connect.create_empty_dispatch_variant 在测试替身里可能不存在，因此这里做
    防御式导入而不是模块级硬依赖：缺它就退回 None，Select4 仍可用（只是部分版本
    会报类型不匹配，此时 Select2 才是首选路径）。
    """
    try:
        from sw_connect import create_empty_dispatch_variant  # type: ignore
    except ImportError:
        try:
            from .sw_connect import create_empty_dispatch_variant  # type: ignore
        except ImportError:
            return None
    try:
        return create_empty_dispatch_variant()
    except Exception:
        return None

pythoncom, _win32com, VARIANT = import_com_dependencies()

PLANE_NAME_ALIASES = {
    "Front Plane": ["Front Plane", "前视基准面"],
    "Top Plane": ["Top Plane", "上视基准面"],
    "Right Plane": ["Right Plane", "右视基准面"],
    "前视基准面": ["前视基准面", "Front Plane"],
    "上视基准面": ["上视基准面", "Top Plane"],
    "右视基准面": ["右视基准面", "Right Plane"],
}

SKETCH_NAME_PREFIX_ALIASES = {
    "Sketch": "草图",
    "草图": "Sketch",
}


@dataclass
class SketchSelectionRef:
    """
    记录草图对象引用，避免后续只能靠 SelectByID2("SKETCH") 按名称反查。

    SolidWorks 2024 中文版中，按名称选择 SKETCH 可能持续返回 False。创建草图时
    保存对象引用，后续特征创建可直接 Select2 草图、草图特征或轮廓。
    """

    name: str
    sketch: object = None
    feature: object = None
    contours: tuple = ()
    regions: tuple = ()
    segments: tuple = ()
    source: str = ""

    def __str__(self):
        """保持与旧代码中普通草图名称字符串相近的表现。"""
        return self.name

    def __eq__(self, other):
        """与草图名字符串比较时按名称等价，保持向后兼容。"""
        if isinstance(other, SketchSelectionRef):
            return self.name == other.name
        if isinstance(other, str):
            return self.name == other
        return NotImplemented

    def __hash__(self):
        return hash(self.name)


class _SketchCacheEntry:
    """
    单个文档的草图选择缓存条目。

    同时保留 model 的弱引用与 ``_oleobj_`` 的标识，用于判定缓存是否仍然对应当前
    文档。仅用 ``id(ole_object)`` 作键是不可靠的：COM 代理被 GC 后 CPython 会复用
    内存地址，旧条目可能错误命中新文档，从而把草图特征建到错误的文档上。
    """

    __slots__ = ("model_ref", "ole_id", "sketches", "fingerprint")

    def __init__(self, model, ole_id):
        self.model_ref = _weak_model(model)
        self.ole_id = ole_id
        self.sketches = {}
        self.fingerprint = None

    def matches(self, model, ole_id):
        """@brief 判断该条目是否仍对应当前模型。"""
        if ole_id is not None and self.ole_id is not None:
            if ole_id != self.ole_id:
                return False
        if self.model_ref is None:
            # 该版本 COM 代理不支持弱引用，只能依赖 _oleobj_ 标识。
            return ole_id is not None and ole_id == self.ole_id
        alive = self.model_ref()
        if alive is None:
            return False
        return alive is model


def _weak_model(model):
    """@brief 尽力创建模型的弱引用；不支持时返回 None。"""
    try:
        return weakref.ref(model)
    except TypeError:
        return None


def _document_fingerprint(model):
    """
    读取所属文档的指纹（标题 + 路径）。

    用于检测"当前活动文档已经换成别的文档"，此时旧缓存必须整体失效。
    """
    try:
        document = get_com_member(model, "GetTitle"), get_com_member(model, "GetPathName")
    except Exception:
        return None
    return document


_SKETCH_SELECTION_CACHE = {}
_SKETCH_CACHE_LIMIT = 32


def clear_sketch_selection_cache(model=None):
    """
    清理草图选择缓存。

    参数:
        model: 指定文档时只清理该文档的条目；None 时清空全部。

    说明:
        长跑进程（例如常驻 MCP server）必须调用本函数，否则缓存会一直持有已关闭
        文档的 ISketch COM 对象，导致 SolidWorks 侧无法释放文档。
    """
    if model is None:
        _SKETCH_SELECTION_CACHE.clear()
        return
    key = _model_cache_key(model)
    _SKETCH_SELECTION_CACHE.pop(key, None)


def _prune_sketch_cache():
    """@brief 淘汰已失效条目；条目数超限时按插入顺序丢弃最旧项。"""
    for key, entry in list(_SKETCH_SELECTION_CACHE.items()):
        if entry.model_ref is not None and entry.model_ref() is None:
            _SKETCH_SELECTION_CACHE.pop(key, None)
    while len(_SKETCH_SELECTION_CACHE) > _SKETCH_CACHE_LIMIT:
        try:
            _SKETCH_SELECTION_CACHE.pop(next(iter(_SKETCH_SELECTION_CACHE)))
        except StopIteration:
            break


def _get_sketch_cache_entry(model, create=False):
    """@brief 取得（或创建）当前模型的缓存条目，自动识别文档切换。"""
    ole_id = _model_cache_key(model)
    entry = _SKETCH_SELECTION_CACHE.get(ole_id)
    if entry is not None and entry.matches(model, ole_id) and entry.fingerprint == _document_fingerprint(model):
        return entry
    if not create:
        return None
    # 文档已切换或地址已被复用：丢弃旧条目，避免跨文档误命中。
    _prune_sketch_cache()
    entry = _SketchCacheEntry(model, ole_id)
    entry.fingerprint = _document_fingerprint(model)
    _SKETCH_SELECTION_CACHE[ole_id] = entry
    return entry


# ============================================================
# 草图操作
# ============================================================

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


def _get_plane_name_candidates(plane_name):
    """
    获取基准面的候选名称列表

    参数:
        plane_name: 用户提供的基准面名称

    返回:
        list[str]
    """
    return PLANE_NAME_ALIASES.get(plane_name, [plane_name])


def _get_sketch_name_candidates(sketch_name):
    """
    获取草图名称候选列表，自动兼容中英文前缀。

    参数:
        sketch_name: 草图名称

    返回:
        list[str]
    """
    sketch_name = str(sketch_name)
    candidates = [sketch_name]
    for prefix, alias in SKETCH_NAME_PREFIX_ALIASES.items():
        if sketch_name.startswith(prefix):
            alias_name = alias + sketch_name[len(prefix):]
            if alias_name not in candidates:
                candidates.append(alias_name)
    return candidates


def _as_tuple(value):
    """把 COM 返回的单对象、SAFEARRAY 或 None 统一成 tuple。"""
    if value is None:
        return ()
    if isinstance(value, tuple):
        return tuple(item for item in value if item is not None)
    if isinstance(value, list):
        return tuple(item for item in value if item is not None)
    return (value,)


def _safe_com_member(obj, attr_name, *args, default=None):
    """读取 COM 成员，失败时返回默认值，避免排障路径再次抛 COM 异常。"""
    if obj is None:
        return default
    try:
        value = get_com_member(obj, attr_name, *args)
        return default if value is None else value
    except Exception:
        return default


def _model_cache_key(model):
    """生成仅用于当前 Python 进程的模型缓存键。"""
    ole_object = getattr(model, "_oleobj_", None)
    if ole_object is not None:
        return id(ole_object)
    return id(model)


def _cache_name_variants(sketch_name):
    """生成草图缓存名称，兼容中英文默认草图名前缀。"""
    variants = []
    for candidate in _get_sketch_name_candidates(sketch_name):
        if candidate not in variants:
            variants.append(candidate)
    return variants


def _cache_sketch_ref(model, sketch_ref):
    """缓存草图对象引用，后续可跳过 SelectByID2("SKETCH")。"""
    if not sketch_ref or not sketch_ref.name:
        return sketch_ref
    entry = _get_sketch_cache_entry(model, create=True)
    for candidate in _cache_name_variants(sketch_ref.name):
        entry.sketches[candidate] = sketch_ref
    return sketch_ref


def _find_cached_sketch_ref(model, sketch_name):
    """从当前进程缓存中查找草图对象引用。"""
    if isinstance(sketch_name, SketchSelectionRef):
        return sketch_name

    entry = _get_sketch_cache_entry(model, create=False)
    if entry is None:
        return None
    for candidate in _cache_name_variants(sketch_name):
        cached = entry.sketches.get(candidate)
        if cached:
            return cached
    return None


def _capture_sketch_ref(model, sketch_obj=None, fallback="Sketch1", source=""):
    """从活动草图或给定草图对象中提取可复用的选择引用。"""
    if sketch_obj is None:
        sketch_obj = _safe_com_member(model.SketchManager, "ActiveSketch")
    if sketch_obj is None:
        return None

    sketch_name = _safe_com_member(sketch_obj, "Name", default=fallback) or fallback
    sketch_ref = SketchSelectionRef(
        name=sketch_name,
        sketch=sketch_obj,
        feature=_safe_com_member(sketch_obj, "GetFeature"),
        contours=_as_tuple(_safe_com_member(sketch_obj, "GetSketchContours")),
        regions=_as_tuple(_safe_com_member(sketch_obj, "GetSketchRegions")),
        segments=_as_tuple(_safe_com_member(sketch_obj, "GetSketchSegments")),
        source=source,
    )
    return _cache_sketch_ref(model, sketch_ref)


def _refresh_sketch_ref(sketch_ref):
    """退出草图后再次刷新 Feature/Contour 引用。"""
    if not sketch_ref or sketch_ref.sketch is None:
        return sketch_ref
    feature = _safe_com_member(sketch_ref.sketch, "GetFeature")
    contours = _as_tuple(_safe_com_member(sketch_ref.sketch, "GetSketchContours"))
    regions = _as_tuple(_safe_com_member(sketch_ref.sketch, "GetSketchRegions"))
    segments = _as_tuple(_safe_com_member(sketch_ref.sketch, "GetSketchSegments"))
    if feature is not None:
        sketch_ref.feature = feature
    if contours:
        sketch_ref.contours = contours
    if regions:
        sketch_ref.regions = regions
    if segments:
        sketch_ref.segments = segments
    return sketch_ref


def _select_first_candidate(extension, candidate_names, entity_type, append=False, mark=0):
    """
    按候选名称列表依次尝试选择实体。

    参数:
        extension: ModelDocExtension 对象
        candidate_names: 候选名称列表
        entity_type: 实体类型字符串
        append: 是否追加选择
        mark: 选择标记

    返回:
        str | None
    """
    for candidate_name in candidate_names:
        if _select_by_id(extension, candidate_name, entity_type, append=append, mark=mark):
            return candidate_name
    return None


def _get_selection_count(model):
    """
    获取当前选择集中的对象数量。

    参数:
        model: IModelDoc2 对象

    返回:
        int
    """
    selection_manager = model.SelectionManager
    count_member = getattr(selection_manager, "GetSelectedObjectCount2")
    return count_member(-1) if callable(count_member) else int(count_member)


def _select_com_object(obj, append=False, mark=0):
    """使用对象自身 Select2/Select4 选择，优先避开 SelectByID2 名称解析。"""
    if obj is None:
        return False
    for method_name in ("Select2", "Select4"):
        method = getattr(obj, method_name, None)
        if not method:
            continue
        try:
            if method_name == "Select2":
                if bool(method(append, mark)):
                    return True
            elif bool(method(append, _empty_callout())):
                return True
        except Exception:
            continue
    return False


def _select_any_object(objects, append=False, mark=0):
    """从一组 COM 对象里尽量选择全部对象，至少选中一个即视为成功。"""
    selected_count = 0
    for obj in _as_tuple(objects):
        if _select_com_object(obj, append=(append or selected_count > 0), mark=mark):
            selected_count += 1
    return selected_count > 0


def _select_sketch_ref(model, sketch_ref, mark=0):
    """
    按稳定优先级选择草图引用。

    轮廓/区域更接近 FeatureExtrusion3 需要的预选对象；不可用时再回退草图
    Feature 或 Sketch 对象。
    """
    if not sketch_ref:
        return False
    model.ClearSelection2(True)
    _refresh_sketch_ref(sketch_ref)
    for objects in (
        sketch_ref.feature,
        sketch_ref.sketch,
        sketch_ref.contours,
        sketch_ref.regions,
        sketch_ref.segments,
    ):
        if _select_any_object(objects, append=False, mark=mark):
            return True
        model.ClearSelection2(True)
    return False


def _select_sketch_feature_by_name(model, sketch_name, mark=0):
    """通过 FeatureByName 找到草图特征后对象级选择。"""
    for candidate in _get_sketch_name_candidates(sketch_name):
        feature = _safe_com_member(model, "FeatureByName", candidate)
        if feature and _select_com_object(feature, append=False, mark=mark):
            return candidate
    return None


def _ensure_sketch_selected(model, sketch_name):
    """
    确保拉伸/切除前已有可用的草图选择。

    参数:
        model: IModelDoc2 对象
        sketch_name: 草图名称

    返回:
        str
    """
    if str(sketch_name) == "__current_selection__" and _get_selection_count(model) > 0:
        return "__current_selection__"

    active_ref = _capture_sketch_ref(model, fallback=str(sketch_name), source="active")
    if active_ref and _select_sketch_ref(model, active_ref):
        return active_ref.name

    cached_ref = _find_cached_sketch_ref(model, sketch_name)
    if cached_ref and _select_sketch_ref(model, cached_ref):
        return cached_ref.name

    selected_feature = _select_sketch_feature_by_name(model, sketch_name)
    if selected_feature:
        return selected_feature

    selected_sketch = _select_first_candidate(
        model.Extension, _get_sketch_name_candidates(sketch_name), "SKETCH"
    )
    if not selected_sketch:
        model.ClearSelection2(True)
        raise ValueError(
            f"无法选择草图: {sketch_name}。已尝试活动草图对象、缓存草图对象、"
            "FeatureByName 和 SelectByID2('SKETCH')。"
        )
    return selected_sketch


def start_sketch(model, plane_name="Front Plane"):
    """
    在指定基准面上开始草图

    参数:
        model: IModelDoc2
        plane_name: 基准面名称
            英文: "Front Plane", "Top Plane", "Right Plane"
            中文: "前视基准面", "上视基准面", "右视基准面"
    """
    model.ClearSelection2(True)

    selected_plane = _select_first_candidate(
        model.Extension, _get_plane_name_candidates(plane_name), "PLANE"
    )
    if selected_plane:
        model.SketchManager.InsertSketch(True)
        return selected_plane

    raise ValueError(f"无法选择基准面: {plane_name}")


def end_sketch(model):
    """
    退出当前草图，并返回可复用的草图选择引用。

    返回值兼容旧调用：调用者可以忽略它；需要稳定选择时可传给
    `extrude_boss()` / `extrude_cut()` 等特征函数。
    """
    sketch_ref = _capture_sketch_ref(model, source="end_sketch")
    model.SketchManager.InsertSketch(True)
    return _cache_sketch_ref(model, _refresh_sketch_ref(sketch_ref))


@contextmanager
def sketch(model, plane_name="Front Plane"):
    """
    草图上下文管理器。

    示例:
        with sketch(model, "Front Plane") as sketch_ref:
            sketch_circle(model, 0, 0, mm(25))
        extrude_boss(model, sketch_ref, mm(50))

    产出值:
        SketchSelectionRef。它保留了草图对象引用和轮廓/区域集合，后续特征可直接
        用对象自身 Select2 建立选择集，无需依赖 SelectByID2 按名称反查——后者在
        中文版或重建后可能持续返回 False。它的 __str__ 返回草图名，所以把它当
        字符串传给旧代码（例如 "Sketch1" 的比较或格式化）同样成立。
    """
    start_sketch(model, plane_name)
    sketch_ref = None
    try:
        sketch_ref = _capture_sketch_ref(model, source="context")
        yield sketch_ref if sketch_ref else current_sketch_name(model)
    finally:
        finished_ref = end_sketch(model)
        if sketch_ref and finished_ref:
            sketch_ref.feature = finished_ref.feature
            sketch_ref.contours = finished_ref.contours
            sketch_ref.regions = finished_ref.regions
            sketch_ref.segments = finished_ref.segments
            _cache_sketch_ref(model, sketch_ref)


def current_sketch_name(model, fallback="Sketch1"):
    """
    获取当前草图名称。

    参数:
        model: IModelDoc2
        fallback: 无法读取当前草图时返回的默认名称
    """
    active_sketch = model.SketchManager.ActiveSketch
    if active_sketch:
        return active_sketch.Name
    return fallback


def sketch_line(model, x1, y1, x2, y2):
    """画直线（单位: 米）"""
    return model.SketchManager.CreateLine(x1, y1, 0, x2, y2, 0)


def sketch_rectangle(model, cx, cy, w, h):
    """以中心点画矩形（单位: 米）"""
    return model.SketchManager.CreateCenterRectangle(
        cx, cy, 0, cx + w / 2, cy + h / 2, 0
    )


def sketch_corner_rectangle(model, x1, y1, x2, y2):
    """以对角线画矩形（单位: 米）"""
    return model.SketchManager.CreateCornerRectangle(x1, y1, 0, x2, y2, 0)


def sketch_circle(model, cx, cy, radius):
    """画圆（单位: 米）"""
    return model.SketchManager.CreateCircleByRadius(cx, cy, 0, radius)


def sketch_arc(model, cx, cy, x1, y1, x2, y2, direction=1):
    """
    画圆弧

    参数:
        cx, cy: 圆心坐标（米）
        x1, y1: 起点坐标
        x2, y2: 终点坐标
        direction: 1=逆时针, -1=顺时针
    """
    return model.SketchManager.CreateArc(cx, cy, 0, x1, y1, 0, x2, y2, 0, direction)


def sketch_polygon(model, cx, cy, radius, sides=6):
    """画正多边形（内切圆方式）"""
    return model.SketchManager.CreatePolygon(cx, cy, 0, cx + radius, cy, 0, sides, True)


def sketch_slot(model, x1, y1, x2, y2, radius):
    """画槽口"""
    return model.SketchManager.CreateSketchSlot(
        0,  # swSketchSlotCreationType_e: 0=Straight
        radius, radius,  # 宽度
        x1, y1, 0,
        x2, y2, 0,
        0, 0, 0,
        1, False
    )


def sketch_spline(model, points):
    """
    画样条曲线

    参数:
        points: [(x1,y1), (x2,y2), ...] 控制点列表（单位: 米）
    """
    import array
    point_array = array.array('d')
    for x, y in points:
        point_array.extend([x, y, 0.0])
    return model.SketchManager.CreateSpline2(point_array, False)


def auto_dimension_sketch(model, sketch=None, sketch_segments=None):
    """@brief 按官方 AutoDimension2 流程为当前草图创建真实模型尺寸。

    SolidWorks 的工程图 ``InsertModelAnnotations`` 只能导入模型中已有的
    显示尺寸，不能凭空为拉伸深度或矩形轮廓生成尺寸。该函数在草图编辑态
    选择水平/垂直基准线，再调用 ``ISketch.AutoDimension2``，因此生成的尺寸
    会成为模型的真实 DisplayDimension，可被工程图导入并复核。

    返回值包含 ``status_code``（官方约定 0 表示成功）、选择数量和是否创建
    尺寸的可审计信息。函数不会把失败伪装成成功。
    """
    active = sketch or _safe_com_member(model.SketchManager, "ActiveSketch")
    if active is None:
        return {"status": "blocked", "status_code": None, "error_code": "SKETCH_NOT_ACTIVE", "selected": 0}

    def segments():
        supplied = _as_tuple(sketch_segments)
        if supplied:
            return supplied
        for name in ("GetSketchSegments", "IGetSketchSegments"):
            value = _safe_com_member(active, name)
            result = _as_tuple(value)
            if result:
                return result
        return ()

    horizontal = None
    vertical = None
    for segment in segments():
        try:
            start = get_com_member(segment, "GetStartPoint2")
            end = get_com_member(segment, "GetEndPoint2")
            dx = abs(float(get_com_member(start, "X")) - float(get_com_member(end, "X")))
            dy = abs(float(get_com_member(start, "Y")) - float(get_com_member(end, "Y")))
        except Exception:
            continue
        if dx > dy and horizontal is None:
            horizontal = segment
        elif dy > dx and vertical is None:
            vertical = segment

    model.ClearSelection2(True)
    selected = 0
    # SW2024 swAutodimMark_e：2=水平基准，4=垂直基准。
    # 官方示例要求垂直线作为水平基准、水平线作为垂直基准。
    for segment, mark in ((vertical, 2), (horizontal, 4)):
        if segment is None:
            continue
        try:
            if bool(segment.Select3(selected > 0, mark, _empty_callout())):
                selected += 1
        except Exception:
            try:
                if bool(segment.Select4(selected > 0, _empty_callout())):
                    selected += 1
            except Exception:
                pass

    status_code = None
    try:
        # swAutodimEntitiesAll=1, Baseline=1, Below=-1, Left=-1。
        status_code = int(get_com_member(active, "AutoDimension2", 1, 1, -1, 1, -1))
    except Exception as exc:
        model.ClearSelection2(True)
        return {
            "status": "failed",
            "status_code": None,
            "error_code": "SKETCH_AUTODIMENSION_CALL_FAILED",
            "error": str(exc),
            "selected": selected,
        }
    try:
        model.GraphicsRedraw2()
    except Exception:
        pass
    model.ClearSelection2(True)
    return {
        "status": "pass" if status_code == 0 else "blocked",
        "status_code": status_code,
        "error_code": None if status_code == 0 else "SKETCH_AUTODIMENSION_REJECTED",
        "selected": selected,
    }


def add_dimension(model, x, y):
    """在指定位置添加尺寸标注"""
    return model.AddDimension2(x, y, 0)


def add_sketch_relation(model, relation_type):
    """
    添加草图几何关系

    常用 relation_type:
        "sgFIXED", "sgHORIZONTAL", "sgVERTICAL", "sgCOLINEAR",
        "sgPARALLEL", "sgPERPENDICULAR", "sgTANGENT", "sgCONCENTRIC",
        "sgEQUAL", "sgSYMMETRIC", "sgMIDPOINT", "sgCOINCIDENT"
    """
    return model.SketchAddConstraints(relation_type)


# ============================================================
# 特征操作
# ============================================================

def _require_feature(result, label, context=None):
    """
    特征创建结果的统一守卫。

    参数:
        result: FeatureManager 返回的特征对象，或 None
        label: 报错用的特征名（如"凸台拉伸"）
        context: 可选上下文（dict 或字符串），失败时一并输出，便于定位
    返回:
        非 None 的特征对象；为 None 时抛 RuntimeError

    说明:
        SolidWorks 特征 API 失败时返回 None 而不抛异常。历史实现直接 return，
        调用方拿到 None 却以为成功，形成"调用成功但什么都没建"的静默故障。
        只给笼统原因（"草图未闭合/方向相反"）等于没抛，必须带上当时的草图名、
        方向、深度、选中数等上下文。
    """
    if result is None:
        detail = ""
        if context:
            if isinstance(context, dict):
                detail = "；上下文: " + ", ".join(f"{k}={v}" for k, v in context.items())
            else:
                detail = f"；上下文: {context}"
        raise RuntimeError(
            f"{label} 创建失败（FeatureManager 返回 None）。常见原因：草图未闭合或未被选中、"
            "拉伸/切除方向与已有实体相反（direction 参数）、轮廓被其它实体完全吞并。"
            f"{detail}"
        )
    return result


def extrude_boss(model, sketch_name, depth, direction=True, merge=True, name=None):
    """
    凸台拉伸

    参数:
        model: IModelDoc2
        sketch_name: 草图名称（如 "Sketch1"）
        depth: 拉伸深度（米）
        direction: True=反转默认方向；凸台默认沿草图法向
        merge: True=合并结果
        name: 特征名，None 则沿用 SolidWorks 默认命名。注意别把特征名当第 4 个
            位置参数传——那会落到 direction 上（truthy 时恰好等于默认，静默无害但属误用）。
    """
    _ensure_sketch_selected(model, sketch_name)
    feature = _require_feature(model.FeatureManager.FeatureExtrusion3(
        True,         # Sd: 单向拉伸
        False,        # Flip
        direction,    # Dir: 拉伸方向
        0,            # T1: Blind
        0,            # T2: Blind
        depth,        # D1
        0.0,          # D2
        False,        # Dchk1
        False,        # Dchk2
        False,        # Ddir1
        False,        # Ddir2
        0.0,          # Dang1
        0.0,          # Dang2
        False,        # OffsetReverse1
        False,        # OffsetReverse2
        False,        # TranslateSurface1
        False,        # TranslateSurface2
        merge,        # Merge
        False,        # UseFeatScope
        True,         # UseAutoSelect
        0,            # T0: 从草图平面开始
        0.0,          # StartOffset
        False         # FlipStartOffset
    ), "凸台拉伸", {"草图": str(sketch_name), "depth_mm": round(depth * 1000, 3),
                    "direction": direction, "merge": merge})
    if name:
        feature.Name = name
    return feature


def extrude_cut(model, sketch_name, depth, direction=True, flip=False):
    """
    切除拉伸

    参数:
        model: IModelDoc2
        sketch_name: 草图名称
        depth: 切除深度（米），0 表示完全贯穿
        direction: True=反转默认方向；切除默认逆草图法向
        flip: True=切除轮廓外侧，False=切除轮廓内侧
    """
    _ensure_sketch_selected(model, sketch_name)
    if depth == 0:
        # 完全贯穿
        end_condition = 1  # swEndCondThroughAll
        depth = 0.01  # 占位值
    else:
        end_condition = 0  # swEndCondBlind

    return _require_feature(model.FeatureManager.FeatureCut4(
        True, bool(flip), bool(direction),  # Sd, Flip, Dir
        end_condition, 0, depth, 0,
        False, False, False, False, 0.0, 0.0,
        False, False, False, False, False,
        True, True, False, False,  # assembly-scope flags -> False for part docs
        False, 0, 0, False, False
    ), "切除拉伸", {"草图": str(sketch_name), "depth_mm": round(depth * 1000, 3),
                    "direction": direction, "flip": flip})


def extrude_midplane(model, sketch_name, total_depth):
    """
    中面拉伸（两侧对称拉伸）

    参数:
        total_depth: 总深度（米），每侧为 total_depth/2
    """
    _ensure_sketch_selected(model, sketch_name)
    return _require_feature(model.FeatureManager.FeatureExtrusion3(
        True,         # Sd: 单向定义，终止条件控制为中面
        False,        # Flip
        True,         # Dir
        6,            # T1: swEndCondMidPlane
        0,            # T2
        total_depth,  # D1
        0.0,          # D2
        False,        # Dchk1
        False,        # Dchk2
        False,        # Ddir1
        False,        # Ddir2
        0.0,          # Dang1
        0.0,          # Dang2
        False,        # OffsetReverse1
        False,        # OffsetReverse2
        False,        # TranslateSurface1
        False,        # TranslateSurface2
        True,         # Merge
        False,        # UseFeatScope
        True,         # UseAutoSelect
        0,            # T0
        0.0,          # StartOffset
        False         # FlipStartOffset
    ), "中面拉伸", {"草图": str(sketch_name), "total_depth_mm": round(total_depth * 1000, 3)})


SW_FM_SWEEP = 17  # swFeatureNameID_e.swFmSweep


def sweep_profile_helix(model, profile_sketch, radius, pitch, height, guide_radii,
                        hand="left", start_angle_deg=0.0, points_per_turn=120, name=None):
    """
    沿螺旋路径扫掠任意闭合截面（非圆截面必须配引导线）。

    参数:
        model: IModelDoc2
        profile_sketch: 位于前视基准面（z=0）的闭合截面草图；可传 sketch() 产出的
            SketchSelectionRef，也可传草图名字符串。
        radius: 螺旋半径（米），取截面形心到轴线的距离。
        pitch: 螺距（米）。
        height: 螺旋轴向长度（米）。
        guide_radii: 引导线半径序列（米），一般给 (内缘半径, 外缘半径)。必填。
        hand: "left" 左旋 / "right" 右旋。
        start_angle_deg: 起始相位角（度）。
        points_per_turn: 每圈样条采样点数，默认 120。
        name: 特征名，None 则沿用 SolidWorks 默认命名。
    返回:
        IFeature

    说明:
        1) 路径与引导线都用 3D 草图样条。不要用 InsertHelix 生成曲线再当路径——
           实测扫掠不跟随，实体被拉成直条。
        2) 非圆截面必须给引导线。只给路径时截面会随路径扭转，实测外径从 29.9 涨到 32.8。
        3) 选择标记固定为：轮廓=1、路径=4、引导线=2；路径与引导线用 "SKETCH" 类型 + 名称。
        4) 已知限制：SolidWorks 2024 的扫掠求解器在"窄截面 + 特定相位"下会退化
           （实测相位 288°/297.3° 建不出特征，微扰点数/引导线顺序/轮廓起点均无效）。
           批量圆周排布请只扫掠一次（相位 0），其余实例在装配体里用
           sw_assembly.apply_component_transform_axis 旋转复用。
    """
    if height <= 0 or pitch <= 0:
        raise ValueError("height 与 pitch 必须为正数")
    if not guide_radii:
        raise ValueError("guide_radii 不能为空：无引导线时截面会随螺旋路径扭转")
    turns = float(height) / float(pitch)
    direction = -1.0 if str(hand).lower().startswith("l") else 1.0
    phase = math.radians(float(start_angle_deg))

    def _helix_spline(radius_value, label):
        model.ClearSelection2(True)
        model.SketchManager.Insert3DSketch(True)
        sketch_obj = model.SketchManager.ActiveSketch
        count = max(8, int(round(turns * float(points_per_turn))) + 1)
        points = []
        for i in range(count):
            t = i / (count - 1) * turns
            angle = direction * 2.0 * math.pi * t + phase
            points += [radius_value * math.cos(angle), radius_value * math.sin(angle), pitch * t]
        segment = model.SketchManager.CreateSpline2(
            VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, points), False
        )
        sketch_name = _safe_com_member(sketch_obj, "Name", default=None)
        model.SketchManager.Insert3DSketch(True)
        if segment is None or not sketch_name:
            raise RuntimeError(f"3D 螺旋样条创建失败: {label}")
        return sketch_name

    path_name = _helix_spline(float(radius), "path")
    guide_names = [_helix_spline(float(r), f"guide@{r}") for r in guide_radii]

    model.ClearSelection2(True)
    if isinstance(profile_sketch, str):
        selected = False
        for candidate in _get_sketch_name_candidates(profile_sketch):
            feature = _safe_com_member(model, "FeatureByName", candidate)
            if feature is not None and _select_com_object(feature, append=False, mark=1):
                selected = True
                break
    else:
        selected = _select_sketch_ref(model, profile_sketch, mark=1)
    if not selected:
        raise RuntimeError(f"轮廓选择失败: {profile_sketch}")

    if not _select_by_id(model.Extension, path_name, "SKETCH", append=True, mark=4):
        raise RuntimeError(f"扫掠路径选择失败: {path_name}")
    for guide_name in guide_names:
        if not _select_by_id(model.Extension, guide_name, "SKETCH", append=True, mark=2):
            raise RuntimeError(f"引导线选择失败: {guide_name}")

    data = model.FeatureManager.CreateDefinition(SW_FM_SWEEP)
    if data is None:
        raise RuntimeError("CreateDefinition(swFmSweep) 返回 None")
    feature = model.FeatureManager.CreateFeature(data)
    if feature is None:
        raise RuntimeError(
            "扫掠特征创建失败（求解器拒绝）。若为批量圆周排布，请改为只扫掠一次（相位 0），"
            "其余实例在装配体里旋转复用——见本函数说明第 4 条。"
        )
    if name:
        feature.Name = name
    return feature


def extrude_tube(model, outer_diameter, inner_diameter, depth, name=None):
    """
    圆筒：外圆凸台 + 内圆切除两步。

    参数:
        outer_diameter: 外径（米）
        inner_diameter: 内径（米）
        depth: 长度（米）
        name: 切除特征名
    返回:
        (凸台特征, 切除特征)

    说明:
        实测"双同心圆草图直接拉伸"在 SolidWorks 2024 上 FeatureExtrusion3 返回 None，
        必须拆成两步。用 revolve_boss 绕轴回转矩形也可以，但需要额外画轴线草图。
    """
    if inner_diameter <= 0 or inner_diameter >= outer_diameter:
        raise ValueError("需满足 0 < inner_diameter < outer_diameter")
    with sketch(model, "Front Plane") as outer_ref:
        sketch_circle(model, 0.0, 0.0, outer_diameter / 2.0)
    boss = extrude_boss(model, outer_ref, depth, direction=False)
    with sketch(model, "Front Plane") as inner_ref:
        sketch_circle(model, 0.0, 0.0, inner_diameter / 2.0)
    cut = extrude_cut(model, inner_ref, depth, direction=True)
    if name:
        cut.Name = name
    return boss, cut


def revolve_boss(model, sketch_name, angle_rad, axis_sketch_name=None):
    """
    旋转凸台

    参数:
        sketch_name: 轮廓草图名称
        angle_rad: 旋转角度（弧度），2*pi 表示 360 度
        axis_sketch_name: 旋转轴草图名称（None 则需预先选择轴线）
    """
    _ensure_sketch_selected(model, sketch_name)
    return model.FeatureManager.FeatureRevolve2(
        True, True, False,
        False, False, False,
        0, 0,
        angle_rad, 0,
        False, False,
        0.0, 0.0,
        0, 0, 0, True, True, True
    )


def fillet(model, radius, edges=None):
    """
    倒圆角（恒定半径）。

    参数:
        model: IModelDoc2
        radius: 圆角半径（米）
        edges: 预先选择的边线列表，None 则使用当前选择集

    返回:
        IFeature 或 None。

    说明:
        IFeatureManager::FeatureFillet 只接受 4 个参数：
        ``(Options, Radius, R1, R2)``。传 7 个参数（在末尾补 None）会在
        SolidWorks 2024/2026 上抛 "非选择性的参数"（0x8002000F），这是旧文档和
        早期封装误导的地方——已在真机实测确认。

        调用前必须先建立边线选择集；选择集为空时 SolidWorks 会对"当前轮廓"
        执行圆角，产生难以排查的错误特征。因此这里显式校验选择集。
    """
    if edges is not None:
        for edge in edges:
            try:
                edge.Select2(True, 0)
            except Exception:
                try:
                    edge.Select4(True, _callout_variant())
                except Exception:
                    continue
    if _get_selection_count(model) <= 0:
        raise ValueError(
            "圆角需要先选择边线。请用 select_edges() 按几何条件选边，"
            "或用 solidworks_list_edges 查看可用边线。"
        )
    return model.FeatureManager.FeatureFillet(195, radius, 0, 0)


def extrude_offset_start(model, sketch_name, depth, start_offset,
                         direction=False, merge=False, name=None):
    """
    从草图平面偏移 `start_offset` 处开始拉伸（T0=3 / swStartOffset）。

    参数:
        model: IModelDoc2
        sketch_name: 草图名或 SketchSelectionRef（**必须单轮廓**）
        depth: 拉伸深度（米）
        start_offset: 起始面相对草图平面的偏移（米）
        direction: 拉伸方向
        merge: 是否与已有实体合并
        name: 特征名
    返回:
        IFeature

    注意:
        1) 仅支持**单轮廓草图**。≥2 个闭合轮廓时 `FeatureExtrusion3` 一律返回
           None（与是否偏移无关）。
        2) `FeatureCut4` 没有等价的偏移起始参数（实测报"非选择性的参数"），
           需要"带偏移的孔/槽"时，改用"实心体 + 末尾一刀通孔"的模式。
    """
    _ensure_sketch_selected(model, sketch_name)
    f = model.FeatureManager.FeatureExtrusion3(
        True, False, bool(direction), 0, 0, depth, 0.0,
        False, False, False, False, 0.0, 0.0,
        False, False, False, False, bool(merge), False, True,
        3, start_offset, False)
    return _require_feature(f, "偏移起始拉伸", {
        "草图": str(sketch_name), "depth_mm": round(depth * 1000, 3),
        "start_offset_mm": round(start_offset * 1000, 3),
        "direction": direction, "merge": merge,
    })


def chamfer_edge_at(model, point, distance, angle_deg=45):
    """
    按三维坐标点选中边并倒角。

    参数:
        model: IModelDoc2
        point: (x, y, z)，米；必须**落在目标边上**（圆边取圆周上任意一点，
               如 (半径, 0, 端面 z)）
        distance: 倒角距离（米）
        angle_deg: 倒角角度
    返回:
        IFeature

    注意:
        1) `SelectByID2` 的 Callout 参数必须传 `VARIANT(VT_DISPATCH, None)`，
           传 Python `None` 会报"类型不匹配"。
        2) 若一个端面有多条边需要倒角，逐条调用本函数即可。
    """
    model.ClearSelection2(True)
    if not model.Extension.SelectByID2(
            "", "EDGE", float(point[0]), float(point[1]), float(point[2]),
            False, 0, _empty_callout(), 0):
        raise RuntimeError("倒角选边失败：坐标点未命中任何边。上下文: "
                           f"point={tuple(round(float(v), 4) for v in point)}")
    cf = chamfer(model, distance, angle_deg)
    return _require_feature(cf, "倒角", {
        "point": tuple(round(float(v), 4) for v in point),
        "distance_mm": round(distance * 1000, 3), "angle_deg": angle_deg,
    })


def chamfer(model, distance, angle_deg=45):
    """
    倒角

    参数:
        distance: 倒角距离（米）
        angle_deg: 倒角角度（度）
    """
    import math
    angle_rad = angle_deg * math.pi / 180.0
    return model.FeatureManager.InsertFeatureChamfer(
        4, 1, distance, angle_rad, 0, 0, 0, 0
    )


def linear_pattern(model, feature_name, d1_x, d1_y, d1_z, d1_spacing, d1_count,
                    d2_x=0, d2_y=0, d2_z=0, d2_spacing=0, d2_count=1):
    """
    线性阵列

    参数:
        feature_name: 要阵列的特征名称
        d1_*: 方向1 的方向向量、间距（米）和数量
        d2_*: 方向2（可选）
    """
    try:
        from .sw_pattern import linear_pattern as create_pattern
    except ImportError:
        from sw_pattern import linear_pattern as create_pattern
    return create_pattern(model, feature_name, (d1_x, d1_y, d1_z), d1_spacing, d1_count,
        (d2_x, d2_y, d2_z), d2_spacing, d2_count)


def circular_pattern(model, feature_name, axis_name, angle_rad, count, equal_spacing=True):
    """
    圆形阵列

    参数:
        feature_name: 要阵列的特征名称
        axis_name: 旋转轴名称
        angle_rad: 总角度（弧度）
        count: 实例数量
        equal_spacing: True=等间距
    """
    try:
        from .sw_pattern import circular_pattern as create_pattern
    except ImportError:
        from sw_pattern import circular_pattern as create_pattern
    return create_pattern(model, feature_name, axis_name, angle_rad, count, equal_spacing)


def shell(model, thickness, faces_to_remove=None, outward=False):
    """
    抽壳

    参数:
        thickness: 壁厚（米）
        faces_to_remove: 要移除（开口）的面名称序列；None 表示封闭抽壳。
            SolidWorks 的 InsertFeatureShell 只读取当前选择集，所以这里会按名称预选
            这些面；任一面选不中即抛错，不会静默产出封闭壳。
        outward: True 时壁厚向外加厚。
    """
    if faces_to_remove:
        model.ClearSelection2(True)
        for face_name in faces_to_remove:
            if not _select_by_id(model.Extension, str(face_name), "FACE", append=True, mark=0):
                model.ClearSelection2(True)
                raise RuntimeError(
                    f"抽壳开口面选择失败: {face_name}。InsertFeatureShell 只读取当前选择集，"
                    "选不中就无法移除该面。"
                )
    return _require_feature(
        model.FeatureManager.InsertFeatureShell(thickness, bool(outward)), "抽壳",
        {"thickness_mm": round(thickness * 1000, 3), "faces_to_remove": faces_to_remove},
    )


def mirror_feature(model, feature_name, mirror_plane_name):
    """
    镜像特征

    参数:
        feature_name: 要镜像的特征名称
        mirror_plane_name: 镜像基准面名称
    """
    _select_by_id(model.Extension, feature_name, "BODYFEATURE", mark=4)
    _select_by_id(model.Extension, mirror_plane_name, "PLANE", append=True, mark=1)
    return model.FeatureManager.InsertMirrorFeature2(False, False, False, False, 0)


def hole_wizard(model, hole_type, standard, fastener_type, size, depth, x, y, z):
    """
    异型孔向导（简化版）
    注意：完整的异型孔向导参数复杂，建议参考 API 文档调整
    """
    # 异型孔向导需要通过 FeatureManager.HoleWizard5 调用
    # 具体参数取决于孔类型，此处提供框架
    pass


def rib(model, sketch_name, thickness, direction=True):
    """
    筋特征

    参数:
        sketch_name: 筋轮廓草图名称
        thickness: 筋厚度（米）
        direction: 拉伸方向
    """
    _ensure_sketch_selected(model, sketch_name)
    return model.FeatureManager.InsertRib(
        direction, False, thickness, 0, False, False, False, 0, False
    )
