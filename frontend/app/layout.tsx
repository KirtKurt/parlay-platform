import type { Metadata } from 'next';
import './globals.css';

const siteUrl = 'https://silverssyndicate.app';

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: 'Silvers Syndicate | Sports Market Intelligence',
    template: '%s | Silvers Syndicate'
  },
  description: 'Sports market movement, slate monitoring, and parlay risk intelligence for informational and entertainment use.',
  alternates: {
    canonical: '/'
  },
  openGraph: {
    type: 'website',
    url: siteUrl,
    siteName: 'Silvers Syndicate',
    title: 'Silvers Syndicate | Sports Market Intelligence',
    description: 'Sports market movement, slate monitoring, and parlay risk intelligence for informational and entertainment use.'
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Silvers Syndicate | Sports Market Intelligence',
    description: 'Sports market movement, slate monitoring, and parlay risk intelligence.'
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
  name: 'Silvers Syndicate',
  url: siteUrl,
  contactPoint: {
    '@type': 'ContactPoint',
    contactType: 'customer support',
    email: 'support@silverssyndicate.app'
  }
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(organizationJsonLd) }}
        />
        {children}
      </body>
    </html>
  );
}
