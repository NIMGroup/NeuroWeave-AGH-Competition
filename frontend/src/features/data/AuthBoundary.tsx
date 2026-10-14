import { useEffect, useState, type ReactNode } from 'react';
import { getSession, login, logout, type DataSession } from './dataApi';
import { useLanguageStore } from '../../stores/languageStore';

export function AuthBoundary({ children }: { children: ReactNode }) {
  const zh = useLanguageStore(s => s.language).startsWith('zh');
  const [session, setSession] = useState<DataSession | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    const refresh = () => { void getSession().then(value => { if (alive) setSession(value); })
      .catch((e: unknown) => { if (alive) setError(e instanceof Error ? e.message : String(e)); }); };
    refresh();
    window.addEventListener('neuroweave-auth-expired', refresh);
    return () => { alive = false; window.removeEventListener('neuroweave-auth-expired', refresh); };
  }, []);
  async function submit() {
    setBusy(true); setError('');
    try { await login(username, password); setPassword(''); setSession(await getSession()); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  if (!session || (session.required && !session.authenticated)) {
    return <main className="flex min-h-screen items-center justify-center bg-canvas p-6">
      <form className="w-full max-w-sm space-y-4 rounded-panel border border-line bg-surface p-6"
        onSubmit={e => { e.preventDefault(); void submit(); }}>
        <h1 className="text-xl font-semibold">NeuroWeave</h1>
        <p>{zh ? '登录后使用数据助手及已授权的数据。' : 'Sign in to use the assistant and your authorized data.'}</p>
        {!session && !error && <p role="status">{zh ? '正在连接…' : 'Connecting…'}</p>}
        {session?.required && <>
          <label className="block">{zh ? '账户' : 'Account'}<input autoComplete="username" required value={username}
            onChange={e => setUsername(e.target.value)} className="mt-1 w-full rounded-control border border-line bg-well p-2" /></label>
          <label className="block">{zh ? '密码' : 'Password'}<input type="password" autoComplete="current-password" required value={password}
            onChange={e => setPassword(e.target.value)} className="mt-1 w-full rounded-control border border-line bg-well p-2" /></label>
          <button disabled={busy} className="rounded-control border border-line px-4 py-2">{busy ? (zh ? '登录中…' : 'Signing in…') : (zh ? '登录' : 'Sign in')}</button>
        </>}
        {error && <p role="alert" className="text-danger">{error}</p>}
      </form>
    </main>;
  }
  return <>
    {session.authenticated && <div className="fixed right-3 top-2 z-50 rounded-chip border border-line bg-surface px-2 py-1 text-xs">
      {session.actor} · <button className="underline" onClick={() => void logout().then(() => window.location.reload())}>{zh ? '退出' : 'Sign out'}</button>
    </div>}
    {children}
  </>;
}
