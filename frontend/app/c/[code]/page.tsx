import { redirect } from 'next/navigation';

export default function PartnerShortLinkPage({ params }: { params: { code: string } }) {
  redirect(`/?ref=${encodeURIComponent(params.code)}`);
}
