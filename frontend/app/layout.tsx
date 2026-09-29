import type { Metadata } from 'next';
import { MemberLanguageGuard } from '@/components/MemberLanguageGuard';
import { PartnerCapture } from '@/components/PartnerCapture';
import { TrackingConsent } from '@/components/TrackingConsent';
import './globals.css';
import './inqsi.css';
import './inqsi-compat.css';
import './tracking.css';
import './inqsi-final-fixes.css';
import './tool-workspace.css';

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app';
const ogImage = '/og-inqsi.svg';
const googleVerification = process.env.NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION;
const bingVerification = process.env.NEXT_PUBLIC_BING_SITE_VERIFICATION;

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  applicationName: 'InQsi',
  category: 'sports analytics',
  creator: 'InQsi',
  publisher: 'InQsi',
  title: {
    default: 'InQsi | Sports Arbitrage & Bet Risk Intelligence',
    template: '%s | InQsi'
  },
  description:
    'InQsi helps sports bettors find arbitrage opportunities and analyze wager risk using global sportsbook pricing, market movement, odds comparison, and independent sports intelligence.',
  keywords: [
    'InQsi',
    'bet risk scanner',
    'sports bet risk analysis',
    'global sportsbook odds',
    'line movement review',
    'sports market intelligence',
    'best line warning',
    'sports risk review',
    'sports arbitrage',
    'sports arbitrage finder',
    'surebet scanner',
    'arbitrage calculator',
    'sportsbook odds comparison'
  ],
  alternates: { canonical: '/', languages: { 'en-US': '/' } },
  verification: {
    google: googleVerification,
    other: bingVerification ? { 'msvalidate.01': bingVerification } : undefined
  },
  openGraph: {
    type: 'website',
    url: siteUrl,
    siteName: 'InQsi',
    title: 'InQsi | Find Opportunity. Find Risk.',
    description:
      'Find sports arbitrage opportunities and scan selections for market, price and fundamentals risk across supported sports worldwide.',
    images: [{ url: ogImage, width: 1200, height: 630, alt: 'InQsi sports arbitrage and bet risk intelligence' }]
  },
  twitter: {
    card: 'summary_large_image',
    title: 'InQsi | Find Opportunity. Find Risk.',
    description: 'Global sports arbitrage, sportsbook odds comparison, market movement and bet risk intelligence.',
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
    contactType: 'member support',
    email: 'support@inqsi.app'
  }
};

const websiteJsonLd = {
  '@context': 'https://schema.org',
  '@type': 'WebSite',
  name: 'InQsi',
  url: siteUrl,
  description: 'Global sports market intelligence for arbitrage discovery, sportsbook odds comparison, line movement and wager risk analysis.',
};

const softwareJsonLd = {
  '@context': 'https://schema.org',
  '@type': 'SoftwareApplication',
  name: 'InQsi',
  applicationCategory: 'SportsApplication',
  operatingSystem: 'Web',
  url: siteUrl,
  description: 'Sports market intelligence application for arbitrage discovery, sportsbook odds comparison, market movement and wager risk analysis.',
};

const productJsonLd = {
  '@context': 'https://schema.org',
  '@type': 'Product',
  name: 'InQsi',
  brand: { '@type': 'Brand', name: 'InQsi' },
  category: 'Sports analytics software',
  url: siteUrl,
  description: 'InQsi helps users find sports arbitrage opportunities and analyze wager risk using sportsbook pricing, line movement and sports intelligence.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
      </head>
      <body style={{ fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif" }}>
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(organizationJsonLd) }} />
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(websiteJsonLd) }} />
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(softwareJsonLd) }} />
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(productJsonLd) }} />
        <MemberLanguageGuard />
        <PartnerCapture />
        {children}
        <TrackingConsent />
      </body>
    </html>
  );
}
