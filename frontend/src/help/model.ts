import source from './content.json';
export interface GuideImage {file:string;alt:string;caption:string}
export interface GuideSection {
  id:string;title:string;summary:string;goal?:string;audience?:string;path?:string;
  paragraphs?:string[];fields?:{name:string;required:string;example:string;advice:string}[];
  steps?:{action:string;expected:string}[];images?:GuideImage[];checks?:string[];
  errors?:{problem:string;solution:string}[];faqs?:{question:string;answer:string}[];
  advanced?:{title:string;paragraphs:string[];code?:string};next?:string;link?:{href:string;label:string};
}
export const guide = source as {title:string;version:string;intro:string;example:string;sections:GuideSection[]};
export function matchesSection(section:GuideSection, query:string) {
  const text=JSON.stringify(section).normalize('NFKC').toLocaleLowerCase();
  return query.normalize('NFKC').trim().toLocaleLowerCase().split(/\s+/).every(term=>text.includes(term));
}
