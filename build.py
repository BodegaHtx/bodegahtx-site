#!/usr/bin/env python3
"""
Bodega site build.

index.html is the single source of truth. Its content lives in the
<script type="application/json" id="bodega-data"> block, which this script
also reads - so the homepage, the per-location pages, the structured data
and the sitemap can never disagree with each other.

Produces dist/:
  index.html                 the homepage, as a standards-mode document
  <slug>/index.html          one page per open store, targeting its own city
  sitemap.xml, robots.txt
  assets/

Why per-location pages: a single page listing six stores competes with
itself. Google ranks a page about Livingston for "gas station Livingston"
far more readily than a page about six Texas towns.

    python3 build.py            build dist/
    python3 build.py --serve    build, then serve dist/ on :8000
"""
import html, json, re, shutil, sys
from pathlib import Path

ROOT   = Path(__file__).resolve().parent
SRC    = ROOT / "index.html"
DIST   = ROOT / "dist"
PORT   = 8000
ORIGIN = "https://bodegahtx.com"
BRAND  = "Bodega"

def clean(t: str) -> str:
    """HTML entities -> plain text, for <meta> and JSON-LD."""
    return html.unescape(re.sub(r"<[^>]+>", "", t)).strip()

def read_source():
    s = SRC.read_text(encoding="utf-8")
    m = re.search(r'<script type="application/json" id="bodega-data">(.*?)</script>', s, re.S)
    if not m:
        raise SystemExit("build: could not find the bodega-data JSON block in index.html")
    return s, json.loads(m.group(1))

def head(title, desc, canonical, extra=""):
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<title>{html.escape(title)}</title>\n'
        f'<meta name="description" content="{html.escape(desc)}">\n'
        f'<link rel="canonical" href="{canonical}">\n'
        '<meta name="theme-color" content="#F5EEE0">\n'
        # Bo favicon - what Google shows in the circle beside search results.
        # Google wants a square that's a multiple of 48px.
        '<link rel="icon" href="/favicon.ico" sizes="any">\n'
        '<link rel="icon" type="image/png" sizes="48x48" href="/assets/favicon-48.png">\n'
        '<link rel="icon" type="image/png" sizes="192x192" href="/assets/favicon-192.png">\n'
        '<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">\n'
        # Google Search Console ownership. Removing it un-verifies the property.
        '<meta name="google-site-verification" content="0EZfFCBNcTX2xMkxhFquPEHuDHkV7Iwgs5e_2zuZuho">\n'
        f'<meta property="og:type" content="website">\n'
        f'<meta property="og:site_name" content="{BRAND}">\n'
        f'<meta property="og:title" content="{html.escape(title)}">\n'
        f'<meta property="og:description" content="{html.escape(desc)}">\n'
        f'<meta property="og:url" content="{canonical}">\n'
        f'<meta property="og:image" content="{ORIGIN}/assets/bo.png">\n'
        '<meta name="twitter:card" content="summary_large_image">\n'
        + extra + '\n</head>\n<body>\n'
    )

def hours_text(store):
    if store.get("open24"):
        return "Open 24 hours"
    if store.get("hours"):
        return "<br>".join(f"{d} {t}" for d, t in store["hours"])
    return "Open daily"

def hours_spec(store):
    if store.get("hoursSpec"):
        return [{
            "@type": "OpeningHoursSpecification",
            "dayOfWeek": h["days"], "opens": h["opens"], "closes": h["closes"],
        } for h in store["hoursSpec"]]
    if not store.get("open24"):
        return []
    return [{
        "@type": "OpeningHoursSpecification",
        "dayOfWeek": ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"],
        "opens": "00:00", "closes": "23:59",
    }]

