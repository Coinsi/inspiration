import { useQuery } from "@tanstack/react-query";
import { api, type Scene, type Script } from "@/lib/api";

export function useShotContext(projectId: string, enabled = true) {
  return useQuery({
    queryKey: ["material-shot-context", projectId],
    enabled,
    queryFn: async () => {
      const scripts = await api.get<Script[]>(`/projects/${projectId}/scripts`);
      const scenes: Record<string, { script: Script; scene: Scene }> = {};
      // Bound concurrency: load scene context per script, never per shot.
      for (let i = 0; i < scripts.length; i += 4) {
        const batch = await Promise.all(
          scripts.slice(i, i + 4).map(async (script) => ({
            script,
            scenes: await api.get<Scene[]>(
              `/projects/${projectId}/scripts/${script.id}/scenes`,
            ),
          })),
        );
        for (const group of batch)
          for (const scene of group.scenes)
            scenes[scene.id] = { script: group.script, scene };
      }
      return { scripts, scenes };
    },
  });
}
