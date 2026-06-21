import type { Metadata } from 'next';
import { TrackingConsent } from '@/components/TrackingConsent';
import './globals.css';
import './inqsi.css';
import './inqsi-compat.css';
import './tracking.css';

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app';
const ogImage = '/og-inqsi.svg';

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  applicationName: 'InQsi',
  title: {
    default: 'InQsi | Sports Market Intelligence, Market Movement & Review Tools',
    template: '%s | InQsi'
  },
  description:
    'InQsi tracks sports market movement, live data status, game leans, selection structure, alerts, data comparison, and review signals in a mobile-first interface.',
  keywords: [
    'InQsi',
    'sports market intelligence',
    'market movement tracker',
    'sports analytics',
    'data comparison',
    'closing line value',
    'sportsbook signals',
    'game leans',
    'live sports data',
    'selection scanner',
    'market stability',
    'risk review'
  ],
  alternates: { canonical: '/' },
  openGraph: {
    type: 'website',
    url: siteUrl,
    siteName: 'InQsi',
    title: 'InQsi | Sports Market Intelligence',
    description:
      'A mobile-first sports market intelligence platform for signals, live status, game leans, scanning, watchlists, alerts, and market data review.',
    images: [{ url: ogImage, width: 1200, height: 630, alt: 'InQsi sports market intelligence' }]
  },
  twitter: {
    card: 'summary_large_image',
    title: 'InQsi | Sports Market Intelligence',
    description: 'Sports market intelligence for sharper review.',
    images: [ogImage]
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
  description: 'Sports market intelligence for market movement, game signals, data comparison, scanning, alerts, and performance tracking.',
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
  description: 'Sports market intelligence application for reviewing market movement, game signals, data comparison, structure, alerts, and performance tracking.',
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
        <TrackingConsent />
      </body>
    </html>
  );
}
