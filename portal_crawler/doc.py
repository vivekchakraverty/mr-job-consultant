"""Parsed HTML page with the bits the classifier, extractor and access checker need."""
import json
from functools import cached_property
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .fetcher import Page
from .urls import normalize


class Doc:
    def __init__(self, page: Page):
        self.page = page
        self.url = page.final_url
        self.soup = BeautifulSoup(page.text, "lxml")
        # JSON-LD must be read before scripts are stripped for the visible text.
        self.json_ld = _read_json_ld(self.soup)
        self.raw_html = page.text
        for tag in self.soup(["script", "style", "noscript", "template", "svg"]):
            tag.decompose()

    @cached_property
    def title(self) -> str:
        return self.soup.title.get_text(" ", strip=True) if self.soup.title else ""

    def meta(self, name: str) -> str:
        tag = self.soup.find("meta", attrs={"name": name}) or self.soup.find("meta", attrs={"property": name})
        return (tag.get("content") or "").strip() if tag else ""

    @cached_property
    def text(self) -> str:
        body = self.soup.body or self.soup
        return " ".join(body.get_text(" ", strip=True).split())

    @cached_property
    def headings(self) -> str:
        return " ".join(h.get_text(" ", strip=True) for h in self.soup.find_all(["h1", "h2"])[:20])

    @cached_property
    def head_text(self) -> str:
        """Title, descriptions and top headings: what the site says about itself."""
        return " ".join([self.title, self.meta("description"), self.meta("og:site_name"),
                         self.meta("og:description"), self.headings])

    @cached_property
    def lang(self) -> str:
        html = self.soup.find("html")
        return (html.get("lang") or "").strip() if html else ""

    @cached_property
    def links(self) -> list[tuple[str, str]]:
        """(absolute url, anchor text) for every <a href>."""
        out, seen = [], set()
        for a in self.soup.find_all("a", href=True):
            url = normalize(urljoin(self.url, a["href"]))
            if url and url not in seen:
                seen.add(url)
                out.append((url, a.get_text(" ", strip=True)))
        return out

    @cached_property
    def has_job_posting(self) -> bool:
        return any(_type_is(item, "JobPosting") for item in self.json_ld) or \
            'itemtype="http://schema.org/JobPosting"' in self.raw_html or \
            'itemtype="https://schema.org/JobPosting"' in self.raw_html

    @cached_property
    def has_password_field(self) -> bool:
        return self.soup.find("input", attrs={"type": "password"}) is not None


def _type_is(item: dict, name: str) -> bool:
    t = item.get("@type")
    return t == name or (isinstance(t, list) and name in t)


def _read_json_ld(soup: BeautifulSoup) -> list[dict]:
    items: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                items.append(node)
                if "@graph" in node:
                    stack.append(node["@graph"])
    return items
