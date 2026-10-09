"""Public data adapters. No login scraping, email guessing, or identity inference."""
import html
import ipaddress
import json
import os
import re
import socket
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

UA = "YCPeopleLocal/1.0 (public company research)"
YC = "https://www.ycombinator.com"
SEASONS = {"Winter": 1, "Spring": 2, "Summer": 3, "Fall": 4}


def safe_url(value, base=""):
    if not isinstance(value, str) or not value.strip():
        return ""
    url = urllib.parse.urljoin(base, value.strip())
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        return ""
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))


def public_address(url):
    p = urllib.parse.urlsplit(safe_url(url))
    if not p.hostname or p.port not in (None, 80, 443):
        raise ValueError("Only public HTTP(S) URLs on standard ports are supported")
    addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Refusing a non-public network address")


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_address(newurl)
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result and urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            result.remove_header("Authorization")
        return result


class Fetcher:
    def __init__(self, delay=0.35):
        self.delay = delay
        self.last = {}
        self.robots = {}
        self.pacing_lock = threading.Lock()

    def pace(self, host):
        # Reserve a host-specific request slot across all collection workers.
        with self.pacing_lock:
            current = time.monotonic()
            slot = max(current, self.last.get(host, 0) + self.delay)
            self.last[host] = slot
        time.sleep(max(0, slot - current))

    def get(self, url, data=None, headers=None, robots=False):
        url = safe_url(url)
        public_address(url)
        host = urllib.parse.urlsplit(url).netloc
        if robots and not self.allowed(url):
            raise RuntimeError("Site robots.txt does not permit this page")
        for attempt in range(3):
            self.pace(host)
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
            try:
                with urllib.request.build_opener(Redirects()).open(req, timeout=12) as response:
                    body = response.read(4_000_001)
                    if len(body) > 4_000_000:
                        raise RuntimeError("Page exceeds the 4 MB response limit")
                    return body.decode("utf-8", "replace")
            except urllib.error.HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise RuntimeError(f"HTTP {error.code} from {host}") from error
                wait = error.headers.get("Retry-After", "")
                if wait.isdigit() and int(wait) > 30:
                    raise RuntimeError(f"{host} requested a long cooldown; retry this scan later") from error
                time.sleep(int(wait) if wait.isdigit() else 2 ** attempt)
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if attempt == 2:
                    raise RuntimeError(f"Could not fetch {host}: {error}") from error
                time.sleep(2 ** attempt)

    def allowed(self, url):
        p = urllib.parse.urlsplit(url)
        root = f"{p.scheme}://{p.netloc}"
        if root not in self.robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                parser.parse(self.get(root + "/robots.txt").splitlines())
            except RuntimeError as error:
                # A missing robots.txt permits crawling; other failures remain visible.
                if "HTTP 404 " not in str(error):
                    raise
                parser.parse([])
            self.robots[root] = parser
        return self.robots[root].can_fetch(UA, url)

    def json(self, url, **kwargs):
        return json.loads(self.get(url, **kwargs))


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.data_page = None
        self.links = []
        self.jsonld = []
        self.anchor = None
        self.script = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-page" in attrs:
            self.data_page = json.loads(attrs["data-page"])
        if tag == "a":
            self.anchor = [attrs.get("href", ""), attrs.get("aria-label", "")]
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self.script = ""

    def handle_data(self, data):
        if self.anchor is not None:
            self.anchor[1] += " " + data
        if self.script is not None:
            self.script += data

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor is not None:
            self.links.append((self.anchor[0], " ".join(self.anchor[1].split())))
            self.anchor = None
        if tag == "script" and self.script is not None:
            try:
                self.jsonld.append(json.loads(self.script))
            except ValueError:
                pass
            self.script = None


def batch_year(batch):
    match = re.fullmatch(r"(?:Winter|Spring|Summer|Fall) (\d{4})", batch)
    if match:
        return int(match[1])
    match = re.fullmatch(r"[WSFX](\d{2})", batch)
    return 2000 + int(match[1]) if match else None


