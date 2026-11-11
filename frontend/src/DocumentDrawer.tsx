import { Alert, Button, Descriptions, Drawer, Skeleton, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { api, errorText } from "./api";
export default function DocumentDrawer({
  id,
  onClose,
}: {
  id: string | null;
  onClose: () => void;
}) {
  const query = useQuery({
    queryKey: ["document", id],
    queryFn: ({ signal }) => api.document(id!, signal),
    enabled: !!id,
  });
  return (
    <Drawer
      title="政策原文 · 来源核验"
      open={!!id}
      onClose={onClose}
      width={640}
      zIndex={1500}
    >
      {query.isPending && <Skeleton active />}
      {query.error && (
        <Alert
          type="error"
          showIcon
          message="原文加载失败"
          description={errorText(query.error)}
          action={<Button onClick={() => query.refetch()}>重试</Button>}
        />
      )}
      {query.data && (
        <>
          <Tag color="blue">{query.data.doc_id}</Tag>
          <h2>{query.data.title}</h2>
          <Descriptions
            column={1}
            items={[
              { key: "tier", label: "卡种", children: query.data.card_tier },
              { key: "category", label: "分类", children: query.data.category },
              {
                key: "year",
                label: "政策年份",
                children: query.data.effective_year,
              },
            ]}
          />
          <div className="policy-content">{query.data.content}</div>
        </>
      )}
    </Drawer>
  );
}
