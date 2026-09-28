import Link from 'next/link';
import type { Metadata } from 'next';

const pages = [
 ['arbitrage-calculator','Arbitrage Calculator','Calculate stake allocation across opposing prices and understand when combined implied probability creates an arbitrage.'],
 ['surebet-calculator','Surebet Calculator','Check two-way and three-way prices for a mathematical surebet and learn how balanced payouts work.'],
 ['sportsbook-odds-comparison','Sportsbook Odds Comparison','Learn why comparing the same market across sportsbooks matters and how price differences create opportunities.'],
 ['what-is-sports-arbitrage','What Is Sports Arbitrage?','A plain-English guide to arbitrage, implied probability, execution risk, stale prices and market settlement.'],
 ['arbitrage-examples','Sports Arbitrage Examples','Walk through transparent two-way and three-way arbitrage examples using American and decimal odds.'],
 ['implied-probability-calculator','Implied Probability Calculator','Convert American odds into implied probability and understand what the price says before removing vig.'],
 ['american-odds-calculator','American Odds Calculator','Convert signed American odds into decimal odds, implied probability and potential payout concepts.'],
 ['sportsbook-vig-calculator','Sportsbook Vig Calculator','Understand sportsbook hold, overround and no-vig probability using opposing market prices.'],
 ['sports-betting-line-movement','Sports Betting Line Movement','Learn what line movement measures, why prices move and why timing and confirmation matter.'],
 ['middle-calculator','Middle Calculator','Understand middle opportunities, the interval between prices and why a middle is different from guaranteed arbitrage.'],
 ['mlb-arbitrage','MLB Arbitrage','How moneylines, run lines and totals can produce cross-sportsbook price differences in baseball.'],
 ['nfl-arbitrage','NFL Arbitrage','How NFL moneylines, spreads and totals can diverge across sportsbooks and what to verify before acting.'],
 ['nba-arbitrage','NBA Arbitrage','How fast-moving NBA moneylines, spreads and totals can create temporary cross-book price differences.'],
 ['nhl-arbitrage','NHL Arbitrage','How NHL moneylines, puck lines and totals can differ across books and why settlement rules matter.'],
 ['market-research','InQsi Market Research','The home for reproducible InQsi research based on aggregated market observations, methodology and clearly stated sample windows.']
] as const;

export const metadata: Metadata = {
 title:'Sports Arbitrage Guides, Calculators & Research',
 description:'Free InQsi guides and calculators for sports arbitrage, surebets, implied probability, American odds, sportsbook vig, line movement, middles and market research.',
 alternates:{canonical:'/learn/arbitrage'}
};

const schema={'@context':'https://schema.org','@type':'CollectionPage',name:'InQsi Sports Arbitrage Learning Center',description:'Sports arbitrage guides, calculators and market research.',hasPart:pages.map(([slug,title])=>({'@type':'WebPage',name:title,url:`https://inqsi.app/learn/arbitrage/${slug}`}))};

export default function Page(){
 return <main className="shell">
  <script type="application/ld+json" dangerouslySetInnerHTML={{__html:JSON.stringify(schema)}}/>
  <section className="hero-card glass-card" style={{marginTop:24}}>
   <p className="eyebrow blue">INQSI LEARNING CENTER</p><h1>Sports arbitrage, explained with the math visible.</h1>
   <p className="hero-copy">Use the calculators and guides to understand prices, implied probability, sportsbook hold, arbitrage, middles and line movement. Educational tools do not place wagers.</p>
   <div className="hero-actions"><Link className="primary-button large" href="/arbitrage-v2">Open ARB Finder</Link><Link className="ghost-button large" href="/arbitrage-v2/calculator">Open Live Calculator</Link></div>
  </section>
  <section className="status-row" style={{flexWrap:'wrap'}}>
   {pages.map(([slug,title,detail])=><article className="status-card" key={slug} style={{minWidth:260,flex:'1 1 300px'}}><span>Guide / Tool</span><strong>{title}</strong><p>{detail}</p><Link href={`/learn/arbitrage/${slug}`}>Read more →</Link></article>)}
  </section>
 </main>;
}
