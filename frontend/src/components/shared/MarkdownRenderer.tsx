import React, { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

export const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  className = "",
}: MarkdownRendererProps) {
  if (!content) return null;

  return (
    <div className={`markdown-body text-sm text-surface-700 leading-relaxed ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h3 className="text-base font-semibold text-surface-800 mt-4 mb-2">{children}</h3>
          ),
          h2: ({ children }) => (
            <h4 className="text-sm font-semibold text-surface-800 mt-3 mb-1.5">{children}</h4>
          ),
          h3: ({ children }) => (
            <h5 className="text-sm font-medium text-surface-700 mt-2 mb-1">{children}</h5>
          ),
          p: ({ children }) => <p className="mb-2 last:mb-0 text-inherit">{children}</p>,
          ul: ({ children }) => <ul className="list-disc pl-6 mb-2 space-y-1">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal pl-6 mb-2 space-y-1">{children}</ol>,
          li: ({ children }) => <li className="text-sm pl-1 text-inherit">{children}</li>,
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent-600 hover:text-accent-700 underline"
            >
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-accent-300 pl-3 my-2 text-surface-600 italic">
              {children}
            </blockquote>
          ),
          code: ({ className, children, ...props }) => {
            const isBlock = className?.includes("language-");
            if (isBlock) {
              return (
                <div className="relative my-2">
                  <pre className="bg-surface-100 rounded-md p-3 overflow-x-auto text-xs font-mono">
                    <code className={className} {...props}>
                      {children}
                    </code>
                  </pre>
                </div>
              );
            }
            return (
              <code className="bg-surface-100 text-surface-700 px-1 py-0.5 rounded text-xs font-mono">
                {children}
              </code>
            );
          },
          pre: ({ children }) => <>{children}</>,
          table: ({ children }) => (
            <div className="overflow-x-auto my-2">
              <table className="min-w-full text-xs border border-surface-200 rounded">
                {children}
              </table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="bg-surface-100">{children}</thead>
          ),
          th: ({ children }) => (
            <th className="px-3 py-1.5 text-left font-medium text-surface-700 border-b border-surface-200">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="px-3 py-1.5 border-b border-surface-100 text-inherit">{children}</td>
          ),
          tr: ({ children }) => (
            <tr className="even:bg-surface-50">{children}</tr>
          ),
          hr: () => <hr className="my-3 border-surface-200" />,
          strong: ({ children }) => (
            <strong className="font-semibold text-surface-800">{children}</strong>
          ),
        }}
      />
    </div>
  );
});
