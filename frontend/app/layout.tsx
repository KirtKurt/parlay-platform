import type { Metadata } from 'next';
import './globals.css';
import './inqsi.css';

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app';

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: 'InQsi | Sports Market Intelligence, Line Movement & Parlay Scanner',
    template: '%s | InQsi'
  },
  description:
    'InQsi tracks sportsbook line movement, live odds, predicted winners, 3-leg parlay rankings, alerts, best available lines, and market risk signals before you lock it in.',
  keywords: [
    'sports market intelligence',
    'line movement tracker',
    'parlay scanner',
    'sports betting analytics',
    'odds comparison',
    'closing line value',
    'sportsbook signals',
    'predicted winners',
    'live odds app',
    'InQsi'
  ],
  alternates: { canonical: '/' },
  openGraph: {
    type: 'website',
    url: siteUrl,
    siteName: 'InQsi',
    title: 'InQsi | Find What Looks Wrong Before You Lock It In',
    description:
      'A mobile-first sports market intelligence platform for signals, live odds, predicted winners, parlay scanning, watchlists, alerts, and best available lines.'
  },
  twitter: {
    card: 'summary_large_image',
    title: 'InQsi | Sports Market Intelligence',
    description: 'Find what looks wrong before you lock it in.'
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      'max-snippet': -1,
      'max-image-preview': 'large',
      'max-video-preview': -1
    }
  }
};

const organizationJsonLd = {
  '@context': 'https://schema.org',
  '@type': 'Organization',
  name: 'InQsi',
  url: siteUrl,
  sameAs: [
    'https://x.com/inqsi',
    'https://instagram.com/inqsi',
    'https://tiktok.com/@inqsi',
    'https://youtube.com/@inqsi',
    'https://discord.gg/inqsi'
  ],
  contactPoint: {
    '@type': 'ContactPoint',
    contactType: 'customer support',
    email: 'support@inqsi.app'
  }
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(organizationJsonLd) }} />
        {children}
      </body>
    </html>
  );
}
