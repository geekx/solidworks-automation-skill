"""SolidWorks 新建文档回归测试。

重点是复现并锁定一个数据破坏型缺陷：当 ``NewDocument`` 返回 ``None`` 时，
旧实现会调用 ``sw.ActiveDoc`` 兜底，把用户先前打开的无关文档当成新建结果返回，
导致后续所有特征静默画到错误的文档上。
"""
from pathlib import Path

import pytest

from scripts import sw_connect


class FakeModel:
    """@brief 最小文档对象，用标题区分身份。"""

    def __init__(self, title, path=""):
        self._title = title
        self._path = path

    def GetTitle(self):
        return self._title

    def GetPathName(self):
        return self._path


class FakeSolidWorks:
    """@brief 维护一个打开文档列表的可控 SolidWorks 假体。"""

    def __init__(self, documents=()):
        self.documents = list(documents)
        self.new_document_return = None
        self.new_document_side_effect = None
        self.new_document_calls = []

    def GetDocuments(self):
        return list(self.documents)

    def NewDocument(self, template, *_args):
        self.new_document_calls.append(template)
        if self.new_document_side_effect is not None:
            self.new_document_side_effect(self)
        return self.new_document_return


@pytest.fixture(autouse=True)
def _stub_template(monkeypatch):
    """@brief 绕过模板查找，专注新建文档的身份判定逻辑。"""
    monkeypatch.setattr(sw_connect, "find_template", lambda _sw, _doc_type: r"C:\fake\part.prtdot")


def test_new_document_uses_returned_model():
    """@brief 正常路径：NewDocument 返回值即为新建文档。"""
    created = FakeModel("Part1")
    sw = FakeSolidWorks()

    def create(document_owner):
        document_owner.documents.append(created)

    sw.new_document_side_effect = create
    sw.new_document_return = created

    assert sw_connect.new_document(sw, "part") is created


def test_new_document_never_falls_back_to_preexisting_active_document():
    """@brief 核心缺陷：NewDocument 返回 None 时不得把用户已打开的文档当成新建结果。

    旧实现在此处返回 ``user_part``，于是后续建模全部写进了用户的文档。
    """
    user_part = FakeModel("UserPart.SLDPRT", r"C:\user\important.sldprt")
    sw = FakeSolidWorks([user_part])
    sw.new_document_return = None  # 未创建任何新文档

    with pytest.raises(RuntimeError) as excinfo:
        sw_connect.new_document(sw, "part")

    message = str(excinfo.value)
    assert "未返回新文档" in message
    # 错误信息必须给出可操作的下一步，而不是只报"失败"
    assert "模板" in message


def test_new_document_recovers_when_return_value_is_none_but_document_exists():
    """@brief 退化路径：返回值缺失时按标题差集找回真正的新文档。"""
    user_part = FakeModel("UserPart.SLDPRT", r"C:\user\important.sldprt")
    created = FakeModel("Part1")
    sw = FakeSolidWorks([user_part])

    def create(document_owner):
        document_owner.documents.append(created)

    sw.new_document_side_effect = create
    sw.new_document_return = None

    assert sw_connect.new_document(sw, "part") is created


def test_new_document_rejects_stale_return_value():
    """@brief 返回值若与已打开文档同名，视为陈旧引用并改走差集定位。"""
    user_part = FakeModel("UserPart.SLDPRT")
    created = FakeModel("Part1")
    sw = FakeSolidWorks([user_part])

    def create(document_owner):
        document_owner.documents.append(created)

    sw.new_document_side_effect = create
    sw.new_document_return = user_part  # 陈旧引用

    assert sw_connect.new_document(sw, "part") is created


def test_new_document_detects_two_documents_with_same_title():
    """@brief 同名文档场景：差集按标题判定，用户文档不会被误取。"""
    user_part = FakeModel("Part1")
    sw = FakeSolidWorks([user_part])
    sw.new_document_return = None

    with pytest.raises(RuntimeError):
        sw_connect.new_document(sw, "part")


def test_snapshot_open_documents_tolerates_none():
    """@brief 无文档时 GetDocuments 可能返回 None。"""

    class EmptySolidWorks:
        def GetDocuments(self):
            return None

    assert sw_connect.snapshot_open_documents(EmptySolidWorks()) == set()


def test_snapshot_open_documents_tolerates_broken_member():
    """@brief 单个文档读取标题失败时不应中断整体快照。"""

    class BrokenSolidWorks:
        def GetDocuments(self):
            return [FakeModel("Good"), object()]

    assert sw_connect.snapshot_open_documents(BrokenSolidWorks()) == {"Good"}


def test_new_document_expands_explicit_template(monkeypatch, tmp_path):
    """@brief 显式模板路径应展开为绝对路径后再传给 NewDocument。"""
    template = tmp_path / "custom.prtdot"
    template.write_bytes(b"template")
    created = FakeModel("Part1")
    sw = FakeSolidWorks()

    def create(document_owner):
        document_owner.documents.append(created)

    sw.new_document_side_effect = create
    sw.new_document_return = created

    sw_connect.new_document(sw, "part", str(template))

    assert Path(sw.new_document_calls[0]) == template.resolve()
