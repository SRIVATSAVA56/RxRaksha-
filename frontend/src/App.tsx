/* ───────────────────────────────────────────────────────
   App.tsx — Root router + AppProvider
   ─────────────────────────────────────────────────────── */

import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AppProvider } from "./state";
import Prescription from "./pages/Prescription";

export default function App() {
  return (
    <AppProvider>
      <BrowserRouter>
        <Routes>
          {/* Prescription input screen */}
          <Route path="/review/prescription" element={<Prescription />} />

          {/* Placeholder redirects — other screens will be added in later phases */}
          <Route path="/" element={<Navigate to="/review/prescription" replace />} />
          <Route path="/review/confirm" element={<PlaceholderScreen title="Confirm" />} />
          <Route path="/review/patient" element={<PlaceholderScreen title="Patient" />} />
          <Route path="/review/results" element={<PlaceholderScreen title="Results" />} />
        </Routes>
      </BrowserRouter>
    </AppProvider>
  );
}

function PlaceholderScreen({ title }: { title: string }) {
  return (
    <div className="min-h-screen bg-[var(--color-page)] flex items-center justify-center">
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-8 text-center">
        <h1 className="text-xl font-semibold text-[var(--color-text)]">{title}</h1>
        <p className="text-sm text-[var(--color-text-2)] mt-2">Screen coming in a later phase.</p>
      </div>
    </div>
  );
}
