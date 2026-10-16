"""Regenerates occasions_in.json from the `holidays` package (not an app dependency).

Usage: pip install holidays && python app/data/gen_occasions_in.py > app/data/occasions_in.json
Add the next year to the loop when extending coverage.
"""
import json, sys, datetime as dt
import holidays
MAP = [  # (key, substring in python-holidays name)
 ("makar_sankranti","Makar Sankranti"),("pongal","Pongal"),("magh_bihu","Magh Bihu"),("vasant_panchami","Basant Panchami"),
 ("maha_shivaratri","Maha Shivaratri"),("holi","Holi"),("ugadi_gudi_padwa","Gudi Padwa"),("eid_al_fitr","Id-ul-Fitr"),
 ("ram_navami","Ram Navami"),("mahavir_jayanti","Mahavir Jayanti"),("good_friday","Good Friday"),("easter","Easter Sunday"),
 ("vaisakhi","Vaisakhi"),("vishu","Vishu"),("bohag_bihu","Bahag Bihu"),("tamil_new_year","Meshadi"),
 ("buddha_purnima","Buddha Purnima"),("eid_al_adha","Id-ul-Zuha"),("muharram","Muharram"),("rath_yatra","Rath Yatra"),
 ("parsi_new_year","Parsi New Year"),("onam","Onam"),("milad_un_nabi","Milad-un-Nabi"),("raksha_bandhan","Raksha Bandhan"),
 ("janmashtami","Janmashtami"),("ganesh_chaturthi","Ganesh Chaturthi"),("durga_puja","Dussehra (Saptami)"),
 ("dussehra","Dussehra"),("karwa_chauth","Karwa Chouth"),("diwali","Diwali"),("govardhan_puja","Govardhan Puja"),
 ("bhai_dooj","Bhai Duj"),("chhath_puja","Chhath Puja"),("guru_nanak_jayanti","Guru Nanak's Jayanti"),
 ("guru_gobind_singh_jayanti","Guru Gobind Singh's Jayanti"),
]
out = {}
for y in (2026, 2027, 2028):
    h = holidays.country_holidays("IN", years=[y], categories=("public", "optional"))
    for d, names in sorted(h.items()):
        for n in names.split("; "):
            for key, sub in MAP:
                base = n.replace(" (estimated)", "")
                if (key in ("holi", "dussehra") and base != sub) or sub not in n:
                    continue
                if True:
                    rec = {"date": d.isoformat(), "confidence": "estimated" if "estimated" in n else "official", "source_name": n}
                    out.setdefault(key, {}).setdefault(str(y), rec)
    # Navratri (Sharad) starts nine days before Dussehra (Vijayadashami).
    dus = out.get("dussehra", {}).get(str(y))
    if dus:
        start = dt.date.fromisoformat(dus["date"]) - dt.timedelta(days=9)
        out.setdefault("navratri", {})[str(y)] = {"date": start.isoformat(), "confidence": "derived",
                                                   "source_name": "Dussehra minus 9 days"}
json.dump({"source": "python-holidays 0.106 (India, public + optional categories; Government of India holiday lists). "
           "Islamic dates marked 'estimated' depend on moon sighting.",
           "generated_on": dt.date.today().isoformat(), "years": [2026, 2027, 2028], "dates": out},
          sys.stdout, indent=1, sort_keys=True)
