import asyncio
import csv
import hashlib
import json
import os
import random
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from dotenv import load_dotenv
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table
from playwright.async_api import Browser, BrowserContext, Error as PWError, Page, async_playwright

from db import AlertDB


console = Console()

#
# High-level overview
# - Configuration: runtime knobs via environment variables (`Config`)
# - Data model: one `AggregatedRow` per product color variant and run
# - Category/grid: discover PDP links by scrolling and normalizing URLs
# - PDP parsing: extract name, prices, discount, sizes, image, color
# - CSV sink: write rows with header management and simple de-duplication
# - Orchestrator: `crawl_category` drives navigation and politeness
# - Entrypoint: `main` wires config, default URLs, and starts the crawl


@dataclass
class Config:
    outlet_base: str = os.getenv("OUTLET_BASE", "https://outlet.arcteryx.com/us/en")
    include_veilance: bool = os.getenv("INCLUDE_VEILANCE", "false").lower() == "true"
    concurrency: int = int(os.getenv("CONCURRENCY", "1"))
    jitter_min_ms: int = int(os.getenv("REQUEST_JITTER_MS_MIN", "700"))
    jitter_max_ms: int = int(os.getenv("REQUEST_JITTER_MS_MAX", "1500"))
    pdp_delay_ms: int = int(os.getenv("PDP_DELAY_MS", "2500"))
    user_agent: str = os.getenv("USER_AGENT", "TopologyScraper/1.0 (+contact: you@example.com)")
    proxy_url: str = os.getenv("PROXY_URL", "")
    proxy_rotate_every: int = int(os.getenv("PROXY_ROTATE_EVERY", "10"))
    output_csv: str = os.getenv("OUTPUT_CSV", "arcteryx_outlet.csv")
    output_db: str = os.getenv("OUTPUT_DB", "arcteryx_outlet.sqlite")
    alert_webhook: str = os.getenv("ALERT_WEBHOOK", "")
    max_colors: int = int(os.getenv("MAX_COLORS", "0"))
def _build_playwright_proxy(proxy_url: str) -> Optional[Dict[str, Any]]:
    try:
        if not proxy_url:
            return None
        parsed = urlparse(proxy_url)
        if not parsed.scheme or not parsed.hostname or not parsed.port:
            return None
        proxy: Dict[str, Any] = {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
        }
        if parsed.username:
            proxy["username"] = parsed.username
        if parsed.password:
            proxy["password"] = parsed.password
        return proxy
    except Exception:
        return None


class AggregatedRow(BaseModel):
    """Canonical output schema for a single product color variant.

    - One row per `(product_url, color)` per run
    - Prices are strings with currency symbol where available
    - Sizes columns hold comma-separated labels
    """
    crawl_ts: str
    locale: str
    category_path: Optional[str]
    name: Optional[str]
    sku: Optional[str]
    product_url: str
    color: Optional[str]
    list_price: Optional[str]
    sale_price: Optional[str]
    discount: Optional[str]
    image_url: Optional[str]
    inventory_amount: Optional[int]
    size_quantities: Optional[str]
    sizes_all: str
    sizes_in_stock: str
    sizes_out_of_stock: str
    num_sizes_in_stock: int
    hash_key: str
    source: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_color_hash_key(product_url: str, color: Optional[str]) -> str:
    m = hashlib.sha1()
    identity = f"{product_url}|{color or ''}".encode("utf-8")
    m.update(identity)
    return m.hexdigest()


async def jitter_sleep(min_ms: int, max_ms: int) -> None:
    await asyncio.sleep(random.uniform(min_ms / 1000.0, max_ms / 1000.0))


async def scroll_and_collect_product_links(page: Page) -> List[str]:
    """Scroll a category/grid until product link count stabilizes and return
    normalized PDP URLs limited to the current origin and expected paths.
    """
    seen_counts: List[int] = []
    last_count = -1
    stable_iterations = 0

    # Loop until product anchors stop increasing for a few iterations
    for _ in range(40):
        anchors = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => e.getAttribute('href'))",
        )
        hrefs = [a for a in anchors if a]
        # Prefer PDPs with '/shop/' in path, but keep outlet PDPs even if different slug
        product_links = []
        for href in hrefs:
            try:
                if not isinstance(href, str):
                    continue
                if "/shop/" in href or re.search(r"/products?/", href):
                    product_links.append(href)
            except Exception:
                continue

        count = len(set(product_links))
        seen_counts.append(count)

        # Scroll to bottom
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await jitter_sleep(300, 800)

        if count == last_count:
            stable_iterations += 1
        else:
            stable_iterations = 0
        last_count = count

        if stable_iterations >= 3:
            break

    # Normalize to absolute URLs, unique, and filter to same host + '/shop/'
    origin = f"{urlparse(page.url).scheme}://{urlparse(page.url).netloc}"
    absolute: Set[str] = set()
    for href in set(product_links):
        try:
            if not isinstance(href, str):
                continue
            if href.startswith("http"):
                abs_url = href
            elif href.startswith("/"):
                abs_url = origin + href
            else:
                abs_url = urljoin(page.url, href)

            parsed_abs = urlparse(abs_url)
            if parsed_abs.netloc != urlparse(origin).netloc:
                continue
            if "/shop/" not in parsed_abs.path:
                continue
            absolute.add(abs_url)
        except Exception:
            continue
    return sorted(absolute)


async def parse_json_ld(page: Page) -> Dict[str, Any]:
    """Merge all JSON-LD script blocks into a single dict for fallbacks."""
    scripts = await page.locator('script[type="application/ld+json"]').all()
    merged: Dict[str, Any] = {}
    for s in scripts:
        try:
            txt = await s.text_content()
            if not txt:
                continue
            data = json.loads(txt)
            if isinstance(data, dict):
                merged.update(data)
            elif isinstance(data, list):
                for d in data:
                    if isinstance(d, dict):
                        merged.update(d)
        except Exception:
            continue
    return merged

async def parse_next_data(page: Page) -> Dict[str, Any]:
    """Read Next.js __NEXT_DATA__ for additional price/name fallbacks."""
    try:
        loc = page.locator('script#__NEXT_DATA__')
        if await loc.count() == 0:
            return {}
        txt = await loc.first.text_content()
        if not txt:
            return {}
        return json.loads(txt)
    except Exception:
        return {}

def _to_price_string(value: Any, currency: Optional[str]) -> Optional[str]:
    """Normalize raw numeric/str price to a display string, preferring
    existing currency markers and adding currency code when provided."""
    try:
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        # If string already includes a currency symbol or code, return as-is
        if re.search(r"[€£$]", s) or re.match(r"^[A-Z]{3}\s", s):
            return s
        return f"{currency} {s}" if currency else s
    except Exception:
        return None

