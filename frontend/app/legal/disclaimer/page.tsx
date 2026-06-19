import type { Metadata } from 'next';
import { LegalDoc } from '@/components/LegalDoc';

export const metadata: Metadata = {
  title: 'Disclaimer',
  description: 'Important disclaimer for Silvers Syndicate sports market intelligence.',
  alternates: { canonical: '/legal/disclaimer' }
};

export default function DisclaimerPage() {
  return (
    <LegalDoc
      title="Disclaimer"
      updated="June 2026"
      intro="This Disclaimer explains what Silvers Syndicate is, what it is not, and the limits of every market signal, ranking, chart, and educational page."
      sections={[
        {
          title: 'Informational and entertainment use only',
          body: [
            'Silvers Syndicate provides sports market intelligence, line movement displays, market signal labels, educational content, and research tools for informational and entertainment purposes only.',
            'Nothing on the site is financial advice, legal advice, gambling advice, betting advice, investment advice, tax advice, or professional advice of any kind.'
          ]
        },
        {
          title: 'No picks, guarantees, or promised outcomes',
          body: [
            'Silvers Syndicate does not guarantee wins, profits, prediction accuracy, successful parlays, correct rankings, or positive results. Any market signal can be wrong, incomplete, delayed, misread, or overtaken by new information.',
            'Terms like steam, resistance, coin flip, chaos, market anomaly, Top-3 containment, risk level, and confidence are analytical labels. They are not instructions to wager and are not guarantees.'
          ]
        },
        {
          title: 'Not a sportsbook or gambling operator',
          body: [
            'Silvers Syndicate does not accept wagers, place bets, hold player funds, process gambling transactions, set official odds, or act as a sportsbook, bookmaker, tipster, agent, or gambling operator.',
            'Users who choose to wager elsewhere do so independently and are solely responsible for their decisions, losses, compliance, taxes, and conduct.'
          ]
        },
        {
          title: 'No affiliation',
          body: [
            'Silvers Syndicate is not affiliated with, endorsed by, sponsored by, or approved by any league, team, athlete, sportsbook, oddsmaker, media company, data provider, payment provider, or regulator.',
            'Team names, league names, event names, and sportsbook names may appear for identification, comparison, commentary, navigation, or analysis only. Official logos should not be used at launch unless properly licensed or otherwise authorized.'
          ]
        },
        {
          title: 'Data limitations',
          body: [
            'Market data can be delayed, unavailable, incomplete, stale, misformatted, changed by providers, or affected by technical issues. We may use previews, demos, test data, or provider data depending on environment and availability.',
            'Users should verify any time-sensitive information directly with the relevant source before making independent decisions.'
          ]
        },
        {
          title: 'Assumption of risk',
          body: [
            'Sports outcomes are uncertain. Wagering involves risk and can result in financial loss. Never wager more than you can afford to lose. Seek help if wagering stops being recreational or causes harm.',
            'Use of Silvers Syndicate is at your own risk. You remain responsible for every decision made before, during, and after using the site.'
          ]
        }
      ]}
    />
  );
}
