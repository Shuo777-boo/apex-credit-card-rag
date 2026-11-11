import { afterEach, expect, it } from 'vitest';
import { boundedHistory, CHAT_KEY, loadSessions, newSession, type Turn } from '../src/chat-store';
import type { AnswerResponse } from '../src/types';
const turn=(question:string,answer:string,insufficient=false):Turn=>({id:question,question,mode:'dense',tier:null,retrievalOnly:false,state:'completed',createdAt:'test',answer:{answer,evidence_insufficient:insufficient} as AnswerResponse});
afterEach(()=>localStorage.clear());
it('keeps the latest refused topic and stops at failed or oversized turns rather than reviving older context',()=>{
 const first=turn('Platinum?','Policy'),second=turn('Travel?','Insufficient evidence',true);
 expect(boundedHistory([first,second]).map(m=>m.content)).toEqual(['Platinum?','Policy','Travel?','Insufficient evidence']);
 expect(boundedHistory([first,{...second,state:'failed',answer:undefined}])).toEqual([]);
 expect(boundedHistory([first,turn('Large','x'.repeat(4001))])).toEqual([]);
 expect(boundedHistory(Array.from({length:5},(_,i)=>turn(String(i),'a')))).toHaveLength(6);
});
it('restores pending local turns as interrupted without resending',()=>{
 const session=newSession();session.turns=[{...turn('Pending',''),state:'pending',answer:undefined}];
 localStorage.setItem(CHAT_KEY,JSON.stringify([session]));
 const restored=loadSessions();expect(restored[0].turns[0].state).toBe('interrupted');expect(restored[0].turns[0].error).toContain('不会自动重新发送');
});