class YCSource:
    def __init__(self, fetch):
        self.fetch = fetch
        self.config = None

    def query(self, params):
        if self.config is None:
            page = self.fetch.get(YC + "/companies")
            match = re.search(r"window\.AlgoliaOpts\s*=\s*(\{[^;\n]+?\})", page)
            if not match:
                raise RuntimeError("YC search configuration changed; the YC adapter needs updating")
            self.config = json.loads(match[1])
        app, key = self.config["app"], self.config["key"]
        if not re.fullmatch(r"[A-Za-z0-9]+", app):
            raise RuntimeError("Invalid YC search application identifier")
        return self.fetch.json(
            f"https://{app}-dsn.algolia.net/1/indexes/YCCompany_production/query",
            data=json.dumps({"params": urllib.parse.urlencode(params)}).encode(),
            headers={"X-Algolia-Application-Id": app, "X-Algolia-API-Key": key,
                     "Content-Type": "application/json"},
        )

    def batches(self, year):
        result = self.query({"hitsPerPage": 0, "facets": '["batch"]', "maxValuesPerFacet": 1000})
        if result.get("exhaustiveFacetsCount") is False:
            raise RuntimeError("YC returned incomplete batch counts; retry later")
        values = result["facets"]["batch"]
        return [{"name": name, "count": count} for name, count in sorted(
            values.items(), key=lambda item: SEASONS.get(item[0].split()[0], 0)) if batch_year(name) == year]

    def companies(self, batch):
        found, page = {}, 0
        while True:
            result = self.query({"hitsPerPage": 500, "page": page,
                                 "facetFilters": json.dumps([["batch:" + batch]])})
            for hit in result["hits"]:
                slug = hit.get("slug", "")
                if re.fullmatch(r"[a-zA-Z0-9_-]{1,150}", slug):
                    found[slug] = {"slug": slug, "name": hit["name"], "batch": batch,
                                   "year": batch_year(batch), "website": safe_url(hit.get("website", "")),
                                   "description": hit.get("one_liner", ""), "team_size": hit.get("team_size"),
                                   "industry": hit.get("subindustry", ""), "yc_url": YC + "/companies/" + slug}
            page += 1
            if page >= result["nbPages"]:
                if len(found) != result["nbHits"]:
                    raise RuntimeError(f"Incomplete company enumeration for {batch}: {len(found)}/{result['nbHits']}")
                return list(found.values())

    def details(self, company):
        page = Page(self.fetch.get(company["yc_url"]))
        if not page.data_page or "company" not in page.data_page.get("props", {}):
            raise RuntimeError("YC company page schema changed or page is unavailable")
        data = page.data_page["props"]["company"]
        people = []
        for founder in data.get("founders", []):
            name = founder.get("full_name", "").strip()
            if not name:
                continue
            profiles = [u for field in ("linkedin_url", "github_url", "twitter_url", "website")
                        if (u := canonical_profile(founder.get(field, "")))]
            people.append({"name": name, "role": founder.get("title") or "Founder", "relation": "yc_founder",
                           "source_url": company["yc_url"], "profiles": profiles,
                           "source_key": "yc:" + str(founder.get("user_id") or name.casefold()),
                           "evidence": "Listed in YC's founders section; not a current-employment verification"})
        return people, safe_url(data.get("website", "")), canonical_profile(data.get("github_url", ""))


def canonical_profile(url):
    url = safe_url(url)
    if not url:
        return ""
    p = urllib.parse.urlsplit(url)
    host = p.hostname.lower().removeprefix("www.")
    path = p.path.rstrip("/")
    if host.endswith(".linkedin.com") or host == "linkedin.com":
        if not re.fullmatch(r"/in/[^/]+", path):
            return ""
        host, path = "linkedin.com", path.lower()
    elif host == "github.com":
        if not re.fullmatch(r"/[A-Za-z0-9-]+", path):
            return ""
        path = path.lower()
    elif host in ("twitter.com", "x.com"):
        if not re.fullmatch(r"/[A-Za-z0-9_]+", path) or path.lower() in ("/share", "/intent", "/home"):
            return ""
        host, path = "x.com", path.lower()
    return f"https://{host}{path}" if host in ("linkedin.com", "github.com", "x.com") else url


