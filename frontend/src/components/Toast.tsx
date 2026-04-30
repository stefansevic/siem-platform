/**
 * In-house toast notification system.
 *
 * Provides:
 *   <ToastProvider> — wraps the app and renders the toast stack.
 *   useToast() — hook that exposes addToast(message, type).
 *
 * Why no library: keeps the bundle small (no react-toastify, sonner)
 * and the API surface limited to what the dashboard actually uses.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from 'react';
import type { ReactNode } from 'react';
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react';

type ToastType = 'success' | 'error' | 'info';

interface Toast {
  id: number;
  message: string;
  type: ToastType;
}

interface ToastContextValue {
  addToast: (message: string, type?: ToastType) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const AUTO_DISMISS_MS = 4000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const removeToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const addToast = useCallback<ToastContextValue['addToast']>(
    (message, type = 'success') => {
      const id = Date.now() + Math.random();
      setToasts((prev) => [...prev, { id, message, type }]);
    },
    [],
  );

  return (
    <ToastContext.Provider value={{ addToast }}>
      {children}
      <ToastStack toasts={toasts} onDismiss={removeToast} />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error('useToast must be used inside <ToastProvider>');
  }
  return ctx;
}

// ============================================
// Rendering
// ============================================

interface ToastStackProps {
  toasts: Toast[];
  onDismiss: (id: number) => void;
}

function ToastStack({ toasts, onDismiss }: ToastStackProps) {
  return (
    <div className="fixed bottom-4 right-4 z-[60] flex flex-col gap-2 pointer-events-none">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={() => onDismiss(t.id)} />
      ))}
    </div>
  );
}

interface ToastItemProps {
  toast: Toast;
  onDismiss: () => void;
}

function ToastItem({ toast, onDismiss }: ToastItemProps) {
  // Auto-dismiss after AUTO_DISMISS_MS
  useEffect(() => {
    const id = setTimeout(onDismiss, AUTO_DISMISS_MS);
    return () => clearTimeout(id);
  }, [onDismiss]);

  const config: Record<ToastType, { Icon: typeof CheckCircle2; classes: string }> = {
    success: {
      Icon: CheckCircle2,
      classes: 'border-green-500/40 text-green-300',
    },
    error: {
      Icon: AlertCircle,
      classes: 'border-red-500/40 text-red-300',
    },
    info: {
      Icon: Info,
      classes: 'border-blue-500/40 text-blue-300',
    },
  };

  const { Icon, classes } = config[toast.type];

  return (
    <div
      className={[
        'pointer-events-auto bg-[var(--color-surface)] border rounded-lg',
        'shadow-lg px-4 py-3 min-w-[260px] max-w-md',
        'flex items-start gap-3 text-sm',
        'animate-in slide-in-from-right-5 fade-in duration-200',
        classes,
      ].join(' ')}
    >
      <Icon size={18} className="shrink-0 mt-0.5" />
      <span className="flex-1 text-[var(--color-text)]">{toast.message}</span>
      <button
        onClick={onDismiss}
        className="text-[var(--color-muted)] hover:text-[var(--color-text)] transition-colors shrink-0"
        aria-label="Dismiss"
      >
        <X size={14} />
      </button>
    </div>
  );
}