def _walk_find_prices(obj: Any) -> Dict[str, Any]:
    """Recursively collect likely price fields from nested dict/list structures."""
    found: Dict[str, Any] = {}
    try:
        if isinstance(obj, dict):
            for k, v in obj.items():
                lk = str(k).lower()
                if lk in ("price", "saleprice", "listprice", "finalprice", "compareatprice", "compare_at_price", "compare_at"):
                    found[lk] = v
                sub = _walk_find_prices(v)
                if sub:
                    found.update(sub)
        elif isinstance(obj, list):
            for it in obj:
                sub = _walk_find_prices(it)
                if sub:
                    found.update(sub)
    except Exception:
        return found
    return found

def _walk_find_prices(obj: Any) -> Dict[str, Any]:
    found: Dict[str, Any] = {}
    try:
        if isinstance(obj, dict):
            for k, v in obj.items():
                lk = str(k).lower()
                if lk in ("price", "saleprice", "listprice", "finalprice", "compareatprice", "compare_at_price"):
                    found[lk] = v
                sub = _walk_find_prices(v)
                if sub:
                    found.update(sub)
        elif isinstance(obj, list):
            for it in obj:
                sub = _walk_find_prices(it)
                if sub:
                    found.update(sub)
    except Exception:
        return found
    return found

async def parse_next_data(page: Page) -> Dict[str, Any]:
    try:
        loc = page.locator('script#__NEXT_DATA__')
        if await loc.count() == 0:
            return {}
        txt = await loc.first.text_content()
        if not txt:
            return {}
        return json.loads(txt)
    except Exception:
        return {}


def _walk_collect_inventory_amounts(
    obj: Any,
    color_hint: Optional[str] = None,
    sku_hint: Optional[str] = None,
) -> List[int]:
    """Traverse a nested dict/list looking for likely inventory amount fields.

    Filters to keys commonly used for ATS/inventory quantities, and prefers
    objects that match the provided color/sku hints when available.
    """
    results: List[int] = []
    try:
        keys_of_interest = {
            "ats",
            "availableToSell",
            "available",
            "quantityAvailable",
            "inventory",
            "inventoryQuantity",
            "qty",
            "quantity",
            "stock",
            "stockQty",
            "onHand",
        }
        if isinstance(obj, dict):
            # Determine if this object matches hints
            matches_hint = False
            try:
                if color_hint:
                    for k in ("color", "colour", "attributeColor", "variantColor"):
                        v = obj.get(k)
                        if isinstance(v, str) and color_hint.lower() in v.lower():
                            matches_hint = True
                            break
                if not matches_hint and sku_hint:
                    for k in ("sku", "skuId", "styleSku", "productSku"):
                        v = obj.get(k)
                        if isinstance(v, str) and v.strip() and v.strip().lower() == sku_hint.strip().lower():
                            matches_hint = True
                            break
            except Exception:
                matches_hint = False

            for k, v in obj.items():
                lk = str(k)
                if lk in keys_of_interest and isinstance(v, (int, float)):
                    val = int(v)
                    # Sanity bounds to avoid capturing prices etc.
                    if 0 <= val <= 2000:
                        # Prefer values on matching objects; still collect others as fallback
                        results.append(val if matches_hint else val)
                # Recurse
                results.extend(_walk_collect_inventory_amounts(v, color_hint, sku_hint))
        elif isinstance(obj, list):
            for it in obj:
                results.extend(_walk_collect_inventory_amounts(it, color_hint, sku_hint))
    except Exception:
        return results
    return results


