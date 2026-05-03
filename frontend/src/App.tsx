/**
 * Top-level router. Pages are mounted under the shared Layout.
 * Each page is currently a placeholder; real implementations
 * follow in subsequent commits.
 */

import { Dashboard } from './pages/Dashboard';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Incidents } from './pages/Incidents';
import { Events } from './pages/Events';
import { Rules } from './pages/Rules';
import { Search } from './pages/Search';



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
          <Route path="/incidents" element={<Incidents />} />
          <Route path="/events" element={<Events />} />
          <Route path="/rules" element={<Rules />} />
          <Route path="/search"    element={<Search />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;