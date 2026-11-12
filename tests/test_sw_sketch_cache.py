"""草图选择缓存的生命周期回归测试。

锁定两类缺陷：
1. 模块级全局字典跨文档累积 COM 引用，长跑进程无法释放已关闭文档。
2. 仅以 ``id(model)`` / ``id(_oleobj_)`` 作键时，COM 代理被 GC 后 CPython 会复用
   内存地址，旧缓存可能错误命中新文档，导致特征建到错误的文档上。
"""
import gc
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# 在导入 sw_part 之前注入 COM 依赖替身，避免真机依赖。
sys.modules.setdefault(
    "sw_preflight",
    types.SimpleNamespace(
        import_com_dependencies=lambda: (
            types.SimpleNamespace(VT_DISPATCH=9),
            types.SimpleNamespace(),
            lambda *args: None,
        )
    ),
)
sys.modules.setdefault(
    "sw_connect",
    types.SimpleNamespace(
        get_com_member=lambda obj, attr, *args: getattr(obj, attr)(*args)
        if args or callable(getattr(obj, attr))
        else getattr(obj, attr),
        # sw_part 在导入时需要这个工厂（用于 Select4 的 Callout 参数）
        create_empty_dispatch_variant=lambda: None,
        mm=lambda value: value / 1000.0,
        deg=lambda value: value * 3.141592653589793 / 180.0,
    ),
)

import sw_part  # noqa: E402


class FakeModel:
    """@brief 带标题/路径指纹的最小文档对象。"""

    def __init__(self, title, path=""):
        self._title = title
        self._path = path
        self._oleobj_ = object()

    def GetTitle(self):
        return self._title

    def GetPathName(self):
        return self._path


@pytest.fixture(autouse=True)
def _clean_cache():
    """@brief 每个用例前后清空缓存，避免用例间串扰。"""
    sw_part.clear_sketch_selection_cache()
    yield
    sw_part.clear_sketch_selection_cache()


def _ref(name="Sketch1", source="test"):
    return sw_part.SketchSelectionRef(name=name, source=source)


def test_cache_hit_within_same_document():
    """@brief 同一文档内应命中缓存，避免退化为 SelectByID2("SKETCH")。"""
    model = FakeModel("Part1")
    ref = _ref()

    sw_part._cache_sketch_ref(model, ref)

    assert sw_part._find_cached_sketch_ref(model, "Sketch1") is ref


def test_cache_does_not_leak_across_documents():
    """@brief 不同文档之间不得互相命中。"""
    first = FakeModel("Part1")
    second = FakeModel("Assembly1")

    sw_part._cache_sketch_ref(first, _ref())

    assert sw_part._find_cached_sketch_ref(second, "Sketch1") is None


def test_cache_rejects_reused_ole_object_id():
    """@brief 核心缺陷：COM 地址被复用后不得误命中旧文档的草图。"""
    original = FakeModel("Part1")
    sw_part._cache_sketch_ref(original, _ref())

    # 模拟 CPython 复用内存地址：新文档拿到与旧文档相同的底层 ole 对象标识。
    reused = FakeModel("DifferentPart")
    reused._oleobj_ = original._oleobj_

    assert sw_part._find_cached_sketch_ref(reused, "Sketch1") is None


def test_cache_rejects_reused_id_after_gc():
    """@brief 原文档被回收后，其缓存条目必须失效。"""
    model = FakeModel("Part1")
    sw_part._cache_sketch_ref(model, _ref())

    del model
    gc.collect()
    sw_part._prune_sketch_cache()

    assert len(sw_part._SKETCH_SELECTION_CACHE) == 0


def test_cache_invalidated_when_active_document_switches():
    """@brief 文档标题变化（同一 COM 地址换绑文档）必须使缓存失效。"""
    model = FakeModel("Part1")
    sw_part._cache_sketch_ref(model, _ref())

    model._title = "Part2"  # 同一地址，文档已切换

    assert sw_part._find_cached_sketch_ref(model, "Sketch1") is None


def test_cache_entries_are_bounded():
    """@brief 缓存条目数必须有上限，防止长跑进程无界增长。"""
    for index in range(sw_part._SKETCH_CACHE_LIMIT + 10):
        model = FakeModel(f"Part{index}")
        sw_part._cache_sketch_ref(model, _ref(f"Sketch{index}"))

    assert len(sw_part._SKETCH_SELECTION_CACHE) <= sw_part._SKETCH_CACHE_LIMIT


def test_clear_cache_for_single_document():
    """@brief 按文档定向清理时不影响其它文档。"""
    first = FakeModel("Part1")
    second = FakeModel("Part2")
    first_ref = _ref()
    second_ref = _ref()

    sw_part._cache_sketch_ref(first, first_ref)
    sw_part._cache_sketch_ref(second, second_ref)

    sw_part.clear_sketch_selection_cache(first)

    assert sw_part._find_cached_sketch_ref(first, "Sketch1") is None
    assert sw_part._find_cached_sketch_ref(second, "Sketch1") is second_ref


def test_clear_cache_all():
    """@brief 全量清理后缓存为空。"""
    sw_part._cache_sketch_ref(FakeModel("Part1"), _ref())
    sw_part._cache_sketch_ref(FakeModel("Part2"), _ref())

    sw_part.clear_sketch_selection_cache()

    assert len(sw_part._SKETCH_SELECTION_CACHE) == 0


def test_explicit_sketch_ref_bypasses_cache():
    """@brief 直接传入 SketchSelectionRef 时无需查缓存。"""
    ref = _ref()
    assert sw_part._find_cached_sketch_ref(FakeModel("Part1"), ref) is ref
