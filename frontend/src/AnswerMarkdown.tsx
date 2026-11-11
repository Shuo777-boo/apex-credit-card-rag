import ReactMarkdown from "react-markdown";
import type { Citation } from "./types";
export default function AnswerMarkdown({
  answer,
  citations,
  open,
}: {
  answer: string;
  citations: Citation[];
  open: (id: string) => void;
}) {
  const linkified = answer.replace(/\[(\d+)\]/g, (text, id) =>
    citations.some((c) => c.id === Number(id))
      ? `[${id}](#citation-${id})`
      : text,
  );
  return (
    <ReactMarkdown
      skipHtml
      components={{
        a: ({ href, children }) => {
          const match = /^#citation-(\d+)$/.exec(href ?? "");
          const citation = match
            ? citations.find((c) => c.id === Number(match[1]))
            : undefined;
          return citation ? (
            <button
              className="inline-citation"
              onClick={() => open(citation.doc_id)}
              aria-label={`引用 ${citation.id}，打开 ${citation.doc_id}`}
            >
              [{children}]
            </button>
          ) : (
            <span>{children}</span>
          );
        },
        img: () => null,
      }}
    >
      {linkified}
    </ReactMarkdown>
  );
}