def platform(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    return {"linkedin.com": "linkedin", "github.com": "github", "x.com": "x"}.get(host, "website")


def walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def team_people(page, url):
    """Only explicit structured Person records; never assign a footer link to a nearby name."""
    found = []
    for root in page.jsonld:
        for node in walk_json(root):
            types = node.get("@type", [])
            types = [types] if isinstance(types, str) else types or []
            name = node.get("name")
            if "Person" not in types or not isinstance(name, str) or not name.strip():
                continue
            links = node.get("sameAs", [])
            links = [links] if isinstance(links, str) else links if isinstance(links, list) else []
            links = [*links, node.get("url", "")]
            profiles = sorted({p for link in links if (p := canonical_profile(safe_url(link, url)))})
            if not profiles:
                continue
            role = node.get("jobTitle", "")
            found.append({"name": name.strip(), "role": role if isinstance(role, str) else "",
                          "relation": "company_listed", "source_url": url, "profiles": profiles,
                          "source_key": "site:" + name.strip().casefold(),
                          "evidence": "Structured Person record on a company team/about page; review affiliation"})
    # Explicitly named personal-profile links also work on sites without JSON-LD.
    # Generic social buttons and company links are deliberately ignored.
    generic = {"linkedin", "github", "twitter", "follow", "connect", "profile", "visit", "learn", "read", "team", "about", "contact", "view", "share"}
    for href, label in page.links:
        profile = canonical_profile(safe_url(href, url))
        words = label.split()
        if (profile and platform(profile) in ("linkedin", "github", "x") and 2 <= len(words) <= 5
                and len(label) <= 70 and all(re.fullmatch(r"[^\W\d_][^\W\d_.'’\-]*", word, re.UNICODE) for word in words)
                and not generic.intersection(w.casefold() for w in words)):
            found.append({"name": label, "role": "", "relation": "company_listed", "source_url": url,
                          "profiles": [profile], "source_key": "site:" + label.casefold(),
                          "evidence": "Named personal-profile link on a company team/about page; review affiliation"})
    return found


def website_people(fetch, website):
    if not website:
        return [], [], []
    page = Page(fetch.get(website, robots=True))
    host = urllib.parse.urlsplit(website).hostname
    paths = []
    orgs = set()
    for href, label in page.links:
        link = safe_url(href, website)
        p = urllib.parse.urlsplit(link)
        if p.hostname == "github.com" and re.fullmatch(r"/[A-Za-z0-9-]+/?", p.path):
            orgs.add(p.path.strip("/"))
        if (p.hostname == host and re.search(r"(?:^|/)(?:team|about|about-us|our-team|people)(?:/|$)", p.path, re.I)
                and link not in paths and link != website):
            paths.append(link)
    people, warnings = [], []
    # Homepage Person records may be blog authors/testimonials. Only parse team/about pages.
    for link in paths[:3]:
        try:
            people.extend(team_people(Page(fetch.get(link, robots=True)), link))
        except Exception as error:
            warnings.append(f"{link}: {error}")
    return people, sorted(orgs)[:2], warnings


def github_members(fetch, org, limit=30):
    if not re.fullmatch(r"[A-Za-z0-9-]+", org):
        return [], []
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    root = "https://api.github.com"
    organization = fetch.json(f"{root}/users/{org}", headers=headers)
    if organization.get("type") != "Organization":
        return [], []
    members = fetch.json(f"{root}/orgs/{org}/public_members?per_page={limit + 1}", headers=headers)
    warnings = [f"GitHub {org}: public member scan capped at {limit}"] if len(members) > limit else []
    people = []
    for member in members[:limit]:
        if member.get("type") != "User":
            continue
        login = member["login"]
        url = canonical_profile(member["html_url"])
        people.append({"name": login, "role": "Public GitHub organization member", "relation": "github_member",
                       "source_url": f"https://github.com/orgs/{org}/people", "source_key": "github:" + login.lower(),
                       "profiles": [url], "evidence": "Public organization membership; employment and real name unverified"})
    return people, warnings
