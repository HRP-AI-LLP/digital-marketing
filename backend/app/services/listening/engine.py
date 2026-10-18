"""Listening orchestration: run due queries against every enabled source, keep what passes the keyword prefilter,
dedupe (canonical URL, then content hash), store, enrich, and alert on spikes. Also the daily/weekly digest."""

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization
from app.models.listening import ListeningMention, ListeningQuery, ListeningSettings
from app.services.inbox.base import clip, parse_time
from app.services.listening import enrich as enricher
from app.services.listening import sources as src
from app.services.listening.text import canonical_url, clean_terms, content_hash, dedupe_key, domain_of, prefilter

log = logging.getLogger("listening")

FIRST_LOOKBACK = timedelta(days=7)
OVERLAP = timedelta(hours=2)
FORCE_MIN = timedelta(minutes=10)
MAX_QUERIES = 25
MAX_PER_SOURCE = 60  # new mentions kept per query and source in one run
SPIKE_COOLDOWN = timedelta(hours=12)
DIGEST_HOUR = 8


def now_utc() -> datetime:
    return datetime.now(UTC)


async def get_settings(session: AsyncSession, org_id: uuid.UUID) -> ListeningSettings:
    s = await session.scalar(select(ListeningSettings))
    if s is None:
        s = ListeningSettings(organization_id=org_id, sources={}, state={}, summary={})
        session.add(s)
        await session.flush()
    return s


def spec_of(q: ListeningQuery) -> src.Spec:
    return src.Spec(terms=clean_terms(q.terms, 12), context_terms=clean_terms(q.context_terms, 10),
                    exclude=clean_terms(q.exclude, 20), languages=[x.lower() for x in clean_terms(q.languages, 5, 5)],
                    countries=[x.upper() for x in clean_terms(q.countries, 3, 2)])


def query_sources(settings: ListeningSettings, q: ListeningQuery, avail: dict) -> list[str]:
    on = src.enabled_sources(settings)
    override = q.sources or {}
    return [s for s in src.SOURCES if on.get(s) and override.get(s, True) and avail.get(s, (True,))[0]]


def _due(entry: dict | None, source: str, now: datetime, force: bool) -> bool:
    last = parse_time((entry or {}).get("last_run_at"))
    if last is None:
        return True
    minimum = FORCE_MIN if force else timedelta(minutes=src.SOURCES[source]["min_minutes"])
    return now - last >= minimum


def _since(entry: dict | None, now: datetime) -> datetime:
    last_ok = parse_time((entry or {}).get("last_ok_at"))
    return max(now - FIRST_LOOKBACK, last_ok - OVERLAP) if last_ok else now - FIRST_LOOKBACK


def _keep(f: src.Found, spec: src.Spec) -> tuple[bool, list[str]]:
    """The deterministic prefilter: a tracked term must appear in what we can see (title, text, author handle or the
    URL slug), the context terms too when set, and no excluded word. Search engines sometimes match only the page
    body; those are dropped on purpose (precision over recall, and no AI cost for them)."""
    url_words = re.sub(r"[/_.\-]+", " ", " ".join(filter(None, [f.url, (f.raw or {}).get("link")])))
    text = " ".join(filter(None, [f.title, f.text, f.author_handle, url_words]))
    ok, matched, _reason = prefilter(text, spec.terms, spec.context_terms, spec.exclude)
    if spec.languages and f.language and f.language.split("-")[0].lower() not in spec.languages:
        return False, matched
    return ok, matched


