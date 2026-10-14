import { StrictMode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot } from 'react-dom/client';
import './index.css';
import App from './App.tsx';
import { AuthBoundary } from './features/data/AuthBoundary';
import { ServerLanding } from './features/agh/ServerLanding';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false
    }
  }
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      {import.meta.env.VITE_AGH_SERVER === 'true' ? <ServerLanding/> :
        <AuthBoundary><App /></AuthBoundary>}
    </QueryClientProvider>
  </StrictMode>
);
