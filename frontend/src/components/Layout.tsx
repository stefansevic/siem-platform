/**
 * Top-level layout shell: sidebar on the left, content on the right.
 */

import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';

export function Layout() {
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        <div className="p-6 max-w-[1400px] mx-auto">
          <Outlet />
        </div>
      </main>
    </div>
  );
}