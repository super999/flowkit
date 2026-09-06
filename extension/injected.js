/**
 * Injected into MAIN world on labs.google — has access to window.grecaptcha
 * Also intercepts TRPC fetch responses to capture fresh signed media URLs.
 *
 * Wrapped in an IIFE + version marker so re-injection (extension reload while
 * the page stays open) does NOT throw "Identifier has already been declared".
 */
(() => {
  const VERSION = 'v4';
  if (window.__FLOWKIT_INJECTED__ === VERSION) return;

  const SITE_KEY = '6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV';

  // Restore originals before re-patching (version bump case)
  if (window.__FLOWKIT_ORIG_FETCH__) window.fetch = window.__FLOWKIT_ORIG_FETCH__;
  if (window.__FLOWKIT_ORIG_XHR_SEND__) XMLHttpRequest.prototype.send = window.__FLOWKIT_ORIG_XHR_SEND__;
  if (window.__FLOWKIT_ORIG_XHR_OPEN__) XMLHttpRequest.prototype.open = window.__FLOWKIT_ORIG_XHR_OPEN__;

  // ─── TRPC Response Monitor + API Request Capture ──────────
  const _originalFetch = window.fetch;
  window.__FLOWKIT_ORIG_FETCH__ = _originalFetch;
  window.fetch = async function (...args) {
    const url = typeof args[0] === 'string' ? args[0] : args[0]?.url || '';
    let requestBody = null;
    try {
      if (args[1]?.body) requestBody = typeof args[1].body === 'string' ? args[1].body : JSON.stringify(args[1].body);
    } catch {}
    const response = await _originalFetch.apply(this, args);
    try {
      // Capture API payloads: anything posted to aisandbox OR tRPC (relative or absolute) with a JSON body
      const isApiCall = url.includes('aisandbox-pa.googleapis.com')
        || url.includes('/api/trpc/');
      if (isApiCall && requestBody && requestBody.length < 200000) {
        window.dispatchEvent(new CustomEvent('CAPTURE_API_REQ', {
          detail: { url, body: requestBody },
        }));
      }
      // Intercept TRPC calls on flow.google.com or labs.google that return project/flow data
      if ((url.includes('/fx/api/trpc/') || url.includes('/api/trpc/')) && response.ok) {
        const clone = response.clone();
        clone.text().then(text => {
          if (text.includes('storage.googleapis.com/ai-sandbox-videofx/')
              || text.includes('flow-content.google/image/')
              || text.includes('flow-content.google/video/')) {
            window.dispatchEvent(new CustomEvent('TRPC_MEDIA_URLS', {
              detail: { url, body: text },
            }));
          }
        }).catch(() => {});
      }
    } catch {}
    return response;
  };

  // Also capture XHR-based API calls (older fetch polyfills / legacy code paths)
  const _originalXhrSend = XMLHttpRequest.prototype.send;
  const _originalXhrOpen = XMLHttpRequest.prototype.open;
  window.__FLOWKIT_ORIG_XHR_SEND__ = _originalXhrSend;
  window.__FLOWKIT_ORIG_XHR_OPEN__ = _originalXhrOpen;
  XMLHttpRequest.prototype.open = function (method, url) {
    this._captureUrl = url;
    return _originalXhrOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    try {
      const url = String(this._captureUrl || '');
      const isApiCall = url.includes('aisandbox-pa.googleapis.com')
        || url.includes('/api/trpc/');
      if (isApiCall && body) {
        const bodyStr = typeof body === 'string' ? body : JSON.stringify(body);
        if (bodyStr.length < 200000) {
          window.dispatchEvent(new CustomEvent('CAPTURE_API_REQ', {
            detail: { url, body: bodyStr },
          }));
        }
      }
    } catch {}
    return _originalXhrSend.apply(this, arguments);
  };

  function getRecaptchaSiteKey() {
    try {
      const script = document.querySelector('script[src*="recaptcha"][src*="render="]');
      if (script) {
        const match = script.src.match(/render=([^&]+)/);
        if (match && match[1] && match[1] !== 'explicit') return match[1];
      }
    } catch {}
    return SITE_KEY;
  }

  window.addEventListener('GET_CAPTCHA', async ({ detail }) => {
    const { requestId, pageAction } = detail;
    try {
      await waitForGrecaptcha();
      const siteKey = getRecaptchaSiteKey();
      const token = await window.grecaptcha.enterprise.execute(siteKey, {
        action: pageAction,
      });
      window.dispatchEvent(new CustomEvent('CAPTCHA_RESULT', {
        detail: { requestId, token },
      }));
    } catch (e) {
      window.dispatchEvent(new CustomEvent('CAPTCHA_RESULT', {
        detail: { requestId, error: e.message },
      }));
    }
  });

  // ─── SSR Media URL Scanner ─────────────────────────────────
  // Project data (incl. all media URLs) is server-side rendered into the page
  // HTML, so it never appears in fetch responses. Scan the DOM periodically.
  // URLs are JSON-escaped in HTML (\u0026, \/), so unescape before matching.
  const _MEDIA_URL_RE = /https:\/\/(?:storage\.googleapis\.com\/ai-sandbox-videofx|flow-content\.google)\/(?:image|video)\/[0-9a-f-]{36}(?:\?[^"'\s<]+)?/g;
  let _lastPageScan = 0;

  function scanPageForMediaUrls() {
    try {
      const root = document.documentElement;
      if (!root) return;
      const now = Date.now();
      if (now - _lastPageScan < 4000) return; // throttle
      _lastPageScan = now;

      // 1) Final signed URLs from browser resource timing (img loads via
      //    media.getMediaUrlRedirect follow redirects → final URL lands here)
      const resourceUrls = performance.getEntriesByType('resource')
        .map(e => e.name)
        .filter(u => u.includes('flow-content.google/') || u.includes('storage.googleapis.com/ai-sandbox-videofx/'))
        .map(u => u.split('?')[0].includes('/image/') || u.split('?')[0].includes('/video/') ? u : u);

      // 2) Direct URL matches from page HTML (SSR data)
      let html = root.outerHTML;
      html = html.replace(/\\u0026/g, '&').replace(/\\\//g, '/');
      const htmlUrls = html.match(_MEDIA_URL_RE) || [];

      const all = Array.from(new Set([...resourceUrls, ...htmlUrls]));
      if (all.length) {
        window.dispatchEvent(new CustomEvent('TRPC_MEDIA_URLS', {
          detail: { url: location.href, body: all.join('\n') },
        }));
      }

      // 3) Collect ALL media ids seen via the redirect endpoint (complete list
      //    even for not-yet-rendered images)
      const redirectNames = html.match(/media\.getMediaUrlRedirect\?name=([0-9a-f-]{36})/g) || [];
      const mediaIds = Array.from(new Set(redirectNames.map(n => n.split('name=')[1])));

      // Debug report so the agent can see what the page actually contains
      const imgSample = Array.from(document.images).slice(0, 12).map(i => (i.currentSrc || i.src || '').slice(0, 160));
      window.dispatchEvent(new CustomEvent('PAGE_SCAN_REPORT', {
        detail: {
          url: location.href,
          htmlLen: html.length,
          resourceUrlCount: resourceUrls.length,
          htmlUrlCount: htmlUrls.length,
          redirectMediaCount: redirectNames.length,
          mediaIds,
          urlSample: all.slice(0, 3),
          imgSample,
        },
      }));
    } catch {}
  }

  window.addEventListener('load', () => {
    setTimeout(scanPageForMediaUrls, 2000);
    setTimeout(scanPageForMediaUrls, 6000);
    setTimeout(scanPageForMediaUrls, 12000);
  });
  document.addEventListener('scroll', scanPageForMediaUrls, { passive: true });
  setInterval(scanPageForMediaUrls, 30000);

  function waitForGrecaptcha(timeout = 10000) {
    return new Promise((resolve, reject) => {
      const start = Date.now();
      const check = () => {
        if (window.grecaptcha?.enterprise?.execute) return resolve();
        if (Date.now() - start > timeout) return reject(new Error('grecaptcha not available'));
        setTimeout(check, 200);
      };
      check();
    });
  }

  window.__FLOWKIT_INJECTED__ = VERSION;
})();
