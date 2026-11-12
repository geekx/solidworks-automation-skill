from __future__ import annotations

import pytest

import sw_feature_resolver as resolver


def _entity(**overrides):
    entity = {
        "entityToken": "ent-" + "a" * 24,
        "entityType": "Face",
        "geometryType": "Cylinder",
        "featureCandidates": [
            {"featureName": "Cut-Extrude3", "featureType": "Cut", "confidence": 0.93},
            {"featureName": "Fillet1", "featureType": "Fillet", "confidence": 0.40},
        ],
        "resolution": {"status": "resolved", "method": "face_get_feature", "confidence": 0.93},
    }
    entity.update(overrides)
    return entity


def test_cylindrical_face_prefers_cut_over_fillet():
    result = resolver.rank_candidates(_entity(), _entity()["featureCandidates"])
    assert result["status"] == "resolved"
    assert result["best"]["featureName"] == "Cut-Extrude3"


def test_unresolved_when_no_candidates():
    result = resolver.rank_candidates({"entityType": "Face", "geometryType": "Planar"}, [])
    assert result["status"] == "unresolved"
    assert result["best"] is None


def test_ambiguous_when_scores_are_close():
    candidates = [
        {"featureName": "Cut-Extrude1", "featureType": "Cut", "confidence": 0.9},
        {"featureName": "Cut-Extrude2", "featureType": "Cut", "confidence": 0.9},
    ]
    result = resolver.rank_candidates({"entityType": "Face", "geometryType": "Cylinder"}, candidates)
    assert result["status"] == "ambiguous"
    assert result["reason"]


def test_imported_body_is_penalized():
    candidates = [
        {"featureName": "ImportedBody2", "featureType": "Imported", "confidence": 0.99},
        {"featureName": "Cut-Extrude3", "featureType": "Cut", "confidence": 0.9},
    ]
    result = resolver.rank_candidates({"entityType": "Face", "geometryType": "Cylinder"}, candidates)
    assert result["status"] == "resolved"
    assert result["best"]["featureName"] == "Cut-Extrude3"


def test_resolve_entity_context_keeps_resolution_method():
    result = resolver.resolve_entity_context(_entity())
    assert result["method"] == "face_get_feature"
    assert result["status"] == "resolved"


def test_resolve_entity_context_reports_unresolved_reason():
    entity = _entity(
        featureCandidates=[],
        resolution={"status": "unresolved", "method": "none", "reason": "导入实体不可追溯"},
    )
    result = resolver.resolve_entity_context(entity)
    assert result["status"] == "unresolved"
    assert result["reason"] == "导入实体不可追溯"


# --------------------------------------------------------------------------- COM


class _Feature:
    def __init__(self, name: str, type_name: str) -> None:
        self.Name = name
        self._type_name = type_name

    def GetTypeName2(self):
        return self._type_name


class _FaceWithFeature:
    def __init__(self, feature) -> None:
        self._feature = feature

    def GetFeature(self):
        return self._feature


class _Dimension:
    def __init__(self, full_name: str) -> None:
        self._full_name = full_name

    def FullName(self):
        return self._full_name


class _DisplayDimension:
    def __init__(self, name: str, next_dimension=None) -> None:
        self._name = name
        self._next = next_dimension

    def GetDimension(self):
        return _Dimension(self._name)

    def GetNext3(self):
        return self._next


def test_resolve_feature_from_face_uses_get_feature():
    face = _FaceWithFeature(_Feature("Cut-Extrude3", "Cut"))
    result = resolver.resolve_feature_from_com(face, "Face")
    assert result["status"] == "resolved"
    assert result["feature"]["featureName"] == "Cut-Extrude3"
    assert result["method"] == "face_get_feature"


def test_resolve_feature_from_imported_face_is_unresolved():
    class _Bare:
        pass

    result = resolver.resolve_feature_from_com(_Bare(), "Face")
    assert result["status"] == "unresolved"
    assert result["reason"]


def test_list_feature_dimensions_walks_display_dimensions():
    second = _DisplayDimension("D2@Sketch4")
    first = _DisplayDimension("D1@Sketch4", second)
    feature = _Feature("Cut-Extrude3", "Cut")
    feature.GetFirstDisplayDimension = lambda: first
    assert resolver.list_feature_dimensions(feature) == ["D1@Sketch4", "D2@Sketch4"]


def test_list_feature_dimensions_survives_missing_api():
    assert resolver.list_feature_dimensions(object()) == []


def test_resolve_feature_from_dimension_reads_full_name():
    class _Dim:
        def GetFeature(self):
            return _Feature("Sketch4", "Sketch")

        def FullName(self):
            return "D1@Sketch4"

    result = resolver.resolve_feature_from_com(_Dim(), "Dimension")
    assert result["status"] == "resolved"
    assert result["dimensionNames"] == ["D1@Sketch4"]


def test_geometry_affinity_is_not_evidence_of_ownership():
    # 亲和度只影响排序；没有候选时不得凭空产生 Feature。
    assert resolver.rank_candidates({"entityType": "Edge", "geometryType": "Line"}, [])["status"] == "unresolved"