def _mention(org_id: uuid.UUID, q: ListeningQuery, f: src.Found, matched: list[str], key: str, chash: str | None) -> ListeningMention:
    canon = canonical_url(f.url)
    return ListeningMention(
        organization_id=org_id, query_id=q.id, query_ids=[str(q.id)], source=f.source, external_id=clip(f.external_id, 500),
        dedupe_key=key, content_hash=chash, url=f.url, canonical_url=canon,
        domain=domain_of((f.raw or {}).get("publisher_url")) if f.source == "google_news" else domain_of(f.url),
        title=clip(f.title, 500), snippet=clip(f.text, 2000), author_name=clip(f.author_name, 300),
        author_handle=clip(f.author_handle, 300), author_url=f.author_url, author_followers=f.author_followers,
        published_at=f.published_at, language=(f.language or None) and f.language[:20], score=f.score,
        comments=f.comments, reach=f.reach, matched_terms=matched, competitor_id=q.competitor_id,
        is_competitor=q.kind == "competitor", tags=[], status="new",
        raw={k: v for k, v in {**(f.raw or {}), "publisher": f.publisher}.items() if v is not None},
    )


async def _store(session: AsyncSession, org_id: uuid.UUID, q: ListeningQuery, found: list[tuple[src.Found, list[str]]],
                 seen: dict[str, ListeningMention]) -> list[ListeningMention]:
    """Insert new mentions; for ones we already have, record that this query matched too."""
    rows = []
    for f, matched in found:
        chash = content_hash(f.title, f.text, f.publisher)
        rows.append((f, matched, dedupe_key(f.source, f.url, f.external_id), chash))
    keys = {r[2] for r in rows}
    hashes = {r[3] for r in rows if r[3]}
    existing: list[ListeningMention] = []
    if keys or hashes:
        conds = [ListeningMention.dedupe_key.in_(keys)]
        if hashes:
            conds.append(ListeningMention.content_hash.in_(hashes))
        existing = list((await session.scalars(select(ListeningMention).where(or_(*conds)))).all())
    for m in existing:
        seen.setdefault(m.dedupe_key, m)
        if m.content_hash:
            seen.setdefault("h:" + m.content_hash, m)
    created: list[ListeningMention] = []
    for f, matched, key, chash in rows:
        m = seen.get(key) or (seen.get("h:" + chash) if chash else None)
        if m is not None:
            if str(q.id) not in (m.query_ids or []):
                m.query_ids = [*(m.query_ids or []), str(q.id)]
            if q.kind == "competitor":
                m.is_competitor = True
                m.competitor_id = m.competitor_id or q.competitor_id
            if matched:
                m.matched_terms = list(dict.fromkeys([*(m.matched_terms or []), *matched]))
            if m.source == f.source:
                for attr in ("score", "comments", "reach", "author_followers"):
                    v = getattr(f, attr)
                    if v is not None:
                        setattr(m, attr, v)
            continue
        if len([c for c in created if c.source == f.source]) >= MAX_PER_SOURCE:
            continue
        m = _mention(org_id, q, f, matched, key, chash)
        session.add(m)
        created.append(m)
        seen[key] = m
        if chash:
            seen["h:" + chash] = m
    await session.flush()
    return created


