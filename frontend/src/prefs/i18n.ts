// Any-language interface. The page is written in English; when another language is chosen, every piece of visible
// interface text (and placeholders, tooltips, accessible labels) is swapped for its translation. Translations come
// from the server, which caches them for everyone and asks the organization's AI model for anything new, so any
// language works: international, Indian national and regional languages, and dialects ("Other language…").
//
// Left untouched: anything inside [translate="no"] / .notranslate (brand names, people's own posts), form fields
// people type into, code, numbers, links and email addresses.
import { api } from "../api/client";

export type Lang = { code: string; name: string; native: string; script?: Script; rtl?: boolean; group: "india" | "world" };
type Script = "deva" | "beng" | "taml" | "telu" | "knda" | "mlym" | "gujr" | "guru" | "orya" | "olck" | "mtei" | "arab" | "urdu"
  | "hebr" | "thai" | "sinh" | "mymr" | "khmr" | "laoo" | "ethi" | "geor" | "armn" | "tibt" | "sc" | "tc" | "jp" | "kr";

const SCRIPT_FONT: Record<Script, [string, string]> = {
  deva: ["Noto Sans Devanagari", "Noto+Sans+Devanagari:wght@400;500;600;700"],
  beng: ["Noto Sans Bengali", "Noto+Sans+Bengali:wght@400;500;600;700"],
  taml: ["Noto Sans Tamil", "Noto+Sans+Tamil:wght@400;500;600;700"],
  telu: ["Noto Sans Telugu", "Noto+Sans+Telugu:wght@400;500;600;700"],
  knda: ["Noto Sans Kannada", "Noto+Sans+Kannada:wght@400;500;600;700"],
  mlym: ["Noto Sans Malayalam", "Noto+Sans+Malayalam:wght@400;500;600;700"],
  gujr: ["Noto Sans Gujarati", "Noto+Sans+Gujarati:wght@400;500;600;700"],
  guru: ["Noto Sans Gurmukhi", "Noto+Sans+Gurmukhi:wght@400;500;600;700"],
  orya: ["Noto Sans Oriya", "Noto+Sans+Oriya:wght@400;500;600;700"],
  olck: ["Noto Sans Ol Chiki", "Noto+Sans+Ol+Chiki:wght@400;500;600;700"],
  mtei: ["Noto Sans Meetei Mayek", "Noto+Sans+Meetei+Mayek:wght@400;500;600;700"],
  arab: ["Noto Naskh Arabic", "Noto+Naskh+Arabic:wght@400;500;600;700"],
  urdu: ["Noto Nastaliq Urdu", "Noto+Nastaliq+Urdu:wght@400;500;600;700"],
  hebr: ["Noto Sans Hebrew", "Noto+Sans+Hebrew:wght@400;500;600;700"],
  thai: ["Noto Sans Thai", "Noto+Sans+Thai:wght@400;500;600;700"],
  sinh: ["Noto Sans Sinhala", "Noto+Sans+Sinhala:wght@400;500;600;700"],
  mymr: ["Noto Sans Myanmar", "Noto+Sans+Myanmar:wght@400;500;600;700"],
  khmr: ["Noto Sans Khmer", "Noto+Sans+Khmer:wght@400;500;600;700"],
  laoo: ["Noto Sans Lao", "Noto+Sans+Lao:wght@400;500;600;700"],
  ethi: ["Noto Sans Ethiopic", "Noto+Sans+Ethiopic:wght@400;500;600;700"],
  geor: ["Noto Sans Georgian", "Noto+Sans+Georgian:wght@400;500;600;700"],
  armn: ["Noto Sans Armenian", "Noto+Sans+Armenian:wght@400;500;600;700"],
  tibt: ["Noto Serif Tibetan", "Noto+Serif+Tibetan:wght@400;500;600;700"],
  sc: ["Noto Sans SC", "Noto+Sans+SC:wght@400;500;700"],
  tc: ["Noto Sans TC", "Noto+Sans+TC:wght@400;500;700"],
  jp: ["Noto Sans JP", "Noto+Sans+JP:wght@400;500;700"],
  kr: ["Noto Sans KR", "Noto+Sans+KR:wght@400;500;700"],
};

