import { useMemo } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";

marked.setOptions({ gfm: true, breaks: true });

export function Markdown({ text, className }: { text?: string | null; className?: string }) {
  const html = useMemo(() => {
    if (!text) return "";
    const raw = marked.parse(String(text), { async: false }) as string;
    return DOMPurify.sanitize(raw);
  }, [text]);
  if (!text) return null;
  return <div className={"md " + (className || "")} dangerouslySetInnerHTML={{ __html: html }} />;
}
