"""
测试用的 SolidWorks COM 替身工厂。

背景：此前每个测试文件各自手搓 Fake 类（``sys.modules["sw_connect"] = SimpleNamespace(...)``
之类），导致同一个假体有七八份互不兼容的实现。本模块提供统一的替身，新增测试
应当复用这里的类，而不是再写一份。

设计原则：
- 替身只实现被测代码**实际调用**的成员，其余情况显式抛错，避免"静默返回 None
  让测试假通过"。
- 记录调用序列（``calls``），便于断言"没有调用 SelectByID2"这类回归要求。
- 单位一律按调用方传入处理，不做换算——单位换算本身是被测对象。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple


class FakeVariant:
    """VARIANT 占位类型，只记录构造参数。"""

    def __init__(self, *args):
        self.args = args


class FakeEdge:
    """边线替身。记录选择状态，支持 Select2/Select4。"""

    def __init__(self, index=0, length_m=0.01, start_m=(0.0, 0.0, 0.0), end_m=(0.0, 0.0, 0.01),
                 is_circle=False, diameter_m=None, selectable=True):
        self.index = index
        self.length_m = length_m
        self.start_m = start_m
        self.end_m = end_m
        self.is_circle = is_circle
        self.diameter_m = diameter_m
        self.selectable = selectable
        self.selected = False
        self.select_calls: List[Tuple[Any, ...]] = []

    def GetLength(self):
        return self.length_m

    def GetCurve(self):
        return FakeCurve(is_circle=self.is_circle, diameter_m=self.diameter_m)

    def GetStartVertex(self):
        return FakeVertex(self.start_m)

    def GetEndVertex(self):
        return FakeVertex(self.end_m)

    def GetCurveParams2(self):
        # 圆： [圆心(3), 轴(3), 半径]
        if self.is_circle:
            return [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, self.diameter_m / 2.0 if self.diameter_m else 0.005]
        # 直线：[起点(3), 终点(3), 方向(3)]
        direction = [self.end_m[axis] - self.start_m[axis] for axis in range(3)]
        return [*self.start_m, *self.end_m, *direction]

    def Select2(self, append, mark):
        self.select_calls.append(("Select2", append, mark))
        self.selected = self.selectable
        return self.selectable

    def Select4(self, append, callout):
        self.select_calls.append(("Select4", append, callout))
        self.selected = self.selectable
        return self.selectable


class FakeVertex:
    """顶点替身。"""

    def __init__(self, point_m):
        self.point_m = point_m

    def GetPoint(self):
        return list(self.point_m)


class FakeCurve:
    """曲线替身。"""

    def __init__(self, is_circle=False, diameter_m=None):
        self.is_circle = is_circle
        self.diameter_m = diameter_m

    def IsLine(self):
        return not self.is_circle

    def IsCircle(self):
        return self.is_circle

    def CircleParams(self):
        return [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, self.diameter_m / 2.0 if self.diameter_m else 0.005]


class FakeFace:
    """面替身。"""

    def __init__(self, edges=(), is_cylinder=False, cylinder_radius_m=None):
        self.edges = list(edges)
        self.is_cylinder = is_cylinder
        self.cylinder_radius_m = cylinder_radius_m

    def GetEdges(self):
        return list(self.edges)

    def GetSurface(self):
        return FakeSurface(is_cylinder=self.is_cylinder, radius_m=self.cylinder_radius_m)


class FakeSurface:
    """曲面替身。"""

    def __init__(self, is_cylinder=False, radius_m=None):
        self.is_cylinder = is_cylinder
        self.radius_m = radius_m

    def IsCylinder(self):
        return self.is_cylinder

    def CylinderParams(self):
        return [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, self.radius_m or 0.005]


class FakeBody:
    """实体替身。"""

    def __init__(self, edges=(), faces=()):
        self.edges = list(edges)
        self.faces = list(faces)

    def GetEdges(self):
        return list(self.edges)

    def GetFaces(self):
        return list(self.faces)


class FakeFeature:
    """特征替身。"""

    def __init__(self, name="Feature1"):
        self.Name = name


class FakeMassProperty:
    """质量属性替身。

    注意 Space 类型的成员（Status）通过 ``has_status=False`` 模拟缺失版本。
    """

    def __init__(self, mass_kg=0.0, volume_m3=0.0, area_m2=0.0, center_m=(0.0, 0.0, 0.0),
                 inertia=None, has_status=False):
        self._mass = mass_kg
        self._volume = volume_m3
        self._area = area_m2
        self._center = list(center_m)
        self._inertia = inertia or [0.0] * 9
        if has_status:
            self.Status = 1
        self.override_calls: List[Any] = []

    def Mass(self):
        return self._mass

    def Volume(self):
        return self._volume

    def SurfaceArea(self):
        return self._area

    def CenterOfMass(self):
        return list(self._center)

    def GetMomentOfInertia(self, _frame):
        return list(self._inertia)

    def SetOverrideMass(self, enabled):
        self.override_calls.append(("SetOverrideMass", enabled))


class FakeModel:
    """
    文档替身。

    参数:
        title / path: 文档指纹。
        box_m: GetPartBox(True) 的 6 个坐标（米）。None 表示未创建几何。
        material: 已分配材料名；None 表示未分配。
    """

    def __init__(self, title="Part1", path="", box_m=None, material=None,
                 edges=(), expectation=None, mass_kg=0.0, volume_m3=0.0):
        self._title = title
        self._path = path
        self._oleobj_ = object()
        self._box_m = box_m
        self.MaterialIdName = material
        self._bodies = [FakeBody(edges=edges)] if edges else []
        self._mass = FakeMassProperty(mass_kg=mass_kg, volume_m3=volume_m3)
        self.calls: List[str] = []
        self.expectation = expectation
        self.selection_count = 0
        self.Extension = FakeExtension(self)
        self.SketchManager = FakeSketchManager()
        self.FeatureManager = FakeFeatureManager(self)

    # ---- 基本标识 ----
    def GetTitle(self):
        return self._title

    def GetPathName(self):
        return self._path

    def GetType(self):
        return 1  # swDocPART

    # ---- 几何 ----
    def GetPartBox(self, use_system_units=True):
        self.calls.append(f"GetPartBox({use_system_units})")
        if self._box_m is None:
            raise RuntimeError("no geometry")
        # 关键：GetPartBox(True) 返回米，GetPartBox(False) 返回毫米。
        if use_system_units:
            return list(self._box_m)
        return [value * 1000.0 for value in self._box_m]

    def GetBodies2(self, _type, _visible_only):
        return list(self._bodies)

    def ForceRebuild3(self, _silent):
        self.calls.append("ForceRebuild3")
        return True

    def ClearSelection2(self, _all):
        self.selection_count = 0
        self.calls.append("ClearSelection2")
        return True

    def GetSelectionCount(self):
        return self.selection_count


class FakeExtension:
    """ModelDocExtension 替身。"""

    def __init__(self, model: FakeModel):
        self._model = model
        self.select_by_id_calls: List[Tuple[Any, ...]] = []

    def SelectByID2(self, *args):
        self.select_by_id_calls.append(args)
        return False

    def CreateMassProperty(self):
        return self._model._mass

    def SaveAs(self, *args):
        self._model.calls.append("SaveAs")
        return True


class FakeSketchManager:
    """SketchManager 替身。"""

    def __init__(self):
        self.ActiveSketch = None

    def InsertSketch(self, _update):
        return None


class FakeFeatureManager:
    """FeatureManager 替身。记录每次特征创建的参数。"""

    def __init__(self, model: FakeModel):
        self._model = model
        self.fillet_calls: List[Tuple[Any, ...]] = []
        self.chamfer_calls: List[Tuple[Any, ...]] = []
        self.extrude_calls: List[Tuple[Any, ...]] = []
        self.fillet_result = FakeFeature("Fillet1")
        self.chamfer_result = FakeFeature("Chamfer1")
        self.extrude_result = FakeFeature("Boss-Extrude1")

    def FeatureFillet(self, *args):
        self.fillet_calls.append(args)
        if self._model.selection_count <= 0:
            raise RuntimeError("非选择性的参数")
        return self.fillet_result

    def InsertFeatureChamfer(self, *args):
        self.chamfer_calls.append(args)
        if self._model.selection_count <= 0:
            raise RuntimeError("非选择性的参数")
        return self.chamfer_result

    def FeatureExtrusion3(self, *args):
        self.extrude_calls.append(args)
        return self.extrude_result

    def FeatureCut4(self, *args):
        self.extrude_calls.append(("cut", *args))
        return self.extrude_result


class FakeSolidWorks:
    """ISldWorks 替身：管理文档列表、新建与关闭。"""

    def __init__(self, documents=(), template_ok=True):
        self.documents = list(documents)
        self.template_ok = template_ok
        self.new_document_return = None
        self.new_document_factory = None
        self.closed: List[str] = []
        self.preferences: Dict[int, Any] = {}

    def GetDocuments(self):
        return list(self.documents)

    def NewDocument(self, _template, *_args):
        if not self.template_ok:
            return None
        if self.new_document_factory is not None:
            document = self.new_document_factory()
            self.documents.append(document)
            return document
        return self.new_document_return

    def CloseDoc(self, title):
        self.closed.append(title)
        self.documents = [doc for doc in self.documents if doc.GetTitle() != title]
        return True

    def CloseAllDocuments(self, _save):
        self.closed.extend(doc.GetTitle() for doc in self.documents)
        self.documents = []
        return True

    def GetUserPreferenceIntegerValue(self, preference):
        return self.preferences.get(preference, 0)

    def SetUserPreferenceIntegerValue(self, preference, value):
        self.preferences[preference] = value
        return True

    def RevisionNumber(self):
        return "32.5.0"


def install_com_stubs(monkeypatch=None):
    """
    为 ``scripts`` 包注入 COM 替身，使被测模块可以离线导入。

    返回一个命名空间，便于测试断言。

    用法::

        stubs = install_com_stubs()
        from scripts import sw_measure
    """
    import sys
    import types

    stub_modules = {
        "sw_preflight": types.SimpleNamespace(
            import_com_dependencies=lambda: (
                types.SimpleNamespace(VT_DISPATCH=9, VT_BYREF=16384, VT_I4=3),
                types.SimpleNamespace(),
                FakeVariant,
            ),
            missing_com_dependencies=lambda: [],
            solidworks_installed=lambda: True,
        ),
    }

    def fake_get_com_member(obj, attr_name, *args):
        member = getattr(obj, attr_name)
        if args:
            return member(*args)
        if callable(member):
            try:
                return member()
            except TypeError:
                return member
        return member

    stub_modules["sw_connect"] = types.SimpleNamespace(
        get_com_member=fake_get_com_member,
        create_empty_dispatch_variant=lambda: FakeVariant(),
        mm=lambda value: value / 1000.0,
        deg=lambda value: math.radians(value),
    )

    for name, module in stub_modules.items():
        sys.modules.setdefault(name, module)
    return stub_modules