const L = (group: Lang["group"], code: string, name: string, native: string, script?: Script, rtl?: boolean): Lang => ({ group, code, name, native, script, rtl });

/** Indian scheduled languages, widely spoken regional languages and dialects, then world languages. */
export const LANGUAGES: Lang[] = [
  L("world", "en", "English", "English"),
  // India: the 22 scheduled languages
  L("india", "hi", "Hindi", "हिन्दी", "deva"), L("india", "bn", "Bengali", "বাংলা", "beng"), L("india", "te", "Telugu", "తెలుగు", "telu"),
  L("india", "mr", "Marathi", "मराठी", "deva"), L("india", "ta", "Tamil", "தமிழ்", "taml"), L("india", "ur", "Urdu", "اردو", "urdu", true),
  L("india", "gu", "Gujarati", "ગુજરાતી", "gujr"), L("india", "kn", "Kannada", "ಕನ್ನಡ", "knda"), L("india", "or", "Odia", "ଓଡ଼ିଆ", "orya"),
  L("india", "ml", "Malayalam", "മലയാളം", "mlym"), L("india", "pa", "Punjabi", "ਪੰਜਾਬੀ", "guru"), L("india", "as", "Assamese", "অসমীয়া", "beng"),
  L("india", "mai", "Maithili", "मैथिली", "deva"), L("india", "sat", "Santali", "ᱥᱟᱱᱛᱟᱲᱤ", "olck"), L("india", "ks", "Kashmiri", "کٲشُر", "arab", true),
  L("india", "ne", "Nepali", "नेपाली", "deva"), L("india", "sd", "Sindhi", "سنڌي", "arab", true), L("india", "kok", "Konkani", "कोंकणी", "deva"),
  L("india", "doi", "Dogri", "डोगरी", "deva"), L("india", "mni", "Manipuri (Meitei)", "ꯃꯤꯇꯩꯂꯣꯟ", "mtei"), L("india", "brx", "Bodo", "बड़ो", "deva"),
  L("india", "sa", "Sanskrit", "संस्कृतम्", "deva"),
  // India: regional languages and dialects
  L("india", "bho", "Bhojpuri", "भोजपुरी", "deva"), L("india", "awa", "Awadhi", "अवधी", "deva"), L("india", "raj", "Rajasthani", "राजस्थानी", "deva"),
  L("india", "mwr", "Marwari", "मारवाड़ी", "deva"), L("india", "bgc", "Haryanvi", "हरियाणवी", "deva"), L("india", "hne", "Chhattisgarhi", "छत्तीसगढ़ी", "deva"),
  L("india", "mag", "Magahi", "मगही", "deva"), L("india", "anp", "Angika", "अंगिका", "deva"), L("india", "bns", "Bundeli", "बुन्देली", "deva"),
  L("india", "bra", "Braj", "ब्रज भाषा", "deva"), L("india", "gbm", "Garhwali", "गढ़वाली", "deva"), L("india", "kfy", "Kumaoni", "कुमाऊँनी", "deva"),
  L("india", "him", "Himachali (Pahari)", "पहाड़ी", "deva"), L("india", "gon", "Gondi", "गोंडी", "deva"), L("india", "bhb", "Bhili", "भीली", "deva"),
  L("india", "tcy", "Tulu", "ತುಳು", "knda"), L("india", "kfa", "Kodava", "ಕೊಡವ", "knda"), L("india", "sck", "Sadri (Nagpuri)", "सादरी", "deva"),
  L("india", "kha", "Khasi", "Khasi"), L("india", "lus", "Mizo", "Mizo ṭawng"), L("india", "grt", "Garo", "A·chik"),
  L("india", "lbj", "Ladakhi", "ལ་དྭགས་སྐད", "tibt"), L("india", "hi-Latn", "Hinglish (Hindi in English letters)", "Hinglish"),
  // World
  L("world", "es", "Spanish", "Español"), L("world", "fr", "French", "Français"), L("world", "de", "German", "Deutsch"),
  L("world", "pt-BR", "Portuguese (Brazil)", "Português (Brasil)"), L("world", "pt-PT", "Portuguese (Portugal)", "Português"),
  L("world", "it", "Italian", "Italiano"), L("world", "nl", "Dutch", "Nederlands"), L("world", "ru", "Russian", "Русский"),
  L("world", "uk", "Ukrainian", "Українська"), L("world", "pl", "Polish", "Polski"), L("world", "tr", "Turkish", "Türkçe"),
  L("world", "ar", "Arabic", "العربية", "arab", true), L("world", "fa", "Persian", "فارسی", "arab", true), L("world", "he", "Hebrew", "עברית", "hebr", true),
  L("world", "ps", "Pashto", "پښتو", "arab", true), L("world", "zh-CN", "Chinese (Simplified)", "简体中文", "sc"),
  L("world", "zh-TW", "Chinese (Traditional)", "繁體中文", "tc"), L("world", "ja", "Japanese", "日本語", "jp"), L("world", "ko", "Korean", "한국어", "kr"),
  L("world", "id", "Indonesian", "Bahasa Indonesia"), L("world", "ms", "Malay", "Bahasa Melayu"), L("world", "fil", "Filipino", "Filipino"),
  L("world", "vi", "Vietnamese", "Tiếng Việt"), L("world", "th", "Thai", "ไทย", "thai"), L("world", "si", "Sinhala", "සිංහල", "sinh"),
  L("world", "my", "Burmese", "မြန်မာ", "mymr"), L("world", "km", "Khmer", "ខ្មែរ", "khmr"), L("world", "lo", "Lao", "ລາວ", "laoo"),
  L("world", "bo", "Tibetan", "བོད་སྐད", "tibt"), L("world", "dz", "Dzongkha", "རྫོང་ཁ", "tibt"), L("world", "sw", "Swahili", "Kiswahili"),
  L("world", "am", "Amharic", "አማርኛ", "ethi"), L("world", "ha", "Hausa", "Hausa"), L("world", "yo", "Yoruba", "Yorùbá"),
  L("world", "ig", "Igbo", "Igbo"), L("world", "zu", "Zulu", "isiZulu"), L("world", "af", "Afrikaans", "Afrikaans"),
  L("world", "el", "Greek", "Ελληνικά"), L("world", "cs", "Czech", "Čeština"), L("world", "sk", "Slovak", "Slovenčina"),
  L("world", "hu", "Hungarian", "Magyar"), L("world", "ro", "Romanian", "Română"), L("world", "bg", "Bulgarian", "Български"),
  L("world", "sr", "Serbian", "Српски"), L("world", "hr", "Croatian", "Hrvatski"), L("world", "sl", "Slovenian", "Slovenščina"),
  L("world", "sv", "Swedish", "Svenska"), L("world", "no", "Norwegian", "Norsk"), L("world", "da", "Danish", "Dansk"),
  L("world", "fi", "Finnish", "Suomi"), L("world", "et", "Estonian", "Eesti"), L("world", "lv", "Latvian", "Latviešu"),
  L("world", "lt", "Lithuanian", "Lietuvių"), L("world", "ka", "Georgian", "ქართული", "geor"), L("world", "hy", "Armenian", "Հայերեն", "armn"),
  L("world", "az", "Azerbaijani", "Azərbaycan"), L("world", "kk", "Kazakh", "Қазақ"), L("world", "uz", "Uzbek", "Oʻzbek"),
  L("world", "mn", "Mongolian", "Монгол"),
];

