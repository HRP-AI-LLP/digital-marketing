import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { auth, bindSession, refreshSession, type Session } from "../api/client";

type AuthState = {
  session: Session | null;
  ready: boolean;
  can: (perm: string) => boolean;
  login: typeof auth.login;
  logout: () => Promise<void>;
  changePassword: typeof auth.changePassword;
  switchOrg: (id: string) => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const qc = useQueryClient();

  useEffect(() => {
    bindSession(setSession);
    refreshSession().finally(() => setReady(true));
  }, []);

  const value: AuthState = {
    session,
    ready,
    can: (p) => !!session?.permissions.includes(p),
    login: auth.login,
    logout: async () => { await auth.logout(); qc.clear(); },
    changePassword: auth.changePassword,
    switchOrg: async (id) => { await auth.switchOrg(id); qc.clear(); },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