def store_schema(store, fuel_catalog, breakfast_hours):
    """One GasStation record. This is what feeds Google's local understanding."""
    feats = list(store.get("amenities", [])) + [clean(t) for t in store.get("tags", [])]
    feats += [t["name"] for t in store.get("tenants", [])]
    feats += [fuel_catalog[k]["name"] for k in store.get("fuels", []) if k in fuel_catalog]
    if store.get("breakfast"):
        feats.append("Breakfast " + clean(store.get("breakfastHours", breakfast_hours)))
    if store.get("lunchHours"):
        feats.append("Lunch " + clean(store["lunchHours"]))
    node = {
        "@context": "https://schema.org",
        "@type": "GasStation",
        "@id": f"{ORIGIN}/{store['slug']}/#store",
        "name": f"{BRAND} #{store['num']}",
        "url": f"{ORIGIN}/{store['slug']}/",
        "image": f"{ORIGIN}/assets/bo.png",
        "priceRange": "$",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": store["street"],
            "addressLocality": store["locality"],
            "addressRegion": store["region"],
            "postalCode": store["postal"],
            "addressCountry": "US",
        },
        "parentOrganization": {"@type": "Organization", "name": BRAND, "url": ORIGIN + "/"},
        "amenityFeature": [
            {"@type": "LocationFeatureSpecification", "name": f, "value": True}
            for f in dict.fromkeys(feats)
        ],
    }
    if store.get("phone"):
        node["telephone"] = store["phone"]
    if store.get("fuelBrand"):
        # Branded fuel (e.g. Valero) sold at a Bodega store - say so plainly.
        node["brand"] = {"@type": "Brand", "name": store["fuelBrand"]}
        node["alternateName"] = f"{BRAND} #{store['num']} {store['fuelBrand']}"
    spec = hours_spec(store)
    if spec:
        node["openingHoursSpecification"] = spec
    return node

