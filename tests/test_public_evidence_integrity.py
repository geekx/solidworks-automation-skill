"""验证公开证据的发布字节和清单链。"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "docs/architecture/evidence/p0-2-1-2026-10-27"


def test_public_evidence_manifest_and_chain():
    manifest = json.loads((EVIDENCE_DIR / "manifest.json").read_bytes())
    for item in manifest["files"]:
        data = (EVIDENCE_DIR / item["published"]).read_bytes()
        assert b"\r\n" not in data, item["published"]
        assert len(data) == item["published_bytes"], item["published"]
        assert hashlib.sha256(data).hexdigest() == item["published_sha256"], item["published"]
    index = json.loads((EVIDENCE_DIR / "p0-2-1-evidence-sha256.json").read_bytes())
    path = "docs/architecture/evidence/p0-2-1-real-gripper-travel-2026-10-27.json"
    entry = next(item for item in index if item["path"] == path)
    data = (ROOT / path).read_bytes()
    assert b"\r\n" not in data
    assert len(data) == entry["size_bytes"]
    assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    item = next(item for item in manifest["files"] if item["published"] == "p0-2-1-evidence-sha256.json")
    assert item["redacted"]
    assert item["original_sha256"] != item["published_sha256"]
