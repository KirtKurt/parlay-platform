import type { Metadata } from 'next';

const siteUrl = (process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app').replace(/\/$/, '');
const canonical = `${siteUrl}/arbitrage-v2`;

export const metadata: Metadata = {
  title: 'Sports Arbitrage Finder & Surebet Scanner',
  description: 'Find verified sports arbitrage opportunities across sportsbooks, compare American odds, calculate stakes and payouts, and review quote freshness in the InQsi ARB Command Center.',
  keywords: [
    'sports arbitrage',
    'sports arbitrage finder',
    'arbitrage betting calculator',
    'surebet scanner',
    'sportsbook odds comparison',
    'arbitrage opportunities',
    'American odds calculator',
    'sportsbook line comparison'
  ],
  alternates: { canonical: '/arbitrage-v2' },
  openGraph: {
    type: 'website',
    url: canonical,
    title: 'InQsi ARB | Sports Arbitrage Finder',
    description: 'Compare sportsbook prices, identify verified arbitrage opportunities, and calculate stakes from current market data.',
    siteName: 'InQsi'
  },
  twitter: {
    card: 'summary_large_image',
    title: 'InQsi ARB | Sports Arbitrage Finder',
    description: 'Sportsbook odds comparison, verified arbitrage opportunities, stake allocation and freshness checks.'
  },
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true, 'max-snippet': -1, 'max-image-preview': 'large' }
  }
};

const arbJsonLd = {
  '@context': 'https://schema.org',
  '@type': 'SoftwareApplication',
  name: 'InQsi ARB',
  applicationCategory: 'SportsApplication',
  operatingSystem: 'Web',
  url: canonical,
  description: 'Sports arbitrage market intelligence software for comparing sportsbook odds, reviewing verified opportunities, quote freshness, and calculating stake allocation and potential payout.',
  featureList: [
    'Sports arbitrage opportunity scanning',
    'Sportsbook odds comparison',
    'American odds display',
    'Arbitrage stake calculator',
    'Quote freshness monitoring',
    'Verified opportunity status'
  ],
  publisher: { '@type': 'Organization', name: 'InQsi', url: siteUrl }
};

const breadcrumbJsonLd = {
  '@context': 'https://schema.org',
  '@type': 'BreadcrumbList',
  itemListElement: [
    { '@type': 'ListItem', position: 1, name: 'InQsi', item: siteUrl },
    { '@type': 'ListItem', position: 2, name: 'Sports Arbitrage', item: canonical }
  ]
};

export default function ArbitrageLayout({ children }: { children: React.ReactNode }) {
  return <>
    <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(arbJsonLd) }} />
    <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumbJsonLd) }} />
    {children}
  </>;
}
