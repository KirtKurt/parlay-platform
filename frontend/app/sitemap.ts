import type { MetadataRoute } from 'next';

const baseUrl = 'https://silverssyndicate.app';
const routes = [
  '',
  '/sports',
  '/sports/nfl',
  '/sports/cfb',
  '/sports/nba',
  '/sports/ncaam',
  '/sports/nhl',
  '/sports/mlb',
  '/sports/tennis',
  '/sports/soccer',
  '/sports/darts',
  '/sports/lacrosse',
  '/sports/table-tennis',
  '/methodology',
  '/pricing',
  '/legal/privacy',
  '/legal/disclaimer',
  '/legal/site-terms',
  '/legal/safe-use',
  '/legal/accessibility',
  '/contact'
];

export default function sitemap(): MetadataRoute.Sitemap {
  return routes.map((route) => ({
    url: `${baseUrl}${route}`,
    lastModified: new Date(),
    changeFrequency: route === '' ? 'daily' : 'weekly',
    priority: route === '' ? 1 : 0.7
  }));
}
