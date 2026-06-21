import type { Metadata } from 'next';
import './globals.css';
import './inqsi.css';

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app';

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  applicationName: 'InQsi',
  title: {
    default: 'InQsi | Sports Market Intelligence, Line Movement & Parlay Scanner',
    template: '%s | InQsi'
  },
  description:
    'InQsi tracks sportsbook line movement, live odds, predicted winners, 3-leg parlay rankings, alerts, best available lines, and market risk signals before you lock it in.',
  keywords: [
    'InQsi',
    'sports market intelligence',
    'line movement tracker',
    'parlay scanner',
    'sports analytics',
    'odds comparison',
    'closing line value',
    'sportsbook signals',
    'predicted winners',
    'live odds app',
    'bet slip scanner',
    'market stability',
    'parlay risk checker'
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

const websiteJsonLd = {
  '@context': 'https://schema.org',
  '@type': 'WebSite',
  name: 'InQsi',
  url: siteUrl,
  description: 'Sports market intelligence for market movement, game signals, line comparison, parlay scanning, alerts, and performance tracking.',
  potentialAction: {
    '@type': 'SearchAction',
    target: `${siteUrl}/sports?query={search_term_string}`,
    'query-input': 'required name=search_term_string'
  }
};

const softwareJsonLd = {
  '@context': 'https://schema.org',
  '@type': 'SoftwareApplication',
  name: 'InQsi',
  applicationCategory: 'SportsApplication',
  operatingSystem: 'Web, iOS, Android',
  url: siteUrl,
  description: 'Sports market intelligence application for reviewing line movement, game signals, best available lines, parlay structure, alerts, and performance tracking.',
  offers: {
    '@type': 'Offer',
    price: '0',
    priceCurrency: 'USD',
    description: '5-day free promo'
  }
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(organizationJsonLd) }} />
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(websiteJsonLd) }} />
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(softwareJsonLd) }} />
        {children}
      </body>
    </html>
  );
}
