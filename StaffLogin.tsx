import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

type AuthResponse = {
  id: number;
  access_token: string;
  email: string;
  full_name: string;
  role?: string;
  assigned_category?: string | null;
};

type StaffLoginProps = {
  apiBase: string;
  token: string | null;
  currentRole: string;
  onAuthSuccess: (data: AuthResponse, plainPassword: string) => void;
};

export default function StaffLogin({ apiBase, token, currentRole, onAuthSuccess }: StaffLoginProps) {
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  useEffect(() => {
    if (token && currentRole === 'staff') {
      navigate('/staff', { replace: true });
    }
  }, [token, currentRole, navigate]);

  const handleSubmit = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      setError('');

      if (!username.trim() || !password.trim()) {
        setError('Please enter your username and password.');
        return;
      }

      setLoading(true);

      try {
        const response = await fetch(`${apiBase}/login`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            email: username.trim(),
            password,
          }),
        });

        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.detail || 'Login failed.');
        }

        if (data.role !== 'staff') {
          setError('Only staff accounts can log in here.');
          return;
        }

        onAuthSuccess(data, password);
        navigate('/staff', { replace: true });
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Login failed.';
        setError(message === 'Only staff accounts can log in here.' ? message : 'Invalid staff credentials.');
      } finally {
        setLoading(false);
      }
    },
    [apiBase, username, onAuthSuccess, navigate, password]
  );

  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-slate-950 p-4 md:p-8">
      {/* ── Background Effects ── */}
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(34,197,94,0.3),_transparent_28%),radial-gradient(circle_at_bottom_right,_rgba(59,130,246,0.25),_transparent_32%)]" />
      <div className="absolute -left-24 top-8 h-64 w-64 rounded-full bg-emerald-500/20 blur-3xl" />
      <div className="absolute right-0 top-48 h-72 w-72 rounded-full bg-blue-500/20 blur-3xl" />
      <div className="absolute bottom-0 left-1/2 h-56 w-56 -translate-x-1/2 rounded-full bg-emerald-400/10 blur-3xl" />

      {/* ── Auth Card ── */}
      <div className="relative z-10 w-full max-w-md">
        <div className="overflow-hidden rounded-3xl border border-white/10 bg-white/5 shadow-2xl backdrop-blur-xl ring-1 ring-white/10">
          <div className="p-8 md:p-10">
            {/* ── Header ── */}
            <div className="mb-8 text-center">
              <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-emerald-500 to-teal-400 shadow-lg shadow-emerald-500/20">
                <svg className="h-8 w-8 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
                </svg>
              </div>
              <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/20 px-3 py-0.5 text-[10px] font-bold uppercase tracking-[0.2em] text-emerald-300 border border-emerald-500/20">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                Staff Portal
              </span>
              <h1 className="mt-3 text-3xl font-black text-white tracking-tight">Welcome Back</h1>
              <p className="mt-1.5 text-sm text-white/50 font-medium">
                Sign in to manage pharmacy inventory.
              </p>
            </div>

            {/* ── Form ── */}
            <form onSubmit={handleSubmit} className="space-y-5">
              {/* ── Email ── */}
              <div>
                <label className="mb-1.5 block text-xs font-bold uppercase tracking-wider text-white/40">
                  Email Address
                </label>
                <input
                  type="email"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  placeholder="staff@five-l.com"
                  className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-white placeholder-white/30 outline-none transition-all focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20"
                />
              </div>

              {/* ── Password ── */}
              <div>
                <label className="mb-1.5 block text-xs font-bold uppercase tracking-wider text-white/40">
                  Password
                </label>
                <div className="relative">
                  <input
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="••••••••"
                    className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 pr-12 text-sm text-white placeholder-white/30 outline-none transition-all focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((prev) => !prev)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-white/40 transition hover:text-white/70"
                  >
                    {showPassword ? (
                      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                        <path strokeLinecap="round" strokeLinejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                        <path strokeLinecap="round" strokeLinejoin="round" d="M3 3l18 18" />
                      </svg>
                    ) : (
                      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                        <path strokeLinecap="round" strokeLinejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                      </svg>
                    )}
                  </button>
                </div>
              </div>

              {/* ── Error ── */}
              {error && (
                <div className="flex items-center gap-2 rounded-xl bg-rose-500/10 border border-rose-500/20 px-4 py-3 text-sm font-medium text-rose-400">
                  <span className="text-lg">⚠️</span>
                  {error}
                </div>
              )}

              {/* ── Login Button ── */}
              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-xl bg-gradient-to-r from-emerald-500 to-teal-400 px-4 py-3.5 text-sm font-bold text-white shadow-lg shadow-emerald-500/20 transition-all hover:shadow-emerald-500/30 hover:scale-[1.01] active:scale-[0.98] disabled:opacity-50"
              >
                {loading ? (
                  <span className="flex items-center justify-center gap-2">
                    <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    Signing in...
                  </span>
                ) : (
                  'Sign In'
                )}
              </button>

              {/* ── Footer Note ── */}
              <div className="mt-6 text-center text-xs text-white/30 border-t border-white/5 pt-4">
                Only authorized pharmacy staff may access this system.
              </div>
            </form>
          </div>
        </div>
      </div>
    </main>
  );
}