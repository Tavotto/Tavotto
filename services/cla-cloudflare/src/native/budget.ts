import type { D1Database, D1Statement } from '../core/types.ts';
import { Rejected } from '../core/types.ts';

// One scheduled invocation shares these counters through the entire call graph.
// These ceilings leave headroom below Free's 50 external requests / 50 D1 queries.
export const FREE_BUDGET = Object.freeze({heads:1,external:40,externalWork:35,sql:40,sqlWork:20,
  workMs:60_000,cleanupMs:90_000,leaseMarginMs:30_000});
// Verification uses eight fixed SQL statements plus two per non-exempt person.
export const VERIFICATION_FINAL_SQL = 3;
export const VERIFICATION_FIXED_SQL = 2+3+VERIFICATION_FINAL_SQL;
export const QUALIFICATION_SQL_PER_PERSON = 2;
export const MAX_SUCCESS_CONTRIBUTORS = Math.floor((FREE_BUDGET.sqlWork-VERIFICATION_FIXED_SQL)/QUALIFICATION_SQL_PER_PERSON);
export class BudgetDeferred extends Rejected {
  readonly reason:string;
  constructor(reason:string) {super('budget_deferred');this.reason=reason;}
}
export class InvocationBudget {
  private readonly clock:()=>number; private readonly wall:()=>number;
  private workEnd:number; private cleanupEnd:number;
  private phase:'work'|'correction'|'settlement'='work';
  private sql=0; private external=0;
  constructor(clock=()=>performance.now(),wall=()=>Date.now()) {
    this.clock=clock;this.wall=wall;const start=clock();
    this.workEnd=start+FREE_BUDGET.workMs;this.cleanupEnd=start+FREE_BUDGET.cleanupMs;
  }
  bindLease(until:number,databaseNow?:number):void {
    // Production uses the clock returned by the claiming SQL statement. The
    // invocation-relative deadline also includes claim transit/setup time.
    this.cleanupEnd=Math.min(this.cleanupEnd,this.clock()+until*1000-(databaseNow===undefined?this.wall():databaseNow*1000)-FREE_BUDGET.leaseMarginMs);
    this.workEnd=Math.min(this.workEnd,this.cleanupEnd-FREE_BUDGET.leaseMarginMs);
  }
  checkWork():void {
    if(this.phase==='settlement' || this.clock()>=(this.phase==='work'?this.workEnd:this.cleanupEnd))throw new BudgetDeferred('time');
  }
  correction():void {
    this.checkWork();if(this.external+5>FREE_BUDGET.external)throw new BudgetDeferred('external');
    this.phase='correction';
  }
  settlement():void {this.phase='settlement';}
  reserveWorkSQL(count:number):void {
    this.checkWork();
    if(!Number.isSafeInteger(count) || count<0 || this.sql+count>FREE_BUDGET.sqlWork)throw new BudgetDeferred('sql');
  }
  chargeSQL(count:number):void {
    if(!Number.isSafeInteger(count) || count<1)throw new Rejected('sql_count_unavailable');
    if(this.clock()>=(this.phase==='settlement'?this.cleanupEnd:this.workEnd))throw new BudgetDeferred('time');
    if(this.sql+count>(this.phase==='settlement'?FREE_BUDGET.sql:FREE_BUDGET.sqlWork))throw new BudgetDeferred('sql');
    this.sql+=count; // Attempts, including failed transactions, are never refunded.
  }
  externalSignal():AbortSignal {
    this.checkWork();const cap=this.phase==='work'?FREE_BUDGET.externalWork:FREE_BUDGET.external;
    if(this.external>=cap)throw new BudgetDeferred('external');
    this.external++;
    return AbortSignal.timeout(Math.max(1,Math.ceil(Math.min(10_000,(this.phase==='work'?this.workEnd:this.cleanupEnd)-this.clock()))));
  }
  snapshot():{sql:number;external:number} {return {sql:this.sql,external:this.external};}
}

// Ignore separators inside SQLite strings, identifiers and comments. Trigger bodies
// conservatively overcount; the background path executes no DDL.
export function sqlStatements(sql:string):number {
  let count=0,content=false,quote='',line=false,block=false;
  for(let i=0;i<sql.length;i++) {
    const c=sql[i]!,next=sql[i+1];
    if(line){if(c==='\n')line=false;continue;}
    if(block){if(c==='*' && next==='/'){block=false;i++;}continue;}
    if(quote){if(c===quote){if(next===quote && quote!==']')i++;else quote='';}continue;}
    if(c==='-' && next==='-'){line=true;i++;continue;}
    if(c==='/' && next==='*'){block=true;i++;continue;}
    if(c===';' ){if(content)count++;content=false;continue;}
    if(!/\s/.test(c)){content=true;if(c==="'" || c==='"' || c==='`' || c==='[')quote=c==='['?']':c;}
  }
  return Math.max(1,count+(content?1:0));
}
export function meterDatabase(db:D1Database,budget:InvocationBudget):D1Database {
  const statements=new WeakMap<D1Statement,{raw:D1Statement;count:number}>();
  const wrap=(raw:D1Statement,count:number):D1Statement=>{
    const wrapped:D1Statement={bind:(...values)=>wrap(raw.bind(...values),count),
      first:async<T>()=>{budget.chargeSQL(count);return raw.first<T>();},
      all:async<T>()=>{budget.chargeSQL(count);return raw.all<T>();},
      run:async()=>{budget.chargeSQL(count);return raw.run();}};
    statements.set(wrapped,{raw,count});return wrapped;
  };
  return {prepare:sql=>wrap(db.prepare(sql),sqlStatements(sql)),batch:async<T>(ss:D1Statement[])=>{
    const entries=ss.map(s=>{const entry=statements.get(s);if(!entry)throw new Rejected('unmetered_statement');return entry;});
    if(entries.length===0)throw new Rejected('empty_batch');
    budget.chargeSQL(entries.reduce((sum,e)=>sum+e.count,0));
    return db.batch<T>(entries.map(e=>e.raw));
  }};
}
