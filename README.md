# doccheap-haystack

[![PyPI](https://img.shields.io/pypi/v/doccheap-haystack.svg)](https://pypi.org/project/doccheap-haystack/)

A [Haystack](https://haystack.deepset.ai/) component for
[doc.cheap](https://doc.cheap): give it photos or scans of passports, national
ID cards or driving licences and it returns Haystack documents with the printed
fields – name, date of birth, document number, expiry, the machine-readable
zone.

One recognised document costs one credit, $0.01. Registering gives 100 free
documents every month. An unreadable image, an empty frame or an unsupported
document type costs nothing, and every answer says whether it was billed.

The package needs Python 3.10 or later. It is built on the
[doc-cheap](https://pypi.org/project/doc-cheap/) client and `haystack-ai`.

## Installation

```sh
pip install doccheap-haystack
```

## The component

`DocCheapConverter` takes a list of `sources`: image file paths, `http(s)://`
URLs or `ByteStream` objects, each one JPEG or PNG image of up to 25 MiB.

It returns two outputs:

- `documents` – one `Document` per image. Its content is one `Label: value`
  line per field that was read. Its meta holds `doc_cheap_scan_id`,
  `doc_cheap_status` and `doc_cheap_billed`, plus the source's `file_path` or
  `url` and any `meta` you passed in.
- `scans` – the API's full answer for each of those images, in the same order.

An image with no readable document still gives a document: its content is
empty and `doc_cheap_status` says why, for example `no_document_found`. A
source that cannot be opened or downloaded, is empty or is too large is skipped
with a warning. Any other failure – a wrong key, no credits left, a bad setting
– is raised.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `api_key` | `Secret.from_env_var("DOC_CHEAP_API_KEY", strict=False)` | The API key. |
| `base_url` | `DOC_CHEAP_API_BASE`, else `https://api.doc.cheap` | The API address. |
| `timeout` | `60.0` | Seconds to wait for the API and for each URL download. |
| `expect_country` | `None` | The issuing country you expect (ISO 3166-1 alpha-3). |
| `retain_hours` | `None` | How long the API keeps the result; `0` keeps nothing. |
| `store_full_path` | `False` | Keep the whole file path in meta, not just the file name. |

## Usage

On its own:

```python
from haystack.utils import Secret
from haystack_integrations.components.converters.doc_cheap import DocCheapConverter

converter = DocCheapConverter(api_key=Secret.from_token("sk_sandbox_public"))
result = converter.run(sources=["https://doc.cheap/og-card.png"])

for document in result["documents"]:
    print(document.meta["doc_cheap_status"], document.meta["doc_cheap_billed"])
    print(document.content)
```

In a pipeline that stores the results:

```python
import os

from haystack import Pipeline
from haystack.components.writers import DocumentWriter
from haystack.document_stores.in_memory import InMemoryDocumentStore
from haystack_integrations.components.converters.doc_cheap import DocCheapConverter

os.environ.setdefault("DOC_CHEAP_API_KEY", "sk_sandbox_public")

store = InMemoryDocumentStore()
pipeline = Pipeline()
pipeline.add_component("reader", DocCheapConverter())
pipeline.add_component("writer", DocumentWriter(document_store=store))
pipeline.connect("reader.documents", "writer.documents")

pipeline.run({"reader": {"sources": ["https://doc.cheap/og-card.png"]}})
print(store.count_documents())
```

The component serialises with the pipeline. The key is saved as the name of
its environment variable, never as its value.

## API keys

- **Live key** – `sk_live_…`, created in the
  [doc.cheap cabinet](https://doc.cheap/docs/concepts/api-keys-and-sessions).
- **Sandbox key** – `sk_sandbox_public` works without an account. It gives 10
  free recognised documents per address in all, and at most 10 requests per
  address an hour, whatever their answer.

## Resources

- [doc.cheap documentation](https://doc.cheap/docs)
- [Your first recognition](https://doc.cheap/docs/start/first-recognition)
- [API reference](https://doc.cheap/docs/reference)
- [The doc-cheap Python client](https://pypi.org/project/doc-cheap/)

## License

[MIT](LICENSE)
