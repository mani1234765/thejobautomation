"use client";

import { FileUp, Loader2 } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "@/lib/toast";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/format";
import type { ResumeDetail } from "@/lib/types";

const MAX_MB = 8;

export function UploadZone({ onUploaded, compact }: { onUploaded: (r: ResumeDetail) => void; compact?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);

  const upload = async (file: File | undefined) => {
    if (!file) return;
    if (!/\.(pdf|docx)$/i.test(file.name)) return toast.error("Upload a PDF or DOCX file.");
    if (file.size > MAX_MB * 1024 * 1024) return toast.error(`Files must be under ${MAX_MB} MB.`);
    const form = new FormData();
    form.append("file", file);
    setBusy(true);
    try {
      const r = await api.upload<ResumeDetail>("/resumes", form);
      if (r.parse_status === "failed") toast.error(r.parse_error ?? "We couldn't read that file.");
      onUploaded(r);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Upload failed.");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files[0]); }}
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-dashed text-center transition-colors",
        compact ? "px-4 py-6" : "px-6 py-12",
        drag ? "border-accent bg-accent-soft/50" : "border-border-strong bg-surface-2/60 hover:bg-surface-2",
      )}
    >
      <input ref={input} type="file" accept=".pdf,.docx" className="sr-only" id="resume-file" onChange={(e) => upload(e.target.files?.[0])} />
      {busy ? (
        <>
          <Loader2 className="size-5 animate-spin text-accent" />
          <p className="mt-3 text-[13px] font-medium">Reading your resume…</p>
          <p className="mt-1 text-xs text-fg-muted">Detecting sections, experience and skills.</p>
        </>
      ) : (
        <>
          <div className="flex size-9 items-center justify-center rounded-lg border border-border bg-surface text-fg-muted shadow-sm">
            <FileUp className="size-4" />
          </div>
          <p className="mt-3 text-[13px] font-medium">
            Drop your resume here or{" "}
            <label htmlFor="resume-file" className="cursor-pointer text-accent underline-offset-2 hover:underline">browse</label>
          </p>
          <p className="mt-1 text-xs text-fg-muted">DOCX keeps your exact formatting when tailoring · PDF up to {MAX_MB} MB</p>
        </>
      )}
    </div>
  );
}