async def run(session: AsyncSession, org_id: uuid.UUID, *, force: bool = False, query_ids: list[str] | None = None,
              only: list[str] | None = None) -> dict:
    """Run every enabled, due (query, source) pair. Commits after each query. Returns a summary."""
    settings = await get_settings(session, org_id)
    now = now_utc()
    stmt = select(ListeningQuery).where(ListeningQuery.enabled.is_(True)).order_by(ListeningQuery.created_at)
    queries = list((await session.scalars(stmt)).all())[:MAX_QUERIES]
    if query_ids:
        queries = [q for q in queries if str(q.id) in set(map(str, query_ids))]
    ctx = await src.build_ctx(session, settings, now - FIRST_LOOKBACK)
    avail = src.availability(ctx)
    created: list[ListeningMention] = []
    seen: dict[str, ListeningMention] = {}
    totals: dict[str, dict] = {}
    for q in queries:
        spec = spec_of(q)
        if not spec.terms:
            continue
        qstate = dict(q.state or {})
        qsrc: dict = dict(qstate.get("sources") or {})
        new_for_query = 0
        for source in query_sources(settings, q, avail):
            if only and source not in only:
                continue
            entry = dict(qsrc.get(source) or {})
            if not _due(entry, source, now, force):
                continue
            ctx.since = _since(entry, now)
            entry.update(last_run_at=now.isoformat(), ok=True, message=None, found=0)
            try:
                rows = await src.collect(source, ctx, spec)
                kept = []
                for f in rows:
                    keep, matched = _keep(f, spec)
                    if keep:
                        kept.append((f, matched))
                new = await _store(session, org_id, q, kept, seen)
                entry.update(found=len(new), seen=len(rows), last_ok_at=now.isoformat())
                created.extend(new)
                new_for_query += len(new)
            except src.SourceError as e:
                entry.update(ok=False, message=e.message, setup=e.setup)
            except Exception as e:  # noqa: BLE001 - one broken source must never stop the others
                log.warning("listening source %s failed for query %s: %s", source, q.id, e)
                entry.update(ok=False, message=clip(f"Something went wrong ({type(e).__name__}). It will try again.", 300))
            qsrc[source] = entry
            t = totals.setdefault(source, {"ok": True, "found": 0, "message": None})
            t["found"] += entry.get("found") or 0
            if not entry["ok"]:
                t.update(ok=False, message=entry["message"])
        qstate.update(sources=qsrc, last_run_at=now.isoformat(), found_last_run=new_for_query)
        q.state = qstate
        await session.commit()

    stats = {"ai": 0, "heuristic": 0, "irrelevant": 0}
    if created:
        stats = await enricher.enrich(session, created, use_ai=settings.use_ai)
        await session.commit()
    state = dict(settings.state or {})
    state.update(last_run_at=now.isoformat(), last_found=len(created), first_run_at=state.get("first_run_at") or now.isoformat())
    src_state = dict(state.get("sources") or {})
    for s_id, t in totals.items():
        src_state[s_id] = {"last_run_at": now.isoformat(), **t}
    state["sources"] = src_state
    settings.state = state
    await session.commit()
    spikes = await detect_spikes(session, org_id, settings) if settings.spike_alerts else []
    relevant = [m for m in created if m.relevant]
    return {"new": len(relevant), "filtered_out": len(created) - len(relevant), "enrichment": stats,
            "needs_response": len([m for m in relevant if m.needs_response]), "sources": totals,
            "spikes": [s["kind"] for s in spikes]}


# ------------------------------------------------------------------ spikes
def when_col():
    return func.coalesce(ListeningMention.published_at, ListeningMention.created_at)


async def _counts(session: AsyncSession, start: datetime, end: datetime) -> tuple[int, int]:
    base = select(func.count()).select_from(ListeningMention).where(
        ListeningMention.relevant.is_(True), when_col() >= start, when_col() < end)
    total = await session.scalar(base) or 0
    neg = await session.scalar(base.where(ListeningMention.sentiment == "negative")) or 0
    return int(total), int(neg)


