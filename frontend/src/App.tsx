import { lazy, Suspense, useEffect, useState } from "react";
import { Button, Tooltip } from "antd";
import {
  ApartmentOutlined,
  ExperimentOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  HomeOutlined,
  QuestionCircleOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import Workbench from "./Workbench";
import Home from './Home';
const Evaluations = lazy(() => import("./Evaluations"));
const Help = lazy(() => import("./Help"));
const currentPage = () => {
  const key=location.hash.split('?')[0];
  return key==='#help'?'help':key==='#evaluations'?'evaluations':key==='#workbench'?'workbench':'home';
};
export default function App() {
  const [page, setPage] = useState<"home" | "workbench" | "evaluations" | "help">(currentPage);
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => {
    const update = () =>
      setPage(currentPage());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  const navigate = (target: typeof page) => {
    setPage(target);
    location.hash = target;
  };
  return (
    <div className={`app-shell ${collapsed ? "is-collapsed" : ""}`}>
      <aside className="sidebar">
        <a
          className="brand"
          href="#home"
          onClick={() => navigate("home")}
        >
          <span className="brand-mark">
            <ApartmentOutlined />
          </span>
          <span className="brand-copy">
            <strong>Apex 知识助手</strong>
            <small>信用卡客服 · 检索与评估</small>
          </span>
        </a>
        <div className="nav-caption">工作空间</div>
        <nav aria-label="主导航">
          {[
            {key:'home' as const,icon:<HomeOutlined/>,label:'首页',sub:'Workspace overview'},
            {
              key: "workbench" as const,
              icon: <MessageOutlined />,
              label: "对话工作台",
              sub: "Knowledge workspace",
            },
            {
              key: "evaluations" as const,
              icon: <ExperimentOutlined />,
              label: "评测实验",
              sub: "Evaluation lab",
            },
            {key:'help' as const,icon:<QuestionCircleOutlined/>,label:'帮助与使用指南',sub:'Help & getting started'},
          ].map((item) => (
            <Tooltip
              key={item.key}
              title={item.label}
              placement="right"
            >
              <button
                className={`nav-item ${page === item.key ? "active" : ""}`}
                onClick={() => navigate(item.key)}
                aria-label={item.label}
                aria-current={page === item.key ? "page" : undefined}
              >
                {item.icon}
                <span>
                  {item.label}
                  <small>{item.sub}</small>
                </span>
              </button>
            </Tooltip>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className="footer-icon">
            <SafetyCertificateOutlined />
          </span>
          <span>
            <b>可追溯 · 可复核</b>
            <small>技术评估工作空间</small>
          </span>
        </div>
        <Button
          className="collapse-button"
          type="text"
          icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
        >
          {collapsed ? null : "收起侧栏"}
        </Button>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            工作空间 <span>/</span>{" "}
            <b>{page === 'home' ? '首页' : page === "workbench" ? "对话工作台" : page === 'help' ? '帮助与使用指南' : "评测实验"}</b>
          </div>
          <span className="workspace-badge">
            <span /> 实时 API 工作空间
          </span>
        </header>
        <main className={page==='workbench'?'workbench-page':''}>
          {page === 'home' && <Home/>}
          {page === 'help' && <Suspense fallback={<div role="status">正在加载使用指南…</div>}><Help/></Suspense>}
          <div hidden={page!=='workbench'}><Workbench/></div>
          {page === 'evaluations' && (
            <Suspense
              fallback={
                <div role="status" className="page-heading">
                  正在加载评测工作区…
                </div>
              }
            >
              <Evaluations />
            </Suspense>
          )}
          <footer className="main-footer" hidden={page==='workbench'}>
            <span>APEX · 信用卡客服检索与 RAG 技术评估</span>
            <span>答案以原始政策证据为依据</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