export function findLang(code?: string | null): Lang | null {
  if (!code) return null;
  return LANGUAGES.find((l) => l.code.toLowerCase() === code.toLowerCase()) ?? null;
}

/** For a language typed by hand ("Haryanvi", "Sylheti"), guess nothing: plain name, left-to-right, default fonts. */
export function langInfo(code?: string | null, name?: string | null): Lang {
  return findLang(code) ?? { code: code || "en", name: name || code || "English", native: name || code || "English", group: "world" };
}

export function scriptFont(l: Lang): string {
  if (!l.script) return "";
  const [family, google] = SCRIPT_FONT[l.script];
  const id = `gf-${family.replace(/ /g, "-")}`;
  if (!document.getElementById(id)) {
    const link = document.createElement("link");
    link.id = id; link.rel = "stylesheet";
    link.href = `https://fonts.googleapis.com/css2?family=${google}&display=swap`;
    document.head.appendChild(link);
  }
  return `"${family}"`;
}

// ------------------------------------------------------------------ the translator
const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "CODE", "PRE", "TEXTAREA", "KBD", "SAMP", "svg-ignore"]);
const ATTRS = ["placeholder", "title", "aria-label"] as const;
const LETTERS = /[A-Za-z]{2,}/;
const LOOKS_RAW = /^(https?:\/\/|www\.|[\w.+-]+@[\w-]+\.[\w.]+$|[#@/][\w./-]*$|[\w-]+\.(png|jpe?g|pdf|csv|mp4|svg|json|tar)$)/i;
const MAX = 400;

type Rec = { src: string; shown: string };
type Listener = (s: { pending: number; error: string | null }) => void;

class Translator {
  lang: Lang | null = null;
  private dict = new Map<string, string>();
  private tried = new Set<string>();
  private queue = new Set<string>();
  private texts = new WeakMap<Text, Rec>();
  private attrs = new WeakMap<Element, Record<string, Rec>>();
  private obs: MutationObserver | null = null;
  private timer: number | null = null;
  private scanTimer: number | null = null;
  private inflight = 0;
  private error: string | null = null;
  private listeners = new Set<Listener>();
  signedIn = false;

  subscribe(l: Listener) { this.listeners.add(l); l(this.state()); return () => { this.listeners.delete(l); }; }
  private state() { return { pending: this.queue.size + this.inflight, error: this.error }; }
  private emit() { const s = this.state(); this.listeners.forEach((l) => l(s)); }

  start(lang: Lang) {
    if (this.lang?.code === lang.code && this.obs) return;
    this.stop(false);
    this.lang = lang;
    this.dict = new Map(Object.entries(this.loadCache(lang.code)));
    this.tried.clear();
    const root = document.documentElement;
    root.lang = lang.code;
    root.dir = lang.rtl ? "rtl" : "ltr";
    this.obs = new MutationObserver(() => this.scheduleScan());
    this.obs.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: [...ATTRS] });
    this.scan();
  }

  /** Back to English. Reload is the reliable way to put every original string back. */
  stop(reload = true) {
    this.obs?.disconnect();
    this.obs = null;
    const was = this.lang;
    this.lang = null;
    document.documentElement.lang = "en";
    document.documentElement.dir = "ltr";
    if (reload && was) window.location.reload();
  }

  private key(code: string) { return `dm.i18n.${code.toLowerCase()}`; }
  private loadCache(code: string): Record<string, string> {
    try { return JSON.parse(localStorage.getItem(this.key(code)) || "{}"); } catch { return {}; }
  }
  private saveCache() {
    if (!this.lang) return;
    try { localStorage.setItem(this.key(this.lang.code), JSON.stringify(Object.fromEntries(this.dict))); } catch { /* full or private */ }
  }

  private scheduleScan() {
    if (this.scanTimer != null) return;
    this.scanTimer = window.setTimeout(() => { this.scanTimer = null; this.scan(); }, 60);
  }

  private skip(el: Element | null): boolean {
    for (let e = el; e; e = e.parentElement) {
      if (SKIP_TAGS.has(e.tagName)) return true;
      if (e.getAttribute("translate") === "no" || e.classList.contains("notranslate") || (e as HTMLElement).isContentEditable) return true;
    }
    return false;
  }

  private wanted(t: string) {
    const s = t.trim();
    return s.length > 1 && s.length <= MAX && LETTERS.test(s) && !LOOKS_RAW.test(s);
  }

  private translate(src: string): string | null {
    const core = src.trim();
    const tr = this.dict.get(core);
    if (tr == null) {
      if (!this.tried.has(core)) this.queue.add(core);
      return null;
    }
    const lead = src.slice(0, src.length - src.trimStart().length);
    const trail = src.slice(src.trimEnd().length);
    return lead + tr + trail;
  }

  scan() {
    if (!this.lang || !document.body) return;
    const obs = this.obs;
    obs?.disconnect();
    try {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode() as Text | null; n; n = walker.nextNode() as Text | null) {
        const cur = n.nodeValue ?? "";
        const rec = this.texts.get(n);
        if (rec && cur === rec.shown) {
          if (rec.shown === rec.src) { const tr = this.dict.get(rec.src.trim()); if (tr != null) { const v = this.translate(rec.src)!; n.nodeValue = v; rec.shown = v; } }
          continue;
        }
        if (!this.wanted(cur) || this.skip(n.parentElement)) continue;
        const next = { src: cur, shown: cur };
        const tr = this.translate(cur);
        if (tr != null) { n.nodeValue = tr; next.shown = tr; }
        this.texts.set(n, next);
      }
      const els = document.body.querySelectorAll("[placeholder],[title],[aria-label]");
      els.forEach((el) => {
        if (this.skip(el)) return;
        const recs = this.attrs.get(el) ?? {};
        for (const a of ATTRS) {
          const cur = el.getAttribute(a);
          if (cur == null) continue;
          const rec = recs[a];
          if (rec && cur === rec.shown) {
            if (rec.shown === rec.src) { const tr = this.translate(rec.src); if (tr != null) { el.setAttribute(a, tr); rec.shown = tr; } }
            continue;
          }
          if (!this.wanted(cur)) continue;
          const next = { src: cur, shown: cur };
          const tr = this.translate(cur);
          if (tr != null) { el.setAttribute(a, tr); next.shown = tr; }
          recs[a] = next;
        }
        this.attrs.set(el, recs);
      });
    } finally {
      if (obs && this.lang) obs.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: [...ATTRS] });
    }
    if (this.queue.size) this.flushSoon();
    this.emit();
  }

  private flushSoon() {
    if (this.timer != null) return;
    this.timer = window.setTimeout(() => { this.timer = null; void this.flush(); }, 150);
  }

  private async flush() {
    const lang = this.lang;
    if (!lang || !this.queue.size) return;
    const batch = [...this.queue].slice(0, 120);
    batch.forEach((t) => { this.queue.delete(t); this.tried.add(t); });
    this.inflight += batch.length;
    this.emit();
    try {
      const body = { language: lang.code, language_name: lang.name, texts: batch };
      const r = this.signedIn
        ? await api<{ translations: Record<string, string> }>("/i18n/translate", { method: "POST", json: body })
        : await fetch("/api/v1/i18n/lookup", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })
          .then((x) => x.json() as Promise<{ translations: Record<string, string> }>);
      if (this.lang?.code !== lang.code) return;
      Object.entries(r.translations || {}).forEach(([k, v]) => this.dict.set(k, v));
      this.error = null;
      this.saveCache();
    } catch (e) {
      this.error = (e as Error).message || "Translation isn't available right now.";
      batch.forEach((t) => this.tried.add(t));
    } finally {
      this.inflight -= batch.length;
      this.emit();
      if (this.lang?.code === lang.code) {
        this.scan();
        if (this.queue.size) this.flushSoon();
      }
    }
  }

  /** After signing in, retry what the sign-in page couldn't translate from the cache alone. */
  setSignedIn(v: boolean) {
    if (v === this.signedIn) return;
    this.signedIn = v;
    if (v && this.lang) { this.tried.clear(); this.scan(); }
  }
}

export const translator = new Translator();
