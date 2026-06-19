import type { Metadata } from 'next';
import { LegalDoc } from '@/components/LegalDoc';

export const metadata: Metadata = {
  title: 'Safe Use',
  description: 'Safe use rules and member expectations for Silvers Syndicate.',
  alternates: { canonical: '/legal/safe-use' }
};

export default function SafeUsePage() {
  return (
    <LegalDoc
      title="Safe Use"
      updated="June 2026"
      intro="Silvers Syndicate is designed to help users slow down, question assumptions, and review market risk before making independent decisions."
      sections={[
        {
          title: 'Use the product as a research tool',
          body: [
            'The platform shows line movement, signal context, weak-leg exposure, and market pressure. It is not a promise of any result and should not be treated as an instruction.',
            'A responsible user treats every market view as one research input, not as a guarantee or replacement for personal judgment.'
          ]
        },
        {
          title: 'Set personal limits',
          body: [
            'Sports outcomes are uncertain. Users should set personal limits, avoid emotional decisions, and never risk money needed for essential expenses.',
            'If sports-related activity creates stress, secrecy, debt, relationship issues, work issues, or loss of control, stop and seek qualified support.'
          ]
        },
        {
          title: 'Age and location rules',
          body: [
            'Use Silvers Syndicate only if you are old enough and legally permitted in your location to use sports market intelligence connected to sports wagering decision making.',
            'Rules vary by jurisdiction. You are responsible for knowing and following the laws and platform rules that apply to you.'
          ]
        },
        {
          title: 'No abuse or circumvention',
          body: [
            'Do not use the service to evade platform rules, commit fraud, manipulate markets, coordinate unlawful activity, exploit technical vulnerabilities, or violate third-party terms.',
            'Do not use bots, scraping tools, credential sharing, or automated systems to bypass access controls or replicate proprietary data.'
          ]
        }
      ]}
    />
  );
}
