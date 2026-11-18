"""Research, math, and file generation tools used by ClaudeX."""

from __future__ import annotations

import ast
import hashlib
import ipaddress
import math
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from .lazy_import import lazy_import
from .web_search import WEB_SEARCH_TOOLS

USER_AGENT = "ClaudeXResearch/1.0 (+local research assistant)"

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

TOOLS = WEB_SEARCH_TOOLS + [
    {"type": "function", "function": {
        "name": "symbolic_math",
        "description": "Perform safe symbolic mathematics using SymPy: simplify, factor, expand, differentiate, integrate, solve, or calculate a limit.",
        "parameters": {"type": "object", "properties": {
            "expression": {"type": "string"},
            "operation": {"type": "string", "enum": ["simplify", "factor", "expand", "differentiate", "integrate", "solve", "limit"]},
            "variable": {"type": "string"},
            "point": {"type": "string"}
        }, "required": ["expression", "operation"]}
    }},
    {"type": "function", "function": {
        "name": "numerical_experiment",
        "description": "Run a bounded, reproducible numerical comparison of two explicit mathematical hypotheses over a parameter range. Supported formulas use x and constants, arithmetic, and sin/cos/exp/log/sqrt. This is a computational experiment, not a physical measurement.",
        "parameters": {"type": "object", "properties": {
            "hypothesis_a": {"type": "string"}, "hypothesis_b": {"type": "string"},
            "x_min": {"type": "number"}, "x_max": {"type": "number"}, "points": {"type": "integer", "minimum": 2, "maximum": 101},
            "relative_tolerance": {"type": "number", "minimum": 0, "maximum": 1}
        }, "required": ["hypothesis_a", "hypothesis_b", "x_min", "x_max"]}
    }},
    {"type": "function", "function": {
        "name": "create_pdf",
        "description": "Create a downloadable, professionally formatted PDF when the user asks for a PDF or exported document. Supply a clear title and complete Markdown content. Supports headings, bold and italic text, lists, tables, code blocks, links, and common inline LaTeX math.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "Short document title."},
            "content": {"type": "string", "description": "Complete document body in Markdown. Use $...$ or \\(...\\) for inline math and $$...$$ or \\[...\\] for display math."}
        }, "required": ["title", "content"]}
    }}
]


def safe_expression(source: str):
    """Parse a limited arithmetic/function grammar; never eval or sympify arbitrary input."""
    sp = lazy_import("sympy")

    if len(source) > 500:
        raise ValueError("Expression is too long.")
    allowed = {"sin": sp.sin, "cos": sp.cos, "tan": sp.tan, "exp": sp.exp,
               "log": sp.log, "sqrt": sp.sqrt, "abs": sp.Abs}
    tree = ast.parse(source, mode="eval")

    def conv(node):
        if isinstance(node, ast.Expression): return conv(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return sp.Number(node.value)
        if isinstance(node, ast.Name):
            if node.id == "pi": return sp.pi
            if node.id == "E": return sp.E
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,20}", node.id): return sp.Symbol(node.id)
            raise ValueError("Invalid symbol.")
        if isinstance(node, ast.BinOp):
            a, b = conv(node.left), conv(node.right)
            funcs = {ast.Add: lambda: a+b, ast.Sub: lambda: a-b, ast.Mult: lambda: a*b,
                     ast.Div: lambda: a/b, ast.Pow: lambda: a**b}
            if type(node.op) not in funcs: raise ValueError("Operator not allowed.")
            return funcs[type(node.op)]()
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            a = conv(node.operand)
            return a if isinstance(node.op, ast.UAdd) else -a
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in allowed and not node.keywords:
            return allowed[node.func.id](*(conv(a) for a in node.args))
        raise ValueError("Unsupported or unsafe expression syntax.")
    return conv(tree)


def numerical_experiment(args: dict[str, Any]) -> dict[str, Any]:
    """Compare two restricted formulas over a finite grid; generated code is never executed."""
    sp = lazy_import("sympy")

    low, high = float(args["x_min"]), float(args["x_max"])
    count = max(2, min(int(args.get("points", 21)), 101))
    tolerance = max(0.0, min(float(args.get("relative_tolerance", 0.05)), 1.0))
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        raise ValueError("Require finite x_min < x_max.")
    x = sp.Symbol("x")
    expressions = {}
    for key in ("hypothesis_a", "hypothesis_b"):
        formula = safe_expression(str(args[key]))
        if formula.free_symbols - {x}:
            raise ValueError("Hypotheses may use only x; other symbols are unresolved parameters.")
        expressions[key] = sp.lambdify(x, formula, modules=[{"sin":math.sin,"cos":math.cos,"exp":math.exp,
            "log":math.log,"sqrt":math.sqrt,"Abs":abs}, "math"])
    samples = []
    for index in range(count):
        point = low + (high-low)*index/(count-1)
        values=[]
        for key in ("hypothesis_a", "hypothesis_b"):
            # Lambdify receives only the AST-derived SymPy expression and whitelisted functions.
            try: value=float(expressions[key](point))
            except (ValueError, ZeroDivisionError, OverflowError): value=float("nan")
            values.append(value if math.isfinite(value) else None)
        a,b=values
        difference=abs(a-b) if a is not None and b is not None else None
        scale=max(abs(a),abs(b),1e-15) if a is not None and b is not None else None
        samples.append({"x":point,"a":a,"b":b,"absolute_difference":difference,
                        "relative_difference":difference/scale if difference is not None else None})
    valid=[row for row in samples if row["relative_difference"] is not None]
    agreement=sum(row["relative_difference"] <= tolerance for row in valid)
    return {"experiment_type":"computational_parameter_sweep","physical_measurement":False,
        "parameters":{"x_min":low,"x_max":high,"points":count,"relative_tolerance":tolerance},
        "hypotheses":{"a":str(args["hypothesis_a"]),"b":str(args["hypothesis_b"])},
        "algorithm":"claudex_grid_compare_v1",
        "algorithm_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "samples":samples,"valid_samples":len(valid),"invalid_samples":count-len(valid),
        "within_tolerance":agreement,"agreement_fraction":agreement/len(valid) if valid else None,
        "max_absolute_difference":max((r["absolute_difference"] for r in valid),default=None)}


