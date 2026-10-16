"""Occasions calendar: Indian national holidays and festivals, regional festivals, global awareness days,
industry/business days and seasons.

Where dates come from (never guessed silently; every occasion carries a `confidence`):
  fixed      same calendar date every year (Republic Day, Teachers' Day, Earth Day ...)
  rule       computed from a weekday rule (Mother's Day = 2nd Sunday of May ...)
  official   lunar/variable date from app/data/occasions_in.json, generated from the `holidays` package
             (Government of India holiday lists) for 2026–2028
  estimated  Islamic dates that depend on moon sighting (marked "(estimated)" in the source)
  derived    computed from an official date (Navratri starts nine days before Dussehra)
  verified   confirmed (or corrected) by web research during a research run
  holidays   a later year read from the `holidays` package at runtime when it is installed
  season     a recurring period (festive season, appraisal season ...)

Variable occasions outside the bundled years (and without the package) are left out rather than guessed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "occasions_in.json"
DATA_SOURCE = "Government of India holiday lists via the python-holidays package"

CATEGORIES = {
    "national": "National days",
    "festival": "Festivals",
    "regional": "Regional festivals",
    "global": "Global awareness days",
    "industry": "Business and industry days",
    "season": "Seasons",
}
# Indian states / UTs used for regional festivals (ISO 3166-2:IN codes without the prefix).
REGIONS = {
    "AS": "Assam", "BR": "Bihar", "DL": "Delhi", "GA": "Goa", "GJ": "Gujarat", "HR": "Haryana", "JH": "Jharkhand",
    "KA": "Karnataka", "KL": "Kerala", "MH": "Maharashtra", "OD": "Odisha", "PB": "Punjab", "TN": "Tamil Nadu",
    "TS": "Telangana", "AP": "Andhra Pradesh", "UP": "Uttar Pradesh", "RJ": "Rajasthan", "MP": "Madhya Pradesh",
    "WB": "West Bengal",
}

TONE_GUIDANCE = {
    "celebratory": "Warm, inclusive greeting. A light, tasteful link to the company is fine; no hard selling.",
    "patriotic": "Respectful national pride. Celebrate India's progress (for example India-built technology); no discounts or hard selling.",
    "commemorative": "A respectful tribute to the person or idea being remembered. No product promotion at all.",
    "solemn": "A solemn, respectful day. Only a short, sensitive message if anything; never promote products.",
    "awareness": "Share useful, credible insight on the cause. Connect to the company's expertise only where it genuinely helps.",
    "business": "Practical, helpful business content. Product tie-ins are welcome when they solve the reader's problem.",
}


@dataclass(frozen=True)
class OccasionDef:
    key: str
    name: str
    category: str
    tone: str = "celebratory"
    regions: tuple[str, ...] = ()  # empty = all of India / global
    month: int | None = None  # fixed date
    day: int | None = None
    rule: tuple[int, int, int] | None = None  # (month, weekday Mon=0, nth)
    season_end: tuple[int, int] | None = None  # (month, day) for seasons; month/day is the start
    hint: str = ""  # how a company like HRP AI can tie in tastefully


D = OccasionDef
CATALOG: dict[str, OccasionDef] = {d.key: d for d in [
    # ---- national
    D("republic_day", "Republic Day", "national", "patriotic", month=1, day=26,
      hint="Celebrate the Constitution and India's digital public infrastructure; 'Made in India' AI."),
    D("independence_day", "Independence Day", "national", "patriotic", month=8, day=15,
      hint="'Made in India AI' and self-reliant technology; freedom from manual HR paperwork."),
    D("gandhi_jayanti", "Gandhi Jayanti", "national", "commemorative", month=10, day=2,
      hint="Simplicity, truth and service; a quiet tribute. No promotion."),
    D("ambedkar_jayanti", "Dr. B. R. Ambedkar Jayanti", "national", "commemorative", month=4, day=14,
      hint="Equality and dignity at work; labour reforms. Tribute only."),
    D("national_youth_day", "National Youth Day", "national", "awareness", month=1, day=12,
      hint="Swami Vivekananda's birthday; youth skills and first jobs in AI."),
    D("childrens_day", "Children's Day", "national", "celebratory", month=11, day=14,
      hint="Curiosity and learning; the next generation of AI builders."),
    # ---- pan-India festivals (dates from the data file unless fixed)
    D("makar_sankranti", "Makar Sankranti", "festival", hint="New beginnings and harvest; a fresh start for HR processes."),
    D("vasant_panchami", "Vasant Panchami", "festival", hint="Learning and knowledge; learning and development at work."),
    D("maha_shivaratri", "Maha Shivaratri", "festival", "celebratory", hint="A respectful greeting only."),
    D("holi", "Holi", "festival", hint="Colour, diversity and teamwork; inclusive celebrations at work."),
    D("eid_al_fitr", "Eid al-Fitr", "festival", hint="Warm Eid wishes; festive advance or bonus payroll made simple."),
    D("ram_navami", "Ram Navami", "festival", hint="A respectful greeting."),
    D("mahavir_jayanti", "Mahavir Jayanti", "festival", "commemorative", hint="Non-violence and compassion; tribute only."),
    D("good_friday", "Good Friday", "festival", "solemn", hint="Solemn day: a brief, respectful message at most."),
    D("easter", "Easter", "festival", hint="Renewal and hope."),
    D("buddha_purnima", "Buddha Purnima", "festival", "commemorative", hint="Mindfulness and balance; wellbeing at work."),
    D("eid_al_adha", "Eid al-Adha (Bakrid)", "festival", hint="Warm wishes; sharing and generosity."),
    D("muharram", "Muharram", "festival", "solemn", hint="Solemn day: no promotional content."),
    D("milad_un_nabi", "Milad-un-Nabi", "festival", "commemorative", hint="A respectful greeting only."),
    D("raksha_bandhan", "Raksha Bandhan", "festival", hint="Bonds of trust and protection; data protection, trusted AI."),
    D("janmashtami", "Janmashtami", "festival", hint="A warm greeting; wisdom and guidance."),
    D("ganesh_chaturthi", "Ganesh Chaturthi", "festival", hint="Remover of obstacles; removing bottlenecks in HR and operations."),
    D("navratri", "Navratri", "festival", hint="Nine days of celebration; celebrating women leaders and teams."),
    D("dussehra", "Dussehra", "festival", hint="Victory of good over evil; winning over manual, error-prone processes."),
    D("diwali", "Diwali", "festival", hint="Festival of lights; Diwali bonus payroll made easy, a bright year ahead."),
    D("bhai_dooj", "Bhai Dooj", "festival", hint="A warm greeting."),
    D("guru_nanak_jayanti", "Guru Nanak Jayanti", "festival", "commemorative", hint="Equality, service and honest work."),
    D("christmas", "Christmas", "festival", month=12, day=25, hint="Season of giving; year-end gratitude to customers and teams."),
    # ---- regional festivals
    D("pongal", "Pongal", "regional", regions=("TN",), hint="Harvest and gratitude."),
    D("magh_bihu", "Magh Bihu", "regional", regions=("AS",), hint="Harvest celebration in Assam."),
    D("ugadi_gudi_padwa", "Ugadi / Gudi Padwa", "regional", regions=("KA", "AP", "TS", "MH", "GA"),
      hint="New Year in the Deccan; new beginnings and plans."),
    D("vaisakhi", "Vaisakhi", "regional", regions=("PB", "HR"), hint="Harvest festival; hard work and prosperity."),
    D("tamil_new_year", "Puthandu (Tamil New Year)", "regional", regions=("TN",), hint="New Year wishes."),
    D("vishu", "Vishu", "regional", regions=("KL",), hint="Kerala New Year."),
    D("bohag_bihu", "Bohag Bihu", "regional", regions=("AS",), hint="Assamese New Year."),
    D("rath_yatra", "Rath Yatra", "regional", regions=("OD",), hint="A respectful greeting."),
    D("parsi_new_year", "Parsi New Year (Navroz)", "regional", regions=("MH", "GJ"), hint="New Year wishes."),
    D("onam", "Onam", "regional", regions=("KL",), hint="Harvest and togetherness; teams that celebrate together."),
    D("durga_puja", "Durga Puja", "regional", regions=("WB", "AS", "OD", "BR", "JH"), hint="Strength and celebration."),
    D("karwa_chauth", "Karwa Chauth", "regional", regions=("PB", "HR", "DL", "RJ", "UP", "MP"), hint="A greeting only."),
    D("govardhan_puja", "Govardhan Puja", "regional", regions=("UP", "RJ", "MP", "HR", "DL"), hint="A greeting only."),
    D("chhath_puja", "Chhath Puja", "regional", regions=("BR", "JH", "UP"), hint="Gratitude to nature."),
    D("maharashtra_day", "Maharashtra Day", "regional", "patriotic", regions=("MH",), month=5, day=1),
    D("kannada_rajyotsava", "Karnataka Rajyotsava", "regional", "patriotic", regions=("KA",), month=11, day=1),
    # ---- global awareness days
    D("new_year", "New Year's Day", "global", month=1, day=1, hint="Fresh starts; HR and AI trends for the year."),
    D("new_years_eve", "New Year's Eve", "global", month=12, day=31, hint="Year in review and thanks."),
    D("intl_day_of_education", "International Day of Education", "global", "awareness", month=1, day=24,
      hint="Learning, upskilling and AI literacy."),
    D("data_privacy_day", "Data Privacy Day", "global", "awareness", month=1, day=28,
      hint="Employee data privacy, the DPDP Act and secure HR systems."),
    D("safer_internet_day", "Safer Internet Day", "global", "awareness", rule=(2, 1, 2),
      hint="Responsible AI and online safety at work."),
    D("womens_day", "International Women's Day", "global", "awareness", month=3, day=8,
      hint="Women in tech and AI, diversity hiring, inclusive workplaces."),
    D("world_health_day", "World Health Day", "global", "awareness", month=4, day=7, hint="Employee health and wellbeing."),
    D("earth_day", "Earth Day", "global", "awareness", month=4, day=22, hint="Paperless HR and sustainable operations."),
    D("labour_day", "International Labour Day", "global", "awareness", month=5, day=1,
      hint="Dignity of work, India's labour codes, fair and compliant payroll."),
    D("mothers_day", "Mother's Day", "global", rule=(5, 6, 2), hint="Working mothers, flexible work and leave policies."),
    D("world_environment_day", "World Environment Day", "global", "awareness", month=6, day=5, hint="Green, paperless workplaces."),
    D("fathers_day", "Father's Day", "global", rule=(6, 6, 3), hint="Working fathers and parental leave."),
    D("yoga_day", "International Day of Yoga", "global", "awareness", month=6, day=21, hint="Wellbeing and balance at work."),
    D("friendship_day", "Friendship Day", "global", rule=(8, 6, 1), hint="Workplace friendships and team culture."),
    D("youth_day_intl", "International Youth Day", "global", "awareness", month=8, day=12, hint="Youth skills and early careers."),
    D("mental_health_day", "World Mental Health Day", "global", "awareness", month=10, day=10,
      hint="Mental health at work, burnout signals HR analytics can surface."),
    D("mens_day", "International Men's Day", "global", "awareness", month=11, day=19, hint="Wellbeing and positive role models."),
    D("disability_day", "International Day of Persons with Disabilities", "global", "awareness", month=12, day=3,
      hint="Accessible workplaces and inclusive hiring."),
    D("human_rights_day", "Human Rights Day", "global", "awareness", month=12, day=10, hint="Fairness and dignity at work."),
    # ---- business and industry days
    D("startup_day", "National Startup Day (Startup India)", "industry", "business", month=1, day=16,
      hint="Startups scaling with AI; HRMS for fast-growing teams."),
    D("union_budget", "Union Budget day", "industry", "business", month=2, day=1,
      hint="What the Budget means for businesses, tax, payroll and AI."),
    D("national_science_day", "National Science Day", "industry", "awareness", month=2, day=28, hint="Science, research and AI in India."),
    D("employee_appreciation_day", "Employee Appreciation Day", "industry", "business", rule=(3, 4, 1),
      hint="Recognition programmes and engagement analytics."),
    D("fiscal_year_end", "Financial year-end", "industry", "business", month=3, day=31,
      hint="Year-end payroll, Form 16, compliance checklists, appraisal cycles."),
    D("new_financial_year", "New financial year", "industry", "business", month=4, day=1,
      hint="New salary structures, tax regime choices and fresh HR plans."),
    D("national_technology_day", "National Technology Day", "industry", "business", month=5, day=11,
      hint="India's technology achievements and home-grown AI."),
    D("intl_hr_day", "International HR Day", "industry", "business", month=5, day=20,
      hint="Thank HR teams; how AI gives HR time back."),
    D("world_productivity_day", "World Productivity Day", "industry", "business", month=6, day=20,
      hint="Productivity with automation, AI agents and analytics."),
    D("statistics_day", "National Statistics Day", "industry", "awareness", month=6, day=29, hint="Data-driven decisions, data lakehouse AI."),
    D("world_youth_skills_day", "World Youth Skills Day", "industry", "awareness", month=7, day=15, hint="Skilling and AI careers."),
    D("ai_appreciation_day", "AI Appreciation Day", "industry", "business", month=7, day=16,
      hint="Practical, responsible AI that businesses actually use."),
    D("itr_deadline", "Income tax return deadline", "industry", "business", month=7, day=31,
      hint="Help employees with Form 16 and tax declarations."),
    D("teachers_day", "Teachers' Day", "industry", "celebratory", month=9, day=5, hint="Mentors, learning and L&D at work."),
    D("engineers_day", "Engineers' Day", "industry", "celebratory", month=9, day=15,
      hint="Celebrate engineers building India's AI products."),
    # ---- seasons (start month/day → season_end)
    D("festive_season", "Festive season", "season", "business", month=10, day=1, season_end=(11, 15),
      hint="Festive bonuses, advances, shift planning and seasonal hiring."),
    D("wedding_season", "Wedding season", "season", "business", month=11, day=15, season_end=(2, 15),
      hint="Leave planning and attendance peaks."),
    D("exam_season", "Exam season", "season", "business", month=2, day=15, season_end=(4, 30),
      hint="Learning, focus and working parents."),
    D("appraisal_season", "Appraisal season", "season", "business", month=3, day=1, season_end=(4, 30),
      hint="Fair, data-driven performance reviews and increments."),
    D("monsoon", "Monsoon", "season", "business", month=6, day=15, season_end=(9, 15),
      hint="Remote work, attendance flexibility and business continuity."),
    D("campus_hiring_season", "Campus hiring season", "season", "business", month=8, day=1, season_end=(11, 30),
      hint="AI-assisted screening and onboarding for freshers."),
    D("cybersecurity_month", "Cybersecurity Awareness Month", "season", "awareness", month=10, day=1, season_end=(10, 31),
      hint="Secure HR data and responsible AI."),
    D("year_end", "Year-end", "season", "business", month=12, day=15, season_end=(12, 31),
      hint="Year in review, leave encashment, planning for next year."),
]}

DEFAULT_SETTINGS: dict = {
    "enabled": True,
    "categories": list(CATEGORIES),
    "regions": ["all"],
    "lead_days": 10,
    "greeting_posts": {
        "enabled": False, "account_ids": [], "time": "09:00", "mode": "approve_first", "post_type": "image",
        "categories": ["national", "festival"], "create_hours_before": 30,
    },
}


@dataclass
class Occasion:
    key: str
    name: str
    category: str
    date: date
    end_date: date | None = None
    tone: str = "celebratory"
    regions: list[str] = field(default_factory=list)
    confidence: str = "fixed"
    source: str = ""
    hint: str = ""
    note: str = ""

    @property
    def id(self) -> str:
        """Stable key per year, e.g. 'diwali:2026'. Used to schedule things once per occasion."""
        return f"{self.key}:{self.date.year}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        d["end_date"] = self.end_date.isoformat() if self.end_date else None
        d["id"] = self.id
        d["category_label"] = CATEGORIES.get(self.category, self.category)
        d["tone_guidance"] = TONE_GUIDANCE.get(self.tone, "")
        return d

    def brief_ref(self) -> dict:
        """Compact structure stored on idea briefs and post meta (used to pick festive media templates)."""
        return {"key": self.key, "id": self.id, "name": self.name, "date": self.date.isoformat(),
                "category": self.category, "tone": self.tone, "regions": self.regions, "confidence": self.confidence}


@lru_cache(maxsize=1)
def _table() -> dict:
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.exception("occasions data file is missing or invalid")
        return {"dates": {}, "years": []}


def bundled_years() -> list[int]:
    return [int(y) for y in _table().get("years") or []]


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


_PKG_NAMES = {  # names used by the `holidays` package, for years beyond the bundled table
    "diwali": "Diwali", "holi": "Holi", "dussehra": "Dussehra", "eid_al_fitr": "Id-ul-Fitr", "eid_al_adha": "Id-ul-Zuha",
    "janmashtami": "Janmashtami", "ganesh_chaturthi": "Ganesh Chaturthi", "raksha_bandhan": "Raksha Bandhan",
    "guru_nanak_jayanti": "Guru Nanak", "onam": "Onam", "makar_sankranti": "Makar Sankranti", "pongal": "Pongal",
    "good_friday": "Good Friday", "easter": "Easter", "muharram": "Muharram", "milad_un_nabi": "Milad-un-Nabi",
    "buddha_purnima": "Buddha Purnima", "mahavir_jayanti": "Mahavir Jayanti", "ram_navami": "Ram Navami",
    "maha_shivaratri": "Maha Shivaratri", "vaisakhi": "Vaisakhi", "chhath_puja": "Chhath Puja",
}


@lru_cache(maxsize=8)
def _package_year(year: int) -> dict[str, tuple[str, str]]:
    """{key: (iso date, confidence)} from the `holidays` package when installed (optional)."""
    try:
        import holidays  # type: ignore[import-not-found]
    except ImportError:
        return {}
    try:
        h = holidays.country_holidays("IN", years=[year], categories=("public", "optional"))
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, tuple[str, str]] = {}
    for d, names in sorted(h.items()):
        for n in str(names).split("; "):
            base = n.replace(" (estimated)", "")
            for key, sub in _PKG_NAMES.items():
                if key in out:
                    continue
                if (key in ("holi", "dussehra") and base != sub) or sub not in base:
                    continue
                out[key] = (d.isoformat(), "estimated" if "estimated" in n else "holidays")
    return out


def _variable_date(key: str, year: int) -> tuple[date, str, str] | None:
    rec = (_table().get("dates") or {}).get(key, {}).get(str(year))
    if rec:
        return date.fromisoformat(rec["date"]), rec.get("confidence", "official"), DATA_SOURCE
    pkg = _package_year(year)
    if key in pkg:
        d, conf = pkg[key]
        return date.fromisoformat(d), conf, "python-holidays (installed package)"
    if key == "navratri":
        dus = _variable_date("dussehra", year)
        if dus:
            return dus[0] - timedelta(days=9), "derived", "Dussehra minus nine days"
    return None


def occasions_for_year(year: int, verified: dict | None = None) -> list[Occasion]:
    verified = verified or {}
    out: list[Occasion] = []
    for d in CATALOG.values():
        end = None
        if d.rule:
            when, conf, src = _nth_weekday(year, *d.rule), "rule", "Weekday rule"
        elif d.month and d.day:
            when, conf, src = date(year, d.month, d.day), "fixed", "Fixed calendar date"
            if d.season_end:
                ey = year + (1 if (d.season_end[0], d.season_end[1]) < (d.month, d.day) else 0)
                end, conf, src = date(ey, *d.season_end), "season", "Recurring season"
        else:
            found = _variable_date(d.key, year)
            if found is None:
                continue
            when, conf, src = found
        occ = Occasion(key=d.key, name=d.name, category=d.category, date=when, end_date=end, tone=d.tone,
                       regions=list(d.regions), confidence=conf, source=src, hint=d.hint)
        v = verified.get(occ.id)
        if isinstance(v, dict) and v.get("date"):
            try:
                vd = date.fromisoformat(str(v["date"]))
            except ValueError:
                vd = None
            if vd is not None:
                if vd != occ.date:
                    occ.note = f"Calendar said {occ.date.isoformat()}; web sources say {vd.isoformat()}."
                    occ.date = vd
                occ.confidence = "verified"
                occ.source = v.get("source_url") or occ.source
        out.append(occ)
    return out


def occasions_between(start: date, end: date, verified: dict | None = None) -> list[Occasion]:
    """Occasions whose (start) date falls in [start, end], sorted by date."""
    out: list[Occasion] = []
    for y in range(start.year - 1, end.year + 1):
        out += [o for o in occasions_for_year(y, verified) if start <= o.date <= end]
    seen: set[str] = set()
    uniq = []
    for o in sorted(out, key=lambda o: (o.date, o.category != "national", o.name)):
        if o.id not in seen:
            seen.add(o.id)
            uniq.append(o)
    return uniq


def merged_settings(cfg: dict | None) -> dict:
    cfg = cfg or {}
    out = {**DEFAULT_SETTINGS, **{k: v for k, v in cfg.items() if k != "greeting_posts"}}
    out["greeting_posts"] = {**DEFAULT_SETTINGS["greeting_posts"], **(cfg.get("greeting_posts") or {})}
    return out


def matches_settings(o: Occasion, cfg: dict) -> bool:
    cfg = merged_settings(cfg)
    if not cfg.get("enabled", True):
        return False
    if o.category not in (cfg.get("categories") or []):
        return False
    regions = [r for r in (cfg.get("regions") or ["all"]) if r]
    if o.regions and "all" not in regions and not set(o.regions) & set(regions):
        return False
    return True


def upcoming(today: date, cfg: dict | None, verified: dict | None = None, days: int | None = None) -> list[Occasion]:
    """Occasions observed by this organization from today up to the lead time (or `days`)."""
    cfg = merged_settings(cfg)
    horizon = days if days is not None else int(cfg.get("lead_days") or 10)
    return [o for o in occasions_between(today, today + timedelta(days=max(0, horizon)), verified)
            if matches_settings(o, cfg)]


def needs_verification(o: Occasion) -> bool:
    return o.confidence in ("estimated", "derived", "holidays")


def verification_query(o: Occasion) -> str:
    return f"{o.name} {o.date.year} date India"


def find(key_or_id: str, year: int | None = None, verified: dict | None = None) -> Occasion | None:
    key, _, y = key_or_id.partition(":")
    year = int(y) if y.isdigit() else year or date.today().year
    return next((o for o in occasions_for_year(year, verified) if o.key == key), None)
