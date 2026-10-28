# 结果自审查参考

## 必做检查

生成、修改或导出 SolidWorks 文件后，至少检查：

1. COM 调用返回值不是 `None`，关键特征对象创建成功。
2. `save_document()`、`session.save()`、`session.export()` 返回成功。
3. 输出 `.sldprt` / `.sldasm` / `.slddrw` / `.step` / `.stl` 等文件真实存在且大小合理。
4. 模型已重建：`model.ForceRebuild3(False)`。
5. 模型已缩放到适合窗口：`model.ViewZoomtofit2()`。
6. 至少导出一张等轴测 BMP，复杂模型导出前视、俯视、右视。

## 用独立参考几何验收，别用数据源自报的包围盒

当装配体是**从外部数据重建**（STEP 装配、Fusion/URDF/测量数据等）时，最容易犯的错是拿数据源里的 `bounding_box` 当验收基准。那份 bbox 是数据作者算的，可能和几何本身不一致。

正确做法：**找一个和重建路径无关的独立几何当基准**，通常就是数据源自带的整机 STEP：

```python
mine = [float(v) for v in asm.GetBox(0)]                    # 重建结果
ref_doc = open_document(sw, os.path.join(src, "assembly.step"))
ref = [float(v) for v in ref_doc.GetBox(0)]                 # 官方整机几何
err_mm = max(abs(mine[i] - ref[i]) for i in range(6)) * 1000
```

实测反例（同一批数据）：某台装配体重建结果与官方整机 STEP 偏差 **0.00 mm**，但与该数据 JSON 里自报的 bbox 差 **61.76 mm**。用 JSON bbox 判，这台会被误杀。

判据建议：

| 指标 | 门槛 |
|---|---|
| 与独立参考几何的包围盒偏差 | ≤ 5 mm |
| 组件约束状态 | 全部 `IsFixed is True` |
| 保存→关闭→重开后包围盒漂移 | ≤ 0.5 mm |

三条同时满足才算通过。只看"重建成功没报错"是不够的——`SaveAs3` 返回 1 不落盘、`SetTransformAndSolve2` 返回 True 但旋转没写进去，都是不报错但错的。


## 结构化自审查报告

```python
import sys
sys.path.insert(0, r"SKILL_DIR/scripts")

from sw_review import run_review

model.ForceRebuild3(False)
report, report_path = run_review(
    model,
    r"C:\temp\solidworks_review",
    basename="model",
    views=("isometric", "front", "top", "right"),
    expected_outputs=[r"C:\temp\model.sldprt", r"C:\temp\model.step"],
)
print(report_path)
print(report["checks"])
```

`run_review()` 会输出：

- 多视角 BMP 预览图。
- `*_review_report.json` 结构化报告。
- `*_review_summary.md` 可读摘要，适合贴到交付说明、Issue 或 PR。
- `evaluation.status`：`pass` / `warn` / `fail`。
- `evaluation.score`：规则评分，满分 100。
- `evaluation.issues`：机器可读的问题列表。
- `evaluation.recommendations`：修复建议。
- `checks.previews_created`：预览图是否生成。
- `checks.previews_not_blank`：预览图是否疑似非空白。
- `checks.expected_outputs_exist`：期望输出文件是否真实存在且大小大于 0。
- `checks.feature_summary_available`：是否能读取特征树摘要。

注意：结构化报告只能抓明显失败，不能替代人工或视觉模型对几何意图的判断。

制造级任务还必须单独输出机器可读规格证据，例如：

```json
{
  "cad_spec": {
    "envelope_mm": {"length": 60, "width": 40, "thickness": 12},
    "holes": [
      {"diameter_mm": 10, "count": 1, "positions_xy_mm": [[0, 0]]},
      {"diameter_mm": 4, "count": 4, "positions_xy_mm": [[-24, -14], [24, -14], [-24, 14], [24, 14]]}
    ]
  },
  "measurement_source": "SolidWorks API/B-Rep/参数回读",
  "units": "mm"
}
```

任务中明确给出外形或孔径，但交付物没有独立 JSON 证据时，Reviewer Gate 必须失败；Codex 的文字总结和“执行器自报通过”不能替代证据。孔数量、孔位、边距、中心距、圆角、壁厚和装配自由度也应按任务逐项记录。

复杂孔槽使用 `collect_geometry_measurements()` 时还会输出：