def location_page(store, data, css):
    fc = data["fuelCatalog"]
    city = f"{store['locality']}, {store['region']}"
    is_truckstop = any(k in store.get("fuels", []) for k in ("tdiesel", "defbulk"))
    kind = "Gas Station &amp; Truck Stop" if is_truckstop else "Gas Station"
    title = f"{BRAND} #{store['num']} — {clean(kind)} in {city}"
    desc = clean(store["blurb"])
    canonical = f"{ORIGIN}/{store['slug']}/"
    schema = f'<script type="application/ld+json">{json.dumps(store_schema(store, fc, data["breakfastHours"]))}</script>'

    def fuel_li(k):
        g = fc[k]
        cls = f' class="k-{g["kind"]}"' if g.get("kind") else ""
        oct_ = (" " + g["octane"]) if g["octane"] != "\u2014" else ""
        return "<li" + cls + ">" + g["name"] + oct_ + "</li>"
    fuels = "".join(fuel_li(k) for k in store.get("fuels", []) if k in fc)
    tags = "".join(f"<li>{t}</li>" for t in store.get("tags", []))
    amen = "".join(f'<li class="amen">{a}</li>' for a in store.get("amenities", []))
    addr_q = re.sub(r"\s+", "+", f"{store['street']}, {city} {store['postal']}")
    maps = "https://www.google.com/maps/search/?api=1&query=" + addr_q
    # output=embed needs no API key, so there is no key to leak or expire
    embed = "https://maps.google.com/maps?q=" + addr_q + "&t=&z=15&ie=UTF8&iwloc=&output=embed"
    tel = re.sub(r"[^0-9]", "", store.get("phone", ""))

    # Photography. A store with no photos simply renders without them - we
    # never show one store's forecourt on another store's page.
    photos = store.get("photos", [])
    lead = next((p for p in photos if p.get("lead")), photos[0] if photos else None)
    if lead:
        lead_html = (
            f'<section class="shot shot-lead" aria-label="{BRAND} #{store["num"]} in {store["locality"]}">'
            f'<figure><img src="/assets/photos/{lead["f"]}.jpg" '
            f'srcset="/assets/photos/{lead["f"]}-800.jpg 800w, /assets/photos/{lead["f"]}.jpg 1400w" '
            f'sizes="100vw" width="1400" height="787" '
            f'alt="{html.escape(lead["alt"], quote=True)}" '
            f'fetchpriority="high" decoding="async">'
            f'<figcaption><b>{BRAND} #{store["num"]} &middot; {store["locality"]}</b>'
            f'<span>{store["street"]}</span></figcaption></figure></section>')
    else:
        lead_html = ""

    rest = [p for p in photos if p is not lead]
    if rest:
        items = "".join(
            f'<li><img src="/assets/photos/{p["f"]}-800.jpg" '
            f'srcset="/assets/photos/{p["f"]}-800.jpg 800w, /assets/photos/{p["f"]}.jpg 1400w" '
            f'sizes="(max-width:700px) 100vw, 33vw" width="800" height="450" '
            f'alt="{html.escape(p["alt"], quote=True)}" loading="lazy" decoding="async"></li>'
            for p in rest)
        gallery_html = (
            '<section class="sec" style="padding-top:0"><div class="wrap">'
            '<h2 class="display" style="font-size:var(--step-2);margin-bottom:1rem">'
            'Inside and out</h2>'
            f'<ul class="gallery">{items}</ul></div></section>')
    else:
        gallery_html = ""

    tenants = store.get("tenants", [])
    if tenants:
        size = f' Inside the {store["size"]} store:' if store.get("size") else ""
        rows = "".join(
            f'<div class="tenant"><h3>{t["name"]}</h3><p>{t["note"]}</p></div>' for t in tenants)
        tenants_html = (
            '<h2 class="display" style="font-size:var(--step-2);margin:2.2rem 0 .6rem">'
            "What&rsquo;s inside</h2>"
            f'<p style="color:var(--muted);margin:0 0 1rem;max-width:58ch">'
            f'More than a forecourt.{size}</p>'
            f'<div class="tenants">{rows}</div>')
    else:
        tenants_html = ""

    body = f'''<div class="topbar"><header class="site-head"><div class="wrap head-in">
<a href="/" aria-label="Bodega home"><img src="/assets/lockup.png" alt="Bodega" width="172" height="60"></a>
<nav class="nav"><a href="/#fuel">Fuel</a><a href="/#breakfast">Breakfast</a><a href="/#locations">All locations</a></nav>
</div></header><div class="stripe" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></div></div>

<main><section class="hero"><div class="wrap hero-in" style="padding-top:clamp(5rem,9vw,7rem)">
<p class="label">{BRAND} #{store['num']} &middot; {city}</p>
<h1 class="shout" style="font-size:var(--step-3)">{kind} in {store['locality']}</h1>
<p class="lede">{store['blurb']}</p>
<address style="font-style:normal;font-size:var(--step-1);font-weight:700;margin-bottom:1.4rem">
{store['street']}<br>{city} {store['postal']}
<br><span style="font-weight:400">{hours_text(store)}</span>
{f'<br><span style="font-weight:400">Breakfast {store.get("breakfastHours", data["breakfastHours"])}</span>' if store.get("breakfast") else ""}
{f'<br><span style="font-weight:400">Lunch {store["lunchHours"]}</span>' if store.get("lunchHours") else ""}
{f'<br><a href="tel:{tel}" style="font-weight:700">{store["phone"]}</a>' if store.get("phone") else ""}
</address>
<div class="cta-row">
<a class="btn btn-primary" href="{maps}" target="_blank" rel="noopener">Get directions</a>
<a class="btn btn-ghost" href="/#locations">All {BRAND} locations</a>
</div></div></section>

{lead_html}

<section class="sec" style="padding-bottom:0"><div class="wrap">
<h2 class="display" style="font-size:var(--step-2);margin-bottom:1rem">Find us</h2>
<div class="map-frame">
<iframe src="{embed}" title="Map showing {BRAND} #{store['num']} at {store['street']}, {city}"
 loading="lazy" referrerpolicy="no-referrer-when-downgrade"
 allowfullscreen></iframe>
</div>
<p style="margin-top:.9rem"><a class="btn btn-ghost" href="{maps}" target="_blank" rel="noopener">Open in Google Maps</a></p>
</div></section>

{gallery_html}

<section class="sec"><div class="wrap">
<h2 class="display" style="font-size:var(--step-2);margin-bottom:1rem">Fuel at this store</h2>
<ul class="chips">{fuels}</ul>
{tenants_html}
{f'<h2 class="display" style="font-size:var(--step-2);margin:2.2rem 0 1rem">For drivers</h2><ul class="chips">{amen}</ul>' if amen else ""}
<h2 class="display" style="font-size:var(--step-2);margin:2.2rem 0 1rem">At this store</h2>
<ul class="chips">{tags}</ul>
</div></section></main>

<div class="stripe" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></div>
<footer class="site-foot"><div class="wrap">
<p><a href="/">&larr; All {BRAND} locations</a></p>
<p>{BRAND} Foods LLC &middot; Texas &middot; <a href="mailto:Admin@bodegahtx.com">Admin@bodegahtx.com</a> &middot; <a href="/privacy/">Privacy</a></p>
</div></footer>'''

    return head(title, desc, canonical, f"<style>{css}</style>\n{schema}") + body + "\n</body>\n</html>\n"


PRIVACY_UPDATED = "November 18, 2026"

