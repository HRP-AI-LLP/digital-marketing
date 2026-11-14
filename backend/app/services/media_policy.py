"""What generated images and videos may show (Brand → Visuals).

people:
  none  (default) no people at all: AI prompts forbid them, stock searches drop people words and results described with
        people, and every stock photo, stock clip and AI image is checked for faces and people before it is used.
  team  the same, except that a scene calling for people gets a NEW AI photo of one of your team members, made
        from their team photo (media marked "Team photo" in the Media library, uploaded with consent): the face is
        kept, pose, clothes and setting are new. The uploaded photo itself is never put in a post.
  allow stock and AI may show people (AI never shows a real person's likeness).

demos: products with their demo screenshots/recordings (see services/demos.py), used for posts about that product.
news_share: about how many posts in a hundred are about current technology and AI industry news.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("media_policy")

PEOPLE_MODES = ("none", "team", "allow")
DEFAULT_NEWS_SHARE = 20

PEOPLE_WORDS = (
    r"people|person|persons|human|humans|man|men|woman|women|girl|girls|boy|boys|guy|guys|lady|ladies|child|children|"
    r"kid|kids|baby|face|faces|portrait|selfie|smil\w*|team ?mates?|team members?|staff members?|new hires?|colleagues?|coworkers?|co-workers?|employees?|staff|"
    r"workers?|businessm[ae]n|businesswom[ae]n|entrepreneurs?|executives?|manager|managers|ceo|founders?|students?|"
    r"teachers?|doctors?|nurses?|engineers?|developers?|programmers?|designers?|customers?|clients?|audience|crowd|"
    r"family|couple|friends?|hands?|fingers?|handshake|meeting|interview|presenter|speaker|model|models|actor|actress|"
    r"influencer|youtuber|user|users|candidates?|recruiters?|hr manager|hiring manager|freelancers?|someone|somebody"
)
_PEOPLE_RX = re.compile(rf"\b(?:{PEOPLE_WORDS})\b", re.I)

AI_RULE = ("Show no people at all: no human figures, faces, hands, body parts, silhouettes, reflections or crowds. "
           "Show technology, devices, screens, dashboards, objects, places, architecture, nature, data or abstract shapes instead.")
NEGATIVE = ("people, person, human, man, woman, child, face, faces, portrait, head, eyes, hands, fingers, arms, body, "
            "silhouette, crowd, figure, mannequin")
STOCK_SAFE = "technology"  # what to search for when nothing is left of the query


@dataclass
class Demo:
    product: str
    keywords: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    asset_ids: list[str] = field(default_factory=list)  # uploads and captures

    def matches(self, text: str) -> bool:
        words = [self.product, *self.keywords]
        return any(w and re.search(rf"\b{re.escape(w.strip())}\b", text, re.I) for w in words)


@dataclass
class Policy:
    people: str = "none"
    news_share: int = DEFAULT_NEWS_SHARE
    demos: list[Demo] = field(default_factory=list)

    @property
    def no_people(self) -> bool:
        return self.people != "allow"


def from_settings(settings: dict | None) -> Policy:
    m = (settings or {}).get("media") or {}
    people = m.get("people") if m.get("people") in PEOPLE_MODES else "none"
    try:
        share = max(0, min(60, int(m.get("news_share", DEFAULT_NEWS_SHARE))))
    except (TypeError, ValueError):
        share = DEFAULT_NEWS_SHARE
    demos = []
    for d in m.get("demos") or []:
        if isinstance(d, dict) and str(d.get("product") or "").strip():
            demos.append(Demo(product=str(d["product"]).strip()[:80],
                              keywords=[str(k).strip()[:60] for k in d.get("keywords") or [] if str(k).strip()][:12],
                              urls=[str(u).strip()[:500] for u in d.get("urls") or [] if str(u).strip()][:6],
                              asset_ids=[str(a) for a in d.get("asset_ids") or []][:40]))
    return Policy(people=people, news_share=share, demos=demos)


def to_settings(p: Policy) -> dict:
    return {"people": p.people, "news_share": p.news_share,
            "demos": [{"product": d.product, "keywords": d.keywords, "urls": d.urls, "asset_ids": d.asset_ids} for d in p.demos]}


async def get(session: AsyncSession) -> Policy:
    from app.models import BrandProfile

    b = await session.scalar(select(BrandProfile).limit(1))
    return from_settings(b.settings if b else None)


# ----------------------------------------------------------------------------- text
def mentions_people(text: str) -> bool:
    return bool(_PEOPLE_RX.search(text or ""))


def strip_people(query: str) -> str:
    """'HR manager reviewing payroll on laptop' -> 'reviewing payroll on laptop' (for stock searches)."""
    q = _PEOPLE_RX.sub(" ", query or "")
    q = re.sub(r"\b(?:a|an|the|with|and|of|by|for|his|her|their|our)\b(?=\s*(?:\b(?:a|an|the|with|and|of|by|for)\b|$))", " ", q, flags=re.I)
    q = re.sub(r"\s+", " ", q).strip(" ,.-")
    return q or STOCK_SAFE


def describes_people(item: dict) -> bool:
    """A stock result whose description, tags or page address mention people."""
    text = " ".join(str(item.get(k) or "") for k in ("alt", "page_url", "title"))
    text = re.sub(r"[-_/]", " ", text)
    return mentions_people(text)


# ----------------------------------------------------------------------------- pixels
_DETECTORS: dict = {}


def _detectors():
    if "ok" not in _DETECTORS:
        try:
            import cv2

            base = Path(cv2.data.haarcascades)
            _DETECTORS["front"] = cv2.CascadeClassifier(str(base / "haarcascade_frontalface_default.xml"))
            _DETECTORS["alt"] = cv2.CascadeClassifier(str(base / "haarcascade_frontalface_alt2.xml"))
            _DETECTORS["profile"] = cv2.CascadeClassifier(str(base / "haarcascade_profileface.xml"))
            hog = cv2.HOGDescriptor()
            hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            _DETECTORS["hog"] = hog
            _DETECTORS["ok"] = True
        except Exception as e:  # noqa: BLE001 - without OpenCV only the text checks apply
            log.warning("people check unavailable: %s", e)
            _DETECTORS["ok"] = False
    return _DETECTORS if _DETECTORS["ok"] else None


def _frame_has_people(img) -> bool:
    """img: BGR numpy array. Faces (front or side, confirmed by two detectors) or a clear full-body figure."""
    import cv2

    d = _detectors()
    if d is None:
        return False
    h, w = img.shape[:2]
    scale = 720 / max(h, w) if max(h, w) > 720 else 1.0
    if scale != 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
    gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    side = min(gray.shape[:2])
    min_face = (max(24, side // 18),) * 2
    faces = d["front"].detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=min_face)
    if len(faces):
        # Haar sees faces in some textures: confirm with a second model
        if len(d["alt"].detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=min_face)):
            return True
    for g in (gray, cv2.flip(gray, 1)):
        if len(d["profile"].detectMultiScale(g, scaleFactor=1.1, minNeighbors=8, minSize=(max(32, side // 12),) * 2)):
            return True
    rects, weights = d["hog"].detectMultiScale(img, winStride=(8, 8), padding=(8, 8), scale=1.06)
    return any(float(wt) > 1.0 and rh > gray.shape[0] * 0.25 for (_, _, _, rh), wt in zip(rects, weights))


def image_has_people(data: bytes) -> bool:
    try:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        return img is not None and _frame_has_people(img)
    except Exception as e:  # noqa: BLE001
        log.info("people check failed: %s", e)
        return False


def video_has_people(path: Path, samples: int = 5) -> bool:
    try:
        import cv2

        cap = cv2.VideoCapture(str(path))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        found = False
        for k in range(samples):
            if n:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * (k + 0.5) / samples))
            ok, frame = cap.read()
            if ok and _frame_has_people(frame):
                found = True
                break
        cap.release()
        return found
    except Exception as e:  # noqa: BLE001
        log.info("people check failed: %s", e)
        return False


async def file_has_people(path: Path, kind: str) -> bool:
    if kind == "video":
        return await asyncio.to_thread(video_has_people, path)
    return await asyncio.to_thread(image_has_people, await asyncio.to_thread(path.read_bytes))


# ----------------------------------------------------------------------------- team photos
async def team_photo(session: AsyncSession, seed: str = ""):
    """A random 'Team photo' from the Media library (or None)."""
    from app.models import MediaAsset

    rows = list((await session.scalars(select(MediaAsset).where(MediaAsset.kind == "image")
                                          .order_by(MediaAsset.created_at.desc()).limit(400))).all())
    team = [a for a in rows if (a.meta or {}).get("team")]
    if not team:
        return None
    return random.Random(seed or None).choice(team)
