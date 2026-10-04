"""The doc.cheap converter: images of identity documents in, Haystack documents out."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from doc_cheap import API_KEY_ENV, MAX_IMAGE_BYTES, Client, Scan
from haystack import Document, component, default_from_dict, default_to_dict, logging
from haystack.components.converters.utils import get_bytestream_from_source, normalize_metadata
from haystack.dataclasses import ByteStream
from haystack.utils import Secret, deserialize_secrets_inplace

from ._version import __version__

logger = logging.getLogger(__name__)

_USER_AGENT = f"doccheap-haystack/{__version__}"
_URL_SCHEMES = ("http://", "https://")

Source = str | Path | ByteStream


class _UnusableImage(ValueError):
    """A source that was read but cannot be sent: empty, or over the API's size limit."""


def _download(url: str, timeout: float) -> ByteStream:
    # The body is read in chunks and abandoned past the API's own size limit,
    # so a wrong or hostile URL cannot make the pipeline hold an unbounded
    # download in memory.
    headers = {"User-Agent": _USER_AGENT}
    with httpx.stream(
        "GET", url, timeout=timeout, follow_redirects=True, headers=headers
    ) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > MAX_IMAGE_BYTES:
                raise _UnusableImage(f"{url} is larger than {MAX_IMAGE_BYTES} bytes")
        mime_type = response.headers.get("content-type")
    return ByteStream(data=bytes(data), meta={"url": url}, mime_type=mime_type)


def _content(scan: Scan) -> str:
    # The document's text is one "Label: value" line per field that was read,
    # in the order the API returns them, so it can be embedded, searched or
    # handed to a prompt like any other converted text.
    lines = []
    for field in scan.get("fields") or []:
        value = field.get("value")
        if value is not None:
            lines.append(f"{field.get('label')}: {value}")
    return "\n".join(lines)


@component
class DocCheapConverter:
    """
    Reads passports, national ID cards and driver's licences with the doc.cheap API.

    Each source is one JPEG or PNG image: a file path, an ``http(s)://`` URL or a
    ``ByteStream``. Every image that could be read becomes a ``Document`` whose
    content holds the printed fields as ``Label: value`` lines and whose meta
    carries the scan's id, status and whether it was billed. The full API answer
    for each image comes out as well, under ``scans``.

    An image that holds no readable document is not an error: it still yields a
    document, with empty content and the status the API gave it.

    ### Usage example

    ```python
    from haystack.utils import Secret
    from haystack_integrations.components.converters.doc_cheap import DocCheapConverter

    converter = DocCheapConverter(api_key=Secret.from_token("sk_sandbox_public"))
    result = converter.run(sources=["https://doc.cheap/og-card.png"])
    print(result["documents"][0].meta["doc_cheap_status"])
    ```
    """

    def __init__(
        self,
        api_key: Secret = Secret.from_env_var(API_KEY_ENV, strict=False),  # noqa: B008
        base_url: str | None = None,
        timeout: float = 60.0,
        expect_country: str | None = None,
        retain_hours: int | None = None,
        store_full_path: bool = False,
    ) -> None:
        """
        Creates a DocCheapConverter.

        :param api_key: The doc.cheap API key. By default it is read from the
            ``DOC_CHEAP_API_KEY`` environment variable. ``sk_sandbox_public`` works
            without an account.
        :param base_url: The API address. By default ``DOC_CHEAP_API_BASE``, else
            ``https://api.doc.cheap``.
        :param timeout: Seconds to wait for the API, and for each URL download.
        :param expect_country: The issuing country you expect, as three upper-case
            letters (ISO 3166-1 alpha-3).
        :param retain_hours: How many hours the API keeps the result; ``0`` keeps
            nothing. By default the account's setting applies.
        :param store_full_path: If True, the full file path is stored in the
            document's meta; if False, only the file name.
        """
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.expect_country = expect_country
        self.retain_hours = retain_hours
        self.store_full_path = store_full_path

    def to_dict(self) -> dict[str, Any]:
        """
        Serializes the component to a dictionary.

        :returns: The dictionary with the serialized data.
        """
        return default_to_dict(
            self,
            api_key=self.api_key.to_dict(),
            base_url=self.base_url,
            timeout=self.timeout,
            expect_country=self.expect_country,
            retain_hours=self.retain_hours,
            store_full_path=self.store_full_path,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocCheapConverter:
        """
        Deserializes the component from a dictionary.

        :param data: The dictionary to deserialize from.
        :returns: The deserialized component.
        """
        deserialize_secrets_inplace(data["init_parameters"], keys=["api_key"])
        return default_from_dict(cls, data)

    def _read(self, source: Source) -> ByteStream:
        if isinstance(source, str) and source.startswith(_URL_SCHEMES):
            stream = _download(source, self.timeout)
        else:
            stream = get_bytestream_from_source(source)
        # Checked here rather than left to the client, so that only a bad
        # source is skipped: the client raises the same error type for a bad
        # setting such as expect_country, and that must not be swallowed.
        if not stream.data:
            raise _UnusableImage(f"{source} is empty")
        if len(stream.data) > MAX_IMAGE_BYTES:
            raise _UnusableImage(f"{source} is larger than {MAX_IMAGE_BYTES} bytes")
        return stream

    @component.output_types(documents=list[Document], scans=list[dict[str, Any]])
    def run(
        self,
        sources: list[str | Path | ByteStream],
        meta: dict[str, Any] | list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Reads each source image and returns what was printed on it.

        A source that cannot be fetched or opened, is empty or is over 25 MiB is
        skipped with a warning, as other Haystack converters do. Any other
        failure is raised: a bad setting, a wrong key, no credits left or the
        service being unavailable, so a misconfigured pipeline fails loudly
        instead of returning nothing.

        :param sources: Image file paths, ``http(s)://`` URLs or ``ByteStream`` objects.
        :param meta: Metadata for the documents: one dictionary for all of them,
            or a list with one dictionary per source.
        :returns: A dictionary with:
            - ``documents``: one ``Document`` per image that was sent.
            - ``scans``: the API's full answer for each of those images, in the
              same order.
        """
        meta_list = normalize_metadata(meta, sources_count=len(sources))
        documents: list[Document] = []
        scans: list[dict[str, Any]] = []
        with Client(
            self.api_key.resolve_value(),
            base_url=self.base_url,
            timeout=self.timeout,
            user_agent=_USER_AGENT,
        ) as client:
            for source, metadata in zip(sources, meta_list, strict=True):
                try:
                    stream = self._read(source)
                except (OSError, ValueError, httpx.HTTPError) as error:
                    logger.warning(
                        "Could not read {source}. Skipping it. Error: {error}",
                        source=source,
                        error=error,
                    )
                    continue
                scan = client.recognize(
                    image=stream.data,
                    expect_country=self.expect_country,
                    retain_hours=self.retain_hours,
                )
                merged = {**stream.meta, **metadata}
                file_path = stream.meta.get("file_path")
                if file_path and not self.store_full_path:
                    merged["file_path"] = Path(file_path).name
                scan_meta = scan.get("meta") or {}
                merged["doc_cheap_scan_id"] = scan_meta.get("id")
                merged["doc_cheap_status"] = scan_meta.get("status")
                merged["doc_cheap_billed"] = scan_meta.get("billed")
                documents.append(Document(content=_content(scan), meta=merged))
                scans.append(dict(scan))
        return {"documents": documents, "scans": scans}
