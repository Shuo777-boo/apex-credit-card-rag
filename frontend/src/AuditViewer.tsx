import { useMemo, useState } from 'react';
import { Button, Pagination, Select, Space } from 'antd';
// Only serialize one selected array item, then render a bounded text page.
export default function AuditViewer({data}:{data:Record<string,unknown>}){
 const keys=Object.keys(data); const [field,setField]=useState(keys[0]);const [item,setItem]=useState(1);const [page,setPage]=useState(1);
 const value=data[field],values=Array.isArray(value)?value:null;
 const text=useMemo(()=>JSON.stringify(values?values[item-1]:value,null,2)??'null',[value,values,item]);
 const size=12000;
 const download=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='audit.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
 return <div><Space wrap><Select aria-label="原始审计字段" value={field} style={{minWidth:220}} options={keys.map(k=>({value:k,label:`${k}${Array.isArray(data[k])?` (${(data[k] as unknown[]).length} 项)`:''}`}))} onChange={v=>{setField(v);setItem(1);setPage(1)}}/><Button onClick={download}>下载完整 JSON</Button></Space>{values&&<><p>逐项阅读，共 {values.length} 项</p><Pagination simple current={item} total={values.length} pageSize={1} onChange={v=>{setItem(v);setPage(1)}}/></>}<pre className="json-block">{text.slice((page-1)*size,page*size)}</pre>{text.length>size&&<Pagination current={page} pageSize={size} total={text.length} showSizeChanger={false} onChange={setPage}/>}</div>;
}