def privacy_page(css):
    """Plain-language privacy policy. Keep it true to what the site actually
    does: no forms, no cookies, no analytics. Update it before adding any."""
    title = f"Privacy Policy — {BRAND}"
    desc = "How bodegahtx.com handles your information: no accounts, no tracking cookies, no data sold."
    canonical = f"{ORIGIN}/privacy/"
    email = '<a href="mailto:Admin@bodegahtx.com">Admin@bodegahtx.com</a>'
    body = f'''<div class="topbar"><header class="site-head"><div class="wrap head-in">
<a href="/" aria-label="Bodega home"><img src="/assets/lockup.png" alt="Bodega" width="172" height="60"></a>
<nav class="nav"><a href="/#fuel">Fuel</a><a href="/#breakfast">Breakfast</a><a href="/#locations">All locations</a></nav>
</div></header><div class="stripe" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></div></div>

<main><section class="sec"><div class="wrap legal" style="max-width:72ch;padding-top:clamp(5rem,9vw,7rem)">
<p class="label">Last updated {PRIVACY_UPDATED}</p>
<h1 class="display" style="font-size:var(--step-3);margin:.4rem 0 1.2rem">Privacy Policy</h1>
<p class="lede">The short version: this website does not ask for your name, does not set tracking cookies,
and we do not sell anyone&rsquo;s information. Here are the details.</p>

<h2>Who we are</h2>
<p>This policy covers bodegahtx.com, run by {BRAND} Foods LLC, which operates Bodega gas stations and
convenience stores in Texas. Questions go to {email}.</p>

<h2>What we collect on this website</h2>
<p>Nothing that identifies you directly. The site has no sign-up forms, no accounts and no checkout.
We do not use analytics or advertising trackers, and we do not set cookies.</p>
<p>Like every website, our hosting provider (Cloudflare) automatically records basic technical
information when a page loads: your IP address, browser type, the page requested and the time. This is
used to deliver the site, keep it secure and stop abuse. It is kept by Cloudflare under
<a href="https://www.cloudflare.com/privacypolicy/" target="_blank" rel="noopener">its own privacy policy</a>.</p>

<h2>Services that load on our pages</h2>
<p>To show fonts, maps and today&rsquo;s fuel prices, some pages load content from other companies. When
they do, your browser contacts those companies directly, and they can see your IP address and browser
details:</p>
<ul>
<li><strong>Google Fonts</strong> for the site&rsquo;s typefaces, and <strong>Google Maps</strong> on store pages
(<a href="https://policies.google.com/privacy" target="_blank" rel="noopener">Google privacy policy</a>).</li>
<li><strong>OpenStreetMap</strong> for the store map on the homepage
(<a href="https://osmfoundation.org/wiki/Privacy_Policy" target="_blank" rel="noopener">OSM privacy policy</a>),
with the map code served by <strong>unpkg</strong>.</li>
<li><strong>GitHub</strong>, which hosts the file our price board reads
(<a href="https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement" target="_blank" rel="noopener">GitHub privacy statement</a>).</li>
</ul>
<p>Links to Google Maps directions take you to Google&rsquo;s own site, where Google&rsquo;s policy applies.</p>

<h2>If you contact us</h2>
<p>If you email us, about a job or anything else, we use what you send only to reply and to handle your
request. We do not add you to a marketing list unless you ask us to, and we do not sell or rent your
information to anyone.</p>

<h2>In our stores</h2>
<p>This policy is about the website. Our stores use security cameras for safety, and card payments
at the pump and register are handled by our payment processors, not stored by this website.</p>

<h2>Children</h2>
<p>This site is not aimed at children under 13, and we do not knowingly collect information from them.</p>

<h2>Your choices</h2>
<p>You can ask us what information we hold about you from emails you have sent, and ask us to correct or
delete it, by writing to {email}. You can also block fonts and maps in your browser; the store addresses,
hours and phone numbers still work without them.</p>

<h2>Changes</h2>
<p>If we add anything that collects information, such as a text club, rewards or an application form, we
will update this page first and change the date at the top.</p>
</div></section></main>

<div class="stripe" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></div>
<footer class="site-foot"><div class="wrap">
<p><a href="/">&larr; All {BRAND} locations</a></p>
<p>{BRAND} Foods LLC &middot; Texas &middot; {email}</p>
</div></footer>'''
    legal_css = (".legal h2{font-size:var(--step-1);margin:2rem 0 .5rem}"
                 ".legal p,.legal li{line-height:1.6}.legal ul{padding-left:1.2rem}"
                 ".legal a{text-decoration:underline}")
    return head(title, desc, canonical, f"<style>{css}{legal_css}</style>") + body + "\n</body>\n</html>\n"


