import { AlertCircle, ChevronLeft, ChevronRight, LoaderCircle } from 'lucide-react'
import './catalog.css'

export function CatalogError({error,retry}:{error:Error;retry:()=>void}) {
  return <div className="catalog-state" role="alert"><AlertCircle/><h2>加载失败</h2><p>{error.message}</p><button className="btn secondary" onClick={retry}>重新加载</button></div>
}
export function CatalogLoading() {return <div className="catalog-state" role="status"><LoaderCircle className="spin"/>正在读取目录…</div>}
export function Pagination({total,offset,onChange}:{total:number;offset:number;onChange:(offset:number)=>void}) {
  return <div className="catalog-pagination"><span>共 {total} 条 · 第 {Math.floor(offset/20)+1} 页</span><button className="btn secondary" disabled={offset===0} onClick={()=>onChange(Math.max(0,offset-20))}><ChevronLeft size={16}/>上一页</button><button className="btn secondary" disabled={offset+20>=total} onClick={()=>onChange(offset+20)}>下一页<ChevronRight size={16}/></button></div>
}
export const taskStatus=(value:string)=>({queued:'排队中',validating:'校验中',running:'运行中',paused:'已暂停',failed:'失败',completed:'已完成',succeeded:'已完成',cancelled:'已取消'}[value]??value)
