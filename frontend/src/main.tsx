import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "antd/dist/reset.css";
import "./styles.css";
const client = new QueryClient({
  defaultOptions: {
    queries: { retry: false, staleTime: 30_000, refetchOnWindowFocus: false },
    mutations: { retry: false },
  },
});
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          token: {
            colorPrimary: "#0968fa",
            borderRadius: 12,
            colorText: "#142952",
            colorTextSecondary: "#7385a5",
            fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif',
          },
          components: {
            Card: { paddingLG: 24 },
            Button: { controlHeight: 40 },
          },
        }}
      >
        <App />
      </ConfigProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