def build() -> Path:
    src, data = read_source()
    # The FIRST </style> closes the main stylesheet and ends the head region.
    # (rfind would catch the <noscript><style> inside the body and drag the
    #  whole page into <head>, which browsers silently paper over.)
    split = src.lower().find("</style>")
    if split == -1:
        raise SystemExit("build: no </style> found in index.html")
    split += len("</style>")
    frag_head, frag_body = src[:split], src[split:]
    css = re.search(r"<style>(.*?)</style>", frag_head, re.S).group(1)

    stores = data["openStores"]
    cities = sorted({s["locality"] for s in stores})
    home_title = f"{BRAND} — Gas Stations &amp; Truck Stops in Texas"
    home_desc = ("Bodega fuel and food across Texas: ethanol-free gas, truck diesel, bulk DEF, "
                 "made-to-order breakfast and the cleanest restrooms on the highway. "
                 + str(len(stores)) + " stores in " + ", ".join(cities) + ".")

    graph = [{
        "@context": "https://schema.org", "@type": "Organization", "@id": ORIGIN + "/#org",
        "name": BRAND, "url": ORIGIN + "/", "logo": ORIGIN + "/assets/lockup.png",
        "email": "Admin@bodegahtx.com",
        "department": [{"@id": f"{ORIGIN}/{s['slug']}/#store"} for s in stores],
    }] + [store_schema(s, data["fuelCatalog"], data["breakfastHours"]) for s in stores]
    schema = "".join(f'<script type="application/ld+json">{json.dumps(n)}</script>' for n in graph)

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()

    # A plain, crawlable list of every location page. The cards above are
    # rendered by JS; a crawler should never have to run a script to discover
    # six pages, and real visitors get a useful footer index out of it.
    loc_links = "".join(
        f'<li><a href="/{st["slug"]}/">{BRAND} #{st["num"]} &mdash; '
        f'{st["locality"]}, {st["region"]}</a></li>' for st in stores)
    footer_nav = (
        '<nav class="foot-locs" aria-label="All locations"><h2>All locations</h2>'
        f'<ul>{loc_links}</ul></nav>')
    frag_body_out = frag_body.strip().replace(
        '<div class="wrap foot-in">', footer_nav + '\n  <div class="wrap foot-in">', 1)

    # homepage: the fragment's own <title> is replaced by the targeted one
    fh = re.sub(r"<title>.*?</title>", "", frag_head, flags=re.S | re.I)
    home = head(clean(home_title), home_desc, ORIGIN + "/", fh.strip() + "\n" + schema) + frag_body_out + "\n</body>\n</html>\n"
    (DIST / "index.html").write_text(home, encoding="utf-8")

    for s_ in stores:
        d = DIST / s_["slug"]
        d.mkdir()
        (d / "index.html").write_text(location_page(s_, data, css), encoding="utf-8")

    (DIST / "privacy").mkdir()
    (DIST / "privacy" / "index.html").write_text(privacy_page(css), encoding="utf-8")

    urls = [ORIGIN + "/"] + [f"{ORIGIN}/{s['slug']}/" for s in stores] + [ORIGIN + "/privacy/"]
    (DIST / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"<url><loc>{u}</loc></url>\n" for u in urls) + "</urlset>\n", encoding="utf-8")
    # Google Search Console second verification method. Google fetches this
    # exact filename and expects exactly this one line. Don't rename either.
    (DIST / "google5716dc86853a3145.html").write_text(
        "google-site-verification: google5716dc86853a3145.html\n", encoding="utf-8")
    (DIST / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {ORIGIN}/sitemap.xml\n", encoding="utf-8")

    shutil.copytree(ROOT / "assets", DIST / "assets")
    # Browsers and Google also request /favicon.ico at the root directly.
    shutil.copy(ROOT / "assets" / "favicon.ico", DIST / "favicon.ico")
    # Let other sites (e.g. Google Business Profile tools) read store photos.
    (DIST / "_headers").write_text(
        "/assets/photos/*\n  Access-Control-Allow-Origin: *\n", encoding="utf-8")
    print(f"built {DIST}/  -  homepage + {len(stores)} location pages, sitemap, robots")
    for s_ in stores:
        print(f"   /{s_['slug']}/  {BRAND} #{s_['num']} - {s_['locality']}, {s_['region']}")
    return DIST


def serve(directory: Path) -> None:
    import functools, http.server, socketserver
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        print(f"serving {directory} at http://localhost:{PORT}")
        httpd.serve_forever()


if __name__ == "__main__":
    out = build()
    if "--serve" in sys.argv:
        serve(out)
