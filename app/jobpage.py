"""Read a job post's full description and find where to apply.

No AI and no account involved: one polite HTTP fetch (robots.txt respected) of the job page.
Falls back to the description the feed/API already gave us when the page can't be read.
"""
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from portal_crawler.config import Settings
from portal_crawler.doc import Doc
from portal_crawler.fetcher import Fetcher
from portal_crawler.urls import registered_domain

APPLY_TEXT = re.compile(r"^\s*(apply|apply now|apply for (this|the) (job|position|role)|apply here|"
                        r"apply on (company|employer)('s)? (site|website)|easy apply|submit (your )?application|"
                        r"postuler|bewerben|jetzt bewerben|candidatar|aplicar|postular)\b", re.I)
ATS_HOSTS = re.compile(r"greenhouse\.io|lever\.co|myworkdayjobs\.com|ashbyhq\.com|smartrecruiters\.com|"
                       r"workable\.com|bamboohr\.com|recruitee\.com|teamtailor\.com|jobvite\.com|icims\.com|"
                       r"breezy\.hr|personio\.|pinpointhq\.com|rippling\.com|jazzhr\.com", re.I)


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for br in soup.find_all(["br"]):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    for block in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "ul", "ol", "section"]):
        block.insert_after("\n")
    text = soup.get_text("")
    lines = [" ".join(l.split()) for l in text.splitlines()]
    out, blank = [], False
    for l in lines:
        if l:
            out.append(l)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def _job_posting(doc: Doc) -> dict:
    for item in doc.json_ld:
        t = item.get("@type")
        if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
            return item
    return {}


NOT_APPLY_PATH = re.compile(r"/(blog|news|articles?|guides?|resources|why-|how-to|login|signin|sign-in|register|"
                            r"signup|pricing|premium|auto-apply)", re.I)


def _apply_url(doc: Doc, posting: dict) -> str | None:
    """Best guess at the application page; None means "apply on the job page itself".

    In order: a link to a known applicant-tracking system, an "Apply" button leading off-site
    (the employer's own form), an "Apply" link on the same site whose path says apply.
    """
    own = registered_domain(doc.url)
    candidates = []
    for url, anchor in doc.links:
        if NOT_APPLY_PATH.search(urlsplit(url).path) or url.split("#")[0] == doc.url.split("#")[0]:
            continue
        is_apply = bool(APPLY_TEXT.match(anchor or ""))
        if ATS_HOSTS.search(url) and (is_apply or re.search(r"apply|job|posting|career", url, re.I)):
            candidates.append((0, url))
        elif is_apply and registered_domain(url) != own:
            candidates.append((1, url))
        elif is_apply and re.search(r"apply", urlsplit(url).path + urlsplit(url).query, re.I):
            candidates.append((2, url))
    for a in doc.soup.find_all(["a", "button"], attrs={"data-apply-url": True}):
        candidates.append((0, urljoin(doc.url, a["data-apply-url"])))
    if candidates:
        return sorted(candidates)[0][1]
    direct = (posting.get("potentialAction") or {}).get("target") if isinstance(posting.get("potentialAction"), dict) else None
    return urljoin(doc.url, direct) if isinstance(direct, str) else None


def _main_text(html: str) -> str:
    try:
        import trafilatura
        text = trafilatura.extract(html, include_comments=False, include_tables=True, favor_recall=True)
        if text:
            return text
    except Exception:
        pass
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    return _html_to_text(str(main))


async def fetch_job_page(url: str, fallback: dict) -> dict:
    """{title, company, location, description, apply_url, source_url, fetched, note}."""
    result = {
        "title": fallback.get("title", ""), "company": fallback.get("company", ""),
        "location": fallback.get("location", ""), "description": fallback.get("description", ""),
        "apply_url": url, "source_url": url, "fetched": False, "note": "",
    }
    async with Fetcher(Settings(per_host_delay=0.5, timeout=20.0)) as fetcher:
        page = await fetcher.get(url)
    if page.error == "robots":
        result["note"] = "This site asks crawlers not to read its job pages, so we used the listing's summary."
        return result
    if not page.ok or not page.is_html:
        result["note"] = f"The job page didn't open for us ({page.error or 'HTTP ' + str(page.status)}), " \
                         "so we used the listing's summary."
        return result

    doc = Doc(page)
    posting = _job_posting(doc)
    description = _html_to_text(posting.get("description", "")) if posting.get("description") else ""
    if len(description) < 400:
        main = _main_text(page.text)
        if len(main) > len(description):
            description = main
    if len(description) < len(result["description"]):
        description = result["description"]  # the feed's copy was more complete

    org = posting.get("hiringOrganization") or {}
    place = posting.get("jobLocation") or {}
    if isinstance(place, list):
        place = place[0] if place else {}
    address = (place.get("address") or {}) if isinstance(place, dict) else {}
    parts = []
    if isinstance(address, dict):
        for key in ("addressLocality", "addressRegion", "addressCountry"):
            v = address.get(key)
            v = v.get("name") if isinstance(v, dict) else v
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
    result.update(
        title=posting.get("title") or result["title"] or doc.title,
        company=(org.get("name") if isinstance(org, dict) else None) or result["company"],
        location=", ".join(parts) or result["location"],
        description=description[:15000],
        apply_url=_apply_url(doc, posting) or page.final_url,
        source_url=page.final_url,
        fetched=True,
    )
    if urlsplit(result["apply_url"]).scheme not in ("http", "https"):
        result["apply_url"] = page.final_url
    return result
