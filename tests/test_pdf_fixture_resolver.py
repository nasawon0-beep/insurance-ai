import json
from pathlib import Path

import pytest

from pdf_fixture_resolver import PRIVATE_PDF_FIXTURE_MAP_ENV, private_pdf


def test_missing_private_fixture_manifest_skips_only_calling_test(monkeypatch):
    monkeypatch.delenv(PRIVATE_PDF_FIXTURE_MAP_ENV, raising=False)

    with pytest.raises(pytest.skip.Exception, match="private real-PDF fixture unavailable"):
        private_pdf("coverage_case_01")


def test_external_manifest_resolves_anonymous_alias(monkeypatch, tmp_path: Path):
    fixture = tmp_path / "synthetic-local.pdf"
    fixture.write_bytes(b"%PDF-1.4\n%%EOF\n")
    manifest = tmp_path / "fixtures.json"
    manifest.write_text(json.dumps({"coverage_case_01": str(fixture)}), encoding="utf-8")
    monkeypatch.setenv(PRIVATE_PDF_FIXTURE_MAP_ENV, str(manifest))

    assert private_pdf("coverage_case_01") == fixture


def test_unknown_alias_in_external_manifest_skips(monkeypatch, tmp_path: Path):
    manifest = tmp_path / "fixtures.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(PRIVATE_PDF_FIXTURE_MAP_ENV, str(manifest))

    with pytest.raises(pytest.skip.Exception, match="private real-PDF fixture unavailable"):
        private_pdf("proposal_case_01")
