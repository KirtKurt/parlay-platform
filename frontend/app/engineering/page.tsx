import type { Metadata } from 'next';
import { EngineeringConsole } from '@/components/EngineeringConsole';
export const metadata: Metadata = { title: 'Engineering Console', robots: { index: false, follow: false } };
export default function Page() { return <EngineeringConsole />; }
