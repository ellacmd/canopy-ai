import { useEffect, useState } from "react";
import { api, describeError } from "./api";

type Resource<T> =
  | { state: "loading" }
  | { state: "ready"; data: T }
  | { state: "error"; error: string };

export function useResource<T>(path: string | null) {
  const [version, setVersion] = useState(0);
  const [result, setResult] = useState<{ path: string; version: number; resource: Resource<T> }>();

  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    async function load(url: string) {
      try {
        const data = await api<T>(url, { signal: controller.signal });
        if (!controller.signal.aborted) setResult({ path: url, version, resource: { state: "ready", data } });
      } catch (error) {
        if (!controller.signal.aborted) setResult({ path: url, version, resource: { state: "error", error: describeError(error) } });
      }
    }
    void load(path);
    return () => controller.abort();
  }, [path, version]);

  const resource: Resource<T> =
    result && result.path === path && result.version === version ? result.resource : { state: "loading" };
  return { resource, reload: () => setVersion((previous) => previous + 1) };
}
