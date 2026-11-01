import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { applyAppearance, loadLocal, saveLocal, type Prefs } from "./appearance";
import { langInfo, scriptFont, translator } from "./i18n";

type Ctx = { prefs: Prefs; update: (p: Partial<Prefs>) => void };
const PrefsCtx = createContext<Ctx | null>(null);

/** Applies the person's theme, font and language everywhere, and keeps them in sync with their account. */
export function apply(p: Prefs) {
  const code = p.language && p.language !== "en" ? p.language : null;
  const lang = code ? langInfo(code, p.language_name) : null;
  applyAppearance(p, lang ? scriptFont(lang) : "");
  if (lang) translator.start(lang);
  else if (translator.lang) translator.stop(true);
}

export function PrefsProvider({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  const [prefs, setPrefs] = useState<Prefs>(() => loadLocal());
  const userId = session?.user.id;

  useEffect(() => { apply(prefs); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Signed in: the account's saved choices win (they follow the person across devices).
  useEffect(() => {
    translator.setSignedIn(!!userId);
    if (!userId) return;
    let off = false;
    api<Prefs>("/me/preferences").then((server) => {
      if (off) return;
      const local = loadLocal();
      if (!server || !Object.keys(server).length) {
        // first time on the server: keep what was chosen in this browser and save it to the account
        if (Object.keys(local).length) void api("/me/preferences", { method: "PUT", json: local }).catch(() => undefined);
        return;
      }
      const merged = { ...local, ...server };
      saveLocal(merged);
      setPrefs(merged);
      apply(merged);
    }).catch(() => undefined);
    return () => { off = true; };
  }, [userId]);

  const ref = useRef(prefs);
  ref.current = prefs;
  const update = useCallback((p: Partial<Prefs>) => {
    const next = { ...ref.current, ...p };
    ref.current = next;
    saveLocal(next);
    setPrefs(next);
    const save = userId ? api("/me/preferences", { method: "PUT", json: p }).catch(() => undefined) : Promise.resolve();
    // Going back to English reloads the page (to restore every original string): save first.
    if (p.language === "en" && translator.lang) void save.then(() => apply(next));
    else apply(next);
  }, [userId]);

  return <PrefsCtx.Provider value={{ prefs, update }}>{children}</PrefsCtx.Provider>;
}

export function usePrefs() {
  const v = useContext(PrefsCtx);
  if (!v) throw new Error("usePrefs outside PrefsProvider");
  return v;
}
