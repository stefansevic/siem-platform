/**
 * Top-level router. Pages are mounted under the shared Layout.
 * Each page is currently a placeholder; real implementations
 * follow in subsequent commits.
 */

import { Dashboard } from './pages/Dashboard';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Layout } from './components/Layout';

// Placeholder pages — replaced with real ones page-by-page.
function Placeholder({ name }: { name: string }) {
  return (
    <div>
      <h1 className="text-2xl font-semibold mb-2">{name}</h1>
      <p className="text-[var(--color-muted)]">Coming next.</p>
    </div>
  );
}

function ComingSoon({ name }: { name: string }) {
  return (
    <div>
      <h1 className="text-2xl font-semibold mb-2">{name}</h1>
      <p className="text-[var(--color-muted)]">
        This page will be implemented when Elasticsearch is integrated
        in week 9. For now, use the Events page to inspect raw activity.
      </p>
    </div>
  );
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/"          element={<Dashboard />} />
          <Route path="/incidents" element={<Placeholder name="Incidents" />} />
          <Route path="/events"    element={<Placeholder name="Events" />} />
          <Route path="/rules"     element={<Placeholder name="Rules" />} />
          <Route path="/search"    element={<ComingSoon  name="Search" />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;