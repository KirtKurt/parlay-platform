import { getApiSnapshot } from '@/lib/api';
import { identityFromProviderKey } from '@/lib/globalSports';

export type SeoEvent={
  id:string; game_id:string; sport_key:string; matchup:string; start:string;
  home_team:string; away_team:string; favorite?:string; favoriteMl?:number|string;
  underdog?:string; underdogMl?:number|string; spread?:string; total?:string; bookCount:number;
};

function fmtAmerican(v:any){const n=Number(v);if(!Number.isFinite(n))return v==null?'':String(v);return n>0?'+'+n:String(n);}
function spreadFromBooks(books:any[],favorite:string){const row=(books||[]).map(b=>b?.spread).find(Boolean);if(!row)return '';const homeFav=Number(row.home_point)<0;const point=homeFav?row.home_point:row.away_point;const price=homeFav?row.home_price:row.away_price;return point==null?'':favorite+' '+point+(price!=null?' ('+fmtAmerican(price)+')':'');}
function totalFromBooks(books:any[]){const row=(books||[]).map(b=>b?.total||b?.overUnder).find(Boolean);if(!row)return '';const point=row.over_point??row.point??row.total;return point==null?'':'O/U '+point;}

export async function getSeoCoverage(){
  const snapshot=await getApiSnapshot();
  const boards=Array.isArray(snapshot.liveMarket?.boards)?snapshot.liveMarket.boards:[];
  const events:SeoEvent[]=[];
  for(const board of boards){
    const providerKey=String(board?.providerSportKey||board?.sportKey||board?.sport_key||'').trim();
    if(!providerKey)continue;
    for(const raw of board?.games||[]){
      const books=Array.isArray(raw?.books)?raw.books:[];
      const primary=books[0]||{};
      const ml=primary?.moneyline||{};
      const home=String(raw?.homeTeam||raw?.home_team||'Home');
      const away=String(raw?.awayTeam||raw?.away_team||'Away');
      const homeMl=ml.home,awayMl=ml.away;
      const homeFav=homeMl!=null&&awayMl!=null&&Number(homeMl)<Number(awayMl);
      const favorite=homeMl!=null&&awayMl!=null?(homeFav?home:away):undefined;
      const underdog=favorite?(favorite===home?away:home):undefined;
      const favoriteMl=favorite===home?homeMl:awayMl;
      const underdogMl=favorite===home?awayMl:homeMl;
      events.push({
        id:String(raw?.gameId||raw?.id||providerKey+'-'+away+'-'+home),
        game_id:String(raw?.gameId||raw?.id||providerKey+'-'+away+'-'+home),
        sport_key:providerKey,
        matchup:away+' @ '+home,
        start:String(raw?.commenceTime||raw?.commence_time||''),
        home_team:home,away_team:away,favorite,favoriteMl,underdog,underdogMl,
        spread:favorite?spreadFromBooks(books,favorite):'',
        total:totalFromBooks(books),
        bookCount:Number(raw?.bookCount||books.length||0)
      });
    }
  }
  const sports=Array.from(new Map<string,ReturnType<typeof identityFromProviderKey>&{eventCount:number;bookCount:number}>(events.map(e=>{const i=identityFromProviderKey(e.sport_key);return [e.sport_key,{...i,eventCount:0,bookCount:0}] as const;})).values());
  for(const sport of sports){const rows=events.filter(e=>e.sport_key===sport.providerKey);sport.eventCount=rows.length;sport.bookCount=Math.max(0,...rows.map(e=>e.bookCount));}
  return {events,sports,apiStatus:snapshot.apiStatus,apiDetail:snapshot.apiDetail};
}
