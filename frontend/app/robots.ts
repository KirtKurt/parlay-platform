import type { MetadataRoute } from 'next';

const baseUrl=(process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app').replace(/\/$/,'');

export default function robots(): MetadataRoute.Robots {
  const privatePaths=['/admin/','/operator/','/engineering','/account/','/api/','/v1/'];
  return {
    rules:[
      {userAgent:'*',allow:'/',disallow:privatePaths},
      {userAgent:'OAI-SearchBot',allow:'/',disallow:privatePaths},
      {userAgent:'GPTBot',allow:'/',disallow:privatePaths}
    ],
    sitemap:`${baseUrl}/sitemap.xml`,host:baseUrl
  };
}
