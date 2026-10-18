"""Site audit check catalog and rules.

Every check has an id, a severity (error / warning / notice), a category, a plain-language explanation
and how to fix it. `evaluate()` turns a CrawlData (+ PageSpeed results) into issue occurrences:
{"check": id, "url": page URL (site-wide checks use the home URL), "detail": {...}}.
`health_score()` weighs severities by the share of pages affected.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.services.siteaudit.crawl import _HREFLANG, _LANG, CrawlData

SEVERITIES = ("error", "warning", "notice")
CATEGORIES: dict[str, str] = {
    "crawl": "Crawlability",
    "links": "Links",
    "redirects": "Redirects",
    "onpage": "Titles, descriptions and headings",
    "content": "Content",
    "images": "Images",
    "indexing": "Indexing and canonicals",
    "sitemap": "Sitemaps and site structure",
    "international": "Languages (hreflang)",
    "markup": "Structured data and social sharing",
    "security": "Security (HTTPS)",
    "mobile": "Mobile",
    "performance": "Speed and Core Web Vitals",
    "ai": "AI search readiness",
    "urls": "URL structure",
}


@dataclass(frozen=True)
class Check:
    id: str
    severity: str
    category: str
    title: str
    why: str
    fix: str
    site: bool = False  # site-wide check (one occurrence for the whole site)

    def out(self) -> dict:
        return {"id": self.id, "severity": self.severity, "category": self.category,
                "category_label": CATEGORIES[self.category], "title": self.title, "why": self.why, "fix": self.fix,
                "site_wide": self.site}


_C: list[Check] = []


def C(id: str, severity: str, category: str, title: str, why: str, fix: str, site: bool = False) -> None:
    _C.append(Check(id, severity, category, title, why, fix, site))


E, W, N = "error", "warning", "notice"

# ---------------------------------------------------------------- crawlability
C("http_4xx", E, "crawl", "Pages return a 4xx error (not found / forbidden)",
  "Visitors and search engines hit a dead end. Links to these pages waste ranking value.",
  "Restore the page, redirect the URL (301) to the closest live page, or remove links to it.")
C("http_5xx", E, "crawl", "Pages return a server error (5xx)",
  "The server failed to answer. Search engines drop pages that keep failing.",
  "Ask your developer or host to check the server logs for these URLs and fix the error.")
C("fetch_failed", E, "crawl", "Pages couldn't be loaded",
  "The page timed out or the connection failed, so nobody can read it.",
  "Open the URL yourself. If it is slow or fails, ask your host to look at server capacity and timeouts.")
C("blocked_by_robots", N, "crawl", "Linked pages are blocked by robots.txt",
  "Search engines can't read these pages, so they won't rank (they may still appear without a description).",
  "If the pages should be found, remove the matching Disallow rule from robots.txt.")
C("robots_missing", W, "crawl", "No robots.txt file",
  "robots.txt tells crawlers what to read and where your sitemap is. Without it they guess.",
  "Create /robots.txt with at least 'User-agent: *', 'Allow: /' and a 'Sitemap:' line.", True)
C("robots_unreachable", E, "crawl", "robots.txt can't be loaded (server error)",
  "When robots.txt fails, Google treats the whole site as blocked until it loads again.",
  "Make sure /robots.txt returns 200 (or 404 if you don't need one). Ask your host to fix the error.", True)
C("robots_blocks_all", E, "crawl", "robots.txt blocks search engines from the whole site",
  "Google and Bing are told not to read any page, so the site can't rank.",
  "Remove 'Disallow: /' for Googlebot / all user agents unless the site is meant to be private.", True)
C("robots_invalid", W, "crawl", "robots.txt has lines crawlers don't understand",
  "Unknown lines are ignored, so the rules may not do what you expect.",
  "Fix the listed lines. Valid directives are User-agent, Allow, Disallow, Sitemap and Crawl-delay.", True)
C("robots_too_large", W, "crawl", "robots.txt is larger than 500 KB",
  "Google reads only the first 500 KB; rules after that are ignored.",
  "Shorten the file: group rules with wildcards and remove duplicates.", True)
C("robots_crawl_delay", N, "crawl", "robots.txt asks crawlers to wait more than 10 seconds",
  "A long Crawl-delay slows down how fast Bing and others find new content (Google ignores it).",
  "Lower or remove the Crawl-delay line unless your server really needs it.", True)
C("crawl_truncated", N, "crawl", "The audit stopped at the page limit",
  "Some pages weren't checked because the crawl reached the page limit in the audit settings.",
  "Raise the page limit in Site audit settings, or exclude sections you don't need to check.", True)

# ---------------------------------------------------------------- links
C("broken_internal_link", E, "links", "Broken internal links",
  "Links on your pages lead to missing pages or files. Visitors get errors and ranking value is lost.",
  "Update each link to a working URL, or redirect the missing URL to a live page.")
C("broken_external_link", W, "links", "Broken links to other websites",
  "Linking to dead pages looks neglected to visitors and search engines.",
  "Replace the link with a working page or remove it.")
C("internal_link_to_redirect", W, "links", "Internal links point to redirects",
  "Every redirect adds a delay and loses a little ranking value.",
  "Change the links to point straight at the final URL.")
C("nofollow_internal", W, "links", "Internal links marked nofollow",
  "nofollow on your own links stops ranking value flowing to your pages.",
  "Remove rel=\"nofollow\" from links to your own pages.")
C("empty_anchor", W, "links", "Links without anchor text",
  "Search engines and screen readers can't tell where the link goes.",
  "Add descriptive link text, or alt text on linked images, or an aria-label.")
C("generic_anchor", N, "links", "Links with generic text like “click here”",
  "Generic link text tells search engines nothing about the target page.",
  "Use words that describe the destination, e.g. “payroll software pricing”.")
C("http_internal_link", W, "links", "HTTPS pages link to HTTP pages on your site",
  "These links go through an extra redirect or load an insecure page.",
  "Change the links to https:// URLs.")
C("js_links", N, "links", "Links that use javascript: instead of a web address",
  "Crawlers can't follow javascript: links, so pages behind them may never be found.",
  "Use normal <a href=\"/page\"> links and attach scripts separately.")
C("broken_image", E, "links", "Broken images",
  "Images that don't load look broken to visitors and are dropped from image search.",
  "Upload the missing image again or update the image address.")
C("too_many_links", W, "links", "Pages with more than 300 links",
  "Very long link lists dilute each link's value and can look spammy.",
  "Keep navigation focused and move long lists to dedicated pages.")
C("no_outlinks", N, "links", "Pages with no internal links",
  "Dead-end pages stop visitors and crawlers from moving on to the rest of the site.",
  "Add links to related pages, the next step or the main sections.")
C("external_http_links", N, "links", "Links to insecure (HTTP) websites",
  "Browsers may warn visitors who follow insecure links.",
  "Switch to the https:// version of the linked site where it exists.")
C("link_to_noindex", N, "links", "Internal links point to noindex pages",
  "Ranking value passed to noindex pages is wasted.",
  "Check the links are needed, or allow the target page to be indexed.")

# ---------------------------------------------------------------- redirects
C("redirect_chain", W, "redirects", "Redirect chains (more than one hop)",
  "Each hop slows visitors down and search engines may stop following after a few.",
  "Point the first URL straight at the final destination with one 301 redirect.")
C("redirect_loop", E, "redirects", "Redirect loops",
  "The URL redirects back to itself, so the page never loads.",
  "Fix the redirect rules so the URL ends at a page that returns 200.")
C("temporary_redirect", W, "redirects", "Temporary redirects (302/307) used",
  "Temporary redirects tell search engines to keep the old URL, so the new one may not rank.",
  "Use a permanent 301 (or 308) redirect when the move is permanent.")
C("redirect_broken", E, "redirects", "Redirects to a broken page",
  "The redirect ends at an error page.",
  "Point the redirect at a live page.")
C("redirect_external", N, "redirects", "Pages redirect to another website",
  "Visitors leave your site; make sure this is intended.",
  "Keep the redirect only if the content really moved to the other site.")
C("meta_refresh", W, "redirects", "Pages use a meta refresh",
  "Meta refresh redirects are slow and confusing for visitors and search engines.",
  "Replace them with a server-side 301 redirect.")
C("http_no_https_redirect", E, "redirects", "The http:// version doesn't redirect to https://",
  "Visitors who type your address without https see an insecure site, and there are two copies of every page.",
  "Add a site-wide 301 redirect from http:// to https:// in your hosting or CDN settings.", True)
C("www_duplicate", W, "redirects", "Both the www and non-www addresses load the site",
  "Two hostnames serving the same pages split ranking signals.",
  "Pick one hostname and 301-redirect the other to it.", True)

# ---------------------------------------------------------------- titles, descriptions, headings
C("title_missing", E, "onpage", "Pages without a title",
  "The title is the clickable headline in search results. Without one, Google invents one.",
  "Add a unique <title> of 30–60 characters that says what the page offers.")
C("title_duplicate", E, "onpage", "Duplicate page titles",
  "Pages with the same title compete with each other and confuse search engines.",
  "Give every page its own title that describes its specific content.")
C("title_too_long", W, "onpage", "Titles longer than 60 characters",
  "Long titles are cut off in search results.",
  "Shorten the title and keep the important words at the start.")
C("title_too_short", W, "onpage", "Titles shorter than 30 characters",
  "Short titles miss the chance to describe the page and its keywords.",
  "Expand the title to 30–60 characters with what the page offers and your brand.")
C("title_multiple", W, "onpage", "Pages with more than one title tag",
  "Search engines may pick the wrong one.",
  "Keep exactly one <title> in the page head.")
C("title_same_as_h1", N, "onpage", "Title and H1 are identical",
  "Not wrong, but a slightly different H1 lets you cover more related wording.",
  "Consider varying the H1 or the title.")
C("desc_missing", W, "onpage", "Pages without a meta description",
  "Search engines show this text under your title. Without it they pick random text from the page.",
  "Write a 70–160 character description that sums up the page and invites the click.")
C("desc_duplicate", W, "onpage", "Duplicate meta descriptions",
  "Identical descriptions make different pages look the same in search results.",
  "Write a unique description for each page.")
C("desc_too_long", N, "onpage", "Meta descriptions longer than 160 characters",
  "Long descriptions are cut off in search results.",
  "Trim the description to about 155 characters.")
C("desc_too_short", N, "onpage", "Meta descriptions shorter than 70 characters",
  "Short descriptions waste space in search results.",
  "Expand the description with the benefit and a call to action.")
C("desc_multiple", W, "onpage", "Pages with more than one meta description",
  "Search engines may use the wrong one.",
  "Keep one meta description per page.")
C("title_desc_same", N, "onpage", "Meta description repeats the title",
  "The description is wasted if it says the same as the title.",
  "Use the description to add detail the title doesn't cover.")
C("h1_missing", W, "onpage", "Pages without an H1 heading",
  "The H1 tells visitors and search engines what the page is about.",
  "Add one H1 at the top of the main content.")
C("h1_multiple", N, "onpage", "Pages with more than one H1",
  "Several H1s make the main topic less clear.",
  "Keep one H1 and turn the others into H2 subheadings.")
C("h1_empty", W, "onpage", "Empty H1 headings",
  "An empty H1 (often an image or logo with no text) gives search engines nothing.",
  "Put real text in the H1, or alt text on an image inside it.")
C("h1_duplicate", N, "onpage", "The same H1 is used on several pages",
  "Pages with the same main heading look like duplicates.",
  "Give each page a heading that describes its own content.")
C("h1_too_long", N, "onpage", "H1 headings longer than 70 characters",
  "Very long headings are hard to scan.",
  "Shorten the H1 and move details into the intro text.")
C("heading_skip", N, "onpage", "Headings skip levels (e.g. H1 → H3)",
  "A broken heading outline is harder for screen readers and search engines to follow.",
  "Use headings in order: H1, then H2, then H3 inside an H2 section.")
C("h2_missing", N, "onpage", "Long pages without subheadings",
  "Subheadings make long pages easy to scan and help search engines understand sections.",
  "Break the text into sections with H2 headings.")

# ---------------------------------------------------------------- content
C("thin_content", W, "content", "Pages with little text (under 200 words)",
  "Thin pages rarely rank because they don't answer questions in depth.",
  "Add useful detail, examples or FAQs, or merge the page with a related one.")
C("duplicate_content", E, "content", "Pages with duplicate content",
  "Search engines pick one copy and ignore the rest, often not the one you want.",
  "Merge the pages, redirect duplicates, or add a canonical tag pointing to the main version.")
C("low_text_ratio", N, "content", "Low text-to-code ratio (under 10%)",
  "Pages that are mostly code load slower and carry little readable content.",
  "Remove unused code and inline styles, and add meaningful text.")
C("large_html", W, "content", "Very large HTML pages (over 2 MB)",
  "Big pages load slowly, especially on mobile, and may not be read in full.",
  "Remove inline images and scripts, and paginate long lists.")
C("lorem_ipsum", W, "content", "Placeholder text (lorem ipsum) on pages",
  "Placeholder text looks unfinished to visitors.",
  "Replace it with real content.")
C("lang_missing", W, "content", "Pages without a language attribute",
  "The lang attribute tells browsers, screen readers and translators the page language.",
  "Add lang=\"en\" (or your language) to the <html> tag.")
C("lang_invalid", N, "content", "Language attribute isn't a valid code",
  "Browsers ignore language codes they don't recognise.",
  "Use a standard code such as en, en-IN or hi.")
C("charset_missing", N, "content", "Pages don't declare a character set",
  "Without a charset, some characters may display as gibberish.",
  "Add <meta charset=\"utf-8\"> at the top of the head.")
C("doctype_missing", N, "content", "Pages without a doctype",
  "Browsers fall back to 'quirks mode' and may display the page incorrectly.",
  "Start the page with <!DOCTYPE html>.")
C("many_resources", N, "content", "Pages load many scripts or stylesheets",
  "Lots of separate files slow the page down.",
  "Combine or remove unused scripts and styles; load non-essential ones later.")
C("dom_too_large", N, "content", "Pages with a very large HTML structure (over 1,500 elements)",
  "Large pages use more memory and respond slower, especially on phones.",
  "Simplify the layout and load long lists on demand.")
C("deprecated_html", N, "content", "Outdated HTML tags (font, center, marquee, frames)",
  "Old tags may display inconsistently and hurt accessibility.",
  "Replace them with modern HTML and CSS.")
C("plugins_used", W, "content", "Pages use Flash or Java plugins",
  "Modern browsers don't run these plugins, so the content is invisible.",
  "Replace the plugin content with HTML5 video, images or text.")

# ---------------------------------------------------------------- images
C("img_missing_alt", W, "images", "Images without alt text",
  "Alt text describes images for blind visitors and helps images appear in search.",
  "Add a short alt text describing each meaningful image (use alt=\"\" for decorative ones).")
C("img_long_alt", N, "images", "Alt text longer than 125 characters",
  "Screen readers may cut off very long alt text.",
  "Keep alt text short and descriptive.")
C("img_large", W, "images", "Large images (over 300 KB)",
  "Heavy images slow pages down, especially on mobile data.",
  "Compress the image and use WebP or AVIF at the size it's shown.")
C("img_no_dims", N, "images", "Images without width and height",
  "Without dimensions the layout jumps while images load (layout shift).",
  "Add width and height attributes (or CSS aspect-ratio) to images.")
C("img_no_lazy", N, "images", "Image-heavy pages without lazy loading",
  "All images load at once even if visitors never scroll to them.",
  "Add loading=\"lazy\" to images below the first screen.")
C("img_legacy_format", N, "images", "Images in old formats (BMP, TIFF)",
  "These formats are huge and some browsers can't show them.",
  "Convert them to WebP, AVIF, JPEG or PNG.")

# ---------------------------------------------------------------- indexing & canonicals
C("noindex_page", W, "indexing", "Pages marked noindex",
  "These pages are kept out of search results. That's fine for thank-you or login pages, a problem elsewhere.",
  "Remove the noindex robots tag (or X-Robots-Tag header) from pages that should rank.")
C("nofollow_page", N, "indexing", "Pages tell crawlers not to follow any link",
  "A nofollow robots tag stops ranking value flowing from the page.",
  "Remove 'nofollow' from the robots meta tag unless it's intended.")
C("canonical_missing", N, "indexing", "Pages without a canonical tag",
  "A canonical tells search engines which URL is the main version (useful when URLs have parameters).",
  "Add <link rel=\"canonical\" href=\"…\"> pointing to the page's own clean URL.")
C("canonical_broken", E, "indexing", "Canonical points to a broken page",
  "Search engines are told the main version is a page that doesn't work.",
  "Point the canonical to a live page that returns 200, usually the page itself.")
C("canonical_redirect", W, "indexing", "Canonical points to a redirect",
  "Canonicals should point straight at the final URL.",
  "Update the canonical to the URL the redirect ends at.")
C("canonical_multiple", E, "indexing", "Conflicting canonical tags",
  "Pages declare several different canonical URLs, so search engines ignore them all.",
  "Keep one canonical tag per page (check your SEO plugin and theme both aren't adding one).")
C("canonical_header_conflict", W, "indexing", "Canonical in the HTTP header and the page disagree",
  "Conflicting signals make search engines ignore both.",
  "Use one method, or make both point to the same URL.")
C("canonical_relative", N, "indexing", "Canonical uses a relative address",
  "Relative canonicals are easy to get wrong when the page is copied or served on another host.",
  "Use a full address starting with https://.")
C("canonical_to_http", W, "indexing", "HTTPS pages have an HTTP canonical",
  "You're telling search engines the insecure version is the main one.",
  "Change the canonical to the https:// URL.")
C("canonical_other_domain", N, "indexing", "Canonical points to another website",
  "The page won't rank for you; the other site gets the credit.",
  "Keep it only for syndicated content. Otherwise point the canonical to your own URL.")
C("canonical_noindex_conflict", W, "indexing", "Noindex pages with a canonical to another URL",
  "noindex and canonical send opposite signals.",
  "Use only one: canonical for duplicates, noindex for pages that shouldn't appear at all.")
C("canonical_to_noindex", W, "indexing", "Canonical points to a noindex page",
  "The main version you've chosen is itself kept out of search.",
  "Point the canonical to an indexable page, or remove noindex from the target.")
C("canonicalized", N, "indexing", "Pages canonicalized to another URL",
  "These pages say another URL is the main version, so they won't rank themselves.",
  "Check this is intended; internal links should point to the canonical URL.")

# ---------------------------------------------------------------- sitemaps & structure
C("sitemap_missing", W, "sitemap", "No XML sitemap found",
  "A sitemap helps search engines find every page, especially new or deep ones.",
  "Create sitemap.xml (most CMSs and SEO plugins can), list it in robots.txt and submit it in Search Console.", True)
C("sitemap_unreachable", E, "sitemap", "A sitemap listed in robots.txt can't be loaded",
  "Search engines can't read a sitemap that returns an error.",
  "Fix the sitemap URL or update the Sitemap: line in robots.txt.", True)
C("sitemap_invalid", E, "sitemap", "Sitemap isn't valid XML",
  "Search engines may reject the sitemap.",
  "Regenerate the sitemap with your CMS or a sitemap plugin and validate it.", True)
C("sitemap_too_large", W, "sitemap", "Sitemap has more than 50,000 URLs or 50 MB",
  "Google ignores sitemaps over these limits.",
  "Split it into several sitemaps listed in a sitemap index.", True)
C("sitemap_broken_urls", E, "sitemap", "Sitemap lists broken URLs",
  "Sitemaps should only contain live pages; errors reduce trust in the sitemap.",
  "Remove these URLs from the sitemap or fix the pages.")
C("sitemap_redirects", W, "sitemap", "Sitemap lists URLs that redirect",
  "Only final URLs belong in a sitemap.",
  "Replace each URL with the address it redirects to.")
C("sitemap_noindex", E, "sitemap", "Sitemap lists noindex pages",
  "You're asking search engines to index pages you've also told them not to index.",
  "Remove noindex pages from the sitemap, or remove the noindex tag.")
C("sitemap_non_canonical", W, "sitemap", "Sitemap lists non-canonical URLs",
  "Sitemaps should list the main version of each page only.",
  "Replace these URLs with their canonical URLs.")
C("sitemap_blocked", W, "sitemap", "Sitemap lists URLs blocked by robots.txt",
  "Search engines can't read pages you've asked them to index.",
  "Remove the URLs from the sitemap or allow them in robots.txt.", True)
C("sitemap_wrong_host", W, "sitemap", "Sitemap lists URLs on another host or protocol",
  "Sitemaps may only list URLs on the same host and protocol (https vs http).",
  "List only https:// URLs of your main hostname.", True)
C("sitemap_future_lastmod", N, "sitemap", "Sitemap has 'last modified' dates in the future",
  "Wrong dates make search engines ignore lastmod for the whole sitemap.",
  "Fix the date generation in your CMS or sitemap plugin.", True)
C("robots_no_sitemap", N, "sitemap", "robots.txt doesn't point to the sitemap",
  "Listing the sitemap in robots.txt lets every crawler find it.",
  "Add 'Sitemap: https://your-site/sitemap.xml' to robots.txt.", True)
C("orphan_page", W, "sitemap", "Orphan pages (in the sitemap but not linked)",
  "No page on your site links to these, so visitors can't reach them and they rank poorly.",
  "Link to them from relevant pages or the navigation, or remove them if they're outdated.")
C("not_in_sitemap", N, "sitemap", "Indexable pages missing from the sitemap",
  "Pages not in the sitemap may be found later or not at all.",
  "Add them to the sitemap (check your sitemap plugin's settings).")
C("deep_page", N, "sitemap", "Pages more than 3 clicks from the home page",
  "Deep pages get crawled less often and receive less ranking value.",
  "Link to important pages from the home page, menus or hub pages.")
C("single_inlink", N, "sitemap", "Pages with only one internal link pointing to them",
  "Few internal links signal the page isn't important.",
  "Add links from related pages with descriptive anchor text.")

# ---------------------------------------------------------------- hreflang
C("hreflang_invalid", E, "international", "Invalid language codes in hreflang",
  "Search engines ignore hreflang with unknown codes.",
  "Use ISO codes like en, en-IN, hi-IN or x-default.")
C("hreflang_broken", E, "international", "hreflang points to broken pages",
  "Alternate language versions must load.",
  "Fix or remove the hreflang link.")
C("hreflang_redirect", W, "international", "hreflang points to redirects",
  "hreflang should point to final URLs.",
  "Update the hreflang link to the redirect's destination.")
C("hreflang_no_return", W, "international", "hreflang without a return link",
  "Language versions must link to each other, or search engines ignore the pair.",
  "Add the matching hreflang link on the other page pointing back.")
C("hreflang_no_self", N, "international", "hreflang set doesn't include the page itself",
  "Each page should list itself in its hreflang set.",
  "Add a hreflang link for the page's own language and URL.")
C("hreflang_no_xdefault", N, "international", "No x-default hreflang",
  "x-default tells search engines which version to show to everyone else.",
  "Add hreflang=\"x-default\" pointing to your main or language-picker page.")
C("hreflang_duplicate", W, "international", "The same language code points to different URLs",
  "Conflicting hreflang entries are ignored.",
  "Keep one URL per language code.")
C("hreflang_lang_mismatch", N, "international", "Page language differs from its hreflang",
  "The page's lang attribute and its own hreflang disagree.",
  "Make the lang attribute and hreflang use the same language.")
C("hreflang_non_canonical", W, "international", "hreflang points to non-canonical pages",
  "hreflang should point to the canonical version of each language page.",
  "Point hreflang links at canonical URLs.")

# ---------------------------------------------------------------- structured data & social
C("jsonld_invalid", E, "markup", "Structured data (JSON-LD) has syntax errors",
  "Search engines can't read broken structured data, so you lose rich results.",
  "Fix the JSON (check commas and quotes) and test it in Google's Rich Results Test.")
C("jsonld_no_context", W, "markup", "Structured data without @context",
  "Without \"@context\": \"https://schema.org\" search engines don't know the vocabulary.",
  "Add \"@context\": \"https://schema.org\" to each JSON-LD block.")
C("jsonld_missing_fields", W, "markup", "Structured data missing required fields",
  "Rich results need certain fields (e.g. headline for Article, name and offers for Product).",
  "Fill in the missing properties listed for each page.")
C("structured_data_missing", N, "markup", "Pages without structured data",
  "Structured data helps search engines and AI assistants understand the page and can earn rich results.",
  "Add schema.org JSON-LD that fits the page (Organization, Product, Article, FAQPage, BreadcrumbList…).")
C("org_schema_missing", N, "markup", "No Organization structured data on the home page",
  "Organization markup with your logo and social profiles feeds Google's knowledge panel and AI answers.",
  "Add Organization JSON-LD with name, url, logo and sameAs links to your profiles.", True)
C("og_missing", W, "markup", "Pages without Open Graph tags",
  "Links shared on LinkedIn, Facebook and WhatsApp show no picture or a random one.",
  "Add og:title, og:description, og:image and og:url tags.")
C("og_incomplete", N, "markup", "Incomplete Open Graph tags",
  "Some of og:title, og:description, og:image and og:url are missing.",
  "Add the missing Open Graph tags (SEO plugins can fill them automatically).")
C("og_image_broken", W, "markup", "Open Graph image doesn't load",
  "Shared links show no picture.",
  "Point og:image to a working image of at least 1200×630 pixels.")
C("og_url_mismatch", N, "markup", "og:url differs from the page's canonical URL",
  "Shares may be counted against a different URL.",
  "Make og:url match the canonical URL.")
C("twitter_card_missing", N, "markup", "Pages without an X (Twitter) card tag",
  "Links shared on X show a plain link instead of a card.",
  "Add <meta name=\"twitter:card\" content=\"summary_large_image\">.")
C("favicon_missing", N, "markup", "No favicon",
  "The favicon appears in browser tabs and in Google's mobile results.",
  "Add a favicon (at least 48×48) and link it with <link rel=\"icon\">.", True)

# ---------------------------------------------------------------- security
C("not_https", E, "security", "Pages served over insecure HTTP",
  "Browsers mark HTTP pages 'Not secure' and search engines prefer HTTPS.",
  "Install an SSL certificate (free with most hosts) and redirect all pages to https://.")
C("mixed_content", E, "security", "HTTPS pages load insecure (HTTP) files",
  "Browsers block or warn about insecure images and scripts on secure pages.",
  "Change the file addresses to https:// or host the files yourself.")
C("insecure_form", E, "security", "Forms send data over HTTP",
  "Anything typed in the form (names, emails, passwords) can be intercepted.",
  "Serve the page and the form's action address over https://.")
C("no_hsts", N, "security", "No HSTS header",
  "HSTS makes browsers always use HTTPS for your site, even if someone types http://.",
  "Ask your host or CDN to add a Strict-Transport-Security header.", True)
C("ssl_error", E, "security", "SSL certificate problem",
  "Visitors see a security warning and most will leave.",
  "Renew or reinstall the certificate so it covers every hostname you use.", True)

# ---------------------------------------------------------------- mobile
C("viewport_missing", W, "mobile", "Pages without a viewport tag",
  "Without it, phones show a shrunken desktop page. Google ranks the mobile version.",
  "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">.")
C("viewport_not_responsive", W, "mobile", "Viewport isn't set to the device width",
  "A fixed-width viewport doesn't adapt to phone screens.",
  "Use content=\"width=device-width, initial-scale=1\".")
C("viewport_no_zoom", N, "mobile", "Pages stop visitors from zooming",
  "Blocking zoom makes text hard to read for many people (an accessibility problem).",
  "Remove user-scalable=no and maximum-scale=1 from the viewport tag.")

# ---------------------------------------------------------------- performance
C("slow_ttfb", W, "performance", "Slow server response (over 800 ms)",
  "The server takes a long time before sending anything, which delays everything else.",
  "Use caching (page cache or CDN), a faster host, or optimise slow database queries.")
C("not_compressed", N, "performance", "Pages aren't compressed",
  "Compression (gzip or Brotli) makes pages several times smaller to download.",
  "Turn on gzip or Brotli compression in your server, host or CDN.")
C("cwv_lcp_poor", W, "performance", "Slow Largest Contentful Paint (main content appears late)",
  "LCP over 4 s is 'poor' in Google's Core Web Vitals and hurts rankings and conversions.",
  "Compress and preload the main image, reduce render-blocking CSS/JS and speed up the server.")
C("cwv_cls_poor", W, "performance", "Layout shifts while loading (poor CLS)",
  "Content jumping around makes visitors mis-click and is a Core Web Vitals failure.",
  "Set sizes for images, ads and embeds, and avoid inserting content above what's already shown.")
C("cwv_inp_poor", W, "performance", "Slow response to clicks and taps (poor INP)",
  "Real visitors wait over half a second after interacting, which feels broken.",
  "Break up long JavaScript tasks, remove heavy third-party scripts and defer non-essential code.")
C("tbt_high", N, "performance", "Heavy JavaScript blocks the page (high Total Blocking Time)",
  "While scripts run, the page can't respond to taps.",
  "Remove unused JavaScript, split bundles and delay third-party tags.")
C("fcp_slow", N, "performance", "First content appears late (slow FCP)",
  "Visitors stare at a blank screen for over 3 seconds.",
  "Inline critical CSS, defer scripts and use a CDN.")
C("crux_failing", W, "performance", "Real visitors experience slow pages (Chrome field data)",
  "Google's real-user data rates this page or site as slow, which is used for ranking.",
  "Work through the PageSpeed suggestions for the slowest metric first.")
C("psi_perf_low", W, "performance", "Low PageSpeed performance score (under 50)",
  "The page is slow to load and become usable on mobile.",
  "Start with the biggest PageSpeed opportunities shown for this page.")
C("psi_perf_medium", N, "performance", "PageSpeed performance could be better (50–89)",
  "The page is acceptable but slower than it could be.",
  "Tackle the PageSpeed opportunities with the biggest savings.")
C("psi_seo_low", N, "performance", "Lighthouse SEO score under 90",
  "Lighthouse found basic SEO problems on this page.",
  "Open PageSpeed Insights for the page and fix the failed SEO audits.")
C("psi_a11y_low", N, "performance", "Lighthouse accessibility score under 90",
  "Some visitors (and AI agents) may struggle to use the page.",
  "Fix contrast, labels and alt text issues listed in PageSpeed Insights.")
C("psi_bp_low", N, "performance", "Lighthouse best-practices score under 90",
  "The page has browser warnings such as console errors or outdated libraries.",
  "Check the best-practices section in PageSpeed Insights.")

# ---------------------------------------------------------------- AI search readiness
C("ai_search_blocked", W, "ai", "AI search assistants are blocked in robots.txt",
  "Assistants like ChatGPT search, Perplexity and Claude can't read or cite your pages in their answers.",
  "Allow OAI-SearchBot, ChatGPT-User, PerplexityBot, Claude-SearchBot and similar agents in robots.txt.", True)
C("ai_training_blocked", N, "ai", "AI training crawlers are blocked in robots.txt",
  "A legitimate choice. Blocking them keeps your content out of future AI models, which may reduce how well they know your brand.",
  "Decide deliberately. To allow them, remove the Disallow rules for GPTBot, ClaudeBot, Google-Extended, CCBot and similar.", True)
C("ai_partial", N, "ai", "AI crawlers are blocked from parts of the site",
  "Some sections are hidden from AI crawlers.",
  "Check the Disallow rules for AI user agents only cover what you want hidden.", True)
C("llms_missing", N, "ai", "No llms.txt file",
  "llms.txt is an emerging, optional file that gives AI assistants a short guide to your most important pages.",
  "Publish /llms.txt in Markdown: a '# Company name' title, a one-paragraph summary and links to key pages.", True)
C("llms_invalid", N, "ai", "llms.txt doesn't follow the format",
  "The file should be plain Markdown starting with a '# Title' line, not a web page.",
  "Serve /llms.txt as text/plain or text/markdown with a '# Title' first line and a list of links.", True)
C("noai_meta", N, "ai", "Pages ask AI tools not to use their content (noai)",
  "noai / noimageai tags are a choice; some AI tools respect them and won't use the page.",
  "Remove the tags if you want AI assistants to learn from and cite the page.")
C("nosnippet", N, "ai", "Pages block snippets (nosnippet / max-snippet:0)",
  "Search engines can't show a text preview, and Google won't use the page in AI Overviews.",
  "Remove nosnippet unless the content must not be quoted.")
C("js_only_content", W, "ai", "Page content appears only after JavaScript runs",
  "Most AI crawlers don't run JavaScript, so they see an empty page.",
  "Render the main content on the server (server-side rendering or pre-rendering).")

# ---------------------------------------------------------------- URLs
C("url_uppercase", N, "urls", "URLs with capital letters",
  "URLs are case-sensitive, so capitals easily create duplicates and broken links.",
  "Use lower-case URLs and redirect the old ones.")
C("url_underscore", N, "urls", "URLs with underscores",
  "Google treats hyphens as word separators but not underscores.",
  "Use hyphens in new URLs; redirect old ones only if you're restructuring anyway.")
C("url_params", N, "urls", "Internal URLs with query parameters",
  "Parameters often create many URLs for the same content.",
  "Use clean URLs, and canonical tags on parameter versions.")
C("url_too_long", N, "urls", "Long URLs (over 115 characters)",
  "Long URLs are hard to share and get cut off in search results.",
  "Use short, descriptive URLs.")
C("url_special_chars", N, "urls", "URLs with spaces or special characters",
  "Spaces and non-ASCII characters get encoded into hard-to-read %XX sequences.",
  "Use only lower-case letters, numbers and hyphens.")
C("url_session_id", W, "urls", "URLs contain session IDs",
  "Session IDs create a new URL for every visitor, flooding search engines with duplicates.",
  "Store sessions in cookies instead of the URL.")
C("url_slash_duplicate", W, "urls", "Same page loads with and without a trailing slash",
  "Two URLs with the same content split ranking signals.",
  "Pick one form and 301-redirect the other.")

CHECKS: dict[str, Check] = {c.id: c for c in _C}
assert len(CHECKS) == len(_C), "duplicate check id"

WEIGHTS = {"error": 0.10, "warning": 0.035, "notice": 0.008}


def catalog() -> list[dict]:
    return [c.out() for c in _C]


# ================================================================ evaluation helpers
def _robots_flags(rec: dict) -> set[str]:
    facts = rec.get("facts") or {}
    raw = (facts.get("meta_robots") or "") + "," + ((rec.get("headers") or {}).get("x-robots-tag") or "").lower()
    flags = set()
    for part in raw.split(","):
        part = part.strip()
        if ":" in part and not part.startswith("max-"):
            part = part.split(":", 1)[1].strip()  # "googlebot: noindex"
        if part:
            flags.add(part)
    if "none" in flags:
        flags |= {"noindex", "nofollow"}
    return flags


def is_noindex(rec: dict) -> bool:
    return "noindex" in _robots_flags(rec)


def ok_html(rec: dict) -> bool:
    return bool(rec.get("is_html") and rec.get("facts") and not rec.get("chain") and rec.get("status") == 200)


def _link_header_canonical(rec: dict) -> str | None:
    from app.services.crawler import normalize_url

    link = (rec.get("headers") or {}).get("link") or ""
    m = re.search(r"<([^>]+)>\s*;\s*rel=\"?canonical\"?", link, re.IGNORECASE)
    return normalize_url(m.group(1), rec["url"]) if m else None


def target_state(d: CrawlData, url: str) -> dict | None:
    """Status info for any URL we know about: crawled page, checked resource/image/external/ref."""
    if url in d.pages:
        rec = d.pages[url]
        return {"status": rec.get("status") or 0, "error": rec.get("error"), "redirect": bool(rec.get("chain")),
                "final_status": rec.get("final_status", rec.get("status")), "rec": rec}
    for pool in (d.resources, d.images, d.external, d.refs):
        if url in pool and ("status" in pool[url] or pool[url].get("error")):
            r = pool[url]
            return {"status": r.get("status") or 0, "error": r.get("error"), "redirect": bool(r.get("redirects")),
                    "final_status": r.get("status"), "rec": None}
    return None


def _broken(st: dict | None, external: bool = False) -> bool:
    if st is None:
        return False
    s = st.get("final_status") if st.get("redirect") else st.get("status")
    s = s or 0
    err = st.get("error") or ""
    if external:
        if s in (404, 410) or s >= 500:
            return True
        return s == 0 and any(x in err for x in ("Domain not found", "ConnectError", "Redirect loop"))
    if s >= 400:
        return True
    return s == 0 and bool(err) and not err.startswith("Blocked")


# ================================================================ evaluation
def evaluate(d: CrawlData, perf: dict | None = None) -> tuple[list[dict], dict[str, dict]]:
    """(issues, page_info) — page_info[url] = {depth, inlinks, inlinks_from, indexable, in_sitemap}."""
    issues: list[dict] = []
    home = d.home or d.start_url

    def add(check: str, url: str, /, **detail) -> None:
        assert check in CHECKS, check
        issues.append({"check": check, "url": url, "detail": detail})

    pages = d.pages
    html = {u: r for u, r in pages.items() if ok_html(r)}

    def resolve(u: str) -> str:
        r = pages.get(u)
        if r and r.get("chain") and not r.get("loop"):
            return r.get("final_url") or u
        return u

    # ---- link graph, inlinks, click depth
    inlinks: dict[str, set[str]] = defaultdict(set)
    for u, r in html.items():
        for t in r["facts"]["internal_links"]:
            if t != u:
                inlinks[t].add(u)
                rt = resolve(t)
                if rt != t and rt != u:
                    inlinks[rt].add(u)
    depth: dict[str, int] = {}
    start = resolve(home)
    if start in html:
        depth[start] = 0
        q = deque([start])
        while q:
            u = q.popleft()
            for t in html[u]["facts"]["internal_links"]:
                t2 = resolve(t)
                if t2 in pages and t2 not in depth:
                    depth[t2] = depth[u] + 1
                    if t2 in html:
                        q.append(t2)
                if t in pages and t not in depth:
                    depth[t] = depth[u] + 1

    def canonical_target(r: dict) -> str | None:
        c = r["facts"].get("canonical")
        return c if c and c != r["url"] else None

    indexable = {u for u, r in html.items() if not is_noindex(r) and canonical_target(r) is None}
    sitemap_ok = any(s.get("status") == 200 and not s.get("error") and s.get("urls") for s in d.sitemaps)

    info: dict[str, dict] = {}
    for u, r in pages.items():
        info[u] = {"depth": depth.get(u), "inlinks": len(inlinks.get(u, ())), "inlinks_from": sorted(inlinks.get(u, ()))[:50],
                   "indexable": u in indexable, "in_sitemap": u in d.sitemap_urls}

    # ---- status, redirects
    for u, r in pages.items():
        s = r.get("status") or 0
        src = r.get("found_from")
        if r.get("loop"):
            add("redirect_loop", u, chain=r.get("chain"))
            continue
        if r.get("chain"):
            chain = r["chain"]
            fs = r.get("final_status") or 0
            if len(chain) >= 2:
                add("redirect_chain", u, hops=[*chain, {"url": r.get("final_url"), "status": fs}])
            temp = [h for h in chain if h["status"] in (302, 303, 307)]
            if temp:
                add("temporary_redirect", u, status=temp[0]["status"], to=r.get("final_url"))
            if fs >= 400 or (fs == 0 and r.get("error")):
                add("redirect_broken", u, to=r.get("final_url"), status=fs, error=r.get("error"))
            if r.get("final_url") and _dom(r["final_url"]) != d.domain:
                add("redirect_external", u, to=r.get("final_url"))
            continue
        if 400 <= s < 500:
            add("http_4xx", u, status=s, linked_from=src)
        elif s >= 500:
            add("http_5xx", u, status=s, linked_from=src)
        elif s == 0 and r.get("error"):
            add("fetch_failed", u, error=r.get("error"), linked_from=src)

    ssl = next((r for r in pages.values() if (r.get("error") or "").startswith("SSL")), None)
    if ssl:
        add("ssl_error", home, error=ssl["error"], url=ssl["url"])

    for u, src in list(d.blocked.items())[:500]:
        add("blocked_by_robots", u, linked_from=src or None)

    # ---- site-wide: robots, sitemaps, variants, llms, AI
    rb = d.robots or {}
    if rb.get("unreachable"):
        add("robots_unreachable", home, status=rb.get("status"), error=rb.get("error"))
    elif not rb.get("found"):
        add("robots_missing", home, status=rb.get("status"))
    else:
        if (rb.get("search_engines") or {}).get("Googlebot") == "blocked":
            add("robots_blocks_all", home, rule="Googlebot can't read the home page")
        if rb.get("invalid_lines"):
            add("robots_invalid", home, lines=rb["invalid_lines"])
        if (rb.get("size") or 0) > 500 * 1024:
            add("robots_too_large", home, size=rb["size"])
        if (rb.get("crawl_delay") or 0) > 10:
            add("robots_crawl_delay", home, crawl_delay=rb["crawl_delay"])
        if not rb.get("sitemaps"):
            add("robots_no_sitemap", home)
    if d.truncated:
        add("crawl_truncated", home, pages=len(pages))

    if not sitemap_ok and not any(s.get("declared") for s in d.sitemaps):
        add("sitemap_missing", home)
    for s in d.sitemaps:
        if s.get("status") != 200:
            add("sitemap_unreachable", home, sitemap=s["url"], status=s.get("status"), error=s.get("error"))
        elif s.get("error"):
            add("sitemap_invalid", home, sitemap=s["url"], error=s["error"])
        if (s.get("urls") or 0) > 50_000 or (s.get("bytes") or 0) > 50 * 1024 * 1024:
            add("sitemap_too_large", home, sitemap=s["url"], urls=s.get("urls"))
        if s.get("off_site"):
            add("sitemap_wrong_host", home, sitemap=s["url"], count=s["off_site"])
        if s.get("future_lastmod"):
            add("sitemap_future_lastmod", home, sitemap=s["url"], count=s["future_lastmod"])
    blocked_sm = d.stats.get("sitemap_blocked") or []
    if blocked_sm:
        add("sitemap_blocked", home, urls=blocked_sm[:20], count=len(blocked_sm))
    for u, meta in d.sitemap_urls.items():
        r = pages.get(u)
        if not r:
            continue
        if r.get("chain"):
            add("sitemap_redirects", u, to=r.get("final_url"), sitemap=meta.get("sitemap"))
        elif (r.get("status") or 0) >= 400 or (r.get("status") == 0 and r.get("error")):
            add("sitemap_broken_urls", u, status=r.get("status"), sitemap=meta.get("sitemap"))
        elif ok_html(r):
            if is_noindex(r):
                add("sitemap_noindex", u, sitemap=meta.get("sitemap"))
            elif canonical_target(r):
                add("sitemap_non_canonical", u, canonical=canonical_target(r), sitemap=meta.get("sitemap"))

    hv = d.http_variant or {}
    if hv and hv.get("error") is None and not hv.get("ok") and hv.get("status"):
        add("http_no_https_redirect", home, url=hv.get("url"), status=hv.get("first_status"), redirects_to=hv.get("redirects_to"))
    if (d.host_variant or {}).get("duplicate"):
        add("www_duplicate", home, url=d.host_variant.get("url"))
    if home.startswith("https://") and resolve(home) in html and "strict-transport-security" not in (html[resolve(home)].get("headers") or {}):
        add("no_hsts", home)

    llms = d.llms or {}
    if not llms.get("found"):
        add("llms_missing", home)
    elif llms.get("html") or not llms.get("has_title"):
        add("llms_invalid", home, content_type=llms.get("content_type"))
    search_blocked = [a["bot"] for a in d.ai_access if a["purpose"] == "search" and a["status"] == "blocked"]
    train_blocked = [a["bot"] for a in d.ai_access if a["purpose"] == "training" and a["status"] == "blocked"]
    partial = [a["bot"] for a in d.ai_access if a["status"] == "partial"]
    if search_blocked:
        add("ai_search_blocked", home, bots=search_blocked)
    if train_blocked:
        add("ai_training_blocked", home, bots=train_blocked)
    if partial:
        add("ai_partial", home, bots=partial)

    home_rec = html.get(resolve(home))
    if home_rec is not None:
        f = home_rec["facts"]
        types = set(f.get("jsonld_types") or []) | set(f.get("microdata_types") or [])
        if not types & {"Organization", "Corporation", "LocalBusiness", "ProfessionalService", "OnlineBusiness",
                        "OnlineStore", "NGO", "EducationalOrganization", "Store", "MedicalOrganization"}:
            add("org_schema_missing", home)
        if not f.get("favicon") and not d.favicon:
            add("favicon_missing", home)

    # ---- duplicates across pages (indexable pages only)
    def groups(key) -> dict[str, list[str]]:
        g: dict[str, list[str]] = defaultdict(list)
        for u in indexable:
            k = key(html[u]["facts"])
            if k:
                g[k].append(u)
        return {k: v for k, v in g.items() if len(v) > 1}

    for k, urls in groups(lambda f: (f.get("title") or "").strip().lower()).items():
        for u in urls:
            add("title_duplicate", u, title=html[u]["facts"]["title"], others=[x for x in urls if x != u][:10])
    for k, urls in groups(lambda f: (f.get("description") or "").strip().lower()).items():
        for u in urls:
            add("desc_duplicate", u, others=[x for x in urls if x != u][:10])
    for k, urls in groups(lambda f: ((f.get("headings") or {}).get("h1") or [""])[0].strip().lower()).items():
        for u in urls:
            add("h1_duplicate", u, h1=k, others=[x for x in urls if x != u][:10])
    for k, urls in groups(lambda f: f.get("content_hash")).items():
        for u in urls:
            add("duplicate_content", u, others=[x for x in urls if x != u][:10])
    slash: dict[str, list[str]] = defaultdict(list)
    for u in html:
        p = urlsplit(u)
        if p.path != "/":
            slash[(p.scheme, p.netloc, p.path.rstrip("/"), p.query)].append(u)
    for urls in slash.values():
        if len(urls) > 1:
            for u in urls:
                if u in indexable:
                    add("url_slash_duplicate", u, others=[x for x in urls if x != u])

    # ---- per page
    for u, r in html.items():
        f = r["facts"]
        flags = _robots_flags(r)
        noidx = "noindex" in flags
        idx = u in indexable
        https = u.startswith("https://")

        # links
        broken_int, to_redirect, to_noidx = [], [], []
        for t in f["internal_links"]:
            st = target_state(d, t)
            if _broken(st):
                broken_int.append({"url": t, "status": st["status"] if not st["redirect"] else st["final_status"]})
            elif st and st["redirect"] and st["rec"] is not None:
                to_redirect.append(t)
            elif st and st["rec"] is not None and ok_html(st["rec"]) and is_noindex(st["rec"]):
                to_noidx.append(t)
        if broken_int:
            add("broken_internal_link", u, links=broken_int[:20], count=len(broken_int))
        if to_redirect:
            add("internal_link_to_redirect", u, links=to_redirect[:20], count=len(to_redirect))
        if to_noidx:
            add("link_to_noindex", u, links=to_noidx[:20])
        broken_ext = []
        for t in f["external_links"]:
            st = target_state(d, t)
            if _broken(st, external=True):
                broken_ext.append({"url": t, "status": st["status"], "error": st.get("error")})
        if broken_ext:
            add("broken_external_link", u, links=broken_ext[:20], count=len(broken_ext))
        if f.get("nofollow_internal"):
            add("nofollow_internal", u, links=f["nofollow_internal"])
        if f.get("empty_anchor_links"):
            add("empty_anchor", u, count=f["empty_anchor_links"])
        if f.get("generic_anchor_links"):
            add("generic_anchor", u, count=f["generic_anchor_links"])
        if f.get("http_internal_links"):
            add("http_internal_link", u, links=f["http_internal_links"])
        if f.get("js_links"):
            add("js_links", u, count=f["js_links"])
        if f.get("link_count", 0) > 300:
            add("too_many_links", u, count=f["link_count"])
        if not f.get("internal_link_count"):
            add("no_outlinks", u)
        if f.get("http_external_links"):
            add("external_http_links", u, count=f["http_external_links"])
        bad_imgs, large_imgs = [], []
        for i in f.get("images", []):
            st = d.images.get(i["src"])
            if st is None:
                continue
            if _broken({"status": st.get("status"), "error": st.get("error"), "redirect": False}, external=True) or \
                    (st.get("status") or 0) in (400, 401, 404, 410):
                bad_imgs.append({"url": i["src"], "status": st.get("status")})
            elif (st.get("size") or 0) > 300 * 1024:
                large_imgs.append({"url": i["src"], "kb": round(st["size"] / 1024)})
        if bad_imgs:
            add("broken_image", u, images=bad_imgs[:20], count=len(bad_imgs))
        if large_imgs:
            add("img_large", u, images=large_imgs[:20], count=len(large_imgs))
        if f.get("meta_refresh"):
            add("meta_refresh", u, content=f.get("meta_refresh"))

        # titles / descriptions / headings
        title = f.get("title") or ""
        if not title:
            add("title_missing", u)
        else:
            if len(title) > 60:
                add("title_too_long", u, title=title, length=len(title))
            elif len(title) < 30:
                add("title_too_short", u, title=title, length=len(title))
        if f.get("title_count", 0) > 1:
            add("title_multiple", u, count=f["title_count"])
        h1s = (f.get("headings") or {}).get("h1") or []
        h1_count = (f.get("heading_counts") or {}).get("h1", 0)
        if title and h1s and h1s[0].strip().lower() == title.strip().lower():
            add("title_same_as_h1", u, title=title)
        desc = f.get("description") or ""
        if not desc:
            add("desc_missing", u)
        else:
            if len(desc) > 160:
                add("desc_too_long", u, length=len(desc))
            elif len(desc) < 70:
                add("desc_too_short", u, length=len(desc), description=desc)
            if title and desc.strip().lower() == title.strip().lower():
                add("title_desc_same", u)
        if f.get("description_count", 0) > 1:
            add("desc_multiple", u, count=f["description_count"])
        if h1_count == 0:
            add("h1_missing", u)
        else:
            if h1_count > 1:
                add("h1_multiple", u, count=h1_count, headings=h1s[:5])
            if not any(h.strip() for h in h1s):
                add("h1_empty", u)
            elif len(h1s[0]) > 70:
                add("h1_too_long", u, length=len(h1s[0]))
        if f.get("heading_skip"):
            add("heading_skip", u, where=f["heading_skip"])
        if f.get("word_count", 0) >= 300 and not (f.get("heading_counts") or {}).get("h2"):
            add("h2_missing", u, words=f["word_count"])

        # content
        wc = f.get("word_count", 0)
        if idx and wc < 200 and not (wc < 50 and f.get("js_app_shell")):
            add("thin_content", u, words=wc)
        if f.get("text_ratio", 1) < 0.1:
            add("low_text_ratio", u, ratio=f.get("text_ratio"))
        if (r.get("html_bytes") or r.get("size") or 0) > 2 * 1024 * 1024:
            add("large_html", u, kb=round((r.get("html_bytes") or r.get("size")) / 1024))
        if f.get("lorem_ipsum"):
            add("lorem_ipsum", u)
        lang = f.get("lang")
        if not lang:
            add("lang_missing", u)
        elif not _LANG.match(lang):
            add("lang_invalid", u, lang=lang)
        if not f.get("charset") and "charset" not in (r.get("content_type") or "").lower():
            add("charset_missing", u)
        if not f.get("doctype"):
            add("doctype_missing", u)
        if f.get("scripts", 0) > 30 or f.get("stylesheets", 0) > 15:
            add("many_resources", u, scripts=f.get("scripts"), stylesheets=f.get("stylesheets"))
        if f.get("dom_nodes", 0) > 1500:
            add("dom_too_large", u, elements=f["dom_nodes"])
        if f.get("deprecated_tags"):
            add("deprecated_html", u, tags=f["deprecated_tags"])
        if f.get("plugins"):
            add("plugins_used", u)
        if wc < 50 and (f.get("js_app_shell") or f.get("scripts", 0) >= 3):
            add("js_only_content", u, words=wc)

        # images
        if f.get("images_missing_alt"):
            add("img_missing_alt", u, images=f["images_missing_alt"][:20], count=len(f["images_missing_alt"]))
        if f.get("images_long_alt"):
            add("img_long_alt", u, images=f["images_long_alt"][:10])
        if f.get("images_no_dims"):
            add("img_no_dims", u, count=f["images_no_dims"])
        if f.get("image_count", 0) >= 6 and not f.get("images_lazy"):
            add("img_no_lazy", u, count=f["image_count"])
        if f.get("legacy_images"):
            add("img_legacy_format", u, images=f["legacy_images"])

        # indexing & canonical
        if noidx:
            add("noindex_page", u, source="X-Robots-Tag header" if "noindex" in ((r.get("headers") or {}).get("x-robots-tag") or "").lower() else "robots meta tag")
        if "nofollow" in flags:
            add("nofollow_page", u)
        if "noai" in flags or "noimageai" in flags:
            add("noai_meta", u)
        if "nosnippet" in flags or "max-snippet:0" in flags:
            add("nosnippet", u)
        canon = f.get("canonical")
        if len(f.get("canonicals") or []) > 1:
            add("canonical_multiple", u, canonicals=f["canonicals"])
        hdr = _link_header_canonical(r)
        if hdr and canon and hdr != canon:
            add("canonical_header_conflict", u, header=hdr, html=canon)
        if not canon and not hdr:
            if not noidx:
                add("canonical_missing", u)
        else:
            c = canon or hdr
            if any(not x.lower().startswith(("http://", "https://")) for x in f.get("canonical_raw") or []):
                add("canonical_relative", u, canonical=(f.get("canonical_raw") or [""])[0])
            if https and c.startswith("http://"):
                add("canonical_to_http", u, canonical=c)
            if _dom(c) != d.domain:
                add("canonical_other_domain", u, canonical=c)
            elif c != u:
                st = target_state(d, c)
                if _broken(st):
                    add("canonical_broken", u, canonical=c, status=st["status"])
                elif st and st["redirect"]:
                    add("canonical_redirect", u, canonical=c)
                elif st and st["rec"] is not None and ok_html(st["rec"]) and is_noindex(st["rec"]):
                    add("canonical_to_noindex", u, canonical=c)
                if noidx:
                    add("canonical_noindex_conflict", u, canonical=c)
                add("canonicalized", u, canonical=c)

        # sitemap & structure
        pi = info[u]
        if sitemap_ok and u in d.sitemap_urls and pi["inlinks"] == 0 and resolve(home) != u:
            add("orphan_page", u)
        if sitemap_ok and idx and u not in d.sitemap_urls:
            add("not_in_sitemap", u)
        if pi["depth"] is not None and pi["depth"] > 3:
            add("deep_page", u, depth=pi["depth"])
        if idx and pi["inlinks"] == 1 and resolve(home) != u:
            add("single_inlink", u, linked_from=pi["inlinks_from"][:1])

        # hreflang
        hl = f.get("hreflang") or []
        if hl:
            codes: dict[str, set[str]] = defaultdict(set)
            for h in hl:
                lang_code, href = h.get("lang") or "", h.get("href")
                if not _HREFLANG.match(lang_code):
                    add("hreflang_invalid", u, hreflang=lang_code)
                if not href:
                    continue
                codes[lang_code.lower()].add(href)
                if href == u:
                    continue
                st = target_state(d, href)
                if _broken(st):
                    add("hreflang_broken", u, href=href, status=st["status"])
                elif st and st["redirect"]:
                    add("hreflang_redirect", u, href=href)
                elif st and st["rec"] is not None and ok_html(st["rec"]):
                    other = st["rec"]["facts"]
                    if not any(x.get("href") == u for x in other.get("hreflang") or []):
                        add("hreflang_no_return", u, href=href, lang=lang_code)
                    oc = other.get("canonical")
                    if oc and oc != href:
                        add("hreflang_non_canonical", u, href=href, canonical=oc)
            if not any(h.get("href") == u for h in hl):
                add("hreflang_no_self", u)
            if "x-default" not in codes:
                add("hreflang_no_xdefault", u)
            dups = [c for c, v in codes.items() if len(v) > 1]
            if dups:
                add("hreflang_duplicate", u, codes=dups)
            own = next((h["lang"] for h in hl if h.get("href") == u), None)
            if own and lang and own.lower() != "x-default" and own.split("-")[0].lower() != lang.split("-")[0].lower():
                add("hreflang_lang_mismatch", u, hreflang=own, lang=lang)

        # structured data & social
        blocks = f.get("jsonld") or []
        errs = [b["error"] for b in blocks if b.get("error")]
        if errs:
            add("jsonld_invalid", u, errors=errs[:5])
        if any(b.get("no_context") for b in blocks if not b.get("error")):
            add("jsonld_no_context", u)
        if f.get("jsonld_missing_fields"):
            add("jsonld_missing_fields", u, missing=f["jsonld_missing_fields"])
        if idx and not blocks and not f.get("microdata_types"):
            add("structured_data_missing", u)
        og = f.get("og") or {}
        need = ("og:title", "og:description", "og:image", "og:url")
        if not og.get("og:title") and not og.get("og:image"):
            add("og_missing", u)
        else:
            missing = [k for k in need if not og.get(k)]
            if missing:
                add("og_incomplete", u, missing=missing)
            if og.get("og:image"):
                from app.services.crawler import normalize_url

                img = normalize_url(og["og:image"], u)
                st = d.images.get(img) if img else None
                if st and ((st.get("status") or 0) >= 400 or (st.get("status") == 0 and st.get("error"))):
                    add("og_image_broken", u, image=img, status=st.get("status"))
            if og.get("og:url"):
                from app.services.crawler import normalize_url

                ou = normalize_url(og["og:url"], u)
                if ou and ou != (canon or u):
                    add("og_url_mismatch", u, og_url=ou, canonical=canon or u)
        if not (f.get("twitter") or {}).get("twitter:card"):
            add("twitter_card_missing", u)

        # security
        if not https:
            add("not_https", u)
        if f.get("mixed_content"):
            add("mixed_content", u, resources=f["mixed_content"][:10], count=len(f["mixed_content"]))
        bad_forms = [fm["action"] for fm in f.get("forms") or []
                     if fm["action"].startswith("http://") or (not https and fm.get("password"))]
        if bad_forms:
            add("insecure_form", u, actions=bad_forms[:5])

        # mobile
        vp = f.get("viewport")
        if vp is None:
            add("viewport_missing", u)
        else:
            vpl = vp.lower().replace(" ", "")
            if "width=device-width" not in vpl:
                add("viewport_not_responsive", u, viewport=vp)
            if "user-scalable=no" in vpl or "user-scalable=0" in vpl or re.search(r"maximum-scale=1(\.0)?($|,)", vpl):
                add("viewport_no_zoom", u, viewport=vp)

        # crawl performance
        if (r.get("ttfb_ms") or 0) > 800:
            add("slow_ttfb", u, ms=r["ttfb_ms"])
        if (r.get("html_bytes") or 0) > 1400 and not (r.get("headers") or {}).get("content-encoding"):
            add("not_compressed", u, kb=round(r["html_bytes"] / 1024, 1))

        # urls
        _url_checks(add, u)

    # ---- PageSpeed
    for p in (perf or {}).get("pages", []):
        _psi_checks(add, p)

    return issues, info


def _dom(u: str) -> str:
    from app.services.crawler import domain_of

    return domain_of(u)


_SESSION = re.compile(r"(^|[?&;])(sid|sessionid|session_id|phpsessid|jsessionid|aspsessionid\w*|s)=", re.IGNORECASE)


def _url_checks(add, u: str) -> None:
    p = urlsplit(u)
    path = p.path
    if re.search(r"[A-Z]", path):
        add("url_uppercase", u)
    if "_" in path:
        add("url_underscore", u)
    if p.query:
        add("url_params", u, query=p.query[:200])
        if _SESSION.search("?" + p.query) or ";jsessionid=" in path.lower():
            add("url_session_id", u)
    elif ";jsessionid=" in path.lower():
        add("url_session_id", u)
    if len(u) > 115:
        add("url_too_long", u, length=len(u))
    if re.search(r"%20|%[89A-Fa-f][0-9A-Fa-f]| ", path) or any(ord(c) > 127 for c in path):
        add("url_special_chars", u)


def _psi_checks(add, p: dict) -> None:
    u = p["url"]
    mob = p.get("mobile") or {}
    if mob.get("error"):
        mob = {}
    desk = p.get("desktop") or {}
    lab = mob.get("lab") or {}
    field = mob.get("field") or {}
    scores = mob.get("scores") or {}
    lcp = lab.get("lcp_ms")
    if (lcp and lcp > 4000) or field.get("lcp_ms_rating") == "poor":
        add("cwv_lcp_poor", u, lab_ms=lcp, field_ms=field.get("lcp_ms"))
    cls = lab.get("cls")
    if (cls is not None and cls > 0.25) or field.get("cls_rating") == "poor":
        add("cwv_cls_poor", u, lab=cls, field=field.get("cls"))
    if field.get("inp_ms_rating") == "poor":
        add("cwv_inp_poor", u, field_ms=field.get("inp_ms"))
    if (lab.get("tbt_ms") or 0) > 600:
        add("tbt_high", u, ms=lab["tbt_ms"])
    if (lab.get("fcp_ms") or 0) > 3000:
        add("fcp_slow", u, ms=lab["fcp_ms"])
    if field.get("overall") == "slow":
        add("crux_failing", u, origin=field.get("origin_fallback"))
    perf = scores.get("performance")
    if perf is not None:
        if perf < 50:
            add("psi_perf_low", u, mobile=perf, desktop=(desk.get("scores") or {}).get("performance"))
        elif perf < 90:
            add("psi_perf_medium", u, mobile=perf, desktop=(desk.get("scores") or {}).get("performance"))
    for k, cid in (("seo", "psi_seo_low"), ("accessibility", "psi_a11y_low"), ("best_practices", "psi_bp_low")):
        v = scores.get(k)
        if v is not None and v < 90:
            add(cid, u, score=v)


# ================================================================ scoring
def health_score(counts_by_check: dict[str, int], total_pages: int) -> int:
    """100 × Π(1 − weight(severity) × √(share of pages affected)); site-wide checks count as the whole site."""
    n = max(1, total_pages)
    score = 1.0
    for cid, count in counts_by_check.items():
        chk = CHECKS.get(cid)
        if chk is None or count <= 0:
            continue
        share = 1.0 if chk.site else min(1.0, count / n)
        score *= max(0.0, 1 - WEIGHTS[chk.severity] * math.sqrt(share))
    return max(0, min(100, round(score * 100)))
