import React from 'react';
import { createRoot } from 'react-dom/client';
import App from '../../src/App';
import { ThemeProvider } from '../../src/ThemeContext';
import '../../src/styles/index.css';
import '../../src/styles/navy.css';
import '../../src/styles/legacy-surfaces.css';
import '../../src/styles/blue.css';
import { requests } from './adapter';

// The production App is mounted as-is. Only engine/auth/native modules are replaced at bundle time.
const audit = { requests, errors: [] as string[], blockedFetch: 0 };
(window as any).__BLUE_AUDIT__ = audit;
window.addEventListener('error', (e) => audit.errors.push(e.message));
window.addEventListener('unhandledrejection', (e) => audit.errors.push(String(e.reason)));
window.fetch = (async () => { audit.blockedFetch += 1; throw new Error('offline fixture'); }) as typeof fetch;
localStorage.setItem('iai.ollamaOnboardingDone', '1');
const theme = new URLSearchParams(location.search).get('theme');
if (theme) localStorage.setItem('insurance-ai-theme', theme);
createRoot(document.getElementById('root')!).render(
  <React.StrictMode><ThemeProvider><App /></ThemeProvider></React.StrictMode>,
);
