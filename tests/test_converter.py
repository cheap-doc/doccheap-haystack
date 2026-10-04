from __future__ import annotations

import base64
import copy
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from doc_cheap import MAX_IMAGE_BYTES, AuthenticationError, DocCheapError, InvalidArgumentError
from haystack import Document, Pipeline
from haystack.dataclasses import ByteStream
from haystack.utils import Secret

from haystack_integrations.components.converters.doc_cheap import DocCheapConverter, __version__

KEY = Secret.from_token("sk_test_key")
IMAGE = b"\x89PNG-bytes"
SCAN: dict[str, Any] = {
    "meta": {"id": "scn_1", "status": "recognized", "billed": True},
    "fields": [
        {"label": "Surname", "value": "SAMPLE"},
        {"label": "Given names", "value": "ALEX"},
        {"label": "Middle name", "value": None},
    ],
}
EMPTY_SCAN: dict[str, Any] = {
    "meta": {"id": "scn_2", "status": "no_document_found", "billed": False},
    "fields": [],
}


def _converter(**kwargs: Any) -> DocCheapConverter:
    kwargs.setdefault("api_key", KEY)
    return DocCheapConverter(base_url="https://api.test", **kwargs)


def _body(route: respx.Route, index: int = -1) -> Any:
    return json.loads(route.calls[index].request.content)


def test_path_source_becomes_a_document(api: respx.MockRouter, tmp_path: Path) -> None:
    route = api.post("/v1/scans").respond(201, json=SCAN)
    image = tmp_path / "passport.png"
    image.write_bytes(IMAGE)

    result = _converter().run(sources=[str(image)], meta={"case": "c-7"})

    assert _body(route) == {"image": base64.b64encode(IMAGE).decode()}
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer sk_test_key"
    assert request.headers["User-Agent"].startswith(f"doccheap-haystack/{__version__} ")
    [document] = result["documents"]
    assert document.content == "Surname: SAMPLE\nGiven names: ALEX"
    assert document.meta == {
        "file_path": "passport.png",
        "case": "c-7",
        "doc_cheap_scan_id": "scn_1",
        "doc_cheap_status": "recognized",
        "doc_cheap_billed": True,
    }
    assert result["scans"] == [SCAN]


def test_store_full_path_keeps_the_whole_path(api: respx.MockRouter, tmp_path: Path) -> None:
    api.post("/v1/scans").respond(201, json=SCAN)
    image = tmp_path / "passport.png"
    image.write_bytes(IMAGE)

    result = _converter(store_full_path=True).run(sources=[image])

    assert result["documents"][0].meta["file_path"] == str(image)


def test_bytestream_source_with_per_source_meta(api: respx.MockRouter) -> None:
    api.post("/v1/scans").mock(
        side_effect=[httpx.Response(201, json=SCAN), httpx.Response(201, json=EMPTY_SCAN)]
    )
    sources: list[str | Path | ByteStream] = [
        ByteStream(data=IMAGE, meta={"origin": "upload"}),
        ByteStream(data=b"other"),
    ]

    result = _converter().run(sources=sources, meta=[{"n": 1}, {"n": 2}])

    first, second = result["documents"]
    assert first.meta["origin"] == "upload"
    assert first.meta["n"] == 1
    assert "file_path" not in first.meta
    assert second.content == ""
    assert second.meta["doc_cheap_status"] == "no_document_found"
    assert second.meta["doc_cheap_billed"] is False


def test_url_source_is_downloaded(api: respx.MockRouter) -> None:
    download = api.get("https://images.test/card.png").respond(
        200, content=IMAGE, headers={"content-type": "image/png"}
    )
    route = api.post("/v1/scans").respond(201, json=SCAN)

    result = _converter().run(sources=["https://images.test/card.png"])

    assert download.calls[0].request.headers["User-Agent"] == f"doccheap-haystack/{__version__}"
    assert _body(route)["image"] == base64.b64encode(IMAGE).decode()
    assert result["documents"][0].meta["url"] == "https://images.test/card.png"


