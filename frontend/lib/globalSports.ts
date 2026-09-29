export type SportIdentity={providerKey:string;family:string;competition:string;slug:string;label:string};

const familyLabels:Record<string,string>={
 americanfootball:'American Football',aussierules:'Aussie Rules',baseball:'Baseball',basketball:'Basketball',
 boxing:'Boxing',cricket:'Cricket',golf:'Golf',handball:'Handball',icehockey:'Ice Hockey',
 lacrosse:'Lacrosse',mma:'MMA',rugbyunion:'Rugby Union',rugbyleague:'Rugby League',
 soccer:'Soccer',tennis:'Tennis',tabletennis:'Table Tennis',darts:'Darts'
};

export function slugify(value:string){return String(value||'').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'');}

export function identityFromProviderKey(providerKey:string,title?:string):SportIdentity{
 const key=String(providerKey||'').trim().toLowerCase();
 const first=key.split('_')[0]||'sport';
 const family=familyLabels[first]?first:(key.startsWith('icehockey_')?'icehockey':first);
 const competition=key.slice(family.length+1)||key;
 return {providerKey:key,family,competition,slug:slugify(key),label:title||competition.split('_').map(x=>x.toUpperCase()===x?x:x.charAt(0).toUpperCase()+x.slice(1)).join(' ')};
}

export function familyLabel(family:string){return familyLabels[family]||family.replace(/[-_]/g,' ').replace(/\b\w/g,c=>c.toUpperCase());}

export function coveragePath(providerKey:string){const i=identityFromProviderKey(providerKey);return `/sports/${slugify(i.family)}/${i.slug}`;}

export function isIndexableCoverage(row:{nEvents?:number;bookCount?:number;hasUsefulContent?:boolean}){
 return Boolean(row.hasUsefulContent) || Number(row.nEvents||0)>0 && Number(row.bookCount||0)>=2;
}
