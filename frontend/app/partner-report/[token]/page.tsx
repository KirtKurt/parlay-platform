import type { Metadata } from 'next';
import { PartnerReportClient } from '@/components/PartnerReportClient';

export const metadata: Metadata = {
  title: 'Creator Partner Report',
  description: 'Private creator partner performance report.',
  robots: { index: false, follow: false }
};

export default function PartnerReportPage({ params }: { params: { token: string } }) {
  return <PartnerReportClient token={params.token} />;
}
