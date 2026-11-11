import { Button, Card, Empty, Tag } from "antd";
import { ApartmentOutlined, ArrowRightOutlined } from "@ant-design/icons";
import type { GraphExpansion, GraphPath } from "./types";
export default function GraphPaths({
  paths,
  expansion,
  open,
}: {
  paths: GraphPath[];
  expansion?: GraphExpansion | null;
  open: (id: string) => void;
}) {
  return (
    <Card
      className="evaluation-card"
      title={
        <>
          <ApartmentOutlined /> 有来源的图扩展路径
        </>
      }
      extra={`${paths.length} 条`}
    >
      <p className="muted">
        路径来自本次后端结果。关系按来源原文、提取方式与版本核验，最终文档以重排列表为准。
      </p>
      {expansion && (
        <>
          <p>种子文档：{expansion.seed_doc_ids.join(" / ") || "无"}</p>
          <p>
            新增文档：
            {expansion.expanded_doc_ids.length
              ? expansion.expanded_doc_ids.map((id) => (
                  <Button type="link" key={id} onClick={() => open(id)}>
                    {id}
                  </Button>
                ))
              : "无"}
          </p>
          <p className="muted">
            实际候选：{expansion.candidate_doc_ids.join(" / ")} · 最多{" "}
            {expansion.max_hops} 跳 · 访问 {expansion.visited_nodes} 节点 ·{" "}
            {expansion.truncated ? "达到上限，已截断" : "未截断"} ·{" "}
            {expansion.version}
          </p>
        </>
      )}
      {!paths.length && <Empty description="本次没有返回图扩展路径" />}
      {paths.map((path, index) => (
        <div className="path-card" key={index}>
          <div className="path-nodes">
            <Tag color="blue">{path.hops} hops</Tag>
            {path.nodes.map((node, i) => (
              <span key={node.id}>
                {i > 0 && <ArrowRightOutlined />}{" "}
                <Tag>
                  {node.type} · {node.label}
                </Tag>
              </span>
            ))}
          </div>
          {path.edges.map((edge, i) => (
            <div className="path-edge" key={i}>
              <b>{edge.relation}</b>{" "}
              <Button
                type="link"
                size="small"
                onClick={() => open(edge.doc_id)}
              >
                {edge.doc_id} · 来源原文
              </Button>
              <p>{edge.evidence}</p>
              <span className="muted">
                提取：{edge.extraction_method} · 版本：{edge.version} · SHA256：
                {edge.source_sha256}
              </span>
            </div>
          ))}
          <div className="muted">路径涉及文档：{path.doc_ids.join(" / ")}</div>
        </div>
      ))}
    </Card>
  );
}
