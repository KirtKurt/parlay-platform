import type { Metadata } from 'next';
import { LegalDoc } from '@/components/LegalDoc';

export const metadata: Metadata = {
  title: 'Disclaimer',
  description: 'Important disclaimer for InQsi sports market intelligence.',
  alternates: { canonical: '/legal/disclaimer' }
};

export default function DisclaimerPage() {
  return (
    <LegalDoc
      title="Disclaimer"
      updated="June 2026"
      intro="This Disclaimer explains what InQsi is, what it is not, and the limits of every market signal, ranking, chart, and educational page."
      sections={[
        {
          title: 'Informational and educational use only',
          body: [
            'InQsi provides sports market intelligence, market movement displays, signal labels, educational content, and research tools for informational and educational purposes only.',
            'Nothing on the site is financial advice, legal advice, wagering advice, investment advice, tax advice, or professional advice of any kind.'
          ]
        },
        {
          title: 'No guarantees or promised outcomes',
          body: [
            'InQsi does not guarantee outcomes, profits, prediction accuracy, ranking accuracy, or positive results. Any market signal can be wrong, incomplete, delayed, misread, or overtaken by new information.',
            'Terms like steam, resistance, reversal, chaos, compression, risk level, and confidence are analytical labels. They are not instructions and are not guarantees.'
          ]
        },
        {
          title: 'Not an operator',
          body: [
            'InQsi does not accept wagers, hold user funds, process gaming transactions, set official odds, or act as a sportsbook, bookmaker, tipster, agent, or gambling operator.',
            'Users remain solely responsible for their own decisions, compliance, taxes, and conduct.'
          ]
        },
        {
          title: 'No affiliation',
          body: [
            'InQsi is not affiliated with, endorsed by, sponsored by, or approved by any league, team, athlete, sportsbook, oddsmaker, media company, data provider, payment provider, or regulator.',
            'Team names, league names, event names, and source names may appear for identification, comparison, commentary, navigation, or analysis only. Official logos should not be used at launch unless properly licensed or otherwise authorized.'
          ]
        },
        {
          title: 'Data limitations',
          body: [
            'Market data can be delayed, unavailable, incomplete, stale, misformatted, changed by providers, or affected by technical issues. If verified data is unavailable, InQsi should show Working on it.',
            'Users should verify time-sensitive information directly with the relevant source before making independent decisions.'
          ]
        },
        {
          title: 'Assumption of risk',
          body: [
            'Sports outcomes are uncertain. Any activity involving money can result in loss. Never risk more than you can afford to lose. Seek help if activity stops being recreational or causes harm.',
            'Use of InQsi is at your own risk. You remain responsible for every decision made before, during, and after using the site.'
          ]
        }
      ]}
    />
  );
}