def test_options_are_sent(api: respx.MockRouter) -> None:
    route = api.post("/v1/scans").respond(201, json=SCAN)

    _converter(expect_country="GBR", retain_hours=0).run(sources=[ByteStream(data=IMAGE)])

    assert _body(route)["options"] == {"expect_country": "GBR", "retain_hours": 0}


def test_unreadable_sources_are_skipped_with_a_warning(
    api: respx.MockRouter, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    route = api.post("/v1/scans").respond(201, json=SCAN)
    api.get("https://images.test/missing.png").respond(404)
    api.get("https://images.test/huge.png").respond(200, content=b"x" * (MAX_IMAGE_BYTES + 1))
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    sources: list[str | Path | ByteStream] = [
        str(tmp_path / "absent.png"),
        str(empty),
        "https://images.test/missing.png",
        "https://images.test/huge.png",
        ByteStream(data=b"x" * (MAX_IMAGE_BYTES + 1)),
        ByteStream(data=IMAGE),
    ]

    with caplog.at_level(logging.WARNING):
        result = _converter().run(sources=sources)

    assert len(result["documents"]) == 1
    assert route.call_count == 1
    assert caplog.text.count("Skipping it") == 5


def test_api_errors_are_raised(api: respx.MockRouter) -> None:
    api.post("/v1/scans").respond(
        401, json={"error": {"code": "invalid_api_key", "message": "Bad key.", "request_id": "r"}}
    )

    with pytest.raises(AuthenticationError):
        _converter().run(sources=[ByteStream(data=IMAGE)])


def test_a_bad_setting_is_raised_not_skipped(api: respx.MockRouter) -> None:
    with pytest.raises(InvalidArgumentError, match="expect_country"):
        _converter(expect_country="gb").run(sources=[ByteStream(data=IMAGE)])


def test_missing_key_is_raised() -> None:
    with pytest.raises(DocCheapError, match="no API key"):
        DocCheapConverter().run(sources=[ByteStream(data=IMAGE)])


def test_key_comes_from_the_environment(
    api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOC_CHEAP_API_KEY", "sk_env_key")
    route = api.post("/v1/scans").respond(201, json=SCAN)

    DocCheapConverter(base_url="https://api.test").run(sources=[ByteStream(data=IMAGE)])

    assert route.calls[0].request.headers["Authorization"] == "Bearer sk_env_key"


def test_mismatched_meta_list_is_refused() -> None:
    with pytest.raises(ValueError, match="length of the metadata list"):
        _converter().run(sources=[ByteStream(data=IMAGE)], meta=[{}, {}])


def test_serialization_round_trip() -> None:
    converter = DocCheapConverter(
        base_url="https://api.test", timeout=5.0, expect_country="GBR", retain_hours=1
    )

    data = converter.to_dict()

    assert data == {
        "type": "haystack_integrations.components.converters.doc_cheap.converter.DocCheapConverter",
        "init_parameters": {
            "api_key": {"type": "env_var", "env_vars": ["DOC_CHEAP_API_KEY"], "strict": False},
            "base_url": "https://api.test",
            "timeout": 5.0,
            "expect_country": "GBR",
            "retain_hours": 1,
            "store_full_path": False,
        },
    }
    restored = DocCheapConverter.from_dict(copy.deepcopy(data))
    assert restored.to_dict() == data


def test_pipeline_dumps_and_loads(api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOC_CHEAP_API_KEY", "sk_env_key")
    api.post("/v1/scans").respond(201, json=SCAN)
    pipeline = Pipeline()
    pipeline.add_component("reader", DocCheapConverter(base_url="https://api.test"))

    loaded = Pipeline.loads(pipeline.dumps())
    result = loaded.run({"reader": {"sources": [ByteStream(data=IMAGE)]}})

    [document] = result["reader"]["documents"]
    assert isinstance(document, Document)
    assert document.meta["doc_cheap_scan_id"] == "scn_1"
