"use client";

import { useEffect, useMemo, useState } from 'react';

const CONSENT_KEY = 'inqsi_consent_v1';

type ConsentState = {
  analytics: boolean;
  marketing: boolean;
  replay: boolean;
  updatedAt: string;
};

declare global {
  interface Window {
    dataLayer?: unknown[];
    gtag?: (...args: unknown[]) => void;
    posthog?: { init?: (...args: unknown[]) => void; capture?: (...args: unknown[]) => void; opt_out_capturing?: () => void; opt_in_capturing?: () => void };
    fbq?: (...args: unknown[]) => void;
    ttq?: { page?: () => void; track?: (...args: unknown[]) => void };
  }
}

function loadScript(id: string, src: string, onLoad?: () => void) {
  if (document.getElementById(id)) return;
  const script = document.createElement('script');
  script.id = id;
  script.async = true;
  script.src = src;
  if (onLoad) script.onload = onLoad;
  document.head.appendChild(script);
}

function maskSensitiveInputs() {
  const selector = 'input, textarea, select, [contenteditable="true"]';
  document.querySelectorAll(selector).forEach((node) => {
    node.setAttribute('data-ph-no-capture', 'true');
    node.setAttribute('data-private', 'true');
  });
}

function installTracking(consent: ConsentState) {
  maskSensitiveInputs();

  const gaId = process.env.NEXT_PUBLIC_GA4_MEASUREMENT_ID;
  if (consent.analytics && gaId) {
    loadScript('inqsi-ga4', `https://www.googletagmanager.com/gtag/js?id=${gaId}`, () => {
      window.dataLayer = window.dataLayer || [];
      window.gtag = function gtag(...args: unknown[]) { window.dataLayer?.push(args); };
      window.gtag('js', new Date());
      window.gtag('consent', 'update', { analytics_storage: 'granted', ad_storage: consent.marketing ? 'granted' : 'denied' });
      window.gtag('config', gaId, { anonymize_ip: true });
    });
  }

  const posthogKey = process.env.NEXT_PUBLIC_POSTHOG_KEY;
  const posthogHost = process.env.NEXT_PUBLIC_POSTHOG_HOST || 'https://app.posthog.com';
  if ((consent.analytics || consent.replay) && posthogKey) {
    loadScript('inqsi-posthog', `${posthogHost}/static/array.js`, () => {
      window.posthog?.init?.(posthogKey, {
        api_host: posthogHost,
        capture_pageview: true,
        autocapture: consent.analytics,
        disable_session_recording: !consent.replay,
        session_recording: {
          maskAllInputs: true,
          maskInputOptions: { password: true, email: true, text: true, textarea: true }
        }
      });
    });
  }

  const metaPixel = process.env.NEXT_PUBLIC_META_PIXEL_ID;
  if (consent.marketing && metaPixel) {
    window.fbq = window.fbq || function fbq(...args: unknown[]) { (window.fbq as unknown[] & ((...inner: unknown[]) => void))?.push?.(args); };
    loadScript('inqsi-meta-pixel', 'https://connect.facebook.net/en_US/fbevents.js', () => {
      window.fbq?.('init', metaPixel);
      window.fbq?.('track', 'PageView');
    });
  }
}

export function TrackingConsent() {
  const [consent, setConsent] = useState<ConsentState | null>(null);
  const [open, setOpen] = useState(false);
  const draft = useMemo(() => consent || { analytics: true, marketing: false, replay: false, updatedAt: new Date().toISOString() }, [consent]);
  const [choices, setChoices] = useState(draft);

  useEffect(() => {
    const stored = localStorage.getItem(CONSENT_KEY);
    if (stored) {
      const parsed = JSON.parse(stored) as ConsentState;
      setConsent(parsed);
      setChoices(parsed);
      installTracking(parsed);
    } else {
      setOpen(true);
    }
  }, []);

  function save(next: ConsentState) {
    const value = { ...next, updatedAt: new Date().toISOString() };
    localStorage.setItem(CONSENT_KEY, JSON.stringify(value));
    setConsent(value);
    setChoices(value);
    setOpen(false);
    installTracking(value);
  }

  if (!open) {
    return <button className="inqsi-privacy-floating" onClick={() => setOpen(true)}>Privacy choices</button>;
  }

  return (
    <section className="inqsi-cookie-banner" role="dialog" aria-modal="false" aria-label="Privacy and cookie choices">
      <div>
        <b>InQsi privacy choices</b>
        <p>We use necessary storage to run the site. With your permission, we also use analytics, marketing pixels, and masked session replay to improve the product.</p>
        <label><input type="checkbox" checked readOnly /> Necessary</label>
        <label><input type="checkbox" checked={choices.analytics} onChange={(event) => setChoices({ ...choices, analytics: event.target.checked })} /> Analytics</label>
        <label><input type="checkbox" checked={choices.marketing} onChange={(event) => setChoices({ ...choices, marketing: event.target.checked })} /> Marketing pixels</label>
        <label><input type="checkbox" checked={choices.replay} onChange={(event) => setChoices({ ...choices, replay: event.target.checked })} /> Masked session replay</label>
        <div className="inqsi-cookie-actions">
          <button onClick={() => save({ analytics: false, marketing: false, replay: false, updatedAt: new Date().toISOString() })}>Reject non-essential</button>
          <button onClick={() => save(choices)}>Save choices</button>
          <button className="inqsi-primary" onClick={() => save({ analytics: true, marketing: true, replay: true, updatedAt: new Date().toISOString() })}>Accept all</button>
        </div>
        <p className="inqsi-cookie-links"><a href="/legal/privacy">Privacy Policy</a> · <a href="/legal/cookies">Cookie Policy</a> · <a href="/privacy-choices">Do Not Sell or Share My Personal Information</a></p>
      </div>
    </section>
  );
}