def validate_public_url(url: str) -> str:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("Only absolute HTTP/HTTPS URLs are accepted.")
    if p.username or p.password:
        raise ValueError("URLs containing credentials are blocked.")
    if p.port not in (None, 80, 443):
        raise ValueError("Only standard HTTP/HTTPS ports are allowed.")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Hostname lookup failed: {exc}")
    if not infos:
        raise ValueError("Hostname did not resolve.")
    for item in infos:
        ip = ipaddress.ip_address(item[4][0])
        if not ip.is_global:
            raise ValueError("Private, local, loopback, link-local, and reserved network addresses are blocked.")
    return url


def safe_get(url: str, timeout: int = 18, max_bytes: int = 5_000_000) -> Any:
    requests = lazy_import("requests")

    current = validate_public_url(url)
    for _ in range(5):
        validate_public_url(current)
        response = requests.get(current, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache", "Pragma": "no-cache"}, timeout=timeout,
                                allow_redirects=False, stream=True)
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("Redirect did not include a destination.")
            current = urljoin(current, location)
            validate_public_url(current)
            continue
        response.raise_for_status()
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            response.close()
            raise ValueError("Page is larger than the 5 MB fetch limit.")
        chunks, total = [], 0
        for chunk in response.iter_content(65536):
            if not chunk: continue
            total += len(chunk)
            if total > max_bytes:
                response.close()
                raise ValueError("Page exceeded the 5 MB fetch limit.")
            chunks.append(chunk)
        response._content = b"".join(chunks)
        response._content_consumed = True
        return response
    raise ValueError("Too many redirects.")


def robots_allowed(url: str) -> tuple[bool, str]:
    requests = lazy_import("requests")

    p = urlparse(url)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    try:
        r = safe_get(robots_url, timeout=8, max_bytes=250_000)
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.parse(r.text.splitlines())
        allowed = rp.can_fetch(USER_AGENT, url)
        return allowed, "robots.txt permits this URL" if allowed else "robots.txt disallows this URL"
    except requests.HTTPError as exc:
        # 404 means no robots policy published; other status failures fail closed.
        if getattr(exc.response, "status_code", None) == 404:
            return True, "No robots.txt file was published"
        return False, f"Could not verify robots.txt ({exc})"
    except Exception as exc:
        return False, f"Could not verify robots.txt ({exc})"


def fetch_page(url: str) -> dict[str, Any]:
    validate_public_url(url)
    allowed, policy = robots_allowed(url)
    if not allowed:
        raise PermissionError(policy)
    response = safe_get(url)
    content_type = response.headers.get("Content-Type", "")
    is_pdf = "application/pdf" in content_type.lower() or response.content.startswith(b"%PDF-")
    if is_pdf:
        try:
            io = lazy_import("io")
            PdfReader = lazy_import("pypdf").PdfReader

            reader = PdfReader(io.BytesIO(response.content), strict=False)
            page_text = []
            for page in reader.pages[:80]:
                extracted = page.extract_text() or ""
                if extracted:
                    page_text.append(extracted)
            text = "\n".join(page_text)
        except Exception as exc:
            raise ValueError(f"Could not extract text from this PDF: {type(exc).__name__}: {exc}") from exc
        if not text.strip():
            raise ValueError("This PDF has no extractable text; it may be scanned or image-only.")
        title = str(reader.metadata.title or response.url) if reader.metadata else response.url
        metadata = {"author": str(reader.metadata.author or "") if reader.metadata else None,
                    "published_at": None, "canonical_url": response.url,
                    "content_type": "application/pdf", "page_count": len(reader.pages)}
    elif "text/plain" in content_type.lower():
        title, text = response.url, response.text
        metadata = {"author": None, "published_at": None, "canonical_url": response.url,
                    "content_type": content_type}
    elif any(t in content_type.lower() for t in ("text/html", "application/xhtml")):
        BeautifulSoup = lazy_import("bs4").BeautifulSoup

        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else response.url
        for node in soup(["script", "style", "noscript", "svg", "nav", "footer", "form"]):
            node.decompose()
        text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
        author = soup.find("meta", attrs={"name": re.compile("author", re.I)})
        published = soup.find("meta", attrs={"property": re.compile("article:published_time", re.I)}) or soup.find("meta", attrs={"name": re.compile("datePublished", re.I)})
        canonical = soup.find("link", rel="canonical")
        metadata = {"author": author.get("content") if author else None,
                    "published_at": published.get("content") if published else None,
                    "canonical_url": urljoin(response.url, canonical.get("href")) if canonical and canonical.get("href") else response.url,
                    "content_type": content_type}
    else:
        raise ValueError(f"Unsupported content type: {content_type or 'unknown'}. Supported formats are HTML, plain text, and text-based PDF.")
    return {"title": title, "url": response.url, "robots": policy, **metadata,
            "content": text[:18000], "retrieved_at": utc_now()}


