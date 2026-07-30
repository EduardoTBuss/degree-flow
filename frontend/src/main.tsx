import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import { applyTheme, readStoredTheme } from "./lib/theme";
import "./styles.css";
import "./styles-v2.css";

// Apply the persisted theme before first render (the inline script in index.html
// already did this pre-CSS; this keeps dev/HMR and non-HTML entrypoints correct).
applyTheme(readStoredTheme());

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