async def detect_spikes(session: AsyncSession, org_id: uuid.UUID, settings: ListeningSettings,
                        *, now: datetime | None = None) -> list[dict]:
    """Compare the last 24 hours with the daily average of the 7 days before. Alerts at most every 12 hours per kind,
    and only once there are 3 days of history (the first run back-fills a week, which isn't a spike)."""
    now = now or now_utc()
    state = dict(settings.state or {})
    first = parse_time(state.get("first_run_at"))
    if first is None or now - first < timedelta(days=3):
        return []
    recent, recent_neg = await _counts(session, now - timedelta(days=1), now)
    base, base_neg = await _counts(session, now - timedelta(days=8), now - timedelta(days=1))
    avg = base / 7
    base_share = (base_neg / base) if base else 0.0
    found: list[dict] = []
    if recent >= max(5, 2.5 * avg):
        found.append({"kind": "volume", "title": f"Mentions are up: {recent} in the last 24 hours",
                      "body": f"That's about {recent / avg:.1f}× the usual {avg:.1f} a day." if avg >= 0.5 else
                      "That's far more than usual.", "link": "/listening"})
    share = recent_neg / recent if recent else 0.0
    if recent_neg >= 4 and share >= max(0.35, base_share + 0.2):
        found.append({"kind": "negative", "title": f"More negative mentions: {recent_neg} in the last 24 hours",
                      "body": f"{round(share * 100)}% of today's mentions are negative (usually {round(base_share * 100)}%).",
                      "link": "/listening?sentiment=negative"})
    spikes = dict(state.get("spikes") or {})
    sent = []
    from app.services import notify as notifier

    for s in found:
        last = parse_time(spikes.get(s["kind"]))
        if last and now - last < SPIKE_COOLDOWN:
            continue
        try:
            await notifier.notify_org(org_id, "listening_spike", s["title"], s["body"], link=s["link"],
                                      permission="listening.view")
            spikes[s["kind"]] = now.isoformat()
            sent.append(s)
        except Exception:  # noqa: BLE001
            log.exception("listening spike notification failed")
    if sent:
        state["spikes"] = spikes
        settings.state = state
        await session.commit()
    return sent


# ------------------------------------------------------------------ digest
def digest_due(settings: ListeningSettings, tz: str, now: datetime | None = None) -> bool:
    if settings.digest not in ("daily", "weekly"):
        return False
    now = now or now_utc()
    local = now.astimezone(ZoneInfo(tz or "Asia/Kolkata"))
    last = parse_time((settings.state or {}).get("last_digest_at"))
    if settings.digest == "daily":
        return local.hour >= DIGEST_HOUR and (last is None or now - last >= timedelta(hours=23))
    if last is not None and now - last >= timedelta(days=8):
        return True  # missed Monday (laptop off)
    return local.weekday() == 0 and local.hour >= DIGEST_HOUR and (last is None or now - last >= timedelta(days=6, hours=12))


async def send_digest(session: AsyncSession, org_id: uuid.UUID, *, force: bool = False) -> dict:
    from app.services import notify as notifier
    from app.services.listening import insights

    settings = await get_settings(session, org_id)
    org = await session.get(Organization, org_id)
    if not force and not digest_due(settings, org.timezone if org else "Asia/Kolkata"):
        return {"sent": False, "reason": "not_due"}
    days = 1 if settings.digest == "daily" else 7
    d = await insights.digest_data(session, days)
    period = "today" if days == 1 else "this week"
    lines = [f"{d['total']} mentions {period}" + (f" ({d['change_label']})" if d.get("change_label") else "") + ".",
             f"Positive {d['sentiment'].get('positive', 0)} · Neutral {d['sentiment'].get('neutral', 0)} · "
             f"Negative {d['sentiment'].get('negative', 0)}."]
    if d["needs_response"]:
        lines.append(f"{d['needs_response']} may need a reply from you.")
    if d["share_of_voice"]:
        lines.append("Share of voice: " + ", ".join(f"{r['name']} {r['share']}%" for r in d["share_of_voice"][:4]) + ".")
    summary = (settings.summary or {}).get("summary")
    gen = parse_time((settings.summary or {}).get("generated_at"))
    if summary and gen and now_utc() - gen <= timedelta(days=days + 1):
        lines += ["", "What people are saying: " + summary]
    if d["top"]:
        lines += ["", "Top mentions:"] + [f"• {m['title']} ({m['source']}) {m['url'] or ''}".strip() for m in d["top"][:5]]
    title = ("Listening: your daily summary" if days == 1 else "Listening: your weekly summary")
    if not d["total"] and not force:
        title += " (quiet)"
    await notifier.notify_org(org_id, "listening_digest", title, "\n".join(lines), link="/listening?tab=overview",
                              permission="listening.view")
    state = dict(settings.state or {})
    state["last_digest_at"] = now_utc().isoformat()
    settings.state = state
    await session.commit()
    return {"sent": True, "total": d["total"]}
