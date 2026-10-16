import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

export type Job = {
  id: string; kind: string; status: "queued" | "running" | "done" | "failed" | "cancelled";
  progress: string | null; error: string | null; result: Record<string, unknown> | null; created_at: string;
};

/** Poll a background task until it finishes. Calls onDone once when it completes. */
export function useJob(jobId: string | null | undefined, onDone?: (j: Job) => void) {
  const [notified, setNotified] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api<Job>(`/jobs/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "done" || s === "failed" || s === "cancelled" ? false : 2000;
    },
  });
  useEffect(() => {
    const j = q.data;
    if (j && jobId && notified !== jobId && (j.status === "done" || j.status === "failed")) {
      setNotified(jobId);
      onDone?.(j);
    }
  }, [q.data, jobId, notified, onDone]);
  return q.data;
}

export async function upload<T>(path: string, form: FormData): Promise<T> {
  return api<T>(path, { method: "POST", body: form });
}
