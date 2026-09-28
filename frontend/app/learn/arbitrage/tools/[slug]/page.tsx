import type { Metadata } from 'next';
import {notFound} from 'next/navigation';
import {ProbabilityCalculator,AmericanCalculator,VigCalculator,SurebetCalculator,MiddleCalculator} from '@/components/SeoCalculators';
import '../../../../arbitrage-v2/arb-v2.css';

const tools={
 'arbitrage-calculator':{title:'Sports Arbitrage Calculator',description:'Calculate whether two American odds create a sports arbitrage and see balanced stake allocation.',C:SurebetCalculator},
 'surebet-calculator':{title:'Surebet Calculator',description:'Check opposing American odds for a mathematical surebet and calculate balanced stakes and payout.',C:SurebetCalculator},
 'implied-probability-calculator':{title:'Implied Probability Calculator',description:'Convert signed American odds into implied probability and decimal odds.',C:ProbabilityCalculator},
 'american-odds-calculator':{title:'American Odds Calculator',description:'Convert American odds to decimal odds, implied probability and potential payout.',C:AmericanCalculator},
 'sportsbook-vig-calculator':{title:'Sportsbook Vig Calculator',description:'Calculate sportsbook overround and normalized no-vig probabilities from two-way American odds.',C:VigCalculator},
 'middle-calculator':{title:'Sports Betting Middle Calculator',description:'Measure the interval between opposing point spreads and understand whether a positive middle exists.',C:MiddleCalculator}
} as const;
export function generateStaticParams(){return Object.keys(tools).map(slug=>({slug}));}
export async function generateMetadata({params}:{params:Promise<{slug:string}>}):Promise<Metadata>{const{slug}=await params;const t=tools[slug as keyof typeof tools];if(!t)return{};return{title:t.title,description:t.description,alternates:{canonical:`/learn/arbitrage/tools/${slug}`},openGraph:{title:`${t.title} | InQsi`,description:t.description,url:`/learn/arbitrage/tools/${slug}`,type:'website'},twitter:{card:'summary_large_image',title:`${t.title} | InQsi`,description:t.description}}}
export default async function Page({params}:{params:Promise<{slug:string}>}){const{slug}=await params;const t=tools[slug as keyof typeof tools];if(!t)notFound();const C=t.C;const schema={'@context':'https://schema.org','@type':'SoftwareApplication',name:t.title,applicationCategory:'FinanceApplication',operatingSystem:'Web',description:t.description,url:`https://inqsi.app/learn/arbitrage/tools/${slug}`};return <main className="arb2 standalone"><script type="application/ld+json" dangerouslySetInnerHTML={{__html:JSON.stringify(schema)}}/><C/></main>}
