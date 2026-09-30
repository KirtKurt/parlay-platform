export type SportIdentity={providerKey:string;family:string;competition:string;slug:string;label:string;geo:string;geoLabel:string};

const familyLabels:Record<string,string>={
 americanfootball:'American Football',aussierules:'Aussie Rules',baseball:'Baseball',basketball:'Basketball',
 boxing:'Boxing',cricket:'Cricket',golf:'Golf',handball:'Handball',icehockey:'Ice Hockey',
 lacrosse:'Lacrosse',mma:'MMA',rugbyunion:'Rugby Union',rugbyleague:'Rugby League',
 soccer:'Soccer',tennis:'Tennis',tabletennis:'Table Tennis',darts:'Darts'
};

export function slugify(value:string){return String(value||'').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'');}

const geoLabels:Record<string,string>={usa:'United States',uk:'United Kingdom',england:'England',scotland:'Scotland',ireland:'Ireland',australia:'Australia',japan:'Japan',korea:'South Korea',brazil:'Brazil',argentina:'Argentina',mexico:'Mexico',canada:'Canada',france:'France',germany:'Germany',italy:'Italy',spain:'Spain',netherlands:'Netherlands',belgium:'Belgium',portugal:'Portugal',turkey:'Turkey',sweden:'Sweden',finland:'Finland',denmark:'Denmark',norway:'Norway',poland:'Poland',greece:'Greece',austria:'Austria',switzerland:'Switzerland'};

function geographyFromCompetition(competition:string){const tokens=competition.split('_');const hit=tokens.find(t=>geoLabels[t]);return hit?{geo:hit,geoLabel:geoLabels[hit]}:{geo:'global',geoLabel:'Global'};}

export function identityFromProviderKey(providerKey:string,title?:string):SportIdentity{
 const key=String(providerKey||'').trim().toLowerCase();
 const first=key.split('_')[0]||'sport';
 const family=familyLabels[first]?first:(key.startsWith('icehockey_')?'icehockey':first);
 const competition=key.slice(family.length+1)||key;
 const geography=geographyFromCompetition(competition);
 return {providerKey:key,family,competition,slug:slugify(key),label:title||competition.split('_').map(x=>x.toUpperCase()===x?x:x.charAt(0).toUpperCase()+x.slice(1)).join(' '),...geography};
}

export function familyLabel(family:string){return familyLabels[family]||family.replace(/[-_]/g,' ').replace(/\b\w/g,c=>c.toUpperCase());}

export function coveragePath(providerKey:string){const i=identityFromProviderKey(providerKey);return `/sports/${slugify(i.family)}/${i.slug}`;}

export function isIndexableCoverage(row:{nEvents?:number;bookCount?:number;hasUsefulContent?:boolean}){
 return Boolean(row.hasUsefulContent) || Number(row.nEvents||0)>0 && Number(row.bookCount||0)>=2;
}
