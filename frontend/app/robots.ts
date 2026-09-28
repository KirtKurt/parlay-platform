import type { MetadataRoute } from 'next';

const baseUrl = (process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app').replace(/\/$/, '');

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: '*',
      allow: '/',
      disallow: [
        '/admin/',
        '/operator/',
        '/engineering',
        '/oauth-readiness',
        '/account-readiness',
        '/data-readiness',
        '/record-storage',
        '/release-checklist',
        '/launch-checklist',
        '/visual-system',
        '/partner-report/',
        '/api/',
        '/v1/'
      ]
    },
    sitemap: `${baseUrl}/sitemap.xml`,
    host: baseUrl
  };
}