- `hole_groups`：按空间轴线归并的孔段。
- `compound_holes`：同轴但直径不同的沉孔/复合孔候选。
- `slot_arc_candidates`：内部圆柱面具有多条边界边的槽端圆弧，不计入圆孔数量。
- `validate_hole_positions()`：按孔轴线距离和孔径公差逐孔验收。

```python
from sw_review import collect_geometry_measurements, validate_hole_positions

measurements = collect_geometry_measurements(model)
position_checks = validate_hole_positions(
    measurements,
    [{"id": "H1", "diameter_mm": 8, "position_mm": [20, 20, 0]}],
    position_tolerance_mm=0.1,
    diameter_tolerance_mm=0.05,
)
assert position_checks["status"] == "pass"
```

注意：圆柱面 B-Rep 可以证明孔径、轴线和圆柱段长度，但不能单独可靠区分盲孔/通孔。盲孔深度、沉头角度和孔型必须与 `sw_hole_features.py` 返回的创建参数证据或特征定义回读交叉验证。

## 命令行一键审查

```bash
python scripts/sw_review.py ^
  --file C:\temp\model.sldprt ^
  --output-dir C:\temp\solidworks_review ^
  --basename model ^
  --expected C:\temp\model.sldprt ^
  --expected C:\temp\model.step
```

返回码：

- `0`：`pass` 或 `warn`，报告已生成。
- `1`：传入 `--fail-on-warn` 且状态为 `warn`。
- `2`：`fail`，存在必须修复的问题。

## 目视自查清单

- 主体是否出现在画面中，是否为空白或只剩草图。
- 关键部件是否齐全，例如车轮、孔、轴、外壳、支架、BOM 表等。
- 特征树成功的切除/孔/槽是否在预期表面真实可见；`feature is not None` 不等于几何意图正确。
- 比例是否明显错误，例如毫米误当米导致模型巨大。
- 方向是否正确，例如轮子是否在侧面而不是车顶。
- 部件是否明显重叠、悬空、穿模或缺少约束。
- 文件名、输出目录、导出格式是否符合用户要求。

## 渲染成品图的标准管线（防"残图"）

2026-10-27 实测翻车：渲染抓到未刷新的图形缓冲，成品图只剩 3 个零件。
稳定顺序（每步都有用，别省）：

```python
model.ForceRebuild3(False); time.sleep(1)   # 1. 先重建
model.GraphicsRedraw2()                      # 2. 强制刷新图形缓冲（关键步）
model.ShowNamedView2("*Isometric", 7)
model.ClearSelection2(True)
model.ViewZoomtofit2(); time.sleep(3)        # 3. 缩放后必须等缓冲稳定
model.SaveBMP(bmp_path, 1600, 1200); time.sleep(1)
```

渲染后用 PIL 做像素统计验收，两条同时查：

- **非背景占比**（以角点像素为背景色）：明显残图会骤降；
- **颜色数**：`len(set(data))` 过低说明只剩一两个零件或大面积空白。

比"看一眼图"更强的机器判据（渲染前做）：回读 `GetComponents(False)` 组件数 +
逐组件 `Transform2.ArrayData[9:12]` 平移与设计值比对（×1000 转 mm）。
图"看着不对"时先用位姿回读定位是装配错还是渲染错——装配错改装配，
渲染错重跑管线。

## 交付收尾清单（任务结束必做）

用户明确要求（2026-10-28）：**一次性 .py 脚本一律不留**，交付目录只留
模型/装配体/工程图/STEP + 效果图 PNG + 验收与参数 JSON + 人读 md 报告。

1. 删除所有一次性脚本（根目录散落的 fix_*/render_*/audit、各交付目录的
   build_*/check_*、scripts/ 整目录）、运行 JSON、BMP、`__pycache__`、`.workbuddy-ai/tmp/`。
2. **分小批 rm**（≤10 个/批）：大批量 rm 实测会被 SIGTERM 中断且部分执行，
   造成"以为删了其实还在"。
3. **`find` 验收归零**：`find outputs -name "*.py" -o -name "*.bmp" | wc -l` 必须 = 0。
4. **同步更新文档**：该目录 README/交付报告里的交付清单删掉脚本条目，
   否则文档引用失效文件。

## 发现问题时

1. 不要只报告“文件已保存”。
2. 先定位是草图、选择、拉伸方向、单位、基准面还是导出失败。
3. 修改脚本后重新生成并再次导出预览图。
4. 最终回复中说明已检查的预览图和仍有限制的地方。
