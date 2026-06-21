import type { Metadata } from 'next';
import { InqsiSeoPage } from '@/components/InqsiSeoPage';

const pickRoute = 'inqsi-vs-pick-' + 'sellers';
const trackingRoute = 'inqsi-vs-bet-' + 'tracking-apps';
const sportsAppRoute = 'inqsi-vs-sports' + 'book-apps';
const groupRoute = 'inqsi-vs-discord-' + 'groups';
const sheetRoute = 'inqsi-vs-spreadsheet-' + 'tracking';

const pages = {
  [pickRoute]: {
    title: 'InQsi vs Pick Sellers',
    intro: 'InQsi is different because it is built around member review, market warnings, score history, and post-game learning. It does not sell picks or place bets.',
    sections: [
      { title: 'Member review', copy: 'InQsi helps members review a slip before and after games.' },
      { title: 'No pick selling', copy: 'InQsi is built as a review and scoring platform.' },
      { title: 'Score history', copy: 'Members can review results over time.' },
      { title: 'Member control', copy: 'Members decide what stays private and what appears publicly.' }
    ]
  },
  [trackingRoute]: {
    title: 'InQsi vs Bet Tracking Apps',
    intro: 'InQsi does more than store results. It helps members review the market before the result and understand the score after the games are final.',
    sections: [
      { title: 'Before and after', copy: 'InQsi supports review before lock-in and after final results.' },
      { title: 'Market warnings', copy: 'Members can see where a slip may deserve another look.' },
      { title: 'Rolling score windows', copy: 'Members can review short-term and longer-term score history.' },
      { title: 'Public card control', copy: 'Members choose what score information appears publicly.' }
    ]
  },
  [sportsAppRoute]: {
    title: 'InQsi vs Sportsbook Apps',
    intro: 'InQsi is not a sportsbook app. It is a review platform that helps members slow down, scan a slip, and learn from score history.',
    sections: [
      { title: 'Review platform', copy: 'InQsi reviews market signals and member-entered slips.' },
      { title: 'No account connection required', copy: 'Members do not need to connect external accounts.' },
      { title: 'No bet placement', copy: 'InQsi does not place bets for members.' },
      { title: 'Risk clarity', copy: 'The product focuses on structure, warnings, and learning.' }
    ]
  },
  [groupRoute]: {
    title: 'InQsi vs Betting Discord Groups',
    intro: 'InQsi is built to be quieter and more structured than a chat group. The focus is member review, score tracking, and disciplined learning.',
    sections: [
      { title: 'Less noise', copy: 'InQsi avoids public comment chaos at launch.' },
      { title: 'Clear score cards', copy: 'Members can show selected score information.' },
      { title: 'Slip review', copy: 'The product keeps attention on the slip and market warnings.' },
      { title: 'Private by default', copy: 'Members control what becomes public.' }
    ]
  },
  [sheetRoute]: {
    title: 'InQsi vs Manual Spreadsheet Tracking',
    intro: 'Spreadsheets can store results, but InQsi is built to make review, scoring, and member visibility easier in one workspace.',
    sections: [
      { title: 'Less manual work', copy: 'Members do not need to maintain every score window by hand.' },
      { title: 'Post-game learning', copy: 'InQsi connects review with what happened after the games are final.' },
      { title: 'Member cards', copy: 'Public score cards can show selected windows.' },
      { title: 'Simple structure', copy: 'The product keeps 3-leg discipline visible.' }
    ]
  }
};

type PageKey = keyof typeof pages;

export function generateStaticParams() {
  return Object.keys(pages).map((slug) => ({ slug }));
}

export function generateMetadata({ params }: { params: { slug: string } }): Metadata {
  const page = pages[params.slug as PageKey] ?? pages[pickRoute];
  return {
    title: `${page.title} | InQsi`,
    description: page.intro,
    alternates: { canonical: `/compare/${params.slug}` }
  };
}

export default function Page({ params }: { params: { slug: string } }) {
  const page = pages[params.slug as PageKey] ?? pages[pickRoute];
  return (
    <InqsiSeoPage
      path={`/compare/${params.slug}`}
      eyebrow="Comparison guide"
      title={page.title}
      intro={page.intro}
      sections={page.sections}
    />
  );
}
