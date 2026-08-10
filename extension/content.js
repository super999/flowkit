/**
 * Content script — bridge between background.js and injected.js
 * Injects injected.js into MAIN world to access window.grecaptcha
 */
(function () {
  const s = document.createElement('script');
  s.src = chrome.runtime.getURL('injected.js');
  s.onload = () => s.remove();
  (document.head || document.documentElement).appendChild(s);
})();

// Send message safely — never throws on invalidated extension context
function safeSend(msg) {
  try {
    const p = chrome.runtime.sendMessage(msg);
    if (p && typeof p.catch === 'function') p.catch(() => {});
  } catch { /* context invalidated — ignore */ }
}

chrome.runtime.onMessage.addListener((msg, _, reply) => {
  if (msg.type !== 'GET_CAPTCHA') return;

  const { requestId, pageAction } = msg;

  const handler = (e) => {
    if (e.detail?.requestId === requestId) {
      window.removeEventListener('CAPTCHA_RESULT', handler);
      clearTimeout(timer);
      try { reply({ token: e.detail.token, error: e.detail.error }); } catch {}
    }
  };

  const timer = setTimeout(() => {
    window.removeEventListener('CAPTCHA_RESULT', handler);
    try { reply({ error: 'CONTENT_TIMEOUT' }); } catch {}
  }, 25000);

  window.addEventListener('CAPTCHA_RESULT', handler);

  window.dispatchEvent(new CustomEvent('GET_CAPTCHA', {
    detail: { requestId, pageAction },
  }));

  return true; // keep channel open for async reply
});

// ─── TRPC Media URL Monitor ─────────────────────────────────
// Forward intercepted TRPC responses with media URLs to background.js
window.addEventListener('TRPC_MEDIA_URLS', (e) => {
  const { url, body } = e.detail || {};
  if (!body) return;
  safeSend({ type: 'TRPC_MEDIA_URLS', trpcUrl: url, body });
});

// ─── API Request Capture ────────────────────────────────────
// Forward captured Flow web API request payloads to background.js
window.addEventListener('CAPTURE_API_REQ', (e) => {
  const { url, body } = e.detail || {};
  if (!body) return;
  safeSend({ type: 'CAPTURE_API_REQ', reqUrl: url, body });
});

// ─── Page Scan Report ───────────────────────────────────────
window.addEventListener('PAGE_SCAN_REPORT', (e) => {
  const d = e.detail || {};
  safeSend({ type: 'PAGE_SCAN_REPORT', detail: d });
});