def _extract_size_label_from_obj(obj: Dict[str, Any]) -> Optional[str]:
    try:
        for k in ("size", "sizeLabel", "size_value", "sizeValue", "attributeSize", "variantSize", "label", "value"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return re.sub(r"\s+", "", v.strip())
        # Attributes array pattern: attributes: [{name: 'Size', value: 'M'}]
        attrs = obj.get("attributes")
        if isinstance(attrs, list):
            for a in attrs:
                if isinstance(a, dict):
                    name = (a.get("name") or a.get("label") or "").strip().lower()
                    if name == "size":
                        val = a.get("value")
                        if isinstance(val, str) and val.strip():
                            return re.sub(r"\s+", "", val.strip())
    except Exception:
        return None
    return None


def _walk_collect_size_quantities(
    obj: Any,
    color_hint: Optional[str] = None,
) -> Dict[str, int]:
    """Traverse nested structures and collect a mapping of size->qty for the
    currently selected colour/variant where possible.
    """
    results: Dict[str, int] = {}
    try:
        def qty_from_value(v: Any) -> Optional[int]:
            try:
                if isinstance(v, (int, float)):
                    val = int(v)
                    if 0 <= val <= 500:
                        return val
            except Exception:
                return None
            return None

        def consider(obj_dict: Dict[str, Any]) -> None:
            size_label = _extract_size_label_from_obj(obj_dict)
            if not size_label:
                return
            # If a color hint exists and this object declares a colour, prefer matches
            if color_hint:
                for ck in ("color", "colour", "attributeColor", "variantColor"):
                    cv = obj_dict.get(ck)
                    if isinstance(cv, str) and color_hint.lower() not in cv.lower():
                        # not the same color; do not record from this object
                        return
            # Find quantity-like fields
            for qk in (
                "ats",
                "availableToSell",
                "available",
                "quantityAvailable",
                "inventory",
                "inventoryQuantity",
                "qty",
                "quantity",
                "stock",
                "stockQty",
                "onHand",
            ):
                qv = obj_dict.get(qk)
                q = qty_from_value(qv)
                if q is not None:
                    results[size_label] = q
                    break

        if isinstance(obj, dict):
            consider(obj)
            for v in obj.values():
                sub = _walk_collect_size_quantities(v, color_hint)
                if sub:
                    results.update(sub)
        elif isinstance(obj, list):
            for it in obj:
                sub = _walk_collect_size_quantities(it, color_hint)
                if sub:
                    results.update(sub)
    except Exception:
        return results
    return results


def _parse_arc_sizes_from_next_data(next_data: Dict[str, Any], color_name: Optional[str]) -> Tuple[List[Tuple[str, bool]], Dict[str, int]]:
    """Arc'teryx-specific parser for sizes and per-size inventory using
    __NEXT_DATA__. Returns (sizes_list, size_qty_map).

    Falls back to empty results if the expected structure is not present.
    """
    try:
        props = next_data.get("props")
        if not isinstance(props, dict):
            return ([], {})
        pageProps = props.get("pageProps")
        if not isinstance(pageProps, dict):
            return ([], {})
        product_raw = pageProps.get("product")
        # Some deployments embed product as a JSON string
        if isinstance(product_raw, str):
            try:
                product = json.loads(product_raw)
            except Exception:
                product = None
        else:
            product = product_raw
        if not isinstance(product, dict):
            return ([], {})

        colour_options = product.get("colourOptions") or {}
        selected_colour_id = None
        try:
            selected_colour_id = colour_options.get("selected")
        except Exception:
            selected_colour_id = None
        # Build sizeId -> label map
        size_options = product.get("sizeOptions") or {}
        size_id_to_label: Dict[str, str] = {}
        try:
            for opt in size_options.get("options", []) or []:
                if isinstance(opt, dict):
                    sid = str(opt.get("value")) if opt.get("value") is not None else None
                    lab = opt.get("label")
                    if sid and isinstance(lab, str) and lab.strip():
                        size_id_to_label[sid] = re.sub(r"\s+", "", lab.strip())
        except Exception:
            pass

        # If colour not selected, try to match by label from colourOptions
        if not selected_colour_id and color_name:
            try:
                opts = colour_options.get("options", []) or []
                for o in opts:
                    if isinstance(o, dict):
                        lab = (o.get("label") or "").strip().lower()
                        if lab and color_name.lower() in lab:
                            selected_colour_id = o.get("value")
                            break
            except Exception:
                selected_colour_id = None

        # Iterate variants for the selected colour
        variants = product.get("variants") or []
        size_qty: Dict[str, int] = {}
        sizes: List[Tuple[str, bool]] = []
        if isinstance(variants, list) and selected_colour_id is not None:
            for v in variants:
                if not isinstance(v, dict):
                    continue
                try:
                    if str(v.get("colourId")) != str(selected_colour_id):
                        continue
                    sid = str(v.get("sizeId")) if v.get("sizeId") is not None else None
                    if not sid:
                        continue
                    label = size_id_to_label.get(sid)
                    if not label:
                        continue
                    inv = int(v.get("inventory") or 0)
                    size_qty[label] = inv
                except Exception:
                    continue

        if size_qty:
            # Order by numeric when possible, else lexical
            try:
                def sort_key(s: str) -> Tuple[int, str]:
                    m = re.match(r"^(\d+)(?:[.xX]?(\d)?)", s)
                    if m:
                        major = int(m.group(1))
                        minor = int(m.group(2)) if m.group(2) else 0
                        return (major * 10 + minor, s)
                    return (10**9, s)
                ordered = sorted(size_qty.keys(), key=sort_key)
            except Exception:
                ordered = sorted(size_qty.keys())
            for lab in ordered:
                sizes.append((lab, size_qty.get(lab, 0) > 0))
        return (sizes, size_qty)
    except Exception:
        return ([], {})


def _extract_image_url_from_next(next_data: Dict[str, Any], color_name: Optional[str]) -> Optional[str]:
    """Prefer colour-specific hero image from Arc'teryx __NEXT_DATA__.
    Fallback to product main image when necessary.
    """
    try:
        props = next_data.get("props")
        if not isinstance(props, dict):
            return None
        pageProps = props.get("pageProps")
        if not isinstance(pageProps, dict):
            return None
        product_raw = pageProps.get("product")
        product = json.loads(product_raw) if isinstance(product_raw, str) else product_raw
        if not isinstance(product, dict):
            return None
        # Helper to normalize asset URLs
        def normalize_url(u: Optional[str]) -> Optional[str]:
            if not u or not isinstance(u, str):
                return None
            u = u.strip()
            if not u:
                return None
            if u.startswith("http://") or u.startswith("https://"):
                return u
            if u.startswith("//"):
                return f"https:{u}"
            if u.startswith("/"):
                return f"https://images.arcteryx.com{u}"
            return u

        # Try matched colour option first
        colour_options = product.get("colourOptions") or {}
        selected_colour_id = None
        try:
            selected_colour_id = colour_options.get("selected")
        except Exception:
            selected_colour_id = None
        if not selected_colour_id and color_name:
            try:
                opts = colour_options.get("options", []) or []
                for o in opts:
                    if isinstance(o, dict):
                        lab = (o.get("label") or "").strip().lower()
                        if lab and color_name.lower() in lab:
                            selected_colour_id = o.get("value")
                            # If this option has a heroImage, use it directly
                            hero = o.get("heroImage") or o.get("image") or o.get("thumbnail")
                            if isinstance(hero, dict) and hero.get("url"):
                                return normalize_url(hero.get("url"))
                            break
            except Exception:
                pass
        # If we have selected id, look up that option's hero image
        try:
            if selected_colour_id:
                for o in (colour_options.get("options", []) or []):
                    if isinstance(o, dict) and str(o.get("value")) == str(selected_colour_id):
                        hero = o.get("heroImage") or o.get("image") or o.get("thumbnail")
                        if isinstance(hero, dict) and hero.get("url"):
                            return normalize_url(hero.get("url"))
                        break
        except Exception:
            pass
        # Fallback to product main image
        main = product.get("mainImage")
        if isinstance(main, dict) and main.get("url"):
            return normalize_url(main.get("url"))
    except Exception:
        return None
    return None


def extract_prices_from_json_ld(json_ld: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Attempt to extract a primary price from JSON-LD offers as a fallback."""
    try:
        offers = json_ld.get("offers")
        if not offers:
            return (None, None, None)
        # Single offer
        if isinstance(offers, dict):
            price = offers.get("price")
            currency = offers.get("priceCurrency")
            price_str = f"{currency} {price}" if (currency and price) else (str(price) if price else None)
            # We do not know list vs sale; treat as sale if only one price available
            return (None, price_str, None)
        # Multiple offers
        if isinstance(offers, list):
            prices: List[float] = []
            currencies: List[str] = []
            for o in offers:
                try:
                    p = float(o.get("price"))
                    prices.append(p)
                    currencies.append(o.get("priceCurrency", ""))
                except Exception:
                    continue
            if prices:
                pmin = min(prices)
                cur = next((c for c in currencies if c), "")
                price_str = f"{cur} {pmin}" if cur else str(pmin)
                return (None, price_str, None)
    except Exception:
        return (None, None, None)
    return (None, None, None)


async def get_price_text(page: Page) -> Optional[str]:
    """Pull raw visible price-related text using a set of tolerant selectors."""
    selectors = [
        "[data-testid='price']",
        "[data-testid*='price']",
        "[data-test*='price']",
        ".product-price, .ProductPrice, .price, .Price, [class*='Price']",
        ".sale-price, .SalePrice, [class*='sale']",
        ".regular-price, .RegularPrice, [class*='regular']",
        "[aria-label*='Price'], [aria-label*='price']",
        # Common compare-at and current price patterns
        "[data-testid*='compare'], .compare-at, .CompareAt, [class*='compare']",
        "[data-testid*='current'], .current-price, .CurrentPrice, [class*='current']",
    ]
    prioritized: List[str] = []
    generic: List[str] = []
    for sel in selectors:
        loc = page.locator(sel)
        try:
            count = await loc.count()
            if count == 0:
                continue
            # Consider up to first 3 matches per selector
            for i in range(min(count, 3)):
                node = loc.nth(i)
                try:
                    txt = await node.inner_text(timeout=1500)
                except Exception:
                    continue
                if not txt:
                    continue
                # Remove financing/BNPL lines that can include smaller dollar amounts
                cleaned = " ".join(
                    [
                        line.strip()
                        for line in (txt.splitlines())
                        if not re.search(r"payments?|klarna|afterpay|interest", line, re.I)
                    ]
                )
                if not cleaned:
                    continue
                if re.search(r"save\s*\d+%", cleaned, re.I):
                    prioritized.append(cleaned)
                else:
                    generic.append(cleaned)
        except Exception:
            continue
    for bucket in (prioritized, generic):
        for txt in bucket:
            if re.search(r"[$€£]\s?\d", txt):
                return txt
    return None


def _extract_currency_prefix(price_str: str) -> str:
    try:
        m = re.match(r"^([A-Z]{3}\s|[$€£])", price_str.strip())
        return m.group(1).strip() if m else ""
    except Exception:
        return ""

async def get_discount_text(page: Page) -> Optional[str]:
    """Extract discount percent text where present; fall back to price text scan."""
    candidates = [
        "text=/Save\s*\d+%/i",
        "[class*='discount']",
        "[data-testid*='discount']",
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if await page.locator(sel).count() == 0:
                continue
            txt = await loc.inner_text(timeout=1000)
            if txt:
                m = re.search(r"(\d{1,2})%", txt)
                if m:
                    return f"{m.group(1)}%"
        except Exception:
            continue
    # Fallback: scan price container text for percent
    any_txt = await get_price_text(page)
    if any_txt:
        m = re.search(r"(\d{1,2})%", any_txt)
        if m:
            return f"{m.group(1)}%"
    return None


def compute_missing_prices(
    list_price: Optional[str], sale_price: Optional[str], discount: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
    """If only list or sale is present and we know the discount percent,
    compute the missing side and return both, preserving currency markers."""
    if not discount or (list_price and sale_price):
        return (list_price, sale_price)
    try:
        d = re.search(r"(\d{1,2})%", discount)
        if not d:
            return (list_price, sale_price)
        pct = int(d.group(1)) / 100.0
        if pct <= 0 or pct >= 0.95:
            return (list_price, sale_price)
        if list_price and not sale_price:
            cur = _extract_currency_prefix(list_price)
            num = float(re.sub(r"[^\d.]", "", list_price))
            sale_val = round(num * (1 - pct) + 1e-6, 2)
            sale_price = f"{cur} {sale_val:.2f}".strip() if cur else f"{sale_val:.2f}"
        elif sale_price and not list_price:
            cur = _extract_currency_prefix(sale_price)
            num = float(re.sub(r"[^\d.]", "", sale_price))
            if 1 - pct > 0:
                list_val = round(num / (1 - pct) + 1e-6, 2)
                list_price = f"{cur} {list_val:.2f}".strip() if cur else f"{list_val:.2f}"
    except Exception:
        return (list_price, sale_price)
    return (list_price, sale_price)


def normalize_price_order(
    list_price: Optional[str], sale_price: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
    """Ensure list price is not lower than sale price when both exist."""
    try:
        if list_price and sale_price:
            def to_float(p: str) -> float:
                return float(re.sub(r"[^\d.]", "", p)) if re.search(r"\d", p) else 0.0
            lp = to_float(list_price)
            sp = to_float(sale_price)
            # Ensure list is the higher price
            if sp > lp:
                return (sale_price, list_price)
    except Exception:
        return (list_price, sale_price)
    return (list_price, sale_price)


async def extract_breadcrumb(page: Page) -> Optional[str]:
    """Try multiple breadcrumb patterns to derive a category path string."""
    try:
        nav_loc = page.locator('nav[aria-label="breadcrumb"]')
        if await nav_loc.count() > 0:
            try:
                crumb = await nav_loc.first.inner_text(timeout=1000)
                crumb = re.sub(r"\s+", " ", (crumb or "")).strip()
                if crumb:
                    return crumb
            except Exception:
                pass
    except Exception:
        pass
    try:
        bc_loc = page.locator(".breadcrumb, .breadcrumbs")
        if await bc_loc.count() > 0:
            try:
                crumb = await bc_loc.first.inner_text(timeout=1000)
                crumb = re.sub(r"\s+", " ", (crumb or "")).strip()
                if crumb:
                    return crumb
            except Exception:
                pass
    except Exception:
        pass
    return None


async def find_color_swatch_locators(page: Page) -> List[str]:
    # Return selector strings to click for each color option (filtering out sizes)
    # Tries several selector patterns and filters out obvious size options.
    candidate_selectors = [
        "[data-testid*='color']:is(button,[role='radio'])",
        "button[aria-label*='Color']",
        "button[aria-label*='colour']",
        "[class*='color'] [role='radio']",
        "[class*='swatch'] [role='radio']",
        # Many PDPs render colours as list items with aria-labels inside a colour selector container
        ".qa--colour-selector li[aria-label]",
        "fieldset[class*='colour'] li[aria-label]",
        "ul[class*='colour'] li[aria-label]",
        "ol[class*='colour'] li[aria-label]",
        "[class*='color'] li[aria-label]",
        "[class*='colour'] li[aria-label]",
    ]
    size_pattern = re.compile(r"^(XXS|XS|S|M|L|XL|XXL|XXXL|\d|\d+\.\d+|\d+M|\d+W)$", re.I)
    results: List[str] = []
    for sel in candidate_selectors:
        try:
            loc = page.locator(sel)
            count = await loc.count()
            if count == 0:
                continue
            for i in range(count):
                el = loc.nth(i)
                try:
                    label = await el.get_attribute("aria-label")
                    if not label:
                        label = (await el.text_content() or "").strip()
                    if label and size_pattern.search(label):
                        continue
                    results.append(f"{sel}:nth-of-type({i+1})")
                except Exception:
                    continue
            if results:
                break
        except Exception:
            continue
    return results

async def read_discounted_color_name(page: Page) -> Optional[str]:
    """Read explicit discounted color label if present and return its text."""
    # Try to read the explicit "Discounted colour:" or "Discounted color:" label
    labels = [
        "text=Discounted colour:",
        "text=Discounted color:",
    ]
    for sel in labels:
        try:
            loc = page.locator(sel)
            if await loc.count() == 0:
                continue
            # The color name may be in the next sibling or following text node
            parent = loc.first.locator("xpath=..")
            try:
                sibling_text = await parent.first.inner_text(timeout=1000)
                # Expect format: "Discounted colour: Blue Tetra/Black"
                m = re.search(r"Discounted colou?r:\s*(.+)$", sibling_text, re.I)
                if m:
                    return m.group(1).strip()
            except Exception:
                pass
        except Exception:
            continue
    return None


async def read_selected_color_name(page: Page) -> Optional[str]:
    # Try to read selected color label
    candidates = [
        "[data-testid='selected-color-name']",
        "[data-testid='pdp-color-label']",
        "[aria-live] .color-name",
        ".selected .color-name, .ColorName",
    ]
    for sel in candidates:
        try:
            txt = await page.locator(sel).first.text_content(timeout=1000)
            if txt:
                return txt.strip()
        except Exception:
            continue
    return None


async def extract_prices(page: Page) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    # Return (list_price, sale_price, discount)
    # Strategy:
    # 1) Look for dedicated compare/current price containers
    # 2) Parse generic price text; if two values, min→sale, max→list
    # 3) Pull discount percent where available
    # 4) As a last resort, scan the whole body for money values and apply min/max
    try:
        # Try to capture separate list/sale by dedicated selectors first
        dedicated = [
            ("list", [
                "[data-testid*='compare']",
                ".compare-at, .CompareAt, [class*='compare']",
                ".price--compare, .price-compare, .was-price, .PriceCompare",
                "sdel, del, .strike, .strikethrough",
            ]),
            ("sale", [
                "[data-testid*='current']",
                ".current-price, .CurrentPrice, [class*='current']",
                ".price--sale, .sale-price, .SalePrice, .PriceSale",
                ".price, .Price .value",
            ]),
        ]
        values: Dict[str, Optional[str]] = {"list": None, "sale": None}
        for key, sels in dedicated:
            for sel in sels:
                try:
                    loc = page.locator(sel).first
                    if await page.locator(sel).count() == 0:
                        continue
                    txt = await loc.inner_text(timeout=1000)
                    txt = re.sub(r"\s+", " ", txt or "").strip()
                    moneys = re.findall(r"[$€£]\s?\d+[\d,.]*", txt)
                    if moneys:
                        values[key] = moneys[0]
                        break
                except Exception:
                    continue

        # Always parse generic price text and override if it contains two amounts
        txt = await get_price_text(page)
        txt = re.sub(r"\s+", " ", txt or "").strip()
        money = re.findall(r"[$€£]\s?\d+[\d,.]*", txt)
        if len(money) >= 2:
            # Choose smallest as sale, largest as list
            def to_float(m: str) -> float:
                return float(re.sub(r"[^\d.]", "", m)) if re.search(r"\d", m) else 0.0
            nums = [(m, to_float(m)) for m in money]
            sale_candidate = min(nums, key=lambda x: x[1])[0]
            list_candidate = max(nums, key=lambda x: x[1])[0]
            values["sale"] = sale_candidate
            values["list"] = list_candidate
        elif len(money) == 1:
            # If copy mentions Save %, single number is likely the list/original price
            if re.search(r"save\s*\d+%", txt, re.I):
                values["list"] = values["list"] or money[0]
            else:
                values["sale"] = values["sale"] or money[0]

        # Extract discount if present anywhere
        discount = None
        if txt:
            mdisc = re.search(r"(\d{1,2})%", txt)
            if mdisc:
                discount = f"{mdisc.group(1)}%"

        # Wide-scan fallback: if one of list/sale is missing or equal, scan the
        # full body text for all currency values and assign smallest to sale and
        # largest to list. This helps when a container only exposes one number
        # (often the compare-at) to narrower selectors.
        try:
            body_txt = await page.locator("body").inner_text(timeout=1500)
        except Exception:
            body_txt = txt or ""
        cleaned_body = " ".join([
            line.strip()
            for line in body_txt.splitlines()
            if not re.search(r"payments?|klarna|afterpay|affirm|interest", line, re.I)
        ])
        money_all = re.findall(r"[$€£]\s?\d+[\d,.]*", cleaned_body)
        if (values["sale"] is None or values["list"] is None or values["sale"] == values["list"]) and len(money_all) >= 2:
            def to_float_all(m: str) -> float:
                return float(re.sub(r"[^\d.]", "", m)) if re.search(r"\d", m) else 0.0
            nums_all = [(m, to_float_all(m)) for m in money_all]
            sale_candidate = min(nums_all, key=lambda x: x[1])[0]
            list_candidate = max(nums_all, key=lambda x: x[1])[0]
            values["sale"] = sale_candidate
            values["list"] = list_candidate

        return (values["list"], values["sale"], discount)
    except Exception:
        pass
    return (None, None, None)

async def dismiss_cookie_banner(page: Page) -> None:
    """Best-effort dismissal of common cookie consent banners."""
    selectors = [
        "#onetrust-accept-btn-handler",
        "button#accept-recommended-btn-handler",
        "button[aria-label*='Accept']",
        "button:has-text('Accept All')",
        "button:has-text('Accept')",
        "[data-testid*='cookie'] button:has-text('Accept')",
    ]
    for sel in selectors:
        try:
            if await page.locator(sel).count() > 0:
                await page.locator(sel).first.click(timeout=1000)
                await asyncio.sleep(0.2)
                break
        except Exception:
            continue

async def select_first_in_stock_size(page: Page) -> None:
    """Click the first available size to encourage price rendering on PDPs."""
    try:
        sizes = await extract_sizes(page)
        for size, instock in sizes:
            if instock and size:
                candidates = [
                    f"[data-testid='pdp-size-option']:has-text('{size}')",
                    f"[role='radio']:has-text('{size}')",
                    f"button[aria-label*='{size}']",
                ]
                for sel in candidates:
                    try:
                        if await page.locator(sel).count() > 0:
                            await page.locator(sel).first.click(timeout=1000)
                            await asyncio.sleep(0.4)
                            return
                    except Exception:
                        continue
    except Exception:
        return


async def extract_sizes(page: Page) -> List[Tuple[str, bool]]:
    """Return list of `(size_label, in_stock)` discovered on the PDP."""
    sizes: List[Tuple[str, bool]] = []
    selectors = [
        "[data-testid='pdp-size-option']",
        "[data-testid='size-selector'] [role='radio']",
        "[role='radiogroup'] [role='radio']",
        ".size, .Size, .size-chip, .sizeChip, button[aria-label*='Size']",
        ".qa--size-list li",
        "[class*='size-list'] li",
    ]
    for sel in selectors:
        loc = page.locator(sel)
        try:
            count = await loc.count()
            if count == 0:
                continue
            for i in range(count):
                el = loc.nth(i)
                try:
                    # Prefer explicit size value (e.g., data-size-value="29-R") from child button/radio
                    label = ""
                    try:
                        btn = el.locator("button,[role='radio']").first
                        if await btn.count() > 0:
                            dv = await btn.get_attribute("data-size-value", timeout=300)
                            if dv:
                                label = dv.strip()
                    except Exception:
                        pass
                    if not label:
                        label = (await el.get_attribute("aria-label", timeout=300) or "").strip()
                    if not label:
                        label = (await el.text_content(timeout=800) or "").strip()
                except Exception:
                    label = ""
                # Normalize e.g., "29-R" -> "29R"
                label = re.sub(r"\s+", "", label)
                label = label.replace("-", "")
                try:
                    disabled_attr = await el.get_attribute("disabled", timeout=300)
                except Exception:
                    disabled_attr = None
                try:
                    aria_disabled = await el.get_attribute("aria-disabled", timeout=300)
                except Exception:
                    aria_disabled = None
                # Consider OOS class markers on button or li (e.g., 'no--stock')
                try:
                    btn = el.locator("button,[role='radio']").first
                    btn_class = await btn.get_attribute("class", timeout=300) if await btn.count() > 0 else None
                except Exception:
                    btn_class = None
                try:
                    el_class = await el.get_attribute("class", timeout=300)
                except Exception:
                    el_class = None
                no_stock = bool(
                    (btn_class and re.search(r"no--stock|sold|out", btn_class, re.I))
                    or (el_class and re.search(r"no--stock|sold|out|disabled", el_class, re.I))
                )
                is_disabled = bool(disabled_attr is not None or (aria_disabled and aria_disabled.lower() == "true") or no_stock)
                sizes.append((label, not is_disabled))
            if sizes:
                break
        except Exception:
            continue
    # Deduplicate
    unique: Dict[str, bool] = {}
    for s, instock in sizes:
        if s and s not in unique:
            unique[s] = instock
    return [(k, v) for k, v in unique.items()]


async def read_image_url(page: Page) -> Optional[str]:
    """Extract a representative product image URL if available."""
    candidates = [
        "figure[data-testid*='hero'] img",
        "[data-testid*='hero'] img",
        "[data-testid='pdp-hero-image'] img",
        ".swiper-slide.swiper-slide-active img",
        "img[alt*='product'], img[alt*='Product'], img.hero, .ProductGallery img",
    ]
    for sel in candidates:
        try:
            node = page.locator(sel).first
            # Prefer currentSrc which resolves srcset selection
            try:
                current = await node.evaluate("el => el.currentSrc || el.src || el.getAttribute('src') || ''", timeout=1000)
            except Exception:
                current = ""
            src = current
            if not src:
                src = await node.get_attribute("src", timeout=500)
            if not src:
                src = await node.get_attribute("data-src", timeout=300)
            if not src:
                srcset = await node.get_attribute("srcset", timeout=300)
                if srcset:
                    parts = [p.strip() for p in srcset.split(",") if p.strip()]
                    if parts:
                        src = parts[0].split(" ")[0]
            if src:
                if src.startswith("//"):
                    return f"https:{src}"
                return src
        except Exception:
            continue
    return None


async def parse_pdp(page: Page, url: str, cfg: Config, locale: str) -> List[AggregatedRow]:
    """Parse a single PDP. For each color option found, extract prices,
    discount, sizes, image, and emit one `AggregatedRow`. De-duplication by
    `(product_url, color)` is applied upstream in the crawler."""
    console.log(f"parse_pdp: start {url}")
    rows: List[AggregatedRow] = []
    json_ld = await parse_json_ld(page)
    next_data = await parse_next_data(page)
    base_name = json_ld.get("name") if isinstance(json_ld, dict) else None
    sku = json_ld.get("sku") if isinstance(json_ld, dict) else None
    category_path = await extract_breadcrumb(page)

    color_selectors = await find_color_swatch_locators(page)
    if not color_selectors:
        color_selectors = ["BODY_NO_COLOR"]  # sentinel for single-color products
    console.log(f"parse_pdp: found {len(color_selectors)} color options")

    if cfg.max_colors and cfg.max_colors > 0:
        color_selectors = color_selectors[: cfg.max_colors]

    # If page indicates a specific discounted colour, prioritize its swatch
    discounted_name = await read_discounted_color_name(page)
    if discounted_name and color_selectors and color_selectors[0] != "BODY_NO_COLOR":
        prioritized: List[str] = []
        others: List[str] = []
        for sel in color_selectors:
            try:
                al = await page.locator(sel).get_attribute("aria-label")
                txt = (al or "").lower()
                if discounted_name.lower() in txt:
                    prioritized.append(sel)
                else:
                    others.append(sel)
            except Exception:
                others.append(sel)
        color_selectors = prioritized + others if prioritized else color_selectors

    for idx, sel in enumerate(color_selectors):
        try:
            color_name: Optional[str] = None
            if sel != "BODY_NO_COLOR":
                try:
                    await page.locator(sel).click(timeout=5000)
                    # After clicking a color, wait for size options and price to refresh
                    await jitter_sleep(cfg.jitter_min_ms, cfg.jitter_max_ms)
                except Exception:
                    # Fallback: click a child interactive element if the list item isn't directly clickable
                    try:
                        await page.locator(f"{sel} button, {sel} [role='radio'], {sel} label").first.click(timeout=3000)
                    except Exception:
                        pass
                # Try to read color name directly from the clicked swatch
                try:
                    color_name = await page.locator(sel).get_attribute("aria-label")
                except Exception:
                    color_name = None
                if not color_name:
                    try:
                        color_name = await page.locator(f"{sel} [aria-label]").first.get_attribute("aria-label")
                    except Exception:
                        color_name = None
                if not color_name:
                    color_name = await read_selected_color_name(page)
                # Wait briefly after color change for DOM/state to settle
                await asyncio.sleep(0.8)

            # Dismiss cookie banner once on PDP
            await dismiss_cookie_banner(page)
            # IMPORTANT: read sizes for this color BEFORE selecting a size,
            # because available inventory can differ per color.
            # Prefer Arc'teryx __NEXT_DATA__ when available; fallback to DOM.
            sizes_from_next, size_qty_map = _parse_arc_sizes_from_next_data(next_data, color_name)
            sizes_before_selection = sizes_from_next or await extract_sizes(page)
            # Then select the first available size to force price rendering
            await select_first_in_stock_size(page)
            list_price, sale_price, discount = await extract_prices(page)
            if not sale_price or not list_price:
                # JSON fallbacks from ld+json and Next.js data
                ld_list, ld_sale, _ = extract_prices_from_json_ld(json_ld)
                nd = await parse_next_data(page)
                found = _walk_find_prices(nd)
                # Try to infer currency from JSON-LD if available
                currency = None
                try:
                    offers = json_ld.get("offers") if isinstance(json_ld, dict) else None
                    if isinstance(offers, dict):
                        currency = offers.get("priceCurrency")
                    elif isinstance(offers, list) and offers:
                        currency = offers[0].get("priceCurrency") if isinstance(offers[0], dict) else None
                except Exception:
                    currency = None

                compare_val = found.get("compareatprice") or found.get("compare_at_price") or found.get("compare_at") or found.get("listprice")
                current_val = found.get("saleprice") or found.get("finalprice") or found.get("price")
                list_price = list_price or _to_price_string(ld_list, currency) or _to_price_string(compare_val, currency)
                sale_price = sale_price or _to_price_string(ld_sale, currency) or _to_price_string(current_val, currency)

            # If only one side present but we have discount, compute the other
            if not discount:
                discount = await get_discount_text(page)
            list_price, sale_price = compute_missing_prices(list_price, sale_price, discount)
            # Final safety: ensure list is higher than sale when both present
            list_price, sale_price = normalize_price_order(list_price, sale_price)
            # Re-read sizes in case the site updates availability after size select
            # After a possible size click, prefer previously resolved sizes
            sizes = sizes_before_selection or await extract_sizes(page)
            # Prefer image from NEXT data for colour; fallback to JSON-LD then DOM
            image_url = _extract_image_url_from_next(next_data, color_name)
            if not image_url:
                try:
                    img_ld = json_ld.get("image") if isinstance(json_ld, dict) else None
                    if isinstance(img_ld, list) and img_ld:
                        image_url = str(img_ld[0])
                    elif isinstance(img_ld, str):
                        image_url = img_ld
                except Exception:
                    image_url = None
            if not image_url:
                image_url = await read_image_url(page)
            # Attempt to extract a numeric inventory amount from Next.js data
            inventory_amount: Optional[int] = None
            size_quantities_str: Optional[str] = None
            try:
                nd_inv = await parse_next_data(page)
                hint_color = (color_name or "")
                inv_values = _walk_collect_inventory_amounts(nd_inv, hint_color, sku)
                if inv_values:
                    # Prefer max to reflect total available if multiple sizes listed
                    total = sum([v for v in inv_values if isinstance(v, int)])
                    # If total seems unrealistically high, fall back to max
                    inventory_amount = total if 0 <= total <= 2000 else max(inv_values)
                # Collect per-size quantities. Prefer Arc-specific mapping if found.
                if not size_qty_map:
                    size_qty_map = _walk_collect_size_quantities(nd_inv, hint_color)
                # If we have a concrete map, also set inventory_amount as sum
                if size_qty_map and (inventory_amount is None or inventory_amount == 0):
                    try:
                        inventory_amount = sum(int(v) for v in size_qty_map.values() if isinstance(v, (int, float)))
                    except Exception:
                        pass
                if size_qty_map:
                    try:
                        size_quantities_str = json.dumps(size_qty_map, ensure_ascii=False)
                    except Exception:
                        size_quantities_str = None
            except Exception:
                inventory_amount = None

            # Fallback name: page title
            name = base_name
            if not name:
                try:
                    name = (await page.locator("h1").first.text_content(timeout=1000) or "").strip() or None
                except Exception:
                    name = None

            sizes_all_list: List[str] = []
            sizes_in_list: List[str] = []
            sizes_out_list: List[str] = []
            if sizes:
                for size, instock in sizes:
                    if size:
                        sizes_all_list.append(size)
                        if instock:
                            sizes_in_list.append(size)
                        else:
                            sizes_out_list.append(size)

            sizes_all = ",".join(sizes_all_list)
            sizes_in = ",".join(sizes_in_list)
            sizes_out = ",".join(sizes_out_list)

            # If color name is missing, use index as a fallback label to avoid dedupe drop
            if not color_name:
                color_name = f"color_{idx+1}"

            hash_key = compute_color_hash_key(url, color_name)
            row = AggregatedRow(
                crawl_ts=now_iso(),
                locale=locale,
                category_path=category_path,
                name=name,
                sku=sku,
                product_url=url,
                color=color_name,
                list_price=list_price,
                sale_price=sale_price,
                discount=discount,
                image_url=image_url,
                inventory_amount=inventory_amount,
                size_quantities=size_quantities_str,
                sizes_all=sizes_all,
                sizes_in_stock=sizes_in,
                sizes_out_of_stock=sizes_out,
                num_sizes_in_stock=len(sizes_in_list),
                hash_key=hash_key,
                source="arcteryx-outlet",
            )
            rows.append(row)
            console.log(f"parse_pdp: color={color_name} sizes_all={len(sizes_all_list)} in_stock={len(sizes_in_list)}")
        except Exception as e:
            console.log(f"parse_pdp: error on color index {idx+1}: {e}")
            continue
    return rows


def expected_header() -> List[str]:
    """Define the CSV header order written by the sink functions."""
    return [
        "crawl_ts",
        "locale",
        "category_path",
        "name",
        "sku",
        "product_url",
        "color",
        "list_price",
        "sale_price",
        "discount",
        "image_url",
        "inventory_amount",
        "size_quantities",
        "sizes_all",
        "sizes_in_stock",
        "sizes_out_of_stock",
        "num_sizes_in_stock",
        "hash_key",
        "source",
    ]


def rotate_if_incompatible(csv_path: Path) -> None:
    """If an existing CSV has a different header, rotate it to a timestamped
    backup to keep new runs consistent."""
    if not csv_path.exists():
        return
    try:
        with csv_path.open("r") as f:
            first_line = f.readline().strip()
        current = [h.strip() for h in first_line.split(",")]
        if current != expected_header():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = csv_path.with_suffix(f".csv.bak_{ts}")
            csv_path.rename(backup)
            console.log(f"Rotated old CSV to {backup}")
    except Exception as e:
        console.log(f"Could not inspect CSV header, rotating: {e}")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = csv_path.with_suffix(f".csv.bak_{ts}")
        try:
            csv_path.rename(backup)
        except Exception:
            pass


async def ensure_csv(csv_path: Path) -> None:
    """Create a new CSV with the expected header if it does not exist."""
    rotate_if_incompatible(csv_path)
    if not csv_path.exists():
        with csv_path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(expected_header())


async def append_rows(csv_path: Path, rows: Iterable[AggregatedRow]) -> None:
    """Append aggregated rows to the CSV in a stable, ordered fashion."""
    with csv_path.open("a", newline="") as f:
        writer = csv.writer(f)
        for r in rows:
            writer.writerow([
                r.crawl_ts,
                r.locale,
                r.category_path or "",
                r.name or "",
                r.sku or "",
                r.product_url,
                r.color or "",
                r.list_price or "",
                r.sale_price or "",
                r.discount or "",
                r.image_url or "",
                r.inventory_amount if r.inventory_amount is not None else "",
                r.size_quantities or "",
                r.sizes_all,
                r.sizes_in_stock,
                r.sizes_out_of_stock,
                r.num_sizes_in_stock,
                r.hash_key,
                r.source,
            ])


async def crawl_category(category_url: str, cfg: Config) -> None:
    """Orchestrate the crawl: open category, collect PDP links, visit each PDP
    with retry/politeness, parse rows, de-duplicate by color, and write CSV."""
    locale = "us-en" if "/us/" in category_url else "ca-en"
    csv_path = Path(cfg.output_csv)
    db = AlertDB(cfg.output_db)
    db.ensure_schema()
    run_id = db.begin_run(now_iso())
    await ensure_csv(csv_path)
    seen_color_keys: Set[str] = set()

    async with async_playwright() as p:
        # Prefer setting proxy at browser launch with explicit auth fields
        launch_kwargs: Dict[str, Any] = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        proxy_conf = _build_playwright_proxy(cfg.proxy_url)
        if proxy_conf:
            launch_kwargs["proxy"] = proxy_conf
        browser = await p.chromium.launch(**launch_kwargs)

        context_args: Dict[str, Any] = {"user_agent": cfg.user_agent}
        context: BrowserContext = await browser.new_context(**context_args)
        page: Page = await context.new_page()
        console.log(f"Navigating to category: {category_url}")
        await page.goto(category_url, wait_until="domcontentloaded", timeout=60000)
        await jitter_sleep(cfg.jitter_min_ms, cfg.jitter_max_ms)

        # Single-PDP mode: when given a /shop/ URL, crawl only that PDP
        if "/shop/" in urlparse(category_url).path:
            product_links = [category_url]
            console.log("Single-PDP mode: crawling provided product only")
        else:
            product_links = await scroll_and_collect_product_links(page)
            console.log(f"Discovered {len(product_links)} product links")

        # Crawl each PDP
        # Optional limit via argv[2]
        limit: Optional[int] = None
        try:
            if len(sys.argv) > 2 and sys.argv[2].isdigit():
                limit = int(sys.argv[2])
        except Exception:
            limit = None

        # Optional start index to support resume. Can be provided via env START_AT
        # or as argv[3] (0-based). If not provided, defaults to 0.
        start_at: int = 0
        try:
            if len(sys.argv) > 3 and sys.argv[3].isdigit():
                start_at = int(sys.argv[3])
            else:
                env_start = os.getenv("START_AT", "0").strip()
                if env_start.isdigit():
                    start_at = int(env_start)
        except Exception:
            start_at = 0

        for i, href in enumerate(product_links):
            if i < start_at:
                continue
            if limit is not None and i >= limit:
                break
            # Normalize href to absolute using origin to avoid duplicate locale segments
            if not href.startswith("http"):
                base_parsed = urlparse(cfg.outlet_base)
                origin = f"{base_parsed.scheme}://{base_parsed.netloc}"
                if href.startswith("/"):
                    href = origin + href
                else:
                    href = urljoin(cfg.outlet_base if cfg.outlet_base.endswith("/") else cfg.outlet_base + "/", href)
            console.log(f"Visiting PDP {i+1}/{len(product_links)}: {href}")
            # Retry navigation up to 3 times on transient errors
            nav_ok = False
            for attempt in range(3):
                try:
                    await page.goto(href, wait_until="domcontentloaded", timeout=60000)
                    nav_ok = True
                    break
                except PWError as e:
                    console.log(f"Navigation failed (attempt {attempt+1}/3) for {href}: {e}")
                    await jitter_sleep(500, 1200)
                    continue
            if not nav_ok:
                continue
            await asyncio.sleep(cfg.pdp_delay_ms / 1000.0)

            try:
                rows = await asyncio.wait_for(parse_pdp(page, href, cfg, locale), timeout=180)
            except Exception as e:
                console.log(f"PDP parse failed for {href}: {e}")
                continue

            # Deduplicate by product_url+color
            new_rows = []
            for r in rows:
                key = f"{r.product_url}|{r.color or ''}"
                if key in seen_color_keys:
                    continue
                seen_color_keys.add(key)
                new_rows.append(r)
            if new_rows:
                await append_rows(csv_path, new_rows)
                # Record observations in DB
                for r in new_rows:
                    db.upsert_variant(
                        hash_key=r.hash_key,
                        product_url=r.product_url,
                        color=r.color,
                        name=r.name,
                        image_url=r.image_url,
                        crawl_ts=r.crawl_ts,
                        num_in_stock=r.num_sizes_in_stock,
                    )
                    db.insert_observation(
                        run_id=run_id,
                        hash_key=r.hash_key,
                        crawl_ts=r.crawl_ts,
                        num_sizes_in_stock=r.num_sizes_in_stock,
                        sizes_in_stock=r.sizes_in_stock,
                        sizes_all=r.sizes_all,
                        size_quantities=r.size_quantities,
                        list_price=r.list_price,
                        sale_price=r.sale_price,
                        discount=r.discount,
                    )
                console.log(f"Appended {len(new_rows)} rows from {href}")
            else:
                console.log(f"No rows extracted for {href}")

            # Politeness
            await jitter_sleep(cfg.jitter_min_ms + 400, cfg.jitter_max_ms + 1200)

        await context.close()
        await browser.close()
        db.finish_run(run_id, now_iso())


def load_env() -> None:
    # Load from .env if present
    try:
        load_dotenv()
    except Exception:
        pass


async def main() -> None:
    """Entrypoint: load configuration, choose start URL, and run the crawl."""
    load_env()
    cfg = Config()

    # If a category URL is provided as argv[1], use that; else default to Men's
    if len(sys.argv) > 1:
        start_url = sys.argv[1]
    else:
        # Men's outlet category as requested
        start_url = "https://outlet.arcteryx.com/us/en/c/mens"

    await crawl_category(start_url, cfg)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.log("Interrupted by user")
    except Exception as e:
        console.log(f"Fatal error: {e}")


