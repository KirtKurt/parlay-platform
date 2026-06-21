import type { Metadata } from 'next';
import { PartnerCapture } from '@/components/PartnerCapture';
import { TrackingConsent } from '@/components/TrackingConsent';
import './globals.css';
import './inqsi.css';
import './inqsi-compat.css';
import './tracking.css';

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app';
const ogImage = '/og-inqsi.svg';
const googleVerification = process.env.NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION;
const bingVerification = process.env.NEXT_PUBLIC_BING_SITE_VERIFICATION;

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  applicationName: 'InQsi',
  title: {
    default: 'InQsi | AI Slip Scanner, 3-Leg Builder & Sports Market Review',
    template: '%s | InQsi'
  },
  description:
    'InQsi is a sports market review platform with an AI Slip Scanner, 3-leg slip builder, line movement review, best-line warnings, saved slips, and post-game accuracy scoring.',
  keywords: [
    'InQsi',
    'AI slip scanner',
    'AI bet slip scanner',
    'AI slip builder',
    '3-leg parlay guide',
    'parlay risk guide',
    'line movement review',
    'sports market intelligence',
    'best line warning',
    'parlay accuracy tracker',
    'post-game slip autopsy',
    'sports risk review'
  ],
  alternates: { canonical: '/' },
  verification: {
    google: googleVerification,
    other: bingVerification ? { 'msvalidate.01': bingVerification } : undefined
  },
  openGraph: {
    type: 'website',
    url: siteUrl,
    siteName: 'InQsi',
    title: 'InQsi | AI Slip Scanner & Sports Market Review',
    description:
      'Review slips before lock-in, cap builds at 3 legs, compare line movement, save slips, and track post-game accuracy.',
    images: [{ url: ogImage, width: 1200, height: 630, alt: 'InQsi AI slip scanner and sports market review' }]
  },
  twitter: {
    card: 'summary_large_image',
    title: 'InQsi | AI Slip Scanner & Sports Market Review',
    description: 'AI slip scanner, 3-leg builder, line movement review, and post-game score tracking.',
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
  description: 'Sports market review for AI slip scanning, 3-leg build discipline, line movement, best-line warnings, saved slips, and post-game scoring.',
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
  description: 'Sports market review application for AI slip scanning, 3-leg slip building, line movement review, saved slips, best-line warnings, public score cards, and post-game accuracy tracking.',
  offers: {
    '@type': 'Offer',
    price: '0',
    priceCurrency: 'USD',
    description: '5-day free promo'
  }
};

const productJsonLd = {
  '@context': 'https://schema.org',
  '@type': 'Product',
  name: 'InQsi',
  brand: { '@type': 'Brand', name: 'InQsi' },
  category: 'Sports analytics software',
  url: siteUrl,
  description: 'InQsi helps customers scan slips, build disciplined 3-leg slips, review line movement, check best-line warnings, save public or private slips, and track post-game score accuracy.',
  offers: {
    '@type': 'Offer',
    priceCurrency: 'USD',
    availability: 'https://schema.org/OnlineOnly',
    url: `${siteUrl}/pricing`
  }
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(organizationJsonLd) }} />
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(websiteJsonLd) }} />
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(softwareJsonLd) }} />
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(productJsonLd) }} />
        <PartnerCapture />
        {children}
        <TrackingConsent />
      </body>
    </html>
  );
}
