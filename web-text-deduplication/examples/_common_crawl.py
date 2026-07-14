"""Common Crawl CDX/WARC ingestion helpers for public Vane examples."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import urlsplit

import pyarrow as pa
from duckdb.datasource import DataSource, DataSourceTask, read_datasource
from selectolax.parser import HTMLParser
from warcio.archiveiterator import ArchiveIterator


COMMON_CRAWL_DATA_BASE = "https://data.commoncrawl.org/"
COMMON_CRAWL_INDEX_BASE = "https://index.commoncrawl.org/"
COMMON_CRAWL_USER_AGENT = "vane-data-common-crawl-example/1.0"
HTML_BLOCK_SELECTOR = (
    "title, article, main, p, h1, h2, h3, h4, h5, h6, li, div, section, "
    "img[alt], figcaption, caption, blockquote, table th, table td, pre, code, "
    'summary, meta[name="description"], meta[property="og:title"], '
    'meta[property="og:description"]'
)

RAW_WARC_SCHEMA = {
    "crawl_id": "VARCHAR",
    "target_url": "VARCHAR",
    "capture_timestamp": "VARCHAR",
    "index_url": "VARCHAR",
    "index_mime": "VARCHAR",
    "index_status": "INTEGER",
    "warc_digest": "VARCHAR",
    "warc_filename": "VARCHAR",
    "warc_offset": "BIGINT",
    "warc_length": "BIGINT",
    "warc_record_id": "VARCHAR",
    "warc_date": "VARCHAR",
    "payload_type": "VARCHAR",
    "http_content_type": "VARCHAR",
    "html_bytes": "BLOB",
}

BLOCK_SCHEMA = {
    "doc_id": "VARCHAR",
    "source": "VARCHAR",
    "domain": "VARCHAR",
    "crawled_at": "VARCHAR",
    "title": "VARCHAR",
    "body": "VARCHAR",
    "block_index": "BIGINT",
    "block_chars": "BIGINT",
    "crawl_id": "VARCHAR",
    "target_url": "VARCHAR",
    "capture_url": "VARCHAR",
    "capture_timestamp": "VARCHAR",
    "warc_record_id": "VARCHAR",
    "warc_date": "VARCHAR",
    "warc_filename": "VARCHAR",
    "warc_offset": "BIGINT",
    "warc_length": "BIGINT",
    "warc_digest": "VARCHAR",
    "http_content_type": "VARCHAR",
}

RAW_WARC_ARROW_SCHEMA = {
    "crawl_id": pa.string(),
    "target_url": pa.string(),
    "capture_timestamp": pa.string(),
    "index_url": pa.string(),
    "index_mime": pa.string(),
    "index_status": pa.int32(),
    "warc_digest": pa.string(),
    "warc_filename": pa.string(),
    "warc_offset": pa.int64(),
    "warc_length": pa.int64(),
    "warc_record_id": pa.string(),
    "warc_date": pa.string(),
    "payload_type": pa.string(),
    "http_content_type": pa.string(),
    "html_bytes": pa.binary(),
}

BLOCK_ARROW_SCHEMA = {
    "doc_id": pa.string(),
    "source": pa.string(),
    "domain": pa.string(),
    "crawled_at": pa.string(),
    "title": pa.string(),
    "body": pa.string(),
    "block_index": pa.int64(),
    "block_chars": pa.int64(),
    "crawl_id": pa.string(),
    "target_url": pa.string(),
    "capture_url": pa.string(),
    "capture_timestamp": pa.string(),
    "warc_record_id": pa.string(),
    "warc_date": pa.string(),
    "warc_filename": pa.string(),
    "warc_offset": pa.int64(),
    "warc_length": pa.int64(),
    "warc_digest": pa.string(),
    "http_content_type": pa.string(),
}


def table_from_rows(
    rows: list[dict[str, Any]], schema: dict[str, pa.DataType]
) -> pa.Table:
    return pa.table(
        {
            name: pa.array([row.get(name) for row in rows], data_type)
            for name, data_type in schema.items()
        }
    )


@dataclass(frozen=True)
class WarcRecordSpec:
    crawl_id: str
    target_url: str
    capture_timestamp: str
    url: str
    mime: str
    status: int
    digest: str
    length: int
    offset: int
    filename: str
    languages: str = ""
    encoding: str = ""

    @classmethod
    def from_mapping(
        cls, row: dict[str, Any], *, crawl_id: str = "", target_url: str = ""
    ) -> "WarcRecordSpec":
        spec = cls(
            crawl_id=str(row.get("crawl_id") or crawl_id),
            target_url=str(
                row.get("target_url") or target_url or row.get("url") or ""
            ),
            capture_timestamp=str(
                row.get("capture_timestamp") or row.get("timestamp") or ""
            ),
            url=str(row.get("url") or ""),
            mime=str(row.get("mime") or ""),
            status=int(row.get("status") or 0),
            digest=str(row.get("digest") or ""),
            length=int(row.get("length") or 0),
            offset=int(row.get("offset") or 0),
            filename=str(row.get("filename") or ""),
            languages=str(row.get("languages") or ""),
            encoding=str(row.get("encoding") or ""),
        )
        spec.validate()
        return spec

    def validate(self) -> None:
        if not self.crawl_id.startswith("CC-MAIN-"):
            raise ValueError(f"invalid Common Crawl ID: {self.crawl_id!r}")
        if not re.fullmatch(r"\d{14}", self.capture_timestamp):
            raise ValueError(
                "Common Crawl capture timestamp must contain 14 digits"
            )
        for label, value in (
            ("target URL", self.target_url),
            ("capture URL", self.url),
        ):
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(f"invalid Common Crawl {label}: {value!r}")
        if not self.filename.startswith(f"crawl-data/{self.crawl_id}/"):
            raise ValueError(
                f"WARC filename does not belong to {self.crawl_id}: {self.filename!r}"
            )
        if self.length <= 0 or self.offset < 0:
            raise ValueError(
                "WARC range length must be positive and offset non-negative"
            )
        if not self.digest:
            raise ValueError("Common Crawl record digest must not be empty")
        if self.status != 200 or self.mime != "text/html":
            raise ValueError("record manifest must contain HTTP 200 text/html captures")

    def as_dict(self) -> dict[str, Any]:
        return {
            "crawl_id": self.crawl_id,
            "target_url": self.target_url,
            "capture_timestamp": self.capture_timestamp,
            "url": self.url,
            "mime": self.mime,
            "status": self.status,
            "digest": self.digest,
            "length": self.length,
            "offset": self.offset,
            "filename": self.filename,
            "languages": self.languages,
            "encoding": self.encoding,
        }


def load_record_manifest(path: Path) -> list[WarcRecordSpec]:
    with path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise ValueError(f"Common Crawl record manifest is empty: {path}")
    specs = [WarcRecordSpec.from_mapping(row) for row in rows]
    keys = {(spec.filename, spec.offset, spec.length) for spec in specs}
    if len(keys) != len(specs):
        raise ValueError("Common Crawl record manifest contains duplicate WARC ranges")
    return specs


def load_target_manifest(path: Path) -> list[tuple[str, int]]:
    with path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    targets = [
        (str(row["target_url"]), int(row.get("captures") or 1)) for row in rows
    ]
    if not targets or any(captures <= 0 for _, captures in targets):
        raise ValueError("target manifest must contain positive capture counts")
    return targets


def query_capture_index(
    *, crawl_id: str, target_url: str, limit: int, timeout: int = 60
) -> list[WarcRecordSpec]:
    params = urllib.parse.urlencode(
        [
            ("url", target_url),
            ("output", "json"),
            ("filter", "status:200"),
            ("filter", "mime:text/html"),
            ("limit", str(limit)),
        ]
    )
    url = f"{COMMON_CRAWL_INDEX_BASE}{crawl_id}-index?{params}"
    request = urllib.request.Request(
        url, headers={"User-Agent": COMMON_CRAWL_USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        lines = response.read().decode("utf-8").splitlines()
    specs = [
        WarcRecordSpec.from_mapping(
            json.loads(line), crawl_id=crawl_id, target_url=target_url
        )
        for line in lines
        if line.strip()
    ]
    if len(specs) < limit:
        raise RuntimeError(
            f"Common Crawl index returned {len(specs)} captures for {target_url}; "
            f"expected at least {limit}"
        )
    return specs[:limit]


def discover_record_specs(
    *, crawl_id: str, targets: Iterable[tuple[str, int]], timeout: int = 60
) -> list[WarcRecordSpec]:
    specs: list[WarcRecordSpec] = []
    for target_url, captures in targets:
        specs.extend(
            query_capture_index(
                crawl_id=crawl_id,
                target_url=target_url,
                limit=captures,
                timeout=timeout,
            )
        )
    return specs


def fetch_warc_range(spec: WarcRecordSpec, *, timeout: int = 60) -> bytes:
    range_end = spec.offset + spec.length - 1
    request = urllib.request.Request(
        urllib.parse.urljoin(COMMON_CRAWL_DATA_BASE, spec.filename),
        headers={
            "User-Agent": COMMON_CRAWL_USER_AGENT,
            "Range": f"bytes={spec.offset}-{range_end}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if getattr(response, "status", None) != 206:
            raise RuntimeError(
                f"Common Crawl range request returned HTTP {response.status}; expected 206"
            )
        payload = response.read(spec.length + 1)
    if len(payload) != spec.length:
        raise RuntimeError(
            f"Common Crawl range returned {len(payload)} bytes; expected {spec.length}"
        )
    return payload


def parse_warc_response(
    payload: bytes, *, max_html_bytes: int
) -> dict[str, Any]:
    for record in ArchiveIterator(io.BytesIO(payload)):
        if record.rec_type != "response":
            continue
        html_bytes = record.content_stream().read(max_html_bytes + 1)
        if len(html_bytes) > max_html_bytes:
            raise ValueError(
                f"WARC HTML payload exceeds max_html_bytes={max_html_bytes}"
            )
        http_headers = record.http_headers
        return {
            "warc_record_id": str(
                record.rec_headers.get_header("WARC-Record-ID") or ""
            ),
            "warc_date": str(record.rec_headers.get_header("WARC-Date") or ""),
            "warc_target_uri": str(
                record.rec_headers.get_header("WARC-Target-URI") or ""
            ),
            "warc_payload_digest": str(
                record.rec_headers.get_header("WARC-Payload-Digest") or ""
            ),
            "payload_type": str(
                record.rec_headers.get_header("WARC-Identified-Payload-Type")
                or ""
            ),
            "http_content_type": str(
                http_headers.get_header("Content-Type") if http_headers else ""
            ),
            "html_bytes": html_bytes,
        }
    raise RuntimeError("byte range did not contain a WARC response record")


@dataclass(frozen=True)
class CommonCrawlRangeTask(DataSourceTask):
    spec: WarcRecordSpec
    timeout: int
    max_html_bytes: int

    def execute(self) -> Iterator[pa.RecordBatch]:
        parsed = parse_warc_response(
            fetch_warc_range(self.spec, timeout=self.timeout),
            max_html_bytes=self.max_html_bytes,
        )
        warc_target_uri = parsed.pop("warc_target_uri")
        warc_payload_digest = parsed.pop("warc_payload_digest")
        if warc_target_uri != self.spec.url:
            raise RuntimeError(
                "WARC target URI does not match the pinned index record: "
                f"{warc_target_uri!r} != {self.spec.url!r}"
            )
        digest_value = warc_payload_digest.split(":", 1)[-1].upper()
        if digest_value != self.spec.digest.upper():
            raise RuntimeError(
                "WARC payload-digest header does not match the pinned index record"
            )
        if not parsed["warc_record_id"]:
            raise RuntimeError("WARC response record is missing WARC-Record-ID")
        payload_type = parsed["payload_type"].split(";", 1)[0].lower()
        http_content_type = parsed["http_content_type"].lower()
        if payload_type != "text/html" and "text/html" not in http_content_type:
            raise RuntimeError("WARC response is not identified as HTML")
        row = {
            "crawl_id": self.spec.crawl_id,
            "target_url": self.spec.target_url,
            "capture_timestamp": self.spec.capture_timestamp,
            "index_url": self.spec.url,
            "index_mime": self.spec.mime,
            "index_status": self.spec.status,
            "warc_digest": self.spec.digest,
            "warc_filename": self.spec.filename,
            "warc_offset": self.spec.offset,
            "warc_length": self.spec.length,
            **parsed,
        }
        yield table_from_rows([row], RAW_WARC_ARROW_SCHEMA).to_batches()[0]


@dataclass(frozen=True)
class CommonCrawlRangeSource(DataSource):
    specs: tuple[WarcRecordSpec, ...]
    timeout: int = 60
    max_html_bytes: int = 5 * 1024 * 1024

    @property
    def schema(self) -> dict[str, str]:
        return RAW_WARC_SCHEMA

    def get_tasks(self) -> Iterator[DataSourceTask]:
        for spec in self.specs:
            yield CommonCrawlRangeTask(
                spec=spec,
                timeout=self.timeout,
                max_html_bytes=self.max_html_bytes,
            )


def common_crawl_range_relation(
    conn: Any,
    specs: Iterable[WarcRecordSpec],
    *,
    timeout: int = 60,
    max_html_bytes: int = 5 * 1024 * 1024,
) -> Any:
    source = CommonCrawlRangeSource(
        specs=tuple(specs),
        timeout=timeout,
        max_html_bytes=max_html_bytes,
    )
    return read_datasource(source, con=conn)


def normalized_domain(url: str) -> str:
    hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
    return hostname.removeprefix("www.")


def capture_date(warc_date: str, capture_timestamp: str) -> str:
    if len(warc_date) >= 10:
        return warc_date[:10]
    if len(capture_timestamp) >= 8:
        return (
            f"{capture_timestamp[:4]}-{capture_timestamp[4:6]}-"
            f"{capture_timestamp[6:8]}"
        )
    return "1970-01-01"


def node_text(node: Any, *, deep: bool = True) -> str:
    tag = str(node.tag or "").lower()
    if tag == "img":
        return str(node.attributes.get("alt") or "")
    if tag == "meta":
        return str(node.attributes.get("content") or "")
    return str(node.text(deep=deep, separator=" ", strip=True) or "")


def extract_html_blocks(
    html_bytes: bytes, *, min_block_chars: int, max_blocks_per_page: int
) -> tuple[str, list[str]]:
    tree = HTMLParser(html_bytes)
    for node in tree.css("script, style, noscript"):
        node.decompose()
    title_node = tree.css_first("title")
    title = node_text(title_node) if title_node is not None else ""
    candidate_nodes = tree.css(HTML_BLOCK_SELECTOR)
    candidate_ids = {node.mem_id for node in candidate_nodes}
    full_text = {
        node.mem_id: re.sub(r"\s+", " ", node_text(node)).strip()
        for node in candidate_nodes
    }
    ancestor_ids: set[int] = set()
    for node in candidate_nodes:
        if len(full_text[node.mem_id]) < min_block_chars:
            continue
        parent = node.parent
        while parent is not None:
            if parent.mem_id in candidate_ids:
                ancestor_ids.add(parent.mem_id)
            parent = parent.parent

    blocks: list[str] = []
    seen: set[str] = set()
    for node in candidate_nodes:
        block = (
            re.sub(r"\s+", " ", node_text(node, deep=False)).strip()
            if node.mem_id in ancestor_ids
            else full_text[node.mem_id]
        )
        if len(block) < min_block_chars or block in seen:
            continue
        seen.add(block)
        blocks.append(block)
        if len(blocks) >= max_blocks_per_page:
            break
    return title, blocks


@dataclass(frozen=True)
class ExtractHtmlBlocksBatch:
    min_block_chars: int = 80
    max_blocks_per_page: int = 200

    def __call__(self, batch: pa.Table) -> pa.Table:
        rows: list[dict[str, Any]] = []
        for page in batch.to_pylist():
            title, blocks = extract_html_blocks(
                bytes(page["html_bytes"] or b""),
                min_block_chars=self.min_block_chars,
                max_blocks_per_page=self.max_blocks_per_page,
            )
            for block_index, block in enumerate(blocks):
                identity = f"{page['warc_record_id']}:{block_index}"
                doc_id = "cc-" + hashlib.blake2b(
                    identity.encode("utf-8"), digest_size=12
                ).hexdigest()
                rows.append(
                    {
                        "doc_id": doc_id,
                        "source": "common-crawl",
                        "domain": normalized_domain(str(page["index_url"] or "")),
                        "crawled_at": capture_date(
                            str(page["warc_date"] or ""),
                            str(page["capture_timestamp"] or ""),
                        ),
                        "title": title,
                        "body": block,
                        "block_index": block_index,
                        "block_chars": len(block),
                        "crawl_id": page["crawl_id"],
                        "target_url": page["target_url"],
                        "capture_url": page["index_url"],
                        "capture_timestamp": page["capture_timestamp"],
                        "warc_record_id": page["warc_record_id"],
                        "warc_date": page["warc_date"],
                        "warc_filename": page["warc_filename"],
                        "warc_offset": page["warc_offset"],
                        "warc_length": page["warc_length"],
                        "warc_digest": page["warc_digest"],
                        "http_content_type": page["http_content_type"],
                    }
                )
        return table_from_rows(rows, BLOCK_ARROW_SCHEMA)
