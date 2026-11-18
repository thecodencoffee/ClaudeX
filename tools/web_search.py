"""Live web search and result-fetch tools for ClaudeX."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlencode

from .lazy_import import lazy_import

WEB_SEARCH_TOOLS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Real-time web search for current or sourced answers. Searches independent providers in parallel, then fetches all returned result pages (up to the requested result count) so you can answer from live page content. Use this in any chat mode when freshness or sources matter.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Focused search query."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 8},
            "region": {"type": "string", "description": "Optional region code, e.g. us-en or in-en."},
            "timelimit": {"type": "string", "enum": ["d", "w", "m", "y"], "description": "Optional recency limit: day, week, month or year."}
        }, "required": ["query"]}
    }},
    {"type": "function", "function": {
        "name": "parallel_search",
        "description": "Search several independent public web providers concurrently and return merged, deduplicated result candidates with URLs and snippets.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Focused search query."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 8},
            "region": {"type": "string", "description": "Optional region code, e.g. us-en or in-en."},
            "timelimit": {"type": "string", "enum": ["d", "w", "m", "y"]}
        }, "required": ["query"]}
    }},
    {"type": "function", "function": {
        "name": "get_results",
        "description": "Fetch readable content from up to eight public URLs in parallel, typically URLs returned by web_search or parallel_search. This is a live network fetch, not a cached result lookup.",
        "parameters": {"type": "object", "properties": {
            "urls": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8}
        }, "required": ["urls"]}
    }},
    {"type": "function", "function": {
        "name": "fetch_data",
        "description": "Fetch and extract readable text from one public web page. Obeys robots.txt, blocks local/private network destinations, and limits downloads.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "Absolute public HTTP or HTTPS URL."}
        }, "required": ["url"]}
    }}]

def _search_backend(backend: str, query: str, region: str, timelimit: str | None, limit: int) -> tuple[str, list[dict[str, Any]]]:
    DDGS = lazy_import("ddgs").DDGS

    rows = DDGS(timeout=12).text(query, region=region, safesearch="moderate",
                               timelimit=timelimit, max_results=limit, backend=backend)
    results = []
    for row in rows or []:
        url = row.get("href") or row.get("url")
        if not url:
            continue
        results.append({"title": row.get("title") or "Untitled", "url": url,
                        "snippet": row.get("body") or row.get("snippet", ""),
                        "date": row.get("date") or row.get("published", ""),
                        "provider": row.get("source") or ("DDGS auto" if backend == "auto" else backend)})
    return backend, results


def parallel_search(query: str, region: str = "wt-wt", timelimit: str | None = None,
                    limit: int = 6) -> tuple[list[dict[str, Any]], str]:
    """Query independent providers concurrently and merge their live result sets."""
    arxiv_id = re.search(r"(?<!\d)(\d{4}\.\d{4,5}(?:v\d+)?)(?!\d)", query)
    if arxiv_id:
        try:
            api_url = "https://export.arxiv.org/api/query?" + urlencode({"id_list": arxiv_id.group(1)})
            from .research import safe_get
            response = safe_get(api_url, timeout=10, max_bytes=1_000_000)
            root = ET.fromstring(response.content)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            results = []
            for entry in root.findall("atom:entry", ns):
                paper_id = entry.findtext("atom:id", "", ns).rsplit("/", 1)[-1]
                results.append({"title": " ".join(entry.findtext("atom:title", "Untitled", ns).split()),
                                "url": f"https://arxiv.org/abs/{paper_id}",
                                "snippet": " ".join(entry.findtext("atom:summary", "", ns).split()),
                                "date": entry.findtext("atom:published", "", ns), "provider": "arXiv API"})
            if results:
                return results[:limit], "arXiv API"
        except Exception:
            pass

    try:
        # DDGS' auto backend selects and runs its available engines internally.
        # Pinning individual engines makes this brittle when providers block or
        # change their pages: every pinned call can fail even while auto works.
        _, results = _search_backend("auto", query, region, timelimit, limit)
    except Exception as exc:
        reason = " ".join(str(exc).split())[:240] or type(exc).__name__
        raise RuntimeError(
            "Live web search failed across the available search engines. "
            f"Search error: {reason}. Check connectivity or retry shortly."
        ) from exc
    if results:
        return results[:limit], "DDGS auto"
    raise RuntimeError(
        "Live web search completed but returned no usable results. Try a more specific query or retry shortly."
    )


def search_web(query: str, region: str, timelimit: str | None, limit: int) -> tuple[list[dict[str, Any]], str]:
    """Compatibility wrapper for callers using the original search function name."""
    return parallel_search(query, region, timelimit, limit)


def get_results(urls: list[str], max_chars: int = 3000) -> list[dict[str, Any]]:
    """Fetch public pages concurrently; each URL goes through the SSRF and robots checks."""
    from .research import fetch_page, utc_now
    unique_urls = list(dict.fromkeys(str(url).strip() for url in urls if str(url).strip()))[:8]
    if not unique_urls:
        raise ValueError("Provide at least one URL to fetch.")
    pages: list[dict[str, Any] | None] = [None] * len(unique_urls)
    with ThreadPoolExecutor(max_workers=min(8, len(unique_urls)), thread_name_prefix="claudex-fetch") as pool:
        futures = {pool.submit(fetch_page, url): index for index, url in enumerate(unique_urls)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                page = future.result()
                page["requested_url"] = unique_urls[index]
                page["content"] = page["content"][:max_chars]
                pages[index] = page
            except Exception as exc:
                pages[index] = {"url": unique_urls[index], "requested_url": unique_urls[index], "error": f"{type(exc).__name__}: {exc}",
                                "retrieved_at": utc_now()}
    return [page for page in pages if page is not None]
