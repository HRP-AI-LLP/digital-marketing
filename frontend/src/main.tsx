import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@fontsource-variable/inter";
import "./index.css";
import App from "./App";
import { AuthProvider } from "./auth/AuthContext";
import { PrefsProvider } from "./prefs/PrefsContext";
import { applyAppearance, loadLocal } from "./prefs/appearance";
import { pausePollingWhenIdle } from "./api/idle";

// Theme and fonts before the first paint, so the page never flashes the wrong colours.
applyAppearance(loadLocal());

// staleTime: switching pages within 30 s reuses what was just loaded instead of asking the server again.
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 30_000 } } });
pausePollingWhenIdle(queryClient);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <PrefsProvider>
            <App />
          </PrefsProvider>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